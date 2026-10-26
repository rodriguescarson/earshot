# Earshot: submission draft

DRAFT for Carson to edit. Nothing here is submitted. Lines marked [PENDING] wait on a result or a
deploy and must be filled in or cut before the form goes in.

## Project pitch

Earshot gives blind and partially sighted fans live football commentary on their phone, in their
own language.

It tells you who has the ball and what just happened. You can also hear where the ball is: a soft
tone moves from one ear to the other as the ball travels up the pitch, and the crowd gets louder
when an attack turns dangerous. So the voice only has to say who and why.

Every name and number you hear comes from the match data, put there by code, and is checked twice
before it plays. The AI's job is narrower. During stoppages it explains what is changing, like a
team pressing higher, and code recomputes each claim before it airs. A listener who can't check
what they're told should never be told something wrong.

## The problem

Live description reaches fans only where a human describer happens to be. In the UK this season,
one broadcaster announced described commentary for about 109 of the 380 league matches. Most
matches, and most languages, get nothing.

## What it does

Press Listen now and a three-minute recorded clip starts straight away. It's a made-up match with a
goal in the first minute and a half, in English, Spanish, Arabic or Hindi.

You choose how Earshot talks to you. It can use its own voice and place the ball in sound. It can
send each line to your screen reader instead, at your own speed, while the ball sound keeps
playing. Or it can put everything in one ear, where the pitch of the ball tone tells you which
third of the field it's in.

Casual mode says what happened and who did it. Analyst mode adds the numbers: pass length, shot
speed, and how each team's tactics are shifting. In Analyst mode you can also follow one stat,
such as shot speed or press height, and hear about it every time it moves.

A match view shows the 22 players and the ball moving with the commentary, so a sighted companion
or a studio can follow the same match the listener hears; it is drawn from the engine's positions,
not from footage, because every match here is made up. Two buttons answer the questions people ask
most: where's the ball, and what did I miss. At half
time and full time there's a recap, which a studio can also take as text or data.

Facts and explanations come in different voices, so you always know which line the AI wrote. On a
computer screen, a second column shows what each agent last decided and which line has the floor.

There is also a live match. It plays in real time on our server, around the clock, and you can
join it from the first screen. The same agents run it as it happens, including the AI's
explanations during stoppages.

## How it's built

Eight agents work on one audio channel. The Spotter turns raw events into moments worth saying,
and the Caller writes the play-by-play from fixed sentences in each language. When you follow a
stat, the StatWatcher reports it. During stoppages, the Analyst reads the match through tools and
returns structured claims; it is a gpt-5.4-mini model in Azure AI Foundry. Before anything plays,
the Checker recomputes every claim and every line, and a separate Auditor reads each line again on
its own code path, against the reviewed sentences, the roster and the event log. The Director then
decides what airs, and cuts the small stuff when a goal goes in. Last, the Voice agent picks the
right Azure neural voice.

Microsoft Agent Framework runs this as workflows, one per event and one per stoppage. Azure AI
Speech supplies two neural voices per language, Azure HD voices where the language has them
(English and Spanish both roles; Arabic facts; Hindi explanations), chosen in a blind A/B on the
same goal line on 26 October. An MCP server gives other agents, a broadcaster's
assistant for example, the same read-only match tools, and it never sees past the current minute.

It runs on Azure Container Apps, Container Registry, Web PubSub, Storage and Static Web Apps,
watched by Application Insights and Log Analytics. All of it is live in one resource group as of 26 October: two container apps, a container registry, a Web PubSub instance, two storage accounts, the static site, Application Insights and a Log Analytics workspace. During judging the live-match worker runs always on at 1 vCPU; outside judging it scales to zero.
No service holds a key: everything signs in with Microsoft Entra managed identity. Usage alerts
and a monthly budget can switch the AI off, and the app then falls back to cached audio by itself.

## The data

The league, clubs and players are invented. A match engine we wrote generates a full 380-match
season of events, and alongside it a hidden log of every tactical change a team makes. The Analyst
never sees that log. It exists so we can grade the Analyst's explanations against what really
happened.

## What we measured

- The season engine passed every check we set before running it: 380 matches, 674,950 events,
  realistic goals, shots, passing and cards, and the same output every run.
- We wrote down what counts as a pass before running anything, then locked the code and tested it
  on 40 matches it had never seen.
- On those 40 matches we planted more than 60,000 wrong names and numbers on purpose, in all four
  languages. The checks caught every one of them before it could air, and never blocked a correct
  line.
- An earlier test run also caught one real mistake of our own: a possession figure that could be
  off by one percent. It is fixed.
- We ran a full 380-match season through the live system on a sped-up clock. Every goal, red
  card and penalty aired, for every kind of listener.
- We also tested whether the AI could say *why* a team changed its tactics, against the hidden log
  of what each team really did. On the first graded match it was right 1 time in 22, worse than a
  simple rule-based guess (4 in 7). So Earshot doesn't say why. The AI still picks which changes
  are worth hearing, and code checks every number in them. The run of record on the 40 unseen
  matches, finished on 26 October, says the same thing with real numbers: the causes that would have
  aired were right 324 times out of 3,387, about 1 in 10 (95% interval 7.4 to 11.8 percent), against
  177 of 747 for the rule-based baseline on the same stoppages, about 1 in 4 (18.4 to 29.4 percent).
  The gap held in 34 of the 40 matches. The model did find roughly as many of the real
  changes as the baseline (62 versus 67 of 184) but raised five times as many false alarms. That
  is why causes stay off air.
- Two targets we missed. We wanted 95% of shots and cards spoken within 3 seconds; on our live
  server with 30 people listening at once it was 71% to 78%, depending on the listening mode.
  And we wanted the first sound of a call within 2 seconds of the event in 95% of cases; it was
  3.6 seconds. Most of the delay is calls queueing for the one voice, and the trip from India to
  the server in Sweden.
- A streamed audio path exists behind a flag: the service's own compressed speech is served as it
  is and the browser places it between the ears. On 20 uncached lines it cut the per-line cost
  from 831 ms to 702 ms at the median and from 1,509 ms to 1,213 ms at the 95th percentile, with
  files 18% smaller. It is measured, not yet the default; the numbers above are the file path.
- After that official run we changed how audio is prepared and gave the server the size it will
  have while judges use it. Measured the same way, with 30 listeners, 97% to 98% of shots and cards
  were spoken within 3 seconds, above the target. The first sound came within 2.3 seconds in 95% of
  cases, close to the target but still over it; what is left is calls waiting for the one voice.
  These numbers come from after we locked the code, so the official results stay the misses above.
- We pre-registered a listening test with blind testers (protocol earshot-listening-v1 in EVALUATION.md, section 4.1). It did not run before the deadline, so there is no listener result in this submission. It is the first thing we do after.

## How it maps to the brief

Category entered: **Best Multi-Agent System**. Eight agents share one audio channel, and the
Director arbitrates who speaks; five of them are deterministic code and one is a model, which is
the point: the model proposes and code decides what airs.

The brief's five stages, in Earshot:

| Stage | Where it happens |
|---|---|
| Ingest | the match engine streams events; the Spotter turns them into moments |
| Interpret | the Caller, StatWatcher and Analyst tools compute possession, pass length, shot speed, press height |
| Explain | the Analyst reads stoppages through tools and returns structured claims; the Checker and Auditor recompute every number before it airs |
| Render | one audio channel, two voices per language, the ball in sound, a screen-reader text mode, and a producer column on wide screens |
| Personalize | Casual or Analyst mode, four languages, follow one stat, left-ear or both-ears output |

Judging criteria, in one line each: the implementation is tested on 40 held-out matches with
pass/fail written before the run (Technological Implementation); eight agents with distinct
roles and one arbitrated channel (Agentic Design); described commentary reaches a fraction of
matches today and Earshot covers every match in four languages (Real-World Impact); the demo
video shows the real app and its own audio (User Experience); the entry is multi-agent by
construction (Category adherence).

## Testing instructions

1. Open https://kind-island-0f3823d0f.5.azurestaticapps.net on a phone or a computer. Headphones
   help.
2. Press **Listen now**. A recorded clip plays at once.
3. To try another language or mode, choose **Choose language and style**.
4. A match view under the score shows all 22 players and the ball moving with the commentary,
   from the engine's own positions; the line on air sits below it. On a screen 1024 px or wider,
   the agents' decisions appear in a second column.
5. To hear a match as it happens, choose **Join the live match** on the first screen. The match
   loops all day, so you may join in the middle of it. Judges' link: [PASTE AT SUBMISSION: the link
   in judge-link.txt, which Stream 2 opened for you. It skips the per-visitor limits. It never
   goes in the repo.]
6. The live-match worker is always on during the Judging Period (27 October to 10 November), so
   joining is immediate.

No sign-in. Every match, club and player is made up.

## Links

- Code: https://github.com/rodriguescarson/earshot (the rules require it public; it is private until the submit day)
- Video: [CARSON: paste the YouTube URL of video/brag-output/brag.mp4, 72 seconds, after upload]
