import { describe, it, expect } from "vitest";
import {
  ASPECT_RATIO_PRESETS,
  aspectRatiosFor,
  durationPresetsFor,
  resolveAspectRatio,
  isMinimaxModel,
  GENERATION_TYPES,
  MINIMAX_DURATION_PRESETS,
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

describe("GENERATION_TYPES", () => {
  it("covers every family in MODEL_OPTIONS, MiniMax included", () => {
    for (const cfg of Object.values(MODEL_OPTIONS)) {
      expect(GENERATION_TYPES.has(cfg.type)).toBe(true);
    }
    expect(GENERATION_TYPES.has("minimax")).toBe(true);
    // Companions are not generation targets.
    expect(GENERATION_TYPES.has("vae")).toBe(false);
  });
});

describe("MiniMax H3 presets", () => {
  it("keeps every duration on the model's 17k+5 frame grid and under maxFrames", () => {
    for (const p of Object.values(MINIMAX_DURATION_PRESETS)) {
      expect((p.duration_frames - 5) % 17).toBe(0);
      expect(p.fps).toBe(24);
      expect(p.duration_frames).toBeLessThanOrEqual(MODEL_OPTIONS["minimax-h3-int8"].maxFrames);
    }
    expect(durationPresetsFor("minimax-h3-int8")).toBe(MINIMAX_DURATION_PRESETS);
  });

  it("declares the template's 20-step floor", () => {
    expect(MODEL_OPTIONS["minimax-h3-int8"].minSteps).toBe(20);
    expect(isMinimaxModel("minimax-h3-int8")).toBe(true);
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

describe("step floors", () => {
  it("declares the fewest steps Wan can render usefully", async () => {
    const { MODEL_OPTIONS, QUALITY_PRESETS } = await import("../videoGeneratorPresets");
    for (const id of ["wan22-5b", "wan22-14b", "wan22-14b-i2v"]) {
      expect(MODEL_OPTIONS[id].minSteps).toBe(20);
      // Fast's raw number may sit below a family's floor; the page raises it.
      const floored = Math.max(
        QUALITY_PRESETS.fast.num_inference_steps,
        MODEL_OPTIONS[id].minSteps,
      );
      expect(floored).toBe(MODEL_OPTIONS[id].minSteps);
    }
  });

  it("does not floor models that are trained for few steps", async () => {
    const { MODEL_OPTIONS } = await import("../videoGeneratorPresets");
    // LTX distilled runs at 8 by design; a floor here would waste time and can
    // degrade distilled output.
    for (const id of ["ltx23-distilled-fp8", "ltx25-distilled-int8"]) {
      expect(MODEL_OPTIONS[id].minSteps).toBe(8);
    }
  });
});

describe("MiniMax H3 capability mirrors", () => {
  it("offers the six ratios the registry declares, ultra-wide and portrait included", () => {
    expect(Object.keys(aspectRatiosFor("minimax-h3-int8"))).toEqual(["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"]);
    expect(ASPECT_RATIO_PRESETS["21:9"].ratio).toBeCloseTo(21 / 9);
    expect(ASPECT_RATIO_PRESETS["3:4"].ratio).toBeCloseTo(0.75);
    expect(resolveAspectRatio("minimax-h3-int8", "3:2")).toBe("21:9");
  });

  it("lists every H3 precision rung with the same floor and profiles", () => {
    for (const id of ["minimax-h3-int8", "minimax-h3-int8-full", "minimax-h3-bf16"]) {
      expect(MODEL_OPTIONS[id].type).toBe("minimax");
      expect(MODEL_OPTIONS[id].minSteps).toBe(20);
      expect(MODEL_OPTIONS[id].speedProfiles).toEqual(["standard", "turbo-8", "turbo-4-768p"]);
      expect(MODEL_OPTIONS[id].supportsI2V).toBe(true);
    }
    expect(MODEL_OPTIONS["minimax-h3-bf16"].resolution).toEqual([1344, 768]);
  });

  it("adds longer duration presets only when the registry declares a tier", async () => {
    const { withDurationTiers } = await import("../videoGeneratorPresets");
    expect(durationPresetsFor("minimax-h3-int8")).toBe(MINIMAX_DURATION_PRESETS);
    const meta = { capabilities: { native_fps: 24, duration_tiers: [{ frames: 175 }, { frames: 243 }, { frames: 362 }] } };
    const presets = durationPresetsFor("minimax-h3-int8", meta);
    expect(Object.keys(presets)).toEqual(["short", "medium", "long", "tier_243", "tier_362"]);
    expect(presets.tier_243).toEqual({ label: "10 s", description: "~10 seconds", duration_frames: 243, fps: 24 });
    expect(presets.tier_362.duration_frames).toBe(362);
    // A tier the presets already cover adds nothing.
    expect(withDurationTiers(MINIMAX_DURATION_PRESETS, { capabilities: { duration_tiers: [{ frames: 175 }] } }))
      .toBe(MINIMAX_DURATION_PRESETS);
  });
});
