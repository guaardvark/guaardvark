import { describe, it, expect } from "vitest";
import {
  ASPECT_RATIO_PRESETS,
  aspectRatiosFor,
  durationPresetsFor,
  fitAreaToRatio,
  resolveAspectRatio,
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

describe("WAN5B_SAMPLER_PROFILES", () => {
  it("offers exactly the profiles the 5B entry advertises", async () => {
    const { WAN5B_SAMPLER_PROFILES } = await import("../videoGeneratorPresets");
    expect(MODEL_OPTIONS["wan22-5b"].samplerProfiles).toEqual(Object.keys(WAN5B_SAMPLER_PROFILES));
    expect(MODEL_OPTIONS["wan22-14b"].samplerProfiles).toBeUndefined();
  });
});


describe("aspectRatiosFor", () => {
  it("offers Wan only its native ratio and the portrait transpose", () => {
    for (const id of ["wan22-5b", "wan22-14b", "wan22-14b-i2v"]) {
      expect(Object.keys(aspectRatiosFor(id))).toEqual(["16:9", "9:16"]);
    }
  });

  it("keeps square out of reach on Wan 5B", () => {
    // 1:1 clamped to 1024x1024, which is off-native and lifts the dynamic shift
    // above the 8.0 that 1280x704 is tuned for — smearing and colour bleed.
    expect(aspectRatiosFor("wan22-5b")["1:1"]).toBeUndefined();
  });

  it("leaves a model that declares nothing unconstrained", () => {
    expect(aspectRatiosFor("cogvideox-5b")).toBe(ASPECT_RATIO_PRESETS);
    expect(aspectRatiosFor("nonexistent-model")).toBe(ASPECT_RATIO_PRESETS);
  });

  it("never offers a ratio that is not a real preset", () => {
    for (const id of Object.keys(MODEL_OPTIONS)) {
      for (const key of Object.keys(aspectRatiosFor(id))) {
        expect(ASPECT_RATIO_PRESETS[key]).toBeDefined();
      }
    }
  });
});

describe("resolveAspectRatio", () => {
  it("keeps a ratio the model supports", () => {
    expect(resolveAspectRatio("wan22-5b", "9:16")).toBe("9:16");
  });

  it("snaps a ratio the model cannot render to its first supported one", () => {
    for (const bad of ["1:1", "4:3", "3:2"]) {
      expect(resolveAspectRatio("wan22-5b", bad)).toBe("16:9");
    }
  });

  it("leaves any ratio alone on an unconstrained model", () => {
    expect(resolveAspectRatio("cogvideox-5b", "1:1")).toBe("1:1");
  });

  it("never resolves to a square frame on Wan 5B, whatever it is handed", () => {
    for (const key of Object.keys(ASPECT_RATIO_PRESETS).concat(["junk", undefined])) {
      const resolved = resolveAspectRatio("wan22-5b", key);
      const { width, height } = fitAreaToRatio(
        1280 * 704, ASPECT_RATIO_PRESETS[resolved].ratio, "wan22-5b", undefined,
      );
      expect(width).not.toBe(height);
    }
  });
});

