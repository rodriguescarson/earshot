import type { Lang } from "../i18n";
import type { Arm } from "./manifest";

export type Output = "app" | "reader" | "oneEar";
export type Mode = "casual" | "analyst";
export const STATS = ["press_height", "danger", "pass_difficulty", "shot_speed", "possession"] as const;
export type Stat = (typeof STATS)[number];

export interface Choices {
  lang: Lang;
  output: Output;
  mode: Mode;
  /** "Follow one stat" (Analyst mode only). */
  stat?: Stat | null;
}

/** The followed stat, only where it applies (Analyst mode). */
export function followed(c: Choices): Stat | null {
  return c.mode === "analyst" && c.stat ? c.stat : null;
}

export const ARM_FOR: Record<Output, Arm> = { app: "full", reader: "bed", oneEar: "mono" };

const BASE = import.meta.env.VITE_CLIP_BASE ?? "/clips";

/**
 * The recorded clip for these choices. A followed stat has its own clip only when the stat crosses a
 * threshold inside the clip; otherwise the plain Analyst clip is exactly what that stat would render.
 */
export function clipBase(c: Choices): string {
  const stat = followed(c);
  return `${BASE}/demo-${c.lang}-${c.mode}${stat ? `-${stat}` : ""}`;
}

const KEY = "earshot.choices";

export function loadChoices(fallback: Choices): Choices {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...fallback, ...(JSON.parse(raw) as Partial<Choices>) } : fallback;
  } catch {
    return fallback;
  }
}

export function saveChoices(c: Choices): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(c));
  } catch {
    /* private mode: choices last for this visit only */
  }
}
