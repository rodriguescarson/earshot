# Fixtures

Real engine output, committed so the agents, Director and web client can be tested without
running the simulator. Every club, player, coach and referee is made up.

## `clip-demo.json`: one 3-minute action-dense clip

Picked by `earshot clip` from the development seeds 1-40: the densest window that starts in play,
has a scored shot (not an own goal) 60-90 s in (design DR-8), at least one shot saved by the
keeper, and at least 50 events. Regenerate after any engine change:

```bash
uv run earshot clip --seeds 1-40 --out fixtures/clip-demo.json
```

A test fails if the committed file differs from what the current engine produces, so the
fixture always matches `ENGINE_VERSION`. Treat its content as data: assert on structure and
invariants, not on a particular player or score, because a recalibration changes them.

| Key | Contents | Model (`earshot.contract`) |
|---|---|---|
| `fixture_version`, `engine_version`, `seed` | provenance | |
| `match` | match header: team sheets, formations, referee, kick-off team, half-time length | `MatchInfo` |
| `clubs` | the two clubs | `ClubInfo` |
| `players` | both 25-man squads (names, short names for commentary, shirts, positions) | `PlayerInfo` |
| `clip` | window bounds (`start_wall_ms`, `end_wall_ms`), period, clock and score at the start | |
| `events` | every event in the window, in order; parse each with `TypeAdapter(Event)` | `Event` |
| `ball` | the 10 Hz ball track for the window, with `danger` (0-1, from the frozen xT) for the team in possession | `BallSample` |

Conventions (from the contract):
- Positions are in the broadcast frame: x 0-105 m left to right as the main camera sees it,
  y 0-68 m. The home team attacks right (towards x=105) in the first half.
- `clock_ms` is the period clock (resets each half); `wall_ms` is real time since kick-off,
  including the half-time interval. Schedule on `wall_ms`.
- Every `stoppage_start` is followed by exactly one `restart` of the same kind. The window between
  them is the dead ball; `expected_dead_ms` is known at the start, the real length only at the
  restart. Cards, substitutions, injuries and VAR checks happen inside a stoppage.
- After a goal: `stoppage_start(kind=goal)` → optional `var_check` → `restart(goal)` → `kickoff`
  by the conceding team.
- A goal is a shot type with `result=success`, or any action with `result=owngoal` (credited to
  the other team).
- No truth data is ever in a fixture.

## Full matches

Any seed can be simulated in about 0.1 s:

```bash
uv run earshot sim --seed 28 --out data/matches          # events + header
uv run earshot ball --seed 28 --out data/matches/m00028.ball.jsonl
uv run earshot roster --out data/roster.json             # all 20 clubs and 500 players
```
