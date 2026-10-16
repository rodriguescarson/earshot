/** The live connection: Server-Sent Events from the worker (Web PubSub drops in behind the same interface). */

export const LIVE_BASE: string = (import.meta.env.VITE_LIVE_BASE as string | undefined) ?? "";

export interface LiveSummary {
  match_id: string;
  home: { team_id: string; name: string };
  away: { team_id: string; name: string };
  home_score: number;
  away_score: number;
  minute: string;
  status: string;
}

export interface LineMessage {
  kind: "line";
  id: string;
  priority: number;
  preempt: boolean;
  never_drop: boolean;
  audio_url: string | null;
  text: string;
  voice_role: "facts" | "explanation";
  late: boolean;
}
export interface BallMessage {
  kind: "ball";
  x: number;
  y: number;
  danger: number;
  in_play?: boolean;
}
export interface StateMessage {
  kind: "state";
  home_score: number;
  away_score: number;
  minute?: string;
  status: string;
}
export type LiveMessage = LineMessage | BallMessage | StateMessage;

export function liveAvailable(): boolean {
  return LIVE_BASE !== "";
}

export async function liveSummary(): Promise<LiveSummary> {
  const r = await fetch(`${LIVE_BASE}/api/live`);
  if (!r.ok) throw new Error(`live ${r.status}`);
  return (await r.json()) as LiveSummary;
}

export function audioUrl(path: string | null): string | null {
  return path ? `${LIVE_BASE}${path}` : null;
}

export function connect(group: string, onMessage: (m: LiveMessage) => void, onDown: () => void): () => void {
  const es = new EventSource(`${LIVE_BASE}/api/stream?group=${encodeURIComponent(group)}`);
  es.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data as string) as LiveMessage);
    } catch {
      /* ignore a malformed message rather than stop the match */
    }
  };
  es.onerror = () => onDown();
  return () => es.close();
}

export async function onDemand(
  group: string,
  kind: "where" | "catchup",
): Promise<{ id: string; text: string; audio_url: string | null }> {
  const r = await fetch(`${LIVE_BASE}/api/ondemand`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ group, kind }),
  });
  if (!r.ok) throw new Error(`ondemand ${r.status}`);
  return (await r.json()) as { id: string; text: string; audio_url: string | null };
}
