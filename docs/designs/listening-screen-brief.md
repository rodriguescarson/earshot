# Listening screen: design brief

Status: CONFIRMED by Carson 2026-10-14 (`/impeccable shape`; direction round: Tactile Plate kept, code-first). No code and no direction contract yet. Stream 1 builds from this on Oct 19 (DT2–DT6 in `earshot.md`).
Product truth: `PRODUCT.md`. Decisions this brief must not reopen: DR-1 to DR-20 in `docs/designs/earshot.md`.

## 1. Job and audience

- **Mode: Operate.** The listener follows a live match. The screen serves the ears, never the other way round.
- **Blind listeners** meet the screen only through VoiceOver or TalkBack, in the DR-15 order. For them the structure *is* the design.
- **Partially sighted listeners** hold the phone close, often zoomed, and glance at it.
- **Fans listening without watching** glance at the phone in their hand.
- **Judges** see the demo video and the desktop link, where the agent table appears at 1024 px and wider.

## 2. Outcome and proof

- **A glance gives three things:** the score, the minute, and the line on air now (Carson, 2026-10-14). There is no visual ball strip; where the ball is lives in the sound.
- **Success:** in one glance the listener knows where the match is and what was just said. Every wait, failure or change is shown as well as spoken (CEO-10, DR-4, DR-5).
- **Content is real, made-up match data:** fictional clubs (Kestrels, Copperline), lines from the Caller templates and Analyst claims. The landing already carries the fiction note (DR-8).

## 3. Selected direction: Tactile Plate

Source: station braille plates, tactile maps and blister paving. The match is a wayfinding plate, read at arm's length or by touch, that always says where you are. It was the assigned direction and Carson kept it.

**System:**
- **Palette:** two colours plus one spot, all measured (WCAG ratios below).

  | Role | Light theme | Dark theme |
  |---|---|---|
  | Ground | chalk `#F1F3F2` | `#121618` |
  | Plate and text | charcoal `#1F2427` | text `#EEF1EF` |
  | Spot, on light surfaces | red `#C42B21` | red `#C42B21` |
  | Spot, on dark surfaces | red `#FF6B5E` | red `#FF6B5E` |

  - The spot only ever means "on air now".
  - There is no blue anywhere. No yellow or cream.
- **Materials:**
  - **Signage plates** with square-cut corners, divided by seams, never shadows.
  - **A raised-dot grid** as the spacing armature: the cell is 0.5rem, so the grid scales with the device's text size, and every plate snaps to whole cells.
  - **One braille wordmark** ("Earshot" in uncontracted braille, real and checked). It is optional on this screen, and braille is never used as decoration elsewhere.
- **Type:** Atkinson Hyperlegible Next only (DR-11). Rank comes from weight and size steps on one scale. The score figures are the largest type on the screen.
- **State grammar (form first, colour second):**
  - filled dot: on air now (the spot colour);
  - hollow dot: waiting or paused;
  - doubled dot: a never-drop line in the transcript (goal, red card);
  - a corduroy band (ridged lines, as on hazard paving): attention states such as the connection notice, restore sound and give-up.
  - Every state also has words (DR-10).

**Raises carried from the declined challengers:**
- **Emission rail:** state reads by form before colour (the grammar above).
- **Timetable rack:** exactly one reversed plate per screen, the line on air now.
- **Botanical folio:** every match state, from kickoff to full time, keeps one layout and scale; only the words change.
- **Crouwel specimen:** the dot grid is the armature.
- **Developer console:** seams, not shadows; one declared collapse priority (the DR-15 order).
- **Streaming wall:** when a line goes on air, the rest of the transcript steps back one tone.

**Focal moment:** the reversed plate holding the line on air now.

**Signature interaction:** when a new line goes on air:
- its dot fills and the plate settles one step;
- the previous line moves to the top of the transcript and steps back one tone.

With `prefers-reduced-motion` on, this becomes an instant swap.

## 4. Scope and boundaries

- **In scope:** a production-ready listening screen, phone first (320–430 px portrait), landscape on phones, and the second column at ≥1024 px. It covers every state in section 5 and both themes.
- **Untouched:**
  - DR-15 structure and focus order;
  - DR-5 wording;
  - DR-14 live regions and templates;
  - DR-16 speech rate;
  - DR-17 lock-screen controls;
  - DR-18 layout rules;
  - the CEO-10 watchdog timings.
- **Anti-goals:**
  - no ball strip;
  - no shadows, gradients, glows or side-stripe borders;
  - no colour-only state;
  - no decorative braille;
  - no stat tiles;
  - no agent names on the fan screen (they live in the agent column).

## 5. States and ranges

All states keep the same plates in the same places.

| State | Score plate | On-air plate | Controls | Notes |
|---|---|---|---|---|
| Before kickoff | clubs, "Before kickoff" | "Kickoff in about a minute. Kestrels play left to right." | Pause, How Earshot talks, Settings | live-only controls hidden until kickoff |
| Live | "Kestrels 1, Copperline 0 · Minute 63 · Live" | the line on air, filled dot | all five | |
| Goal | score updates with the line | goal line; doubled dot in the transcript | all five | the replay (CEO-6) follows on air |
| Paused | unchanged, "Paused" | last line, hollow dot | Resume first | resume jumps to live after catch-up |
| Half-time | "Half-time" | recap line | "Play recap" via the Recaps heading | DR-5 |
| Full time | "Full time. Kestrels 2, Copperline 1" heading takes focus | full-time recap | "Listen to a full match" | DR-1 |
| Switching language | unchanged | "Switching to Español." in the new language | disabled during the switch, with words | then catch-up |
| Connection notice | "Reconnecting" | corduroy band plus the CEO-10 notice | Pause only | within 12 s of silence |
| Sound blocked | unchanged | corduroy band, "Tap to restore sound" as the one primary action, focused | | DR-4 |
| Gave up (2 min) | unchanged | "Couldn't reach the match. Try again." | "Try again" | DR-4 |
| Cached-only | clubs, "Recorded clip" | the clip's line | Where's the ball? and Catch me up hidden, not greyed | DR-1 |

**Ranges:**
- **Club names:** 6–14 characters in Latin script. Arabic and Hindi names are often wider. The score plate wraps clubs onto two lines before it shrinks type.
- **Minute:** 1 to 90+ with added time, as "Minute 90+4".
- **Score:** 0–9 per side; two digits must still fit.
- **On-air line:** 3–25 words for the Caller. Analyst lines run up to about 35 words and are prefixed "Analysis:".
- **Transcript:** capped at 50 lines (DR-15).
- **Speech rate:** up to 1.5×, so lines change about every 2–4 s at peak.

## 6. Interaction and layout

- **Phone portrait, top to bottom:**
  1. the score plate (`<h1>`, DR-15 text);
  2. the reversed on-air plate;
  3. Pause/Resume, full width;
  4. Where's the ball? and Catch me up side by side;
  5. How Earshot talks and Settings side by side;
  6. the transcript;
  7. Recaps.
  - The DOM order is DR-15's. Side-by-side pairs stack at 320 px and at 200% text.
- **Phone landscape:** controls on the left, the transcript on the right (DR-18). The score plate and the on-air plate span the top.
- **≥1024 px:** the agent table (DR-9) is a second column in the same plate-and-seam grammar, as a `<complementary>` landmark after Recaps.
- **Sizing:** targets are 48 px or larger. Base text is at least 18 px and follows the device setting. Controls are lift-button plates: a label, an optional dot for state, and a seam outline in charcoal at 3:1 or more.
- **Feedback:** focus stays on the button just pressed. Pressed state is a one-step plate inset, never colour alone.

## 7. Constraints and open decisions

- **Measured contrast** (computed 2026-10-14; Playwright re-measures in DT12):

  | Theme | Pair | Ratio |
  |---|---|---|
  | Light | text on ground | 14.06:1 |
  | Light | red on ground | 5.07:1 |
  | Light | chalk on charcoal plate | 14.06:1 |
  | Light | red dot `#FF6B5E` on the plate | 5.61:1 |
  | Dark | text on ground | 16.0:1 |
  | Dark | red on ground | 6.52:1 |
  | Dark | reversed chalk plate text | 16.0:1 |
  | Dark | red `#C42B21` on the chalk plate | 4.97:1 |

- **RTL:** plates and dots mirror to inline-start in Arabic. Use logical properties only.
- **Font:** `tnum` is unverified for Atkinson Hyperlegible Next. Check it in DT1; the fallback is a tabular face for the score and minute only.
- **Open, and the builder must not invent them:**
  - whether the braille wordmark appears on this screen or only on the landing;
  - the exact plate inset distance for the pressed and settle states;
  - the type-scale ratio;
  - what the dark-theme seam colour is (it must measure 3:1 against both ground and plate).
- **After the first screen is built:** impeccable's finish review, then its documenter writes DESIGN.md (DR-20).
