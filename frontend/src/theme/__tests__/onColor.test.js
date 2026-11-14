import { describe, it, expect } from "vitest";
import { flattenOver, foregroundFor } from "../onColor";

describe("flattenOver", () => {
  it("reads a translucent tint as it looks over the page, so ink is chosen for what is seen", () => {
    // 12% cyan over a near-black page is near black: white ink, though opaque cyan takes black.
    const seen = flattenOver("rgba(0, 229, 255, 0.12)", "#121212");
    expect(seen).toBe("rgb(16, 43, 46)");
    expect(foregroundFor(seen).mode).toBe("dark");
    expect(foregroundFor("rgb(0, 229, 255)").mode).toBe("light");
  });

  it("leaves an opaque colour, and one it cannot parse, as given", () => {
    expect(flattenOver("#f6d32d", "#000")).toBe("#f6d32d");
    expect(flattenOver("not-a-colour", "#000")).toBe("not-a-colour");
  });
});
