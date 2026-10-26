# Earshot: demo video script (draft)

DRAFT for Carson. Target length 1:50, under the rules' two-minute limit. The rules also ban third-party trademarks and
copyrighted music. So: no club badges, no broadcaster names, no music, only Earshot's own sounds.

## What Carson records (about 15 minutes)

1. **The phone, with VoiceOver on.** Screen recording with sound (Control Centre > Screen Recording, long-press,
   Microphone off). Headphones in, so the stereo is real.
   - Open the site and press **Listen now**. Let it run past the goal: the save is at 1:10 and the goal at 1:13.
   - Lock the phone for ten seconds while it plays, then unlock it.
   - Go back and choose **Choose language and style** > Arabic > My screen reader > Casual > Listen.
2. **The narration**, read into the laptop mic in a quiet room. Each line below is one take, so a mistake only
   costs one line. Save it as `narration.m4a`.

## What I assemble

- Desktop captures of the producer column ("Behind the voice") and the live match. I record these myself.
- The cut, the captions and the stereo mix, with ffmpeg, from the two recordings above.
- The captions burned in, so a judge watching with the sound off still follows.

## The script

| Time | Picture | Sound |
|---|---|---|
| 0:00-0:08 | Black. A caption: "Headphones on. Eyes closed if you like." | Earshot on its own: the ball tone moving from the left ear to the right, the crowd rising, then "Varero shoots, saved by Barana!" (the clip's own line at 1:10) |
| 0:08-0:22 | The phone on the landing screen. | **Narration:** "Blind fans follow football by ear. This season in the UK, described commentary covers about a hundred and nine of the three hundred and eighty league matches. Most matches get nothing, and most languages get nothing at all." |
| 0:22-0:50 | The phone recording: Listen now, the score plate, the line on air, the goal. Hold on the goal. | Earshot's own audio, ducked under one line of **narration** at 0:24: "This is Earshot. Listen to where the ball is." Then let the goal play out, crowd and all. |
| 0:50-1:02 | The phone locked, still playing. Then Arabic, right to left, with VoiceOver reading the lines. | **Narration:** "It keeps going with the screen locked. It speaks four languages. Or it can hand every line to your own screen reader, at your own speed." (Keep the locked-screen sentence only if the lock test passes on the iPhone; otherwise cut it and the locked shot.) |
| 1:02-1:25 | The desktop: the "Behind the voice" column ticking through Spotter, Caller, Checker, Auditor and Director. | **Narration:** "Eight agents share one voice. Every name and number you hear comes from the match data, and it is checked twice before it plays. The AI only picks which changes are worth saying, in the stoppages, and code checks its numbers too." |
| 1:25-1:42 | Plain cards, one fact each, big type: "63,430 wrong names and numbers planted. 63,430 caught." Then: "Can the AI say why? On 40 unseen matches, right 1 time in 10. A plain rule: 1 in 4. So it doesn't." | **Narration:** "We tried to break it. On forty matches it had never seen, we planted more than sixty thousand wrong facts. It caught every one. We also asked whether the AI could explain why a team changed. On forty unseen matches it was right one time in ten, and a plain rule did better. So Earshot doesn't guess." |
| 1:42-1:50 | The live match on the desktop, then the site address. | **Narration:** "Earshot. Live description for every match, in your language." |

## Notes

- **The planted-defect number:** 63,430 is the total across the four injection points on the held-out seeds
  (EVALUATION.md, runs of record). Recheck it against the final EVALUATION.md before recording.
- **The 109 of 380 figure:** sourced from Broadband TV News, 2026-10-08. No broadcaster is named on screen.
- **If the held-out Explanations result changes** the "1 time in 22" line, swap in the held-out number
  (due about Oct 21).
- **Upload:** YouTube, unlisted or public; the rules need a public URL on the project page. Carson uploads it.
