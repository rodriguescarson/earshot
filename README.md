# Earshot

Live AI audio-described football commentary for blind and partially sighted fans, built for the Microsoft x Premier League "Inside the Game" hackathon. Every match, club and player here is made up.

## Status: retired (20 November 2026)

Earshot was submitted on 27 October 2026 in the Best Multi-Agent Orchestration category. It did not
place; the six winners were announced on 19 November 2026. The live demo and every Azure resource
behind it were deleted on 20 November 2026, so the live links in [docs/submission.md](docs/submission.md)
no longer work. The code, the documents and the results of record stay here.

| What | Where |
|---|---|
| Demo video (1:57) | https://youtu.be/j-MYF-HFq9k |
| Submission text | [docs/submission.md](docs/submission.md) |
| Results of record | [EVALUATION.md](EVALUATION.md) and [reports/](reports/) |
| Design and every review decision | [docs/designs/earshot.md](docs/designs/earshot.md) |
| Architecture | [docs/img/architecture.png](docs/img/architecture.png) |
| Everything not in git: demo clips, audio caches, the held-out run folder, load tests, the videos | the [archive-2026-11-20 release](https://github.com/rodriguescarson/earshot/releases/tag/archive-2026-11-20) |
| The exact code the held-out Explanations run used | tag `heldout-run-of-record` (the same change is on `main` as #35) |

What it measured, in one line each:

- 63,430 wrong names and numbers planted across 40 held-out matches in four languages; every one was
  caught before it aired, and no correct line was blocked.
- Every goal, red card and penalty aired across a full 380-match season.
- The AI's explanations of tactical changes were right about 1 time in 10, against 1 in 4 for a plain
  rule, so they were never aired.

To run it again you need your own Azure AI Foundry and Azure AI Speech resources; `infra/main.bicep`
describes the stack and `infra/README.md` the deploy.

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
- `EARSHOT_LIVE_TRACKS`: `1` (default) renders the live match's player frames at load and sends
  positions with the ball messages for the match view; `0` leaves the view with the ball only.
- `EARSHOT_AUDIO_STREAM=1`: serve the service's own mono MP3 per line and let the browser pan it
  (measured in EVALUATION.md; off by default, so the numbers of record describe what runs).

## Data we keep

Earshot stores no personal data. Every match, club and player is made up.

- **IP addresses** are used only as rate-limit keys, held in the worker's memory, and never written to disk or logs. The MCP app keeps them for a one-minute window. The live-session limiter keeps them for the life of a session token (at most 60 minutes) plus a per-UTC-day minute count, which is dropped the next day. A restart forgets them all. The servers run with uvicorn's access log off, so caller IPs never reach Log Analytics. Application Insights keeps its default IP masking.
- **Judge access codes** are stored only as SHA-256 hashes.
- **Spend counters and the cached-only flag** sit in one Blob (`admin/flags.json`) and hold no user data.
- **In the browser**, the app keeps the listener's choices (language, output, mode) in `localStorage` on their device. There are no cookies and no accounts.
- **Speech audio** is cached by a hash of the text it speaks. That text is commentary on a made-up match, never anything a listener typed. No listener free text reaches a model.
