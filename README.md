# Earshot

Live AI audio-described football commentary for blind and partially sighted fans, built for the Microsoft x Premier League "Inside the Game" hackathon. Every match, club and player here is made up.

Status: in development. The design and every review decision live in [docs/designs/earshot.md](docs/designs/earshot.md).

## Develop

```bash
uv sync
uv run pytest
uv run earshot schemas   # regenerate schemas/*.v1.json after a contract change
scripts/ci.sh            # every CI check, locally (GitHub Actions doesn't run on this repo)
```

Merging and deploying go through `scripts/merge-pr.sh` and `scripts/deploy.sh`; see `infra/README.md`.

## Switches

- `EARSHOT_VOICE_TIER`: `hd` (default) uses Azure HD neural voices where the language has them;
  `standard` restores the voices the evaluation of record used.
- `EARSHOT_AUDIO_STREAM=1`: serve the service's own mono MP3 per line and let the browser pan it
  (measured in EVALUATION.md; off by default, so the numbers of record describe what runs).

## Data we keep

Earshot stores no personal data. Every match, club and player is made up.

- **IP addresses** are used only as rate-limit keys, held in the worker's memory, and never written to disk or logs. The MCP app keeps them for a one-minute window. The live-session limiter keeps them for the life of a session token (at most 60 minutes) plus a per-UTC-day minute count, which is dropped the next day. A restart forgets them all. The servers run with uvicorn's access log off, so caller IPs never reach Log Analytics. Application Insights keeps its default IP masking.
- **Judge access codes** are stored only as SHA-256 hashes.
- **Spend counters and the cached-only flag** sit in one Blob (`admin/flags.json`) and hold no user data.
- **In the browser**, the app keeps the listener's choices (language, output, mode) in `localStorage` on their device. There are no cookies and no accounts.
- **Speech audio** is cached by a hash of the text it speaks. That text is commentary on a made-up match, never anything a listener typed. No listener free text reaches a model.
