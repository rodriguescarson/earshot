# /// script
# requires-python = ">=3.12"
# dependencies = ["azure-identity>=1.19", "openai>=1.80", "requests>=2.32", "numpy>=2.0"]
# ///
"""P0 smoke test: one chat call and two speech clips, keyless (Entra) auth only.

Uses the signed-in `az login` identity. No API keys exist on the resource
(disableLocalAuth=true), so a key-based call failing is expected and correct.

Writes p0/evidence/smoke.json (latencies, model, voices) and stereo WAVs panned
left/right, the way Earshot places the ball in sound.
"""

from __future__ import annotations

import io
import json
import os
import time
import wave
from pathlib import Path

import numpy as np
import requests
from azure.identity import AzureCliCredential
from openai import OpenAI

ENDPOINT = os.environ["EARSHOT_AI_ENDPOINT"].rstrip("/")
DEPLOYMENT = os.environ.get("EARSHOT_FAST_DEPLOYMENT", "fast")
SCOPE = "https://cognitiveservices.azure.com/.default"
OUT = Path(__file__).parent / "evidence"
LINES = [
    ("en-GB", "Minute 63. Goal, Kestrels, Okafor. Kestrels 2, Copperline 1.", -0.6),
    ("ar-SA", "الدقيقة 63. هدف لكيستريلز، أوكافور. كيستريلز 2، كوبرلاين 1.", 0.6),
]


def token() -> str:
    return AzureCliCredential().get_token(SCOPE).token


def chat() -> dict:
    client = OpenAI(base_url=f"{ENDPOINT}/openai/v1/", api_key=token())
    t0 = time.perf_counter()
    r = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[{"role": "user", "content": "Reply with exactly: Earshot P0 ok"}],
        max_completion_tokens=20,
    )
    return {
        "latency_ms": round((time.perf_counter() - t0) * 1000),
        "model": r.model,
        "reply": r.choices[0].message.content,
        "usage": r.usage.model_dump() if r.usage else None,
    }


def voices(headers: dict) -> dict[str, str]:
    r = requests.get(f"{ENDPOINT}/tts/cognitiveservices/voices/list", headers=headers, timeout=30)
    r.raise_for_status()
    picked: dict[str, str] = {}
    for v in r.json():
        loc = v["Locale"]
        if loc in {"en-GB", "ar-SA"} and loc not in picked and v.get("VoiceType") == "Neural":
            picked[loc] = v["ShortName"]
    return picked


def synth(headers: dict, locale: str, voice: str, text: str) -> tuple[bytes, int]:
    ssml = f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>{text}</voice></speak>"
    h = headers | {
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        "User-Agent": "earshot-p0",
    }
    t0 = time.perf_counter()
    r = requests.post(f"{ENDPOINT}/tts/cognitiveservices/v1", headers=h, data=ssml.encode(), timeout=60)
    r.raise_for_status()
    return r.content, round((time.perf_counter() - t0) * 1000)


def pan_to_stereo(mono_wav: bytes, pan: float, path: Path) -> float:
    """Constant-power pan, pan in [-1, 1]; returns clip length in seconds."""
    with wave.open(io.BytesIO(mono_wav)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
    theta = (pan + 1) * np.pi / 4
    left, right = x * np.cos(theta), x * np.sin(theta)
    stereo = np.clip(np.stack([left, right], axis=1), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(stereo.tobytes())
    return len(x) / rate


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    result: dict = {"endpoint": ENDPOINT, "auth": "Entra (az login), no keys"}
    result["chat"] = chat()
    headers = {"Authorization": f"Bearer {token()}"}
    picked = voices(headers)
    result["voices"] = picked
    result["tts"] = []
    for locale, text, pan in LINES:
        audio, ms = synth(headers, locale, picked[locale], text)
        path = OUT / f"smoke-{locale}.wav"
        secs = pan_to_stereo(audio, pan, path)
        result["tts"].append(
            {
                "locale": locale,
                "voice": picked[locale],
                "latency_ms": ms,
                "chars": len(text),
                "seconds": round(secs, 2),
                "pan": pan,
                "file": str(path.name),
            }
        )
    (OUT / "smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
