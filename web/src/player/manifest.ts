/** A rendered clip: one mixed audio file per arm plus the timed lines and score timeline. */

export type Arm = "speech" | "pan" | "full" | "bed" | "mono";

export interface ClipLine {
  t_ms: number;
  end_ms: number;
  text: string;
  voice_role: "facts" | "explanation";
  priority: number;
  never_drop: boolean;
  cut: boolean;
  template_id: string;
  source?: "caller" | "analyst" | "fallback" | "statwatcher" | "on_demand" | "recap" | "notice";
  /** The sonified goal replay: App voice and one-ear only, never sent to a screen reader. */
  replay?: boolean;
  pan: number;
}

export type AgentName =
  | "Spotter"
  | "Caller"
  | "StatWatcher"
  | "Checker"
  | "Auditor"
  | "Analyst"
  | "Director"
  | "Voice";

export interface AgentEntry {
  t_ms: number;
  agent: AgentName;
  kind: "info" | "ok" | "block" | "air";
  text: string;
}

export interface TimelinePoint {
  t_ms: number;
  home_score: number;
  away_score: number;
  minute: string;
  status: "before_kickoff" | "live" | "dead" | "half_time" | "full_time";
}

export interface Manifest {
  clip_id: string;
  description: string;
  language: string;
  listening_mode: "casual" | "analyst";
  duration_ms: number;
  audio: Partial<Record<Arm, string>>;
  home: { team_id: string; name: string };
  away: { team_id: string; name: string };
  start: { home_score: number; away_score: number; minute: string };
  lines: ClipLine[];
  timeline: TimelinePoint[];
  agents?: AgentEntry[];
  /** Ball samples at 10 Hz (every other sample kept): metres on a 105 x 68 pitch. */
  ball?: BallSample[];
  /** Player tracks from the engine, thinned to `hz`: one row per frame, x and y per player in `ids` order. */
  players?: PlayerTracks;
}

export interface PlayerTracks {
  hz: number;
  ids: string[];
  home: boolean[];
  names: string[];
  t_ms: number[];
  xy: number[][];
}

export interface PlayerPos {
  id: string;
  name: string;
  home: boolean;
  x: number;
  y: number;
}

/** Player positions at tMs, interpolated between the two nearest frames; empty before the first. */
export function playersAt(m: Manifest, tMs: number): PlayerPos[] {
  const p = m.players;
  if (!p || p.t_ms.length === 0) return [];
  const t = p.t_ms;
  const first = t[0];
  if (first === undefined || tMs < first) return [];
  let lo = 0;
  let hi = t.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    const v = t[mid];
    if (v !== undefined && v <= tMs) lo = mid;
    else hi = mid - 1;
  }
  const a = p.xy[lo];
  const ta = t[lo];
  if (!a || ta === undefined) return [];
  const b = p.xy[lo + 1];
  const tb = t[lo + 1];
  const f = b && tb !== undefined && tb > ta ? Math.min(1, (tMs - ta) / (tb - ta)) : 0;
  return p.ids.map((id, i) => {
    const ax = a[2 * i] ?? 0;
    const ay = a[2 * i + 1] ?? 0;
    const bx = b?.[2 * i] ?? ax;
    const by = b?.[2 * i + 1] ?? ay;
    return {
      id,
      name: p.names[i] ?? id,
      home: p.home[i] ?? false,
      x: ax + (bx - ax) * f,
      y: ay + (by - ay) * f,
    };
  });
}

export interface BallSample {
  t_ms: number;
  x: number;
  y: number;
  in_play: boolean;
}

/** The last ball sample at or before tMs, or null before the first one. */
export function ballAt(m: Manifest, tMs: number): BallSample | null {
  const b = m.ball;
  const first = b?.[0];
  if (!b || !first || tMs < first.t_ms) return null;
  let lo = 0;
  let hi = b.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    const s = b[mid];
    if (s && s.t_ms <= tMs) lo = mid;
    else hi = mid - 1;
  }
  return b[lo] ?? null;
}

/** Each agent's latest decision at tMs. */
export function latestByAgent(m: Manifest, tMs: number): Map<AgentName, AgentEntry> {
  const out = new Map<AgentName, AgentEntry>();
  for (const e of m.agents ?? []) {
    if (e.t_ms > tMs) break;
    out.set(e.agent, e);
  }
  return out;
}

export interface Score {
  homeScore: number;
  awayScore: number;
  minute: string;
  status: TimelinePoint["status"];
}

export const TRANSCRIPT_CAP = 50;

export function scoreAt(m: Manifest, tMs: number): Score {
  let s: Score = {
    homeScore: m.start.home_score,
    awayScore: m.start.away_score,
    minute: m.start.minute,
    status: "live",
  };
  for (const p of m.timeline) {
    if (p.t_ms > tMs) break;
    s = { homeScore: p.home_score, awayScore: p.away_score, minute: p.minute, status: p.status };
  }
  return s;
}

/** The line being spoken at tMs, if any. */
export function onAirAt(m: Manifest, tMs: number): ClipLine | null {
  for (const line of m.lines) {
    if (line.t_ms <= tMs && tMs < line.end_ms) return line;
    if (line.t_ms > tMs) break;
  }
  return null;
}

/** Lines already started by tMs, newest first, capped. */
export function spokenBy(m: Manifest, tMs: number): ClipLine[] {
  const out: ClipLine[] = [];
  for (const line of m.lines) {
    if (line.t_ms > tMs) break;
    out.push(line);
  }
  return out.reverse().slice(0, TRANSCRIPT_CAP);
}

export async function loadManifest(base: string): Promise<Manifest> {
  const r = await fetch(`${base}/manifest.json`);
  if (!r.ok) throw new Error(`manifest ${r.status}`);
  return (await r.json()) as Manifest;
}
