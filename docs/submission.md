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

Two buttons answer the questions people ask most: where's the ball, and what did I miss. At half
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
Speech supplies two neural voices per language. An MCP server gives other agents, a broadcaster's
assistant for example, the same read-only match tools, and it never sees past the current minute.

It runs on Azure Container Apps, Container Registry, Web PubSub, Storage and Static Web Apps,
watched by Application Insights and Log Analytics. [PENDING: confirm each is live at submission.]
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
- In a test run, the checks caught every wrong name and number we planted on purpose, including
  a real player's name in the wrong place.
- That test run also caught one real mistake of our own: a possession figure that could be off by
  one percent. It is fixed.
- We also tested whether the AI could say *why* a team changed its tactics, against the hidden log
  of what each team really did. On the first graded match it was right 1 time in 22, worse than a
  simple rule-based guess (4 in 7). So Earshot doesn't say why. The AI still picks which changes
  are worth hearing, and code checks every number in them. [PENDING: the same test on the 40
  unseen matches.]
- [PENDING: the run on 40 matches the system had never seen. Facts, coverage, never-drop and the
  Analyst against a simple baseline.]
- [PENDING: the listening test with eyes closed, Oct 18-19, with the number of listeners stated.]

## Testing instructions

1. Open https://kind-island-0f3823d0f.5.azurestaticapps.net on a phone or a computer. Headphones
   help.
2. Press **Listen now**. A recorded clip plays at once.
3. To try another language or mode, choose **Choose language and style**.
4. On a screen 1024 px or wider, the agents' decisions appear in a second column.
5. To hear a match as it happens, choose **Join the live match** on the first screen. The match
   loops all day, so you may join in the middle of it. Judges' link: [PENDING: the link with the
   judge code, from Stream 2; it skips the per-visitor limits.]

No sign-in. Every match, club and player is made up.

## Links

- Code: [PENDING: public GitHub URL]
- Video: [PENDING: YouTube URL, under two minutes]
