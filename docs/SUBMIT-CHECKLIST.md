# Earshot: what Carson does by hand before 12:29 IST on 28 October

Deadline: 27 October 2026, 11:59 PM Pacific = 28 October, 12:29 IST. Submit on
https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon (Projects page),
not Devpost. Judging 27 Oct to 10 Nov; winners by 20 Nov.

## Done by the sessions (nothing for you here)

- Held-out Explanations run of record: 40/40, filed in EVALUATION.md and reports/ (PR #50, #51).
- docs/submission.md: every field drafted, numbers final, "How it maps to the brief" added.
- Architecture diagram: docs/img/architecture.png (3840x2160, official Azure icons).
- Demo video: video/brag-output/brag-narrated.mp4, 1:57, narrated, filmed on the current app (pitch
  map, all 22 players, HD voices). https://youtu.be/j-MYF-HFq9k. It replaced the first cut (brag.mp4,
  72 s, https://youtu.be/t_ZsD_Av0xw) on 27 Oct. The script is in docs/video-script.md.
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

Done: the repo is public. Some early squash commits carry a personal author email in their
metadata. That was accepted rather than rewritten, because a rewrite changes the commit ids
that EVALUATION.md cites (for example eval-freeze f69549b). The address is not written in
any file.
Confirm the licence question: the repo has no LICENSE file today. The rules do not require one,
but judges will look. Add one if you want (MIT is the usual pick) or leave it; your call.

## 3. Upload the video

- File: video/brag-output/brag-narrated.mp4 (1:57; the rules cap it at 2 min). Thumbnail:
  video/brag-output/brag-narrated.jpg.
- YouTube, visibility Public (not unlisted). Title, description and chapters:
  video/brag-output/youtube-narrated.txt.
- The URL, https://youtu.be/j-MYF-HFq9k, is in docs/submission.md. On the form, replace the old
  video link with it and save.

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

## 6. State at wrap-up (29 Oct, 01:30 IST)

Done:
- Submitted on 27 Oct at 03:50 IST (Innovation Studio project 127126). The repo is public.
- The video is the narrated 1:57 cut, https://youtu.be/j-MYF-HFq9k. It is both the file on the
  project page and the link in its description; both were checked after a reload. The first cut,
  https://youtu.be/t_ZsD_Av0xw, is no longer referenced anywhere.
- The worker has been always on since 27 Oct 03:58 IST: `f54bc56`, HD voices, streaming off,
  match view on. `DEPLOY_ENABLED=false`. The judging-worker load tests are in EVALUATION.md.
- Secrets check on 28 Oct:
  - gitleaks over all 163 commits on every branch: clean;
  - none of the subscription id, tenant id, AI endpoint, judge code, storage keys, SAS tokens or
    private keys appear in any file, commit, PR or issue;
  - `.env` was never committed, only `.env.example` with placeholders;
  - Earshot uses Entra sign-in throughout, so there are no API keys to leak.
- The two forked sessions (Stream 2 and the submission session) are archived. All 24 local branches
  were merged and are deleted. The Stream 2 worktree is removed.

Left for Carson:
1. **Delete 9 old Actions runs.** These deploy runs from 16 Oct printed the subscription, tenant
   and client ids in their public logs: the workflow passes them as plain variables. They are ids,
   not credentials, but they should not be public. Deleting runs is permanent, so it is yours:
   `for id in 38910902329 38911146771 38912532992 38913894830 38914720376 38916904076 38929143461 38929176806 38930335972; do gh run delete $id --repo rodriguescarson/earshot; done`
   If deploys are ever turned back on, move `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and
   `AZURE_SUBSCRIPTION_ID` to repository secrets first, so they are masked.
2. **Turn on secret scanning and push protection.** They are free on a public repo and both are
   off: repo Settings, then Advanced Security.
3. **Delete 7 merged remote branches.** All were squash-merged and the tips match their PRs:
   `git push origin --delete chore/deploy-lowest chore/stream2-next-3 eval/post-queue feat/audio-queue feat/eval-harness-auditor feat/mono-centre feat/split-audio-queues`
4. **Unlist the first video** (https://youtu.be/t_ZsD_Av0xw) whenever you like.
5. **Take it to ₹0 after judging.** From 11 Nov 13:29 IST the worker can go to `--min-replicas 0`.
   After the winners (by 21 Nov 13:29 IST) and any prize forms, delete `rg-earshot`, purge the
   soft-deleted AI account and delete the budget. The commands are in `~/hackathons/README.md`.

Kept on purpose: `~/earshot-eval-runs/heldout-2026-10-17`, the pinned checkout and raw outputs of
the held-out run of record. Its summary and per-match counts are in `reports/`.
