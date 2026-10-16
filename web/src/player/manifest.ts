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
  pan: number;
}

export type AgentName = "Spotter" | "Caller" | "Checker" | "Analyst" | "Director" | "Voice";

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
