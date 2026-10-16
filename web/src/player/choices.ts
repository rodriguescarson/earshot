import type { Lang } from "../i18n";
import type { Arm } from "./manifest";

export type Output = "app" | "reader" | "oneEar";
export type Mode = "casual" | "analyst";

export interface Choices {
  lang: Lang;
  output: Output;
  mode: Mode;
}

export const ARM_FOR: Record<Output, Arm> = { app: "full", reader: "bed", oneEar: "mono" };

const BASE = import.meta.env.VITE_CLIP_BASE ?? "/clips";

export function clipBase(c: Choices): string {
  return `${BASE}/demo-${c.lang}-${c.mode}`;
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
