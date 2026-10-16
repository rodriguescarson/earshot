import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dot } from "../components/Dot";
import { type Announcer, LiveRegions } from "../components/LiveRegions";
import { ARM_FOR, type Output } from "../player/choices";
import { type Manifest, onAirAt, scoreAt, spokenBy } from "../player/manifest";
import { useClipPlayer } from "../player/useClipPlayer";

export function Listen({
  base,
  manifest,
  initialOutput,
}: {
  base: string;
  manifest: Manifest;
  initialOutput: Output;
}) {
  const { t } = useTranslation();
  const [output, setOutput] = useState<Output>(initialOutput);
  const [choosing, setChoosing] = useState(false);
  const player = useClipPlayer(base, manifest, ARM_FOR[output]);
  const announcer = useRef<Announcer>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const restore = useRef<HTMLButtonElement>(null);
  const howBtn = useRef<HTMLButtonElement>(null);
  const firstOption = useRef<HTMLInputElement>(null);

  const score = scoreAt(manifest, player.tMs);
  const onAir = onAirAt(manifest, player.tMs);
  const spoken = useMemo(() => spokenBy(manifest, player.tMs), [manifest, player.tMs]);
  const names = useMemo(() => ({ home: manifest.home.name, away: manifest.away.name }), [manifest]);
  const scoreText = t("listen.score", {
    ...names,
    homeScore: score.homeScore,
    awayScore: score.awayScore,
    minute: score.minute,
  });
  const statusText =
    player.state === "paused"
      ? t("listen.status.paused")
      : player.state === "ended"
        ? t("listen.status.ended")
        : t("listen.status.recorded");

  useEffect(() => {
    void player.play();
  }, [player.play]);

  // Screen-reader output: each line goes to a live region as it starts (never-drop ones are urgent).
  const lastAnnounced = useRef<number>(-1);
  useEffect(() => {
    if (output !== "reader" || !onAir || onAir.t_ms === lastAnnounced.current) return;
    lastAnnounced.current = onAir.t_ms;
    const text =
      onAir.voice_role === "explanation" ? `${t("listen.analysisPrefix")} ${onAir.text}` : onAir.text;
    announcer.current?.announce(text, onAir.never_drop);
  }, [output, onAir, t]);

  useEffect(() => {
    if (player.state === "blocked") {
      restore.current?.focus();
      announcer.current?.announce(t("listen.soundBlocked"), true);
    }
    if (player.state === "ended") heading.current?.focus();
  }, [player.state, t]);

  // Lock-screen and headphone controls (Media Session).
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.metadata = new MediaMetadata({
      title: t("media.title", {
        ...names,
        homeScore: score.homeScore,
        awayScore: score.awayScore,
        minute: score.minute,
      }),
      artist: t("media.artist"),
    });
    navigator.mediaSession.setActionHandler("play", () => void player.play());
    navigator.mediaSession.setActionHandler("pause", () => player.pause());
  }, [t, names, score.homeScore, score.awayScore, score.minute, player.play, player.pause]);

  useEffect(() => {
    if (choosing) firstOption.current?.focus();
  }, [choosing]);

  const ended = player.state === "ended";
  return (
    <main className="listen">
      <LiveRegions ref={announcer} />
      <section className="plate score" aria-live="off">
        <h1 ref={heading} tabIndex={-1} className="score__line">
          {ended
            ? t("listen.ended", { ...names, homeScore: score.homeScore, awayScore: score.awayScore })
            : scoreText}
          <span className="score__status"> {statusText}</span>
        </h1>
      </section>

      <section className="onair" aria-label={t("listen.onAir")}>
        <Dot
          state={
            onAir && player.state === "playing" ? (onAir.never_drop ? "never-drop" : "on-air") : "waiting"
          }
        />
        <p className="onair__text">{onAir ? onAir.text : t("listen.waiting")}</p>
      </section>

      {player.state === "blocked" && (
        <button ref={restore} type="button" className="primary attention" onClick={() => void player.play()}>
          {t("listen.restore")}
        </button>
      )}

      {choosing ? (
        <form
          className="choose"
          onSubmit={(e) => {
            e.preventDefault();
            setChoosing(false);
            requestAnimationFrame(() => howBtn.current?.focus());
          }}
        >
          <fieldset>
            <legend>{t("listen.howTalks")}</legend>
            {(["app", "reader", "oneEar"] as const).map((o, i) => (
              <label key={o} className="choice">
                <input
                  ref={i === 0 ? firstOption : undefined}
                  type="radio"
                  name="output"
                  value={o}
                  checked={output === o}
                  onChange={() => setOutput(o)}
                />
                <span className="choice__label">
                  {t(o === "app" ? "listen.outputApp" : "listen.outputReader")}
                </span>
                <span className="choice__hint">
                  {t(o === "app" ? "listen.outputAppHint" : "listen.outputReaderHint")}
                </span>
              </label>
            ))}
          </fieldset>
          <button type="submit" className="primary">
            {t("listen.close")}
          </button>
        </form>
      ) : (
        <div className="controls">
          {ended ? (
            <button type="button" className="primary" onClick={() => void player.play()}>
              {t("listen.again")}
            </button>
          ) : (
            <button
              type="button"
              className="primary"
              onClick={() => (player.state === "playing" ? player.pause() : void player.play())}
            >
              {player.state === "playing" ? t("listen.pause") : t("listen.resume")}
            </button>
          )}
          <button ref={howBtn} type="button" className="secondary" onClick={() => setChoosing(true)}>
            {t("listen.howTalks")}
          </button>
        </div>
      )}

      <section className="transcript" aria-labelledby="transcript-h">
        <h2 id="transcript-h">{t("listen.transcript")}</h2>
        {spoken.length === 0 ? (
          <p className="muted">{t("listen.transcriptEmpty")}</p>
        ) : (
          <ol className="transcript__list">
            {spoken.map((line, i) => (
              <li
                key={`${line.t_ms}-${line.template_id}`}
                className={i === 0 && onAir === line ? "is-current" : undefined}
              >
                {line.never_drop && <Dot state="never-drop" />}
                {line.voice_role === "explanation" ? `${t("listen.analysisPrefix")} ${line.text}` : line.text}
              </li>
            ))}
          </ol>
        )}
      </section>

      <section className="recaps" aria-labelledby="recaps-h">
        <h2 id="recaps-h">{t("listen.recaps")}</h2>
        <p className="muted">{t("listen.recapsEmpty")}</p>
      </section>
    </main>
  );
}
