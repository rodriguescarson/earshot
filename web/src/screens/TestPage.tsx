import { useEffect, useRef, useState } from "react";
import { sharedAudio, unlockAudio } from "../player/audioElement";

/**
 * Listening test, facilitator view (protocol earshot-listening-v1, pre-registered in EVALUATION.md).
 * The facilitator runs this page; the listener only hears the audio and answers out loud.
 */

type Arm = "speech" | "pan" | "full";
interface Probe {
  t_ms: number;
  score: string;
  possession: string;
  where: "left" | "middle" | "right";
}
interface TestClip {
  id: string;
  seed: number;
  dir: string;
  home: string;
  away: string;
  probes: Probe[];
}
interface TestPlan {
  protocol: string;
  arms: Record<Arm, string>;
  orders: Arm[][];
  headphones: string;
  clips: TestClip[];
}
interface Answer {
  participant: number;
  order: number;
  trial: number;
  clip: string;
  seed: number;
  arm: Arm;
  probe: number;
  t_ms: number;
  score: boolean;
  possession: boolean;
  where: boolean;
}

const BASE = "/clips/listen-test";
const QUESTIONS: { key: "score" | "possession" | "where"; ask: string }[] = [
  { key: "score", ask: "What's the score?" },
  { key: "possession", ask: "Who has the ball?" },
  { key: "where", ask: "Where is the ball: left, middle or right?" },
];

export function TestPage() {
  const [plan, setPlan] = useState<TestPlan | null>(null);
  const [participant, setParticipant] = useState(1);
  const [trial, setTrial] = useState(-1);
  const [probe, setProbe] = useState<number | null>(null);
  const [marks, setMarks] = useState<Partial<Record<"score" | "possession" | "where", boolean>>>({});
  const [answers, setAnswers] = useState<Answer[]>([]);
  const nextProbe = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    void fetch(`${BASE}/test.json`)
      .then((r) => r.json())
      .then(setPlan);
  }, []);

  const order = (participant - 1) % 3;
  const clip = plan && trial >= 0 ? plan.clips[trial] : undefined;
  const arm = plan && trial >= 0 ? plan.orders[order]?.[trial] : undefined;

  // biome-ignore lint/correctness/useExhaustiveDependencies: move focus only when the trial or probe changes, not on every mark
  useEffect(() => {
    heading.current?.focus();
  }, [trial, probe]);

  useEffect(() => {
    if (!clip || !arm) return;
    const el = sharedAudio();
    el.src = `${BASE}/${clip.dir}/${arm}.m4a`;
    nextProbe.current = 0;
    const onTime = () => {
      const p = clip.probes[nextProbe.current];
      if (p && el.currentTime * 1000 >= p.t_ms) {
        el.pause();
        setProbe(nextProbe.current);
        nextProbe.current += 1;
      }
    };
    el.addEventListener("timeupdate", onTime);
    void el.play();
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.pause();
    };
  }, [clip, arm]);

  if (!plan) return <main className="listen">Loading the test…</main>;

  if (trial < 0)
    return (
      <main className="listen test">
        <h1 ref={heading} tabIndex={-1}>
          Listening test (facilitator)
        </h1>
        <p className="muted">
          Three clips, three ways of hearing them. The listener wears stereo headphones and answers out loud.
          You mark each answer. Protocol {plan.protocol}, pre-registered.
        </p>
        <label className="choice">
          <span className="choice__label">Participant number</span>
          <input
            type="number"
            min={1}
            value={participant}
            onChange={(e) => setParticipant(Math.max(1, Number(e.target.value) || 1))}
          />
          <span className="choice__hint">
            Arm order {order + 1} of 3: {plan.orders[order]?.join(", ")}
          </span>
        </label>
        <button
          type="button"
          className="secondary"
          onClick={() => {
            unlockAudio();
            const el = sharedAudio();
            el.src = `${BASE}/${plan.headphones}`;
            void el.play();
          }}
        >
          Headphone check: Left, then Right
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => {
            unlockAudio();
            setTrial(0);
          }}
        >
          Start trial 1
        </button>
      </main>
    );

  if (!clip || !arm) {
    const blob = URL.createObjectURL(
      new Blob([JSON.stringify({ protocol: plan.protocol, answers }, null, 2)]),
    );
    const rate = (k: "score" | "possession" | "where", a: Arm) => {
      const rows = answers.filter((x) => x.arm === a);
      return rows.length ? `${rows.filter((x) => x[k]).length}/${rows.length}` : "–";
    };
    return (
      <main className="listen test">
        <h1 ref={heading} tabIndex={-1}>
          Participant {participant}: done
        </h1>
        <table className="agents__table">
          <thead>
            <tr>
              <th scope="col">Arm</th>
              {QUESTIONS.map((q) => (
                <th key={q.key} scope="col">
                  {q.key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(["speech", "pan", "full"] as Arm[]).map((a) => (
              <tr key={a}>
                <th scope="row">{plan.arms[a]}</th>
                {QUESTIONS.map((q) => (
                  <td key={q.key}>{rate(q.key, a)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        <a className="primary" href={blob} download={`earshot-listening-p${participant}.json`}>
          Download results (JSON)
        </a>
      </main>
    );
  }

  const p = probe !== null ? clip.probes[probe] : undefined;
  return (
    <main className="listen test">
      <h1 ref={heading} tabIndex={-1}>
        Trial {trial + 1} of 3: clip {clip.id.toUpperCase()}
      </h1>
      <p className="muted">
        {clip.home} v {clip.away}. Arm: {plan.arms[arm]} (don't tell the listener).
      </p>
      {p ? (
        <form
          className="choose"
          onSubmit={(e) => {
            e.preventDefault();
            setAnswers((xs) => [
              ...xs,
              {
                participant,
                order,
                trial,
                clip: clip.id,
                seed: clip.seed,
                arm,
                probe: probe ?? 0,
                t_ms: p.t_ms,
                score: !!marks.score,
                possession: !!marks.possession,
                where: !!marks.where,
              },
            ]);
            setMarks({});
            setProbe(null);
            if (nextProbe.current >= clip.probes.length) setTrial(trial + 1);
            else void sharedAudio().play();
          }}
        >
          {QUESTIONS.map((q) => (
            <fieldset key={q.key}>
              <legend>
                Ask: {q.ask} <span className="muted">(true answer: {p[q.key]})</span>
              </legend>
              {[true, false].map((v) => (
                <label key={String(v)} className="choice">
                  <input
                    type="radio"
                    name={q.key}
                    checked={marks[q.key] === v}
                    onChange={() => setMarks((m) => ({ ...m, [q.key]: v }))}
                    required
                  />
                  <span className="choice__label">{v ? "Correct" : "Wrong or don't know"}</span>
                </label>
              ))}
            </fieldset>
          ))}
          <button type="submit" className="primary">
            Save and continue
          </button>
        </form>
      ) : (
        <p>Playing. The clip pauses by itself when it's time to ask.</p>
      )}
    </main>
  );
}
