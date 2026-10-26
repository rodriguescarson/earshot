"""Speech synthesis through Azure AI Speech, keyless (Entra), cached per unique SSML.

The cache key is the full SSML document (text, language, voice and prosody), so a line is
synthesized once and replayed for every listener and every re-render. Pan is applied after
synthesis, so it is not part of the key here.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr

import httpx
import numpy as np
from azure.identity import DefaultAzureCredential

from earshot.agents.catalog import (
    LOCALES,
    STANDARD_FOR,
    VOICES_HD,
    VOICES_STANDARD,
    is_hd,
    load,
    placeholders,
    voice_tier,
)
from earshot.contract import Language, Line, VoiceRole

SAMPLE_RATE = 24_000
OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"
OUTPUT_FORMAT_MP3 = "audio-24khz-96kbitrate-mono-mp3"
MP3_BITRATE = 96_000
"""The streamed path asks the service for compressed audio and serves it as it is: no decode, no
ffmpeg, no per-pan file. Pan happens in the browser (EARSHOT_AUDIO_STREAM=1)."""
SCOPE = "https://cognitiveservices.azure.com/.default"
log = logging.getLogger(__name__)


def voice_for(language: Language, role: VoiceRole) -> str:
    table = VOICES_HD if voice_tier() == "hd" else VOICES_STANDARD
    facts, explanation = table[language]
    return explanation if role is VoiceRole.EXPLANATION else facts


def fallback_ssml(ssml: str) -> str | None:
    """The same document on the standard voice, if it names an HD voice; else None."""
    for hd, std in STANDARD_FOR.items():
        if f'name="{hd}"' in ssml:
            return ssml.replace(f'name="{hd}"', f'name="{std}"', 1)
    return None


def ssml_for(line: Line, *, excited: bool = False) -> str:
    cat = load(line.language)
    parts = []
    for tid in line.template_id.split("+"):  # Analyst lines join a claim template and a cause template
        names = placeholders(cat.template(tid, line.listening_mode))
        parts.append(
            cat.ssml_body(tid, line.listening_mode, {k: v for k, v in line.slots.items() if k in names})
        )
    body = " ".join(parts)
    if line.late:
        body = f"{cat.ssml_body('late.1', line.listening_mode, {})}{body}"
    voice = voice_for(line.language, line.voice_role)
    if is_hd(voice):
        # HD voices read the emotion off the text; a prosody wrapper only flattens them (A/B, 2026-10-26).
        inner = body
    else:
        rate, pitch = ("+12%", "+6%") if excited else ("+4%", "+0%")
        inner = f"<prosody rate={quoteattr(rate)} pitch={quoteattr(pitch)}>{body}</prosody>"
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f"xml:lang={quoteattr(LOCALES[line.language])}>"
        f"<voice name={quoteattr(voice)}>{inner}</voice></speak>"
    )


@dataclass(frozen=True)
class Clip:
    path: Path
    duration_ms: int


class Synthesizer:
    def __init__(self, cache_dir: Path, endpoint: str | None = None) -> None:
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._endpoint = endpoint
        self._cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self._token: tuple[str, float] | None = None
        self._http = httpx.Client(timeout=30)
        self.characters = 0
        """Characters sent to the service this session (cache hits cost nothing)."""

    @property
    def endpoint(self) -> str:
        """Resolved on the first synthesis, so a cache-only Synthesizer needs no endpoint at all."""
        return (self._endpoint or os.environ["EARSHOT_AI_ENDPOINT"]).rstrip("/")

    def _bearer(self) -> str:
        if self._token is None or self._token[1] - time.time() < 120:
            tok = self._cred.get_token(SCOPE)
            self._token = (tok.token, tok.expires_on)
        return self._token[0]

    def _path(self, ssml: str, ext: str = "wav") -> Path:
        prefix = "v3" if ext == "mp3" else "v2"
        return self.cache_dir / f"{prefix}-{hashlib.sha256(ssml.encode()).hexdigest()[:32]}.{ext}"

    def cached(self, ssml: str) -> Clip | None:
        """The clip if it is already in the cache; never calls the service."""
        path = self._path(ssml)
        return Clip(path, wav_duration_ms(path)) if path.exists() else None

    def cached_mp3(self, ssml: str) -> Clip | None:
        path = self._path(ssml, "mp3")
        return Clip(path, mp3_duration_ms(path)) if path.exists() else None

    def synthesize_mp3(self, ssml: str) -> Clip:
        """Compressed mono speech straight from the service, cached under its own key."""
        path = self._path(ssml, "mp3")
        if not path.exists():
            data = self._request(ssml, OUTPUT_FORMAT_MP3)
            tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.part")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            self.characters += len(ssml)
        return Clip(path, mp3_duration_ms(path))

    def synthesize(self, ssml: str) -> Clip:
        path = self._path(ssml)
        if not path.exists():
            data = self._request(ssml, OUTPUT_FORMAT)
            # A unique scratch name: two lines with the same SSML synthesized at once used to share one
            # ".part" file, and the second rename failed (load test, 2026-10-17: 43 lines aired as text).
            tmp = path.with_suffix(f".{uuid.uuid4().hex[:8]}.part")
            try:
                tmp.write_bytes(trim_silence(data))
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            self.characters += len(ssml)
        return Clip(path, wav_duration_ms(path))

    def _request(self, ssml: str, output_format: str) -> bytes:
        """One synthesis call with retries; falls back to the standard voice if an HD voice is refused."""
        if True:
            r: httpx.Response | None = None
            for attempt in range(5):
                try:
                    r = self._http.post(
                        f"{self.endpoint}/tts/cognitiveservices/v1",
                        headers={
                            "Authorization": f"Bearer {self._bearer()}",
                            "Content-Type": "application/ssml+xml",
                            "X-Microsoft-OutputFormat": output_format,
                            "User-Agent": "earshot",
                        },
                        content=ssml.encode(),
                    )
                except httpx.TransportError:  # timeouts and dropped connections are transient
                    if attempt == 4:
                        raise
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if (r.status_code == 429 or r.status_code >= 500) and attempt < 4:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if 400 <= r.status_code < 500 and (alt := fallback_ssml(ssml)) is not None:
                    # The service refused an HD voice (preview withdrawn, region): the standard voice
                    # says the same line instead.
                    log.warning("HD voice refused (%s); falling back to the standard voice", r.status_code)
                    return self._request(alt, output_format)
                r.raise_for_status()
                break
            assert r is not None
            return r.content


def trim_silence(wav_bytes: bytes, threshold_db: float = -45.0, pad_ms: int = 40) -> bytes:
    """Cut leading and trailing silence so a line's airtime is its speech, not the service's padding."""
    with wave.open(io.BytesIO(wav_bytes)) as w:
        params = w.getparams()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    loud = np.flatnonzero(np.abs(x.astype(np.int32)) > 32768 * 10 ** (threshold_db / 20))
    if len(loud) == 0:
        return wav_bytes
    pad = int(params.framerate * pad_ms / 1000)
    x = x[max(0, loud[0] - pad) : loud[-1] + pad]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def mp3_duration_ms(path: Path) -> int:
    """Constant-bitrate MP3 from the service, no tags: duration follows from the size."""
    return int(path.stat().st_size * 8 / MP3_BITRATE * 1000)


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path)) as w:
        return int(w.getnframes() / w.getframerate() * 1000)


def read_mono(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SAMPLE_RATE
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def read_stereo(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getsampwidth() == 2 and w.getframerate() == SAMPLE_RATE
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        return x.reshape(-1, 2) if w.getnchannels() == 2 else np.repeat(x[:, None], 2, axis=1)


def write_stereo(path: Path, stereo: np.ndarray) -> None:
    pcm = (np.clip(stereo, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    path.write_bytes(buf.getvalue())
