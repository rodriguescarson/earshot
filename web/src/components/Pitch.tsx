import { useTranslation } from "react-i18next";

/** The pitch as a wayfinding plate: lines in seam, the ball in ink. The spot colour stays reserved
 * for "on air", so the ball never borrows it. Decorative for screen-reader users, who hear the ball;
 * labelled once so it is not a silent image. Coordinates are metres on a 105 x 68 pitch. */
export interface BallPos {
  x: number;
  y: number;
  inPlay: boolean;
  danger?: number;
}

export interface PlayerDot {
  id: string;
  name: string;
  home: boolean;
  x: number;
  y: number;
}

export function Pitch({
  ball,
  players = [],
  home,
  away,
}: {
  ball: BallPos | null;
  players?: PlayerDot[];
  home: string;
  away: string;
}) {
  const { t } = useTranslation();
  const L = 105;
  const W = 68;
  const pad = 3;
  const r = ball?.inPlay === false ? 1.5 : 1.8;
  return (
    <figure className="pitch" aria-label={t("listen.pitch", { home, away })}>
      <svg
        viewBox={`${-pad} ${-pad} ${L + 2 * pad} ${W + 2 * pad}`}
        className="pitch__svg"
        role="img"
        aria-hidden="true"
      >
        <rect x="0" y="0" width={L} height={W} className="pitch__turf" />
        <rect x="0" y="0" width={L} height={W} className="pitch__line" />
        <line x1={L / 2} y1="0" x2={L / 2} y2={W} className="pitch__line" />
        <circle cx={L / 2} cy={W / 2} r="9.15" className="pitch__line" />
        <rect x="0" y={(W - 40.32) / 2} width="16.5" height="40.32" className="pitch__line" />
        <rect x={L - 16.5} y={(W - 40.32) / 2} width="16.5" height="40.32" className="pitch__line" />
        <rect x="0" y={(W - 18.32) / 2} width="5.5" height="18.32" className="pitch__line" />
        <rect x={L - 5.5} y={(W - 18.32) / 2} width="5.5" height="18.32" className="pitch__line" />
        <rect x="-1.2" y={(W - 7.32) / 2} width="1.2" height="7.32" className="pitch__goal" />
        <rect x={L} y={(W - 7.32) / 2} width="1.2" height="7.32" className="pitch__goal" />
        {players.map((p) => (
          <g
            key={p.id}
            className={`pitch__player${p.home ? " pitch__player--home" : " pitch__player--away"}`}
          >
            <circle cx={p.x} cy={p.y} r="1.7" className="pitch__shirt" />
            <title>{p.name}</title>
          </g>
        ))}
        {ball && (
          <g className={`pitch__ball${ball.inPlay ? "" : " pitch__ball--dead"}`}>
            {ball.danger !== undefined && ball.danger > 0.5 && (
              <circle cx={ball.x} cy={ball.y} r={r + 2.2 * ball.danger} className="pitch__danger" />
            )}
            <circle cx={ball.x} cy={ball.y} r={r} className="pitch__dot" />
          </g>
        )}
      </svg>
      <figcaption className="pitch__ends" aria-hidden="true">
        <span>{home}</span>
        <span>{away}</span>
      </figcaption>
    </figure>
  );
}
