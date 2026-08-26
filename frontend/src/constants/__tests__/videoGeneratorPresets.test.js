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
  it("offers Wan its native ratio, the portrait transpose, and square", () => {
    for (const id of ["wan22-5b", "wan22-14b", "wan22-14b-i2v"]) {
      expect(Object.keys(aspectRatiosFor(id))).toEqual(["16:9", "9:16", "1:1"]);
    }
  });

  it("offers square on Wan, which renders it", () => {
    // Square was withdrawn on the theory that off-native frames warp. The output
    // directory disproves it: seven 1:1 Wan I2V renders at 512x512 and 736x736.
    // The warping came from the sampler shift being scaled by pixel area, which
    // starved every non-native size; that is fixed at its source.
    expect(aspectRatiosFor("wan22-5b")["1:1"]).toBeDefined();
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
    for (const bad of ["4:3", "3:2"]) {
      expect(resolveAspectRatio("wan22-5b", bad)).toBe("16:9");
    }
  });

  it("keeps square on Wan rather than snapping it away", () => {
    expect(resolveAspectRatio("wan22-5b", "1:1")).toBe("1:1");
  });

  it("leaves any ratio alone on an unconstrained model", () => {
    expect(resolveAspectRatio("cogvideox-5b", "1:1")).toBe("1:1");
  });

  it("always resolves to a ratio the model actually declares", () => {
    const allowed = Object.keys(aspectRatiosFor("wan22-5b"));
    for (const key of Object.keys(ASPECT_RATIO_PRESETS).concat(["junk", undefined])) {
      expect(allowed).toContain(resolveAspectRatio("wan22-5b", key));
    }
  });
});


describe("guidance defaults", () => {
  it("uses the Wan family default the backend workflows use", async () => {
    const { MODEL_DEFAULT_GUIDANCE } = await import("../videoGeneratorPresets");
    // Backend _create_wan22_t2v_workflow / _create_wan22_i2v_workflow both
    // default to 3.5. The UI sending 5.0 meant the 14B never ran at the value
    // its own workflow chose.
    expect(MODEL_DEFAULT_GUIDANCE.wan).toBe(3.5);
  });

  it("lets a model override its family when its backend default differs", async () => {
    const { MODEL_OPTIONS } = await import("../videoGeneratorPresets");
    // _create_wan22_5b_workflow defaults to 5.0.
    expect(MODEL_OPTIONS["wan22-5b"].defaultGuidance).toBe(5.0);
    // The 14B has no override, so it takes the family value.
    expect(MODEL_OPTIONS["wan22-14b-i2v"].defaultGuidance).toBeUndefined();
  });
});
