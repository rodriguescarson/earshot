import { describe, expect, it } from "vitest";
import { ballAt, type Manifest, onAirAt, scoreAt, spokenBy, TRANSCRIPT_CAP } from "./manifest";

const line = (t: number, end: number, text: string) => ({
  t_ms: t,
  end_ms: end,
  text,
  voice_role: "facts" as const,
  priority: 50,
  never_drop: false,
  cut: false,
  template_id: "x.1",
  pan: 0,
});

const m: Manifest = {
  clip_id: "c",
  description: "",
  language: "en",
  listening_mode: "casual",
  duration_ms: 10_000,
  audio: { full: "full.m4a" },
  home: { team_id: "a", name: "A" },
  away: { team_id: "b", name: "B" },
  start: { home_score: 2, away_score: 2, minute: "84" },
  lines: [line(1000, 2000, "one"), line(3000, 5000, "two"), line(6000, 7000, "goal")],
  timeline: [
    { t_ms: 500, home_score: 2, away_score: 2, minute: "84", status: "live" },
    { t_ms: 5900, home_score: 2, away_score: 3, minute: "85", status: "dead" },
  ],
};

describe("clip manifest", () => {
  it("uses the start score before the first timeline point", () => {
    expect(scoreAt(m, 0)).toMatchObject({ homeScore: 2, awayScore: 2, minute: "84" });
  });
  it("updates the score at the goal", () => {
    expect(scoreAt(m, 6000)).toMatchObject({ awayScore: 3, minute: "85", status: "dead" });
  });
  it("finds the line on air and nothing between lines", () => {
    expect(onAirAt(m, 3500)?.text).toBe("two");
    expect(onAirAt(m, 2500)).toBeNull();
  });
  it("lists spoken lines newest first", () => {
    expect(spokenBy(m, 6500).map((l) => l.text)).toEqual(["goal", "two", "one"]);
  });
  it("caps the transcript", () => {
    const many = { ...m, lines: Array.from({ length: 80 }, (_, i) => line(i, i + 1, `l${i}`)) };
    expect(spokenBy(many, 1000)).toHaveLength(TRANSCRIPT_CAP);
  });
});

describe("ballAt", () => {
  const withBall: Manifest = {
    ...m,
    ball: [
      { t_ms: 0, x: 52.5, y: 34, in_play: true },
      { t_ms: 200, x: 60, y: 30, in_play: true },
      { t_ms: 400, x: 70, y: 20, in_play: false },
    ],
  };
  it("returns null before the first sample and the latest sample at or before t", () => {
    expect(ballAt(withBall, -1)).toBeNull();
    expect(ballAt(withBall, 0)?.x).toBe(52.5);
    expect(ballAt(withBall, 199)?.x).toBe(52.5);
    expect(ballAt(withBall, 200)?.x).toBe(60);
    expect(ballAt(withBall, 9999)?.in_play).toBe(false);
    expect(ballAt(m, 100)).toBeNull();
  });
});
