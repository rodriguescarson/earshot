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
