# Earshot

Live AI audio-described football commentary for blind and partially sighted fans, built for the Microsoft x Premier League "Inside the Game" hackathon. Every match, club and player here is made up.

Status: in development. The design and every review decision live in [docs/designs/earshot.md](docs/designs/earshot.md).

## Develop

```bash
uv sync
uv run pytest
uv run earshot schemas   # regenerate schemas/*.v1.json after a contract change
```
