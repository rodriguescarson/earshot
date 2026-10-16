# One image, two entrypoints (eng D2/E-4): the worker (default) and the read-only MCP app
# (command override: uvicorn earshot.api.mcp:app). Built remotely in ACR, never on the laptop.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN pip install --no-cache-dir uv==0.12.5

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# The MCP app's demo match when no worker is publishing snapshots: public events and the roster
# only. The simulator's truth log is never written into the image.
RUN uv run --no-dev earshot sim --seed 26 --out data/mcp-replay >/dev/null \
 && uv run --no-dev earshot roster --out data/mcp-replay/roster.json \
 && test ! -e data/hidden

ENV PATH=/app/.venv/bin:$PATH PORT=8080 EARSHOT_MCP_REPLAY=/app/data/mcp-replay
RUN useradd --uid 10001 --no-create-home earshot && chown -R earshot /app/data
USER 10001
EXPOSE 8080
CMD ["uvicorn", "earshot.api.worker:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
