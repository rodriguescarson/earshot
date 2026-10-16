# Earshot evaluation (pre-registered)

Status: **pre-registered 2026-10-16, before the first full-season run.** Thresholds, data splits,
definitions and sources below are fixed. Results are appended under "Results" with the command
that produced each number, misses included. Changing a threshold after a run is not allowed; if a
definition turns out wrong, the old and new results are both reported.

Every check here is code in this repo. Commands assume `uv sync` has run.

## 1. Data splits

| Set | Seeds | Use |
|---|---|---|
| Development slice | 1-40 (season rounds 1-4) | engine development, metric definitions, baseline detector thresholds, xT and pass-difficulty fits, listening-test clips |
| Calibration | 1-120 | engine parameters tuned towards the cited ranges (see section 3, "calibrated-to") |
| Full season | 1-380 (seed N = season match N, rounds 1-38) | the P1 gate (G1-G10), the realism report, the xT refit |
| Held-out evaluation | 9001-9040 (rounds 27-30 replayed with fresh randomness) | the Explanations result, pass-difficulty generalisation, the accelerated-clock run with real Analyst calls. **Not generated before `eval-freeze`.** |
| Fresh block | 9101-9140 | only if a frozen item changes after `eval-freeze`; both results are then reported |

The season includes the development and calibration seeds, so the realism (G3) and planted-signal
(G5) results on the season are not held-out results. They are reported as such.

## 2. The `eval-freeze` tag

Tagged at the end of the day before the run of record, after every item below exists, and before
any held-out seed is generated:

- the Analyst prompt and the claim templates (Stream 1);
- `src/earshot/stats/models/xt.json` (12 x 8 xT, value iteration, fitted on seeds 1-40);
- `src/earshot/stats/models/pass_difficulty.json` (IRLS logistic fit and deciles, seeds 1-40);
- the baseline detector (`src/earshot/eval/baseline_detector.py`: `SPREAD`, `Z_MIN = 2.5`, rules);
- the metric definitions (`src/earshot/stats/metrics.py`) and the grading rule (`src/earshot/eval/grading.py`);
- the dead-time parameters (`src/earshot/sim/stoppages.py`), which set the Analyst timeout;
- the MCP tool schemas;
- `ENGINE_VERSION` (1.1.0 at pre-registration).

The run of record cites the tag and the seeds in its manifest.

## 3. P1 engine gate (G1-G10)

Command for the season: `uv run earshot gate --seeds 1-380 --workers 8 --model-seeds 301-380 --report reports/gate-season.json`.
The same command with `--seeds 1-40` is the slice gate.

| # | Check | Pass if | Applies to |
|---|---|---|---|
| G1 | Determinism | identical sha256 of every events, header and truth file across two runs, one with 8 workers and one with 1 | slice and season |
| G2 | Completeness | all matches present; every event validates against the contract; clocks never go backwards; every `stoppage_start` has exactly one `restart` before the next; the score equals the goal events (own goals to the other side); no event from a player who is not on the pitch (sent off, subbed off or unused); event ids unique; `wall_ms` never goes backwards | slice and season |
| G3 | Realism | each gated season mean lies inside its band (table below) | season (reported on the slice) |
| G4 | Truth vocabulary | every truth label is in the closed vocabulary; 100% of red cards are followed by `RED_CARD_REORG` for that team within 60 s | slice and season |
| G5 | Planted signal | per setting: AUC of the 10-minute before/after change, planted against null windows, at least 0.75 (0.70 for line height), and direction right in at least 80% of planted changes; AUC above 0.97 is flagged "too easy" | slice and season |
| G6 | Baseline detector | recall against the truth log at least 1.5 times its recall against time-shuffled truth (mean of 20 shuffles); false alarms per match reported | slice and season |
| G7 | Models | pass difficulty, fitted on seeds 1-40 and evaluated on season rounds 31-38 (seeds 301-380): log loss at most 0.90 times the base-rate log loss, calibration error (10 equal-count bins) at most 0.03. The approved plan's 0.02 is reported too. No xG model is built (CEO-7 replaced xG with xT), so the xG part of G7 is not applicable | season |
| G8 | Physical | player frames are built for the render match only (CEO-4): median outfield distance 9.5-12 km, sprint distance 150-450 m, ball within 1.5 m of 99% of events, speed at most 9.5 m/s, acceleration at most 8 m/s² | render match only |
| G9 | Speed | one match's events in under 0.5 s; the full season in at most 5 minutes on 8 workers | season |
| G10 | Dead-ball windows | median of at least 20 dead-ball windows of 15 s or more per match; every home goal ends at x > 100 in the first half and x < 5 in the second | slice and season |

### G5 method (planted signal)
For every truth change and every setting it moves by at least 0.2: the setting's metric over the
10 minutes before the ramp starts, against the 10 minutes after the ramp ends. Changes are skipped
when the comparison crosses half-time or overlaps another change to the same setting. Null samples:
up to 6 random times per team per match with no change for that team within 12 minutes, same
comparison with a 90 s gap. The score is |after - before|. Setting-to-metric mapping: press height
-> `press_height`, tempo -> `tempo`, directness -> `directness`, width -> `width`, line height ->
`line_height` (definitions in `src/earshot/stats/metrics.py`). Code: `src/earshot/eval/signal.py`.

### G3 realism bands
Band rule: the span of the verified season values, widened by 10% for counts and minutes, or by 3
percentage points for rates. Values are per match, both teams. All URLs accessed 2026-10-16; the
quotes are stored in `src/earshot/eval/reference.py`. "Verified" means the quote was fetched
first-hand when writing that file; unverified values (gathered by a research pass and not
re-fetched) are reported but never gate.

| Metric | Reference values | Band | Source | Verified | Tag |
|---|---|---|---|---|---|
| Goals | 2.85, 3.28, 2.99 (2022-23, 2023-24, 2024-25 to Mar 2025) | 2.57-3.61 | [premierleague.com/news/4271974](https://www.premierleague.com/news/4271974) | yes | calibrated-to |
| Shots | 25.3, 27.6, 26.1 | 22.8-30.4 | same | yes | calibrated-to |
| Pass completion | 81.3%, 82.9%, 83.7% (2022-23 to 2024-25) | 78.3-86.7% | [premierleague.com/en/news/4426039](https://www.premierleague.com/en/news/4426039) | yes | calibrated-to |
| Yellow cards | 4.2 (2023-24) | 3.78-4.62 | [theanalyst.com 2024-25 trends](https://theanalyst.com/articles/premier-league-2024-25-data-trends-stats) | yes | calibrated-to |
| Ball in play (effective play), min | 54.82, 58.18, 56.98 | 49.3-64.0 | [theanalyst.com ball in play](https://theanalyst.com/articles/premier-league-ball-in-play-are-we-seeing-less-football-2025-26) | yes | calibrated-to |
| Corners | 10.24 (3,890 in 2024-25) | 9.2-11.3 | same | yes | calibrated-to |
| Home win share | 48.4% (2022-23), 39.0% (2024-25, part season) | 36.0-51.4% | [theanalyst.com home wins](https://theanalyst.com/articles/premier-league-home-wins-covid-levels) | yes | calibrated-to |

Reported, not gated: fouls 21.5-22.1 (Liu et al. 2025, citing a secondary source; the approved
plan gated fouls, but no first-hand source was found, so the check is demoted and said so here),
penalties 0.22-0.28 (premierleague.com, verified), passes 893-941 (premierleague.com, verified),
added time about 9.7 min (theanalyst.com, verified), throw-ins 39 (Stone et al. 2025), goal kicks
17, free kicks 33 and 108 interruptions (Siegle & Lames 2012, Bundesliga), offsides 3.84 and
shots on target 9.1-9.9 (StatMuse team tables summed by the research pass), red cards 0.08-0.16.

Dead time per restart was set from verified Opta 2024-25 means (theanalyst.com): corners 33.6 s,
goal kicks 28.3 s, throw-ins 15.6 s. The engine draws log-normal dead times whose medians follow
from those means.

**Calibration disclosure.** Engine parameters were tuned on seeds 1-120 towards goals, shots, pass
completion, fouls, yellow and red cards, corners, throw-ins, offsides, penalties, ball-in-play time
and home wins ("calibrated-to"). Shots on target, total passes, goal kicks, free kicks, crosses,
added time and the dead-ball window count were never targeted ("emergent").

**Not sourced** (stated, not claimed): shot speed and pass speed in matches (no league-wide median
or range found; the Bundesliga Match Facts article counts "fast shots (>100 km/h)" and a 137 km/h
record only; the EURO 2024 velocity paper returned HTTP 403), and added time per half. So the CEO
criterion "median shot and pass speed inside their cited range" **cannot be evaluated**. The
engine's speeds are reported without a realism claim.

## 4. Product success criteria (design, "Success Criteria" and the CEO additions)

Measured on the held-out seeds 9001-9040 unless stated.

| Criterion | Pass if | Method and owner |
|---|---|---|
| Never-drop | 100% of goals, red cards and penalties awarded aired; latency distribution reported | accelerated-clock run, every profile P1-P5 (S2 harness, S1 Director) |
| Coverage | at least 95% of shots and yellow cards described within 3.0 s, per profile | accelerated-clock run over the full season; TTS and Analyst latency drawn from the deployed load test; Checker, Director and auditor real |
| Facts | the independent auditor finds zero aired lines with a name or number outside the allowed set, in any language; planted-defect recall and false-block rate reported per injection point (Analyst JSON, slot values, roster entries, template strings), with real-but-wrong slot values credited to the auditor | auditor (S1) + planted-defect harness (S2) |
| Explanations | cause-label accuracy of the Analyst reported next to the baseline detector's, on the same analysis windows of the same matches | `src/earshot/eval/grading.py`: a label for team T at a window starting at t is correct if the truth log has a change with that label for T that started in the 12 minutes before t. Accuracy = correct / labels given; recall and false alarms per match also reported. Analysis windows: every `stoppage_start` with `expected_dead_ms` >= 15 s except half-time and full-time. Real Analyst calls run on these 40 matches only |
| Latency | p95 from event to first audio byte under 2.0 s on the deployed stack, with N stated against the Speech quota | load test (S2) |
| Pass difficulty | log loss at most 0.90 times the base rate, calibration error at most 0.03 | `earshot gate ... --model-seeds` on the held-out seeds |
| Ball and shot speed | median shot speed and median pass speed inside their cited ranges | **not evaluable** (section 3); speeds reported |
| Casual vs Analyst | Analyst lines (P5) carry at least 3 times the numeric slots per minute of Casual lines (P1); Casual airs no number other than score, minute and shot speeds of 100 km/h or more | accelerated-clock run |
| Per-mode caps | no window airs more claims than its mode allows | Director test (S1) |
| Recaps | zero out-of-set values found by the auditor | auditor |
| MCP | tool contract tests pass; a leakage test shows no tool returns events after `clock_ms` | `tests/` (S2) |
| Render | every overlay cue within +/-1 frame of its event | render match (S1) |
| Voice mapping | 100% of line types mapped to the right voice | test (S1) |
| Goal replay | share of replays that play in full reported; target at least 90% | supplementary run on the same seeds (built after the run of record) |
| Focus stat | precision 100% and recall at least 80% against crossings recomputed on the auditor's path; lines per minute reported | accelerated-clock run |
| Template quality | Foundry fluency and coherence evaluators over every rendered template per language, reported as a limit-aware check, not proof of fluency | Foundry evaluators (S2) |
| Listening test, blind testers, device | as in the design; reported whichever way they go | Carson / S1 |

Evaluation profiles: P1 Casual, App voice, nothing followed; P2 Analyst, App voice, followed club
and player; P3 Casual, screen-reader mode; P4 Analyst, one-ear mode, following shot speed; P5
Analyst, App voice, nothing followed.

### 4.1 Listening test protocol (earshot-listening-v1), pre-registered 2026-10-16

Committed before any participant is tested; the commit timestamp is the proof.

- **Question:** does placing the ball in sound help a listener follow the match?
- **Arms (within-subject):**
  - `speech`: speech only, centred;
  - `pan`: speech panned to the ball;
  - `full`: speech with pan, plus the continuous ball tone and crowd layer.

  The goal replay is excluded from all three arms.
- **Clips:** three 3-minute clips from development seeds, chosen by `python -m earshot.listening` as the three distinct matches whose action-density scores are closest together, each with a goal 60-90 s in. The demo match is excluded.
  - Seeds 36, 32 and 12.
  - Fixtures and clips: `data/clips/listen-test/`.
  - Clip order is fixed (A, B, C).
- **Arm order:** three orders (a Latin square), assigned by participant number: order = (participant - 1) mod 3.
  - Order 1: speech, pan, full.
  - Order 2: pan, full, speech.
  - Order 3: full, speech, pan.
- **Setup:** stereo headphones, with a left/right check before trial 1.
  - The facilitator runs the `#test` page; the listener only hears.
  - The arm is never named to the listener.
- **Probes:** up to four per clip, at in-play moments at least 20 s apart (11 probes over the three clips). At each, the clip pauses and the facilitator asks three questions:
  - "What's the score?"
  - "Who has the ball?"
  - "Where is the ball: left, middle or right?"

  The true answers come from the events and the ball track. Each answer is marked correct, or wrong / don't know.
- **Primary outcome:** "where" accuracy per arm. **Secondary:** score and possession accuracy per arm.
- **Decision rules (from the design's Oct 19 checkpoint):**
  - Pan stays if `pan` "where" accuracy is at least `speech` "where" accuracy.
  - The continuous layer (and the goal replay with it) stays if `full` is at least `pan` on "where" and no worse on score and possession.
- **Reporting:**
  - n is stated, and every result is reported whichever way it goes.
  - With small n these are descriptive counts, not significance claims.
  - Blind and partially sighted participants are reported separately from sighted eyes-closed volunteers.

## 5. Results

Appended after each run, with the command, the commit and the date. Development runs are listed
separately and are not results of record.

### Development runs (not of record)
- 2026-10-16, engine 1.1.0, `uv run earshot gate --seeds 1-40 --workers 8`: G1, G2, G3 (slice
  means, informational), G4, G5, G6, G9 and G10 all PASS. Details in `reports/gate-slice.json`.

### P1 gate on the full season (run of record for G1-G7, G9, G10)
2026-10-16, engine 1.1.0, Python 3.12.14, NumPy 2.5.4, pydantic 2.14.1, commit `76d08e3` (branch
`feat/season-deploy`, after EVALUATION.md was committed in `d8493a8`). Command:
`uv run earshot gate --seeds 1-380 --workers 8 --model-seeds 301-380 --report reports/gate-season.json`

| Gate | Result | Numbers |
|---|---|---|
| G1 determinism | PASS | 380 matches, events, headers and truth identical with 8 workers and 1 |
| G2 completeness | PASS | 380/380 matches, 674,950 events, every invariant held |
| G3 realism | PASS | all 7 gated means inside their bands (table below) |
| G4 truth vocabulary | PASS | 1,702 truth changes, all in the vocabulary; 49 red cards, each followed by RED_CARD_REORG within 60 s |
| G5 planted signal | PASS | AUC / direction right: press height 0.803 / 87.6%, tempo 0.880 / 98.0%, directness 0.813 / 94.9%, width 0.858 / 98.1%, line height 0.878 / 96.0%; none above 0.97 |
| G6 baseline detector | PASS | recall 0.408 against 0.093 for time-shuffled truth (4.4x, needed 1.5x); accuracy 0.267; 13.9 false alarms per match |
| G7 pass difficulty | PASS | rounds 31-38 (seeds 301-380), 65,934 passes: log loss 0.379 vs 0.435 base rate (0.872x, needed <= 0.90x); calibration error 0.0068 (needed <= 0.03; also inside the plan's 0.02) |
| G8 physical | PASS (render match) | seed 26: see the G8 section below |
| G9 speed | PASS | 380 matches in 3.67 s on 8 workers; slowest match 0.076 s |
| G10 dead-ball windows | PASS | median 70.5 windows of 15 s or more per match (min 43, max 99); every home goal ends at the right goal |

Season means against the cited bands (G3; "calibrated-to" means tuned on seeds 1-120, which are
part of the season, so passing is expected; "emergent" was never tuned):

| Metric | Engine | Band | Gated | Tag |
|---|---|---|---|---|
| Goals | 2.66 | 2.57-3.61 | yes | calibrated-to |
| Shots | 24.5 | 22.8-30.4 | yes | calibrated-to |
| Pass completion | 83.7% | 78.3-86.7% | yes | calibrated-to |
| Yellow cards | 3.96 | 3.78-4.62 | yes | calibrated-to |
| Ball in play, min | 54.7 | 49.3-64.0 | yes | calibrated-to |
| Corners | 10.66 | 9.2-11.3 | yes | calibrated-to |
| Home win share | 44.2% | 36.0-51.4% | yes | calibrated-to |
| Fouls | 22.6 | 19.4-24.3 | no | calibrated-to |
| Penalties | 0.28 | 0.20-0.31 | no | calibrated-to |
| Passes | 844 | 804-1035 | no | emergent |
| Throw-ins | 41.6 | 35.1-42.9 | no | calibrated-to |
| Goal kicks | 16.5 | 15.3-18.7 | no | emergent |
| Free kicks incl. offside | 26.5 | 29.7-36.3 | no | emergent, **outside** the (Bundesliga, older) reference |
| Interruptions | 98.2 | 97.2-118.8 | no | emergent |
| Offsides | 4.12 | 3.46-4.22 | no | calibrated-to |
| Shots on target | 8.33 | 8.19-10.87 | no | emergent, at the bottom of the band |
| Red cards | 0.129 | 0.072-0.171 | no | calibrated-to |
| Added time, min | 9.0 | 8.8-10.7 | no | emergent |

Misses and weak spots, stated: free kicks (including offside restarts) are below the only source
found, an older Bundesliga sample; shots on target sit at the bottom of their band; goals sit low in
theirs (2.66 against a 2022-25 EPL span of 2.85-3.28). Shot and pass speeds remain unsourced.

**xT refit (K2).** The danger signal ships with xT refitted on the full season (seeds 1-380,
`src/earshot/stats/models/xt.json`), frozen at `eval-freeze`. Largest cell change against the
seeds 1-40 fit: +0.078, in the far corner cell of the attacking third (x-band 11, y-band 7), which
had no data on the slice; the grid maximum moved from 0.184 to 0.194.

### Planted-defect harness (development run, not of record)
`reports/planted-dev.json`: seeds 1-10, 25% injection rate, against Stream 1's Caller and Checker
(throwaway local merge with `feat/agents-core`). Analyst JSON 660 defects, recall 1.0, false-block
0.0 on 2,136 clean claims. Slot values (en, ar) 1,158 defects, recall 1.0, false-block 0.0 on
7,914 clean lines. Roster entries recall 0.0 and names typed into template text recall 0.0: both
by design (the Checker reads the same roster and templates), and the auditor's job. Template
numerals recall 1.0. The run of record is on the held-out seeds.

### G8 on the render match (frames, CEO-4)
2026-10-16, `uv run earshot frames --seed 26 --out data/frames/m00026.frames.npz` (numbers in
`reports/g8-frames.json`). Frames are steered at 10 Hz towards the engine's own recorded team shapes
under a 9.3 m/s speed cap and a 7.5 m/s^2 acceleration cap; recording leaves the events byte-identical.

| Match | Median outfield distance | Median sprint (> 7 m/s) | Actions with ball within 1.5 m | Max speed | Max accel | G8 |
|---|---|---|---|---|---|---|
| 26 (render match) | 9.85 km | 202 m | 99.3% | 9.3 m/s | 7.5 m/s^2 | PASS |
| 5 (not tuned on) | 9.55 km | 374 m | **98.9%** | 9.3 m/s | 7.5 m/s^2 | miss (ball-near) |
| 17 (not tuned on) | 9.59 km | 286 m | **98.8%** | 9.3 m/s | 7.5 m/s^2 | miss (ball-near) |

Stated plainly: the two frame parameters (off-ball drift 13 m, look-ahead 4 s) were tuned on the render
match itself, so its PASS is a plausibility check of the frame model, not an independent result. On two
matches it never saw, the ball-near criterion misses by about 0.1 percentage points (the engine sometimes
picks an interceptor further away than a player can run in the ball's flight time). G8 was pre-registered
for the render match only, as the CEO plan scoped it.

### Auditor (development run, not of record)
2026-10-16, seeds 1-10 only (the held-out seeds 9001-9040 were not touched). The independent auditor
(`earshot.agents.auditor`) re-reads each line against the reviewed template files and roster and
recounts every number from the event log; it shares no code with the Checker's slot path.
- 84,728 clean lines (Caller 72,576, code fallback 5,248, StatWatcher 6,064, recaps 840; four
  languages, both modes), each with its exact SSML.
- First pass: 208 findings. 120 were a real defect: the code fallback spoke possession rounded twice
  (the match tool rounds the share to three places, then speech rounded half-to-even), so 52.55% was
  said as "52". Fixed by rounding once from the unrounded share, with a regression test. The other
  88 were gaps in the auditor (recap possession not tied to the named club; a recap red-card minute
  checked against goal minutes); fixed in the auditor. Second pass: 0 findings.
- Planted slot defects on seed 4 (all classes, English Analyst): 100% caught, including
  real-but-wrong values the set-membership test cannot see (another real player 760/760, the other
  club 583/583, scores swapped 3/3).
- The 16 rendered demo clips: 557 lines audited, 0 findings, including the Analyst's claim lines.
The auditor was changed after seeing these development results, so they are not evidence of its
recall; the run of record is on the held-out seeds with the auditor frozen at `eval-freeze`.

### Accelerated evaluation run, ET11 (development, not of record)
2026-10-16. The run of record waits for `eval-freeze`, the held-out seeds and the latency model from
the deployed load test. Everything here uses the development seasons and a **provisional** latency
model (ready = `READY_DELAY_MS` 350 ms, Analyst = its whole budget, delivery 0), so none of it is a
result. It records what the harness measures and what it found.

- **Harness:** `src/earshot/eval/run.py`.
  - Whole matches run through Stream 1's live broadcaster (`LiveMatch`: the Agent Framework
    pipeline, window lines, claim gating, StatWatcher, recaps) on a virtual clock, with a
    `LiveDirector` per profile P1-P5 in English.
  - Targets come from the event log, not the Spotter: goals, red cards and penalties for
    never-drop; shots and yellow cards for coverage.
- **Runtime (ET11 check: 380 matches inside 15 min on 8 workers, failed ids reported):**
  - `uv run earshot evaluate --seeds 1-380 --workers 8`, at commit `6deb846`, run from a detached
    worktree: 380/380 matches, 0 failed, **402.5 s**.
  - The same run at `eaecd2a`: 396.6 s, 0 failed. **PASS.**
- **The finding it made (fixed by Stream 1 in `24f4efb`):**
  - Before the fix, the broadcaster's event feeder waited for each Analyst window run, so the card
    that follows a foul waited for the Analyst.
  - Seeds 1-40, P1, at `eaecd2a`: yellow cards within 3 s **28/149** (p50 4,367 ms); red-card p95
    5,756 ms; coverage 0.878.
  - With window runs on their own task (`--windows background`, now the broadcaster as built):
    **149/149** (p50 855 ms); red-card p95 709 ms; coverage 0.977.
  - `--windows inline` still reproduces the old behaviour.
- **The full season as built**, at `6deb846` with provisional latency; summary in
  `reports/eval-season-dev-provisional.json`:

| Profile | Never-drop aired (1,011 goals, 49 reds, 105 penalties) | Coverage within 3 s | Shots within 3 s (of 9,324) | Yellow cards within 3 s (of 1,505) | Numeric slots a minute |
|---|---|---|---|---|---|
| P1 Casual, app voice | all; p95 447 / 697 / 693 ms | 0.979 | 9,092 | 1,505 | 0.253 |
| P2 Analyst, app voice, club + player | all | 0.974 | 9,042 | 1,504 | 1.573 |
| P3 Casual, screen reader | all | 0.979 | 9,092 | 1,505 | 0.253 |
| P4 Analyst, one ear, shot speed | all | 0.971 | 9,009 | 1,504 | 1.700 |
| P5 Analyst, app voice | all | 0.976 | 9,062 | 1,504 | 1.573 |

  - P5 airs 6.22 times P1's numeric slots a minute (criterion: at least 3).
  - Casual numbers outside the allowed set: 0. The first run found `red_card.1` saying "down to N
    players" 49 times; Stream 1 fixed it in `24f4efb`.
  - P1 and P3 are identical offline by construction: `est_ms` uses the same airtime formula as the
    screen-reader estimate.
- **Baseline cause labels** on the same analysis windows (development season, graded against the
  truth log; not the held-out Explanations result): 7,186 labels, 1,916 correct (accuracy 0.267),
  recall 695/1,702 (0.408), 13.9 false alarms a match.

### Real Analyst, trial (development, not of record)
Seed 26 only: 49 analysis windows, the first 20 called, the rest skipped by `--max-calls 20`. Two
runs, both with `uv run earshot evaluate --seeds 26 --workers 1 --real-analyst --max-calls 20
--tokens-per-hour 300000`.

**The runs:**

| | Run 1 (`bd75a29`) | Run 2 (`50f53ae`, `--analyst-cache`) |
|---|---|---|
| Summary | `reports/eval-analyst-trial-seed26.json` | `reports/eval-analyst-trial-seed26-gated.json` |
| Tokens a call (p50) | 3,802 | 3,796 |
| Latency p50 / p95 | 3,891 / 5,490 ms | 3,711 / 5,186 ms |
| Timeouts | 2 of 20 | 1 of 20 |

**Cause labels against the truth log:**

| Run | Model's raw causes (proposed) | Gated causes on verified claims (what would air) | Baseline, same windows |
|---|---|---|---|
| 1 | 2 of 28 correct | not graded | 4 of 7 |
| 2 | 1 of 28 correct | 1 of 22 correct; the gate dropped 6 | 4 of 7 |

- **Grading change:** run 1 graded the raw cause on accepted claims. Run 2 grades what airs: the
  cause `gate_cause` leaves on a claim the live `verify` accepts, as `LiveMatch._window_lines`
  does. That is the definition for the run of record. Run 1's note that "all 28 labels would have
  aired" was wrong, because it did not apply the gate.
- **Signal for the Analyst prompt:** the model gives a cause in most windows, and almost none are
  right. It is one match, so a signal, not a result.
- **What this means for the run of record:**
  - About 2,000-2,800 calls on the 40 held-out matches, about 8-11M tokens.
  - Under the kill-switch alerts (500K tokens an hour, 3M a day) that is about 3.5 days of paced
    running (`--tokens-per-hour`, `--resume`).
  - Raising those alerts for the run is Carson's decision.

### Load test, trial (development, not of record)
`earshot loadtest --url <worker> --listeners 10 --minutes 3`, 2026-10-16, image `1b2172a`, from
India to Sweden Central. Summary in `reports/loadtest-trial-2026-10-16.json`.

- **Setup:** 10 SSE listeners spread over the five profiles' groups, each fetching every line's
  audio. The run makes its own temporary judge code and removes it.
- **Event to first audio byte (Caller lines):** p50 2,826 ms, **p95 5,866 ms** (target 2.0 s:
  **miss**).

  | Component | p50 | p95 |
  |---|---|---|
  | Audio ready | 123 ms | 688 ms |
  | Event to the Director starting the line (the single channel's queue) | 302 ms | 2,765 ms |
  | Start to message sent (`_publish_line`, mostly ffmpeg on 0.5 vCPU) | 716 ms | 2,799 ms |
  | SSE transit | 52 ms | 59 ms |
  | Audio first byte | 502 ms | 804 ms |

- **Errors:** 6 connect timeouts.
- **Limits:**
  - one load-generator machine, far from the region;
  - two clocks (the worker's and this machine's), skew not corrected;
  - 3 minutes.
- **Proposed to Stream 1:** encode the M4A while the line waits in the queue.
