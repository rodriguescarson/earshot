import { describe, expect, it } from "vitest";
import { clipBase, followed } from "./choices";

describe("followed stat", () => {
  it("applies only in Analyst mode", () => {
    expect(followed({ lang: "en", output: "app", mode: "analyst", stat: "shot_speed" })).toBe("shot_speed");
    expect(followed({ lang: "en", output: "app", mode: "casual", stat: "shot_speed" })).toBeNull();
    expect(followed({ lang: "en", output: "app", mode: "analyst" })).toBeNull();
  });

  it("picks the stat's own clip, and the plain clip otherwise", () => {
    expect(clipBase({ lang: "ar", output: "reader", mode: "analyst", stat: "press_height" })).toMatch(
      /\/demo-ar-analyst-press_height$/,
    );
    expect(clipBase({ lang: "ar", output: "reader", mode: "casual", stat: "press_height" })).toMatch(
      /\/demo-ar-casual$/,
    );
  });
});
