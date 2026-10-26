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
  /** Streamed path: mono MP3 the browser pans itself. Absent or "m4a": already panned in the file. */
  codec?: "m4a" | "mp3";
  pan?: number;
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
export interface RecapMessage {
  kind: "recap";
  period: "half_time" | "full_time";
  text: string;
  lines: string[];
}

export type LiveMessage = LineMessage | BallMessage | StateMessage | RecapMessage;

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

const CODE_KEY = "earshot.judgeCode";

/**
 * A judge's access code arrives in the URL fragment (#code=..., DR-6) and is never put in a URL
 * again: it is kept for this tab only and sent as a header when trading it for a stream ticket.
 */
export function judgeCode(): string | null {
  const m = /(?:^#|&)code=([^&]+)/.exec(window.location.hash);
  if (m?.[1]) {
    const code = decodeURIComponent(m[1]);
    try {
      sessionStorage.setItem(CODE_KEY, code);
    } catch {
      /* private mode: the code lasts for this page only */
    }
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return code;
  }
  try {
    return sessionStorage.getItem(CODE_KEY);
  } catch {
    return null;
  }
}

async function streamTicket(code: string): Promise<string | null> {
  try {
    const r = await fetch(`${LIVE_BASE}/api/ticket`, { method: "POST", headers: { "x-earshot-code": code } });
    if (!r.ok) return null;
    return ((await r.json()) as { ticket?: string }).ticket ?? null;
  } catch {
    return null;
  }
}

export type LinkState = "open" | "reconnecting" | "refused";

/**
 * Listen to one group over Server-Sent Events. A dropped connection retries by itself
 * ("reconnecting"); a stream the server turns away (limits, or a judge ticket that a restart made
 * stale) ends closed: judges get one fresh ticket, everyone else is told Earshot is busy.
 */
export function connect(
  group: string,
  onMessage: (m: LiveMessage) => void,
  onState: (s: LinkState) => void,
): () => void {
  let es: EventSource | null = null;
  let stopped = false;
  let retried = false;
  const code = judgeCode();

  const open = async () => {
    const ticket = code ? await streamTicket(code) : null;
    if (stopped) return;
    const q = `group=${encodeURIComponent(group)}${ticket ? `&ticket=${encodeURIComponent(ticket)}` : ""}`;
    es = new EventSource(`${LIVE_BASE}/api/stream?${q}`);
    es.onopen = () => onState("open");
    es.onmessage = (e) => {
      try {
        onMessage(JSON.parse(e.data as string) as LiveMessage);
      } catch {
        /* ignore a malformed message rather than stop the match */
      }
    };
    es.onerror = () => {
      if (es?.readyState !== EventSource.CLOSED) {
        onState("reconnecting");
        return;
      }
      if (code && !retried) {
        retried = true;
        void open();
        return;
      }
      onState("refused");
    };
  };
  void open();
  return () => {
    stopped = true;
    es?.close();
  };
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
