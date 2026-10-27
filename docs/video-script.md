# Earshot: the demo video

Published 27 October 2026: https://youtu.be/j-MYF-HFq9k (1:57, public). It replaced the first cut,
https://youtu.be/t_ZsD_Av0xw (72 s, captions only). That cut was filmed on the 24 October app,
before the pitch map, the 22-player match view and the HD voices.

The rules ban third-party trademarks and copyrighted music. So the video has no music, no league,
club or broadcaster names, and no club marks. Every sound is Earshot's own, except the narrator.

## How it is made

Everything is rendered from code on the current app (`main`, e93cc30). The render scripts live in
`video/brag-output/work/`, which is gitignored:

- `capture2.mjs` drives the built web app in headless Chrome. It overrides the audio element's
  clock, so each frame is the shipped UI at an exact clip time. It captures the English casual clip
  from 1:02 to 1:33 (the save at 1:10 and the goal at 1:13), three seconds each of Arabic, Spanish
  and Hindi, the landing page and the setup steps.
- `narrate.py` synthesizes the narration one sentence at a time with Azure AI Speech, in the HD
  voice `en-US-Ava:DragonHDLatestNeural`. The app's own English voices are en-GB Ryan and Sonia, so
  the narrator never sounds like the commentary.
- `timeline.py` places each sentence on the clock and writes `timeline.json` (the captions). It
  mixes Earshot's own clip audio under the narration, ducked by a sidechain compressor, at -16 LUFS.
- `comp2.html` composes the frames, captions and cards, and `render2.mjs` renders it at 30 fps.
  ffmpeg encodes it, with the goal frame as frame 0 (the thumbnail).

## The script

| Time | Picture | Narration (captioned on screen) |
|---|---|---|
| 0:00 | "Headphones on." / "Close your eyes. Follow the ball." A ball dot moves along a left-ear to right-ear line. | None. The ball tone pans from ear to ear. |
| 0:06 | The landing page. | Blind fans follow football by ear. But live description needs a human describer, so most matches never get one. In the UK this season, about 109 of 380 league matches will. And only in English. |
| 0:21 | The app playing the match: score plate, the pitch with all 22 players and the ball, the line on air, the "Behind the voice" panel. | This is Earshot. It turns live match data into commentary you can hear. |
| 0:27 | The same, through the save (0:30) and the goal (0:33). | None. Earshot's own audio, unducked: the calls, the ball tone, the goal call. |
| 0:40 | The goal replayed in sound. | The calls come straight from the data. Under them, a tone follows the ball from one ear to the other. The pitch shows the engine's own positions, for anyone watching with you. You don't need it to follow. |
| 0:51 | Setup: the language step, then Arabic, Spanish and Hindi side by side, then the output step, then the detail step with Analyst chosen. | Pick English, Spanish, Arabic or Hindi. Each language has its own lines and its own voices. Earshot can speak, or send every line to your own screen reader. Analyst mode adds the numbers. |
| 1:06 | A close-up of the "Behind the voice" agent table during the goal. | Behind the one voice are eight agents. The Caller writes each line from a fixed template. A Checker and a separate Auditor re-check every name and number, then the Director decides what airs. The AI model only suggests which changes are worth saying, and code checks its numbers too. |
| 1:24 | Card: "On 40 matches Earshot had never seen, we planted 63,430 wrong names and numbers on purpose. It caught all 63,430." | We tried to break it. On 40 matches it had never seen, we planted 63,430 wrong names and numbers. It caught every one. |
| 1:35 | Card: "Can the AI explain why a team changed its tactics? On 40 matches it had never seen: right 1 time in 10. A plain rule: 1 in 4. So Earshot doesn't guess." | We also asked the AI why teams changed tactics. It was right one time in ten. A simple rule did better, so Earshot doesn't guess. |
| 1:44 | The Earshot plate, the Azure stack, the site address. | It's built on Azure AI Foundry, the Microsoft Agent Framework and Azure AI Speech. Earshot: live description for every match, in your language. |

## Sources for the numbers

- **109 of 380:** Broadband TV News, 2026-10-08. No broadcaster is named on screen.
- **63,430 planted, all caught:** the planted-defect run of record on the held-out seeds 9001-9040
  (EVALUATION.md, Facts).
- **1 time in 10, against 1 in 4:** the held-out Explanations run of record. The gated Analyst was
  right 324 of 3,387 times (9.6%) and the baseline 177 of 747 (23.7%) (EVALUATION.md).
