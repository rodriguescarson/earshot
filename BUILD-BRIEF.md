# Build brief: Inside the Game hackathon entry

Working name: **Earshot** (check the name is free before using it publicly).

Paste this whole file into Claude Code, run from this folder. It is one prompt, but it builds in
phases. Each phase ends with a gate that must pass with evidence before the next phase starts.
Report anything that was not verified.

---

## 0. Hard facts (official rules, github.com/microsoft/insidethegamehackathon, read 2026-10-14)

- **Registration closes Oct 20, 12:00 PT (Oct 21, 00:30 IST).** Register at https://aka.ms/insidethegame first.
- **Submission closes Oct 27, 23:59 PT (Oct 28, 12:29 IST).**
- Required: built new after Sep 29. Public GitHub repo. Video under 2:00 on YouTube or Vimeo,
  showing the product working. Elevator pitch that names the Microsoft tech used. A working link
  judges can use free until **Nov 10** (judging ends then, so the deployment stays up until then).
- Synthetic data only. No real clubs, players, crests, league marks, broadcaster names or
  copyrighted music anywhere: app, repo, video. Clubs must not evoke real ones either
  (no "North London Rovers").
- A project wins at most **one Grand Prize plus one Category Prize**. Primary category:
  **Best Multi-Agent System** (justified in section 3).
- Judging is five criteria at 20% each: Technological implementation, Agentic design, Real-world
  impact, UX and presentation, Category adherence. Ties are broken on Technological
  implementation, which explicitly includes "optimized synthetic data creation" and the hero tech
  list: Microsoft Foundry, Agent Framework, Azure MCP, GitHub Copilot Agent Mode, Fabric,
  GitHub SDK/CLI, Azure AI services, Azure databases.
- The brief's five stages must each be visible in the product: **Ingest, Interpret, Explain,
  Render, Personalize**. It also names player tagging, pass difficulty, ball and shot speed,
  narratives and recaps, "control vs chaos", favourite club, a player-focus mode, and
  **multi-language** output. Cover each one or say in the README why not.

## 1. The product

**One line:** live football you can hear. A team of AI agents turns a synthetic match's event
stream into verified, audio-described commentary. It speaks the listener's language, follows their
club and player, and places the ball in stereo so you can hear where play is.

**Why now:** on 8 Oct 2026 a UK broadcaster announced live descriptive audio commentary for blind
and partially sighted fans. It covers about 109 of the 380 league games a season, in English, and
was built with RNIB. Source: https://www.diageo.com/en/news-and-media/press-releases/2026/sky-media-and-guinness-launch-sky-sports-biggest-expansion
(cite it in the README, but never name the broadcaster in the app or video). Human describers
can't cover every match in every language. This can.

**One engine, three outputs.** The same verified moment stream drives:
1. the blind or partially sighted fan's audio feed on a phone (primary user),
2. any fan listening without watching: commuting, second language, radio-style,
3. the broadcaster: a described-audio track plus timed overlay cues for sighted viewers.

So it answers the Grand Prize wording ("accessible and personalized experiences for fans,
analysts, and broadcasters") directly. As far as a scan of ~15 competitor repos on 2026-10-14
shows, no other entry does accessibility.

## 2. Architecture (Azure, deployed for real)

```
synthetic engine --> Event Hubs --> Container Apps worker (KEDA scales on hub lag)
                                     | stats + agents (Agent Framework)
                                     v
                Cosmos DB (match state, aired lines, audit log)
                                     v
           Web PubSub --> listener apps (per-listener lines + audio)
           Azure AI Speech (neural voices, per language)
           Microsoft Foundry project: model deployments, evaluations, Content Safety
           App Insights via OpenTelemetry; Key Vault + managed identity; no keys in code
           MCP server exposing read-only match tools (key moments, player report, catch-up)
```

- IaC: Bicep plus `azure.yaml` for `azd up`. GitHub Actions deploys through OIDC.
- Usage guard for the public demo: cap agent runs per hour, tokens per day and TTS characters.
  Add an Azure budget alert.
- Python 3.12 backend (FastAPI). Web app in Next.js or Vite + React. Biome for the frontend;
  ruff + pyright for Python. CI runs lint, typecheck, tests and build on every PR. Never commit
  to main. Secrets gitignored.
- Use **Microsoft Agent Framework 1.0** (GA April 2026, the successor to Semantic Kernel). Read
  its current docs and installed source before writing agent code. Do not use Semantic Kernel.

## 3. The agents: airtime is the scarce resource

Each listener has one serial audio channel. Describing play in real time and explaining it
compete for the same seconds, and a line that is late is worse than no line. That is the reason
for multiple agents: specialists propose lines, and a director decides what airs. A single agent
cannot do this well. Make that argument in the README, in those terms.

| Agent | Kind | Job |
|---|---|---|
| Spotter | code | Turns raw events into describable moments with priority and a deadline. |
| Caller | code templates | Instant play-by-play ("Okafor, left wing, running at the full-back"). No model on the hot path. |
| Analyst | Foundry model | During stoppages only: explains why (pressure shift, momentum, control vs chaos), citing event ids. |
| Checker | code | Rejects any line whose numbers, names or claims are not in the event log. |
| Airtime Director | code + small model | Schedules the channel: preempts for goals and big chances, holds analysis for dead-ball moments, drops stale lines. |
| Voice | per listener | Personalizes (club, followed player, verbosity), translates, synthesizes speech. |

Failure handling, demonstrated live: if the Analyst times out, the Director fills the slot with a
checked stat line. If Speech fails, lines go to an ARIA live region so the screen reader speaks
them. Every handoff writes to a shared board that the producer view shows.

## 4. Synthetic data engine ("optimized synthetic data creation" is scored)

- Seeded and deterministic. 22 players plus ball at 10 Hz. Events have ids, timestamps and
  coordinates. 20 fictional clubs with no real geography cues.
- A hidden truth log of tactical states (pressing height, line height, tempo, width) that change
  during a match. The Analyst never sees it. Evaluation uses it.
- Generates a **full 380-match synthetic season**.
- Realism report: compare per-match aggregates (goals, shots, pass completion, cards) with
  published top-flight ranges. Cite the sources and show where the engine misses.
- Derived metrics computed by code: pass distance, accuracy and difficulty; ball and shot speed;
  player distance and speed with threshold tags; xG; pressure and momentum.

## 5. App UX

- Screen-reader-first and WCAG 2.2 AA. Measure contrast, don't eyeball it.
- One screen, one decision: (1) language, (2) club and player, skippable, (3) Listen.
- Listening controls: verbosity (action only, or action plus why), speech pace, "Where's the ball?",
  "Catch me up" (a spoiler-safe recap up to now).
- Stereo: the ball's position along the pitch maps to left/right pan (Web Audio StereoPanner).
  Optional earcons for the final third and goals. Android vibration patterns; the Vibration API
  is not supported on iOS Safari, so say that in the UI.
- i18n from the first commit. Text and voice in at least English, Spanish, Arabic (full RTL) and
  Hindi.
- Sighted view: a 2D pitch with the same timed cues as overlays.
- Producer view: cue timeline, agent board, approve/hold switch, and a download of the described
  audio track plus overlay JSON (with a versioned schema).
- Design: use `/impeccable` (Operate mode). Run `detect.mjs` before and after UI work.

## 6. Measurement: pre-register in EVALUATION.md before running

Write the pass/fail targets down and commit them before the first full run. Run on the whole
380-match season, not a sample. Report misses as results.

- At least 95% of goals, shots and cards described within 3.0 s of the event. Report the
  distribution and a 95% CI.
- Zero aired lines containing a number or name absent from the event log, audited across every
  aired line.
- p95 latency from event to first audio byte under 2.0 s on the deployed Azure stack with N
  concurrent listeners. Load-test it and publish N and the numbers.
- Stale-drop rate and Analyst timeout rate.
- Foundry evaluations (groundedness, relevance, coherence) on Analyst lines. Publish whatever the
  scores are; never hardcode a score in the UI.
- Analyst cause accuracy against the hidden truth log, compared with a rule-based baseline.
- Comprehension with a control: after a 3-minute clip, listeners answer "score, who has the ball,
  where". Compare described audio against plain play-by-play. If blind testers take part, report
  n and their quotes honestly. With n=3, say n=3.

## 7. Phases and gates

- **P0, hour zero:** Azure subscription. Install `az` and `azd` (neither is installed on this
  Mac). Foundry project with two model deployments, a Speech resource, a budget alert. Make one
  live call to each and keep the output.
- **P1:** engine, event contract, stats, tests. Gate: realism report and the full season generated.
- **P2:** Spotter, Caller, Director, Checker and the audio web app, run locally on templates only.
  Gate: coverage and latency measured on 20 matches.
- **P3:** Analyst on Agent Framework, Voice (translation + TTS), failure handling. Gate: kill the
  Analyst mid-match and the audio keeps going; the Checker audit shows zero.
- **P4:** Azure deploy via azd, the MCP server, the usage guard. Gate: live URL plus load-test numbers.
- **P5:** full-season evaluation and the comprehension test.
- **P6:** README, pitch, video. The README is judge-first: a "Judging this? Start here (5 min)"
  block, an architecture diagram, the agent table, a stage-to-code map, a hero-tech table with file
  paths, a results table with the command beside each number, and known limitations.

## 8. Rules for the builder

- Numbers come from code; words come from models. Every number the UI shows comes from a
  measurement.
- Template mode is labelled as template mode, never presented as AI.
- Never claim a Microsoft technology that isn't imported and used. Only list Copilot Agent Mode
  or Fabric if they were actually used.
- Keep the repo private while building. Make it public when submitting: entries are visible to
  every other entrant, and this angle is easy to copy.

## 9. Video (1:50, no music unless the licence is kept)

| Time | Screen | Beat |
|---|---|---|
| 0:00-0:12 | Black screen, described audio playing, panning left to right | "This is a football match." |
| 0:12-0:25 | Phone with a screen reader running the app | The problem, and the 109-of-380 fact (no broadcaster name) |
| 0:25-0:55 | Producer view: agents proposing lines, Director preempting for a chance, Checker rejecting one | Multi-agent, explained |
| 0:55-1:15 | Same moment in Spanish and Arabic, then a followed-player mode | Personalize |
| 1:15-1:30 | Kill the Analyst; audio carries on | Recovery |
| 1:30-1:50 | Architecture in 10 s, then the measured results table | Proof |
