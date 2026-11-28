# One image, two entrypoints (eng D2/E-4): the worker (default) and the read-only MCP app
# (command override: uvicorn earshot.api.mcp:app). Built remotely in ACR, never on the laptop.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
# ffmpeg: every live line is served as AAC (earshot.voice.encode); without it no line airs.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir uv==0.12.5

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Stored matches: the MCP app's demo match (when no worker is publishing snapshots) and the live
# broadcaster's rotation. Development seeds only (held-out seeds wait for eval-freeze); public
# events, headers and the roster only. The simulator's truth log is never written into the image.
RUN uv run --no-dev earshot sim --seed 26 --out data/mcp-replay >/dev/null \
 && uv run --no-dev earshot roster --out data/mcp-replay/roster.json \
 && for s in 26 28 31; do uv run --no-dev earshot sim --seed $s --out data/matches >/dev/null; done \
 && for s in 26 28 31; do uv run --no-dev earshot frames --seed $s --out data/matches/m000$s.frames.npz >/dev/null; done \
 && test ! -e data/hidden

ENV PATH=/app/.venv/bin:$PATH PORT=8080 EARSHOT_MCP_REPLAY=/app/data/mcp-replay \
    EARSHOT_MATCH_DIR=/app/data/matches EARSHOT_AUDIO_DIR=/app/data/live-audio EARSHOT_TTS_CACHE=/app/data/tts-cache
RUN useradd --uid 10001 --no-create-home earshot && chown -R earshot /app/data
USER 10001
EXPOSE 8080
# No access log: it would write callers' IPs into Log Analytics (30-day retention), against SEC6.
CMD ["uvicorn", "earshot.api.server:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
