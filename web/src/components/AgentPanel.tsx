import { useTranslation } from "react-i18next";
import { type AgentName, type ClipLine, latestByAgent, type Manifest } from "../player/manifest";

const AGENTS: AgentName[] = [
  "Spotter",
  "Caller",
  "StatWatcher",
  "Checker",
  "Auditor",
  "Analyst",
  "Director",
  "Voice",
];
const WINDOW_MS = 60_000;

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function pattern(line: ClipLine): string {
  if (line.source === "analyst") return "url(#air-analyst)";
  if (line.source === "fallback") return "url(#air-fallback)";
  return "var(--ink)";
}

/** The agents behind the voice: airtime for the last minute, then each agent's latest decision. */
export function AgentPanel({ manifest, tMs }: { manifest: Manifest; tMs: number }) {
  const { t } = useTranslation();
  const latest = latestByAgent(manifest, tMs);
  const from = tMs - WINDOW_MS;
  const width = 600;
  const x = (ms: number) => ((Math.max(from, Math.min(tMs, ms)) - from) / WINDOW_MS) * width;
  const recent = manifest.lines.filter((l) => l.end_ms > from && l.t_ms <= tMs);
  const onAir = recent.find((l) => l.t_ms <= tMs && tMs < l.end_ms);
  const airSeconds = recent.reduce(
    (sum, l) => sum + (Math.min(l.end_ms, tMs) - Math.max(l.t_ms, from)) / 1000,
    0,
  );

  return (
    <aside className="agents" aria-labelledby="agents-h">
      <h2 id="agents-h">{t("agents.title")}</h2>
      <p className="agents__lede">{t("agents.lede")}</p>
      <figure className="airtime">
        <svg viewBox={`0 0 ${width} 40`} preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <pattern
              id="air-analyst"
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="8" height="8" className="airtime__ground" />
              <rect width="4" height="8" className="airtime__ink" />
            </pattern>
            <pattern id="air-fallback" width="8" height="8" patternUnits="userSpaceOnUse">
              <rect width="8" height="8" className="airtime__ground" />
              <circle cx="4" cy="4" r="2" className="airtime__ink" />
            </pattern>
          </defs>
          <rect x="0" y="0" width={width} height="40" className="airtime__track" />
          {recent.map((l) => (
            <rect
              key={`${l.t_ms}-${l.template_id}`}
              x={x(l.t_ms)}
              y="4"
              width={Math.max(1, x(l.end_ms) - x(l.t_ms))}
              height="32"
              style={{ fill: l === onAir ? "var(--spot)" : pattern(l) }}
            />
          ))}
          {recent
            .filter((l) => l.cut)
            .map((l) => (
              <rect
                key={`cut-${l.t_ms}`}
                x={x(l.end_ms) - 1}
                y="0"
                width="3"
                height="40"
                className="airtime__cut"
              />
            ))}
        </svg>
        <figcaption>
          {t("agents.airtime", { seconds: Math.round(airSeconds) })}{" "}
          <span className="legend">
            <span className="legend__swatch legend__swatch--caller" /> {t("agents.legendCaller")}
            <span className="legend__swatch legend__swatch--analyst" /> {t("agents.legendAnalyst")}
            <span className="legend__swatch legend__swatch--fallback" /> {t("agents.legendFallback")}
          </span>
        </figcaption>
      </figure>
      <table className="agents__table">
        <thead>
          <tr>
            <th scope="col">{t("agents.agent")}</th>
            <th scope="col">{t("agents.decision")}</th>
            <th scope="col">{t("agents.time")}</th>
          </tr>
        </thead>
        <tbody>
          {AGENTS.filter(
            (a) => a !== "StatWatcher" || (manifest.agents ?? []).some((e) => e.agent === a),
          ).map((a) => {
            const e = latest.get(a);
            return (
              <tr key={a} className={e ? `is-${e.kind}` : undefined}>
                <th scope="row">{a}</th>
                <td>
                  {e ? (
                    <>
                      {e.kind === "block" && <strong className="tag">{t("agents.blocked")} </strong>}
                      {e.kind === "air" && <strong className="tag tag--air">{t("agents.onAir")} </strong>}
                      {e.text.replace(/^on air: /, "")}
                    </>
                  ) : (
                    <span className="muted">{t("agents.idle")}</span>
                  )}
                </td>
                <td className="agents__time">{e ? clock(e.t_ms) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </aside>
  );
}
