import { describe, it, expect } from "vitest";
import {
  durationPresetsFor,
  MODEL_OPTIONS,
  MOTION_PRESETS,
  WAN_5B_DURATION_PRESETS,
  WAN_DURATION_PRESETS,
} from "../videoGeneratorPresets";

describe("durationPresetsFor", () => {
  it("gives Wan 2.2 5B its native 24fps, 4n+1 frames, within maxFrames", () => {
    const presets = durationPresetsFor("wan22-5b");
    expect(presets).toBe(WAN_5B_DURATION_PRESETS);
    for (const p of Object.values(presets)) {
      expect(p.fps).toBe(24);
      expect((p.duration_frames - 1) % 4).toBe(0);
      expect(p.duration_frames).toBeLessThanOrEqual(MODEL_OPTIONS["wan22-5b"].maxFrames);
    }
  });

  it("keeps the 14B models on 16fps", () => {
    for (const id of ["wan22-14b", "wan22-14b-i2v"]) {
      expect(durationPresetsFor(id)).toBe(WAN_DURATION_PRESETS);
      for (const p of Object.values(durationPresetsFor(id))) expect(p.fps).toBe(16);
    }
  });

  it("matches each family's native rate and frame rule", () => {
    for (const p of Object.values(durationPresetsFor("hunyuan-t2v"))) {
      expect(p.fps).toBe(24);
      expect((p.duration_frames - 1) % 4).toBe(0);
    }
    for (const p of Object.values(durationPresetsFor("ltx25-distilled-int8"))) {
      expect((p.duration_frames - 1) % 8).toBe(0);
    }
    for (const p of Object.values(durationPresetsFor("cogvideox-5b"))) expect(p.fps).toBe(8);
  });
});

describe("MOTION_PRESETS", () => {
  it("covers the four bands the backend enhancer maps to phrases", () => {
    const strengths = Object.values(MOTION_PRESETS).map((p) => p.motion_strength);
    expect(strengths).toEqual([0.5, 1.0, 1.5, 2.0]);
  });
});
