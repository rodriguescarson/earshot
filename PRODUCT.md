# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

TypeScript with Vite and React for the web client, served from Azure Static Web Apps. Python 3.12 backend on Microsoft Agent Framework, with Azure Web PubSub, AI Foundry and Speech. Decided in `docs/designs/earshot.md` (approved 2026-10-14). Mobile-first web, not a native app.

## Users

- **Primary: blind and partially sighted football fans.** They follow a live match on their phone, often with VoiceOver or TalkBack on, often with the screen locked and headphones or one earbud in. Their job: know what is happening, where the ball is, and why, as it happens, in their language, following their club and player.
- **Fans listening without watching:** commuting, cooking, at work. Same job; they pick Casual or Analyst listening.
- **Broadcasters and studios:** they need a described-audio track and timed overlay cues produced from the same match, plus a producer view of what each agent decided.
- **Hackathon judges** (through Nov 10, 2026): they open a link, usually on a desktop, and need to hear the product work within a minute and see the agents behind it.

## Product Purpose

Earshot turns a match's event stream into live audio-described commentary on the fan's phone. It speaks in the listener's language, follows their club and player, and places the ball in stereo sound. It exists because live description today only reaches fans where a human describer or stadium hardware happens to be. In the UK, one broadcaster announced description for about 109 of 380 league games, in English only.

Success means a blind fan can close their eyes and follow a whole match: they always know where the ball is, never sit in unexplained silence, and are never told a fact that is wrong.

Earshot starts as an entry to the Microsoft x Premier League "Inside the Game" hackathon (submission Oct 27, 2026 PT) and continues as a product after Nov 10, 2026. Phase 2 needs a licensed live event feed.

## Positioning

Code produces every name and number a listener hears, straight from event data. The model only explains *why*, only in dead-ball pauses, and only through a fixed set of claims that code checks before they air. Generic AI commentary puts the model on the facts, which is the wrong way round for a listener who cannot check what they are told.

The ball is placed in sound: a continuous tone panned along the pitch and a crowd layer that rises with danger, so speech only has to say who and why. Several agents compete for one serial audio channel, and a Director arbitrates what airs.

## Operating Context

- A phone in a pocket with the screen locked. Lock-screen and headphone buttons matter as much as on-screen controls.
- A screen reader running alongside Earshot's own voice. The listener chooses App voice (Earshot speaks) or My screen reader (lines go to live regions at the reader's own rate).
- Patchy connections (trains, stadium concourses). Drops get spoken notices and catch-up.
- Mid-match changes: language, listening mode, followed stat, club and player, speech rate.
- A judge on a desktop, where the agent table appears as a second column at 1024 px and wider.
- A producer on a desktop: the airtime bar, the agent table and the downloads.

## Capabilities and Constraints

- **Listening:** Casual and Analyst modes; "Follow one stat" (Analyst only); follow a club and player; "Where's the ball?"; "Catch me up"; half-time and full-time recaps; a goal replay; two voices, one for checked facts and one for explanations; speech rate 1×, 1.25× and 1.5×.
- **Agents:** Spotter, Caller, StatWatcher, Analyst, Checker, Voice and the Director. "On air" means holding the single audio channel. A "dead-ball window" is a stoppage long enough for an explanation.
- **Broadcast outputs:** a described-audio track, overlay cues as JSON, and a read-only MCP server bounded to match time.
- **Resilience:** a silence watchdog (spoken notice within 12 s), and a cached-only mode under budget pressure in which recorded clips keep working.
- **Languages:** English, Spanish, Arabic (RTL) and Hindi. Play-by-play templates are written per language, never machine-translated at runtime.
- **Hackathon constraints:** synthetic data only (made-up clubs and players, no real marks, broadcaster names or copyrighted audio); a public repo at submission; a demo video under 2 minutes; the demo stays live and free for judges until Nov 10, 2026.
- **Open decisions (post-hackathon):** accounts and sign-in, notifications, support, account deletion and export, the licensed live feed provider, rights to real club and player names, pricing. None is decided. The hackathon build has no accounts.

## Brand Commitments

- **Name:** Earshot.
- **Voice:** short, plain lines in the manner of a radio describer: who, where and what first, then why. Match time is said as "Minute 63". Every state is spoken as well as shown.
- **Fiction disclosure:** the landing says "A made-up match between made-up clubs."
- **Binding visual constraints** (approved in the 2026-10-14 design review, recorded without expansion):
  - two colours plus one spot colour, and the spot only ever means "on air now";
  - never orange with blue; no yellow or cream backgrounds;
  - colour is never the only signal;
  - the typeface is Atkinson Hyperlegible Next;
  - follow the device's light/dark setting, with both themes at WCAG AA.

## Evidence on Hand

- The design doc with every review decision: `docs/designs/earshot.md`. The scope summary: `docs/designs/earshot-ceo-plan.md`.
- The office-hours wireframe, now stale on several screens: `~/.gstack/projects/inside-the-game/designs/wireframe-20261014/sketch.png`.
- The hackathon rules: github.com/microsoft/insidethegamehackathon, `OFFICIAL RULES.md`.
- **Absent, so never fabricate:** blind-tester results (outreach pending; the listening test is planned for Oct 18–19, small n), testimonials, user or listener counts, broadcaster partners, a logo, real match data, and performance numbers not yet measured.

## Product Principles

1. **Facts come from code.** A listener who cannot check must never hear an unchecked claim. The model explains, code states.
2. **Ears first.** Sound carries where the ball is, and speech carries who and why. The screen serves the listener, never the other way round.
3. **Never silent without saying why.** Every wait, failure and mode change is spoken as well as shown.
4. **One way in per step.** One decision per screen, one primary action. A choice that applies to only one mode appears only after that mode is chosen.
5. **One engine, three audiences.** The fan's commentary, the broadcaster's track and the producer's view all come from the same moment stream.

## Accessibility & Inclusion

- WCAG 2.2 AA in both themes: body text at least 4.5:1; large text and UI parts at least 3:1.
- Screen readers are first-class: VoiceOver (iOS Safari) and TalkBack (Android Chrome). Live regions exist at load and are cleared before each update. Fixed announcement templates. A logical heading and focus order.
- Text follows the device's text-size setting, with a base of at least 18 px. Layouts reflow at 320 px wide and at 200% text without horizontal scrolling. Touch targets are 48 px.
- `prefers-reduced-motion` is respected.
- Spoken output works with one ear: a mono cue mode, speech pan capped, and headphones recommended but not required.
- `lang` and `dir` follow the listener's language, and Arabic runs right to left.
