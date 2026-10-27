# Earshot: what Carson does by hand before 12:29 IST on 28 October

Deadline: 27 October 2026, 11:59 PM Pacific = 28 October, 12:29 IST. Submit on
https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon (Projects page),
not Devpost. Judging 27 Oct to 10 Nov; winners by 20 Nov.

## Done by the sessions (nothing for you here)

- Held-out Explanations run of record: 40/40, filed in EVALUATION.md and reports/ (PR #50, #51).
- docs/submission.md: every field drafted, numbers final, "How it maps to the brief" added.
- Architecture diagram: docs/img/architecture.png (3840x2160, official Azure icons).
- Demo video: video/brag-output/brag.mp4, 72 s, result card swapped to the held-out number.
- Azure HD voices, the match view (22 players + ball, recorded and live) and a streamed audio path
  behind a flag are on main and deployed; Stream 1 measures the flag on Oct 27.
- Clean clone: `uv sync` 6 s, 1,259 tests pass in 88 s. Live site plays the clip from the deployed URL.
- Stream 1 (session earshot-p0-p1-plan) did the Oct 27 infra at 03:58-05:28 IST:
  - the worker is always on at `f54bc56` (HD voices, match view, streaming off);
  - the judges' link checks out;
  - three 30-listener load tests: p95 2.80 s with HD, 2.75 s with streaming, 2.48 s with the
    earlier voices (EVALUATION.md);
  - DEPLOY_ENABLED=false.

## 1. Decide the voice (today)

Listen to video/voice-ab/ab-goal.mp3 and ab-routine.mp3. Reply with A, B, C or D. C is the Azure HD
voice. If you pick B, C or D the 16 clips are re-synthesised and the live match serves the new audio;
Stream 1 is told before it merges so the load test runs on the final audio.

## 2. Make the repo public (required by the rules, section on submission requirements)

Decide first about the 42 squash commits authored as rodriguescarson@hotmail.com:
- accept them (fastest), or
- rewrite with a mailmap and force-push main (ask a session to do it; it is a 10-minute job but
  it rewrites history the judges will clone).
Then: GitHub → rodriguescarson/earshot → Settings → Danger Zone → Change visibility → Public.
Confirm the licence question: the repo has no LICENSE file today. The rules do not require one,
but judges will look. Add one if you want (MIT is the usual pick) or leave it; your call.

## 3. Upload the video

- File: video/brag-output/brag.mp4 (72 s; the rules cap it at 2 min). Poster: video/brag-output/brag.jpg.
- YouTube, visibility Public (not unlisted). Title suggestion: "Earshot: live football you can
  follow by ear (Inside the Game hackathon)". Description: paste share-copy.txt plus the live URL.
- Paste the URL into docs/submission.md (the one [CARSON] marker) and into the form.

## 4. Fill the form (Projects page)

- Category: Best Multi-Agent Orchestration (also eligible for the Grand Prize).
- Pitch, problem, what it does, how it's built, data, what we measured, testing instructions: copy
  from docs/submission.md, section by section.
- Testing instructions: add the judges' link from
  ~/Library/Application Support/Claude/side-session-notes/local_2727389e-51ad-4886-bd2d-349ab4e7916f/judge-link.txt
  (it skips the per-visitor limits; it never goes in the repo).
- Hosted URL: https://kind-island-0f3823d0f.5.azurestaticapps.net
- Code URL: https://github.com/rodriguescarson/earshot (public by then).
- Video URL: from step 3.
- Architecture diagram: upload docs/img/architecture.png.
- If you ran a listening test on 18-19 Oct, replace the "did not run" sentence in the draft with
  the numbers; otherwise leave it, it is honest as written.
- Save, reload the page, and check every field persisted before pressing Submit.

## 5. After submit

- Tell both sessions it is in, so nothing on main, the video or the links moves until winners are
  announced (freeze).
- Keep the worker always-on through 10 November (Stream 1's deploy). Budget: ₹1,953 of ₹4,800
  spent on 26 Oct, and ₹2,120 by early 27 Oct. Judging adds roughly ₹1,500-2,500. Raise the
  budget only if you want headroom.
- If you ever want the faster earlier voices for judging, it is one setting, not a deploy:
  `az containerapp update -g rg-earshot -n ca-earshot-worker --set-env-vars EARSHOT_VOICE_TIER=standard`
  (add `--subscription` from .env). Removing the variable goes back to HD.
