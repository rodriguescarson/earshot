import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dot } from "../components/Dot";
import { type Announcer, LiveRegions } from "../components/LiveRegions";
import { audioUrl, connect, type LineMessage, type LiveSummary, onDemand } from "../live/client";
import { Continuous } from "../live/continuous";
import { sharedAudio } from "../player/audioElement";
import type { Choices } from "../player/choices";

const WIRE_OUTPUT = { app: "app_voice", reader: "screen_reader", oneEar: "one_ear" } as const;
const STALE_MS = 4000;

interface Queued {
  line: LineMessage;
  at: number;
}

/** A live match: lines arrive as the server's Director starts them; the ball is heard in the browser. */
export function LiveListen({ choices, summary }: { choices: Choices; summary: LiveSummary }) {
  const { t } = useTranslation();
  const group = useMemo(
    () => `${summary.match_id}.${choices.lang}.${choices.mode}.${WIRE_OUTPUT[choices.output]}.-.-.-`,
    [summary.match_id, choices],
  );
  const [score, setScore] = useState({
    home: summary.home_score,
    away: summary.away_score,
    minute: summary.minute,
  });
  const [onAir, setOnAir] = useState<LineMessage | null>(null);
  const [spoken, setSpoken] = useState<{ seq: number; line: LineMessage }[]>([]);
  const seq = useRef(0);
  const [paused, setPaused] = useState(false);
  const [down, setDown] = useState(false);
  const announcer = useRef<Announcer>(null);
  const current = useRef<LineMessage | null>(null);
  const queue = useRef<Queued[]>([]);
  const layer = useRef<Continuous | null>(null);
  const reader = choices.output === "reader";

  const play = useCallback(
    (line: LineMessage) => {
      seq.current += 1;
      const entry = { seq: seq.current, line };
      setSpoken((xs) => [entry, ...xs].slice(0, 50));
      if (reader || !line.audio_url) {
        // Screen-reader mode, or no speech for this line (cached-only mode or the daily speech cap).
        announcer.current?.announce(
          line.voice_role === "explanation" ? `${t("listen.analysisPrefix")} ${line.text}` : line.text,
          line.never_drop,
        );
        setOnAir(line);
        return;
      }
      const el = sharedAudio();
      const busy = current.current !== null && !el.paused && !el.ended;
      if (busy && !(line.preempt && !current.current?.never_drop)) {
        queue.current.push({ line, at: performance.now() });
        return;
      }
      const url = audioUrl(line.audio_url);
      current.current = line;
      setOnAir(line);
      if (!url) return;
      el.src = url;
      layer.current?.duck(true);
      void el.play().catch(() => undefined);
    },
    [reader, t],
  );

  useEffect(() => {
    const el = sharedAudio();
    const next = () => {
      layer.current?.duck(false);
      current.current = null;
      setOnAir(null);
      while (queue.current.length) {
        const q = queue.current.shift() as Queued;
        if (q.line.never_drop || performance.now() - q.at < STALE_MS) {
          play(q.line);
          return;
        }
      }
    };
    el.addEventListener("ended", next);
    return () => el.removeEventListener("ended", next);
  }, [play]);

  const playRef = useRef(play);
  playRef.current = play;
  const oneEar = choices.output === "oneEar";

  useEffect(() => {
    if (paused) return;
    if (!reader && !layer.current) layer.current = new Continuous(oneEar);
    layer.current?.resume().catch(() => undefined);
    const stop = connect(
      group,
      (m) => {
        setDown(false);
        if (m.kind === "line") playRef.current(m);
        else if (m.kind === "state")
          setScore((s) => ({ home: m.home_score, away: m.away_score, minute: m.minute ?? s.minute }));
        else if (m.kind === "ball") layer.current?.ball(m.x, m.danger, m.in_play ?? true);
      },
      () => setDown(true),
    );
    return stop;
  }, [group, paused, reader, oneEar]);

  useEffect(() => {
    if (down) announcer.current?.announce(t("listen.reconnecting"), true);
  }, [down, t]);

  useEffect(
    () => () => {
      layer.current?.close();
      layer.current = null;
    },
    [],
  );

  const ask = async (kind: "where" | "catchup") => {
    try {
      const r = await onDemand(group, kind);
      play({
        kind: "line",
        id: r.id,
        priority: 70,
        preempt: true,
        never_drop: false,
        audio_url: r.audio_url,
        text: r.text,
        voice_role: "facts",
        late: false,
      });
    } catch {
      announcer.current?.announce(t("listen.tryAgain"));
    }
  };

  return (
    <main className="listen">
      <LiveRegions ref={announcer} />
      <section className="plate score" aria-live="off">
        <h1 tabIndex={-1} className="score__line">
          {t("listen.score", {
            home: summary.home.name,
            away: summary.away.name,
            homeScore: score.home,
            awayScore: score.away,
            minute: score.minute,
          })}
          <span className="score__status">
            {" "}
            {down ? t("listen.reconnecting") : paused ? t("listen.status.paused") : t("listen.status.live")}
          </span>
        </h1>
      </section>
      <section className="onair" aria-label={t("listen.onAir")}>
        <Dot state={onAir ? (onAir.never_drop ? "never-drop" : "on-air") : "waiting"} />
        <p className="onair__text">{onAir ? onAir.text : t("listen.waiting")}</p>
      </section>
      <div className="controls">
        <button
          type="button"
          className="primary"
          onClick={() => {
            if (paused) {
              setPaused(false);
              void ask("catchup");
            } else {
              setPaused(true);
              sharedAudio().pause();
              layer.current?.pause();
            }
          }}
        >
          {paused ? t("listen.resume") : t("listen.pause")}
        </button>
        <button type="button" className="secondary" onClick={() => void ask("where")} disabled={paused}>
          {t("listen.whereBall")}
        </button>
        <button type="button" className="secondary" onClick={() => void ask("catchup")} disabled={paused}>
          {t("listen.catchUp")}
        </button>
      </div>
      <section className="transcript" aria-labelledby="live-transcript-h">
        <h2 id="live-transcript-h">{t("listen.transcript")}</h2>
        {spoken.length === 0 ? (
          <p className="muted">{t("listen.transcriptEmpty")}</p>
        ) : (
          <ol className="transcript__list">
            {spoken.map(({ seq: n, line }, i) => (
              <li key={n} className={i === 0 && onAir === line ? "is-current" : undefined}>
                {line.never_drop && <Dot state="never-drop" />}
                {line.voice_role === "explanation" ? `${t("listen.analysisPrefix")} ${line.text}` : line.text}
              </li>
            ))}
          </ol>
        )}
      </section>
      <section className="recaps" aria-labelledby="live-recaps-h">
        <h2 id="live-recaps-h">{t("listen.recaps")}</h2>
        <p className="muted">{t("listen.recapsEmpty")}</p>
      </section>
    </main>
  );
}
