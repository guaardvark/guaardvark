export const QUALITY_PRESETS = {
  fast: {
    label: "⚡ Fast",
    description: "Quick preview (10 steps)",
    num_inference_steps: 10,
    width: 720,
    height: 480,
  },
  standard: {
    label: "✨ Standard",
    description: "Good quality (30 steps)",
    num_inference_steps: 30,
    width: 720,
    height: 480,
  },
  high: {
    label: "🎬 High Quality",
    description: "Best details (40 steps)",
    num_inference_steps: 40,
    width: 720,
    height: 480,
  },
  maximum: {
    label: "🏆 Maximum",
    description: "Maximum quality (50 steps)",
    num_inference_steps: 50,
    width: 720,
    height: 480,
  },
};

export const COGVIDEOX_DURATION_PRESETS = {
  short: { label: "Short", description: "~3 seconds", duration_frames: 24, fps: 8 },
  medium: { label: "Medium", description: "~4 seconds", duration_frames: 33, fps: 8 },
  long: { label: "Long", description: "~6 seconds", duration_frames: 49, fps: 8 },
};

/** Wan 2.2 14B (T2V / I2V) is 16fps-native. */
export const WAN_DURATION_PRESETS = {
  short: { label: "Short", description: "~2 seconds", duration_frames: 33, fps: 16 },
  medium: { label: "Medium", description: "~3 seconds", duration_frames: 49, fps: 16 },
  long: { label: "Long", description: "~5 seconds", duration_frames: 81, fps: 16 },
};

/** Wan 2.2 TI2V-5B is 24fps-native (official template: 121 frames @ 24fps); frames 4n+1. */
export const WAN_5B_DURATION_PRESETS = {
  short: { label: "Short", description: "~2 seconds", duration_frames: 49, fps: 24 },
  medium: { label: "Medium", description: "~3 seconds", duration_frames: 73, fps: 24 },
  long: { label: "Long", description: "~5 seconds", duration_frames: 121, fps: 24 },
};

/** LTX-2.3 / 2.5 frame counts must be 8n+1. 161 @ 16fps ≈ 10s (16GB Ada target). */
export const LTX_DURATION_PRESETS = {
  short: { label: "Short", description: "~4 seconds", duration_frames: 65, fps: 16 },
  medium: { label: "Medium", description: "~6 seconds", duration_frames: 97, fps: 16 },
  long: { label: "Long", description: "~10 seconds", duration_frames: 161, fps: 16 },
};

/** HunyuanVideo frame counts must be 4n+1; native 24fps. 73 @ 24fps ≈ 3s (official template). */
// MiniMax H3 samples on a 17k+5 frame grid at 24 fps; 124 (~5 s) is the
// official template's default and the low end of the trained range. Longer
// clips are unmeasured on 16GB, so "long" stops at ~7 s until one is timed.
export const MINIMAX_DURATION_PRESETS = {
  short: { label: "Short", description: "~3 seconds", duration_frames: 73, fps: 24 },
  medium: { label: "Medium", description: "~5 seconds", duration_frames: 124, fps: 24 },
  long: { label: "Long", description: "~7 seconds", duration_frames: 175, fps: 24 },
};

export const HUNYUAN_DURATION_PRESETS = {
  short: { label: "Short", description: "~2 seconds", duration_frames: 49, fps: 24 },
  medium: { label: "Medium", description: "~3 seconds", duration_frames: 73, fps: 24 },
  long: { label: "Long", description: "~4 seconds", duration_frames: 97, fps: 24 },
};

export const MOTION_PRESETS = {
  subtle: { label: "🌊 Subtle", description: "Gentle movement", motion_strength: 0.5 },
  normal: { label: "🎯 Normal", description: "Balanced motion", motion_strength: 1.0 },
  dynamic: { label: "💨 Dynamic", description: "More movement", motion_strength: 1.5 },
  intense: { label: "🔥 Intense", description: "Maximum motion", motion_strength: 2.0 },
};

export const OUTPUT_QUALITY_TIERS = {
  draft: { label: "Draft", description: "Raw model output — fastest, lowest polish", interpolation: 1, upscale: false },
  standard: { label: "Standard", description: "2x FPS interpolation for smoother motion", interpolation: 2, upscale: false },
  cinema: { label: "Cinema", description: "2x FPS + 2x upscale — recommended for final output", interpolation: 2, upscale: true },
};

export const KEYFRAME_MODEL_OPTIONS = {
  "from-lora": {
    label: "Auto (character LoRA base)",
    description: "Z-Image / SDXL / FLUX from the cast member’s training base",
  },
  "flux-schnell": { label: "FLUX.1-schnell", description: "Fast, beautiful stills (default without cast)" },
  "flux-dev-lora": { label: "FLUX-dev + LoRA", description: "Comfy FLUX stills (non-cast or explicit)" },
  sdxl: { label: "SDXL", description: "High-fidelity stills without LoRA" },
  "sdxl-lora": { label: "SDXL + LoRA", description: "Legacy SDXL character path" },
};

export const DEFAULT_KEYFRAME_MODEL = "flux-schnell";

/** Wan 2.2 5B sampling profiles (backend WAN5B_SAMPLER_PROFILES). */
export const WAN5B_SAMPLER_PROFILES = {
  official: { label: "Official — uni_pc, shift 8 (default)", description: "ComfyUI's bundled Wan 2.2 5B template settings" },
  adaptive: { label: "Guaardvark — euler, adaptive shift", description: "Experimental: shift scales with resolution, floors at 3 below ~740px" },
};

// Family defaults, mirroring the backend workflow signatures. A model whose
// backend default differs from its family carries `defaultGuidance` in
// MODEL_OPTIONS below; keep the two in step or the UI will send a value the
// backend never chose for itself.
// MiniMax H3 runs without CFG (BasicGuider); the value is display-only.
export const MODEL_DEFAULT_GUIDANCE = { wan: 3.5, cogvideox: 6.0, ltx: 1.0, hunyuan: 6.0, minimax: 1.0 };

export const ASPECT_RATIO_PRESETS = {
  "16:9": { label: "16:9", description: "Widescreen", ratio: 16 / 9 },
  "9:16": { label: "9:16", description: "Portrait/Vertical", ratio: 9 / 16 },
  "1:1": { label: "1:1", description: "Square", ratio: 1 },
  "4:3": { label: "4:3", description: "Standard", ratio: 4 / 3 },
  "3:2": { label: "3:2", description: "Classic", ratio: 3 / 2 },
  "21:9": { label: "21:9", description: "Ultra-wide", ratio: 21 / 9 },
  "3:4": { label: "3:4", description: "Portrait", ratio: 3 / 4 },
};

export const PROMPT_STYLES = {
  cinematic: { label: "Cinematic", description: "Film-quality lighting and motion" },
  realistic: { label: "Realistic", description: "Photorealistic detail" },
  artistic: { label: "Artistic", description: "Stylized and expressive" },
  anime: { label: "Anime (Japanese)", description: "Japanese cel-shaded animation" },
  "3d_animation": { label: "3D Animation (Pixar-style)", description: "Polished CGI, expressive characters" },
  stop_motion: { label: "Stop-motion / Claymation", description: "Tactile clay, handcrafted feel" },
  hand_drawn: { label: "Hand-drawn 2D (Ghibli-style)", description: "Painterly watercolor backgrounds" },
  western_cartoon: { label: "Western Cartoon", description: "Bold outlines, flat shading, snappy motion" },
  none: { label: "None", description: "No enhancement" },
};

export const VIDEO_SIZE_PRESETS = {
  small: { label: "Small", description: "512px (faster)", baseSize: 512 },
  medium: { label: "Medium", description: "576px", baseSize: 576 },
  large: { label: "Large", description: "720px (CogVideoX native)", baseSize: 720 },
  hd: { label: "HD", description: "1280px (CPU offload, slower)", baseSize: 1280 },
  fullhd: { label: "Full HD", description: "1920px (CPU offload, much slower)", baseSize: 1920 },
};

export const MODEL_OPTIONS = {
  "wan22-5b": {
    label: "Wan 2.2 5B TI2V (Recommended)",
    description: "Fast 5s clips, fits 16GB — no offload. Text + image to video.",
    type: "wan",
    nativeFps: 24,
    samplerProfiles: ["official", "adaptive"],
    maxFrames: 121,
    resolution: [1280, 704],
    // The 5B's backend workflow defaults to 5.0, unlike the 14B's 3.5.
    defaultGuidance: 5.0,
    // Native 1280x704, plus the portrait transpose and square. Square renders
    // fine; what smeared was the sampler shift scaled by pixel area, and that is
    // fixed at its source.
    aspectRatios: ["16:9", "9:16", "1:1"],
    defaultSteps: 20,
    // Below this Wan is under-resolved: structure never settles at high noise,
    // which reads as smearing and colour bleed rather than as a faster render.
    // A quality preset must not be able to reach below it.
    minSteps: 20,
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
    // 1280×736 (0.94 MPx) is proven on 16GB; 1920×1920 (3.7 MPx) never finishes
    // and reads as "the aspect selector is broken". Aspect is preserved.
    maxPixelArea: 1_000_000,
  },
  "wan22-14b": {
    label: "Wan 2.2 14B (GGUF Q5)",
    description: "Best quality, 5s videos (~11GB VRAM)",
    type: "wan",
    nativeFps: 16,
    maxFrames: 81,
    resolution: [832, 480],
    aspectRatios: ["16:9", "9:16", "1:1"],
    defaultSteps: 25,
    // Below this Wan is under-resolved: structure never settles at high noise,
    // which reads as smearing and colour bleed rather than as a faster render.
    // A quality preset must not be able to reach below it.
    minSteps: 20,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 32,
    maxPixelArea: 1_000_000,
  },
  "wan22-14b-i2v": {
    label: "Wan 2.2 14B I2V (GGUF Q5)",
    description: "Top-tier image-to-video, 5s clips (~11GB VRAM)",
    type: "wan",
    nativeFps: 16,
    maxFrames: 81,
    resolution: [832, 480],
    aspectRatios: ["16:9", "9:16", "1:1"],
    defaultSteps: 25,
    // Below this Wan is under-resolved: structure never settles at high noise,
    // which reads as smearing and colour bleed rather than as a faster render.
    // A quality preset must not be able to reach below it.
    minSteps: 20,
    supportsT2V: false,
    supportsI2V: true,
    dimensionAlignment: 32,
    maxPixelArea: 1_000_000,
  },
  "cogvideox-5b": {
    label: "CogVideoX 5B",
    description: "6s videos, in-process diffusers — no ComfyUI needed (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 16,
  },
  "cogvideox-5b-i2v": {
    label: "CogVideoX 5B I2V",
    description: "Image-to-video, 6s (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    supportsT2V: false,
    supportsI2V: true,
    dimensionAlignment: 16,
  },
  "ltx23-distilled-fp8": {
    label: "LTX-2.3 Distilled FP8 (16GB)",
    description: "Lightricks LTX-2.3 — 8 steps, up to ~10s on 16GB Ada. T2V + I2V.",
    type: "ltx",
    maxFrames: 161,
    resolution: [768, 512],
    defaultSteps: 8,
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
  },
  "ltx25-distilled-int8": {
    label: "LTX-2.5 Distilled Int8 (16GB)",
    description: "Lightricks LTX-2.5 — 8 steps, up to ~10s on 16GB Ada. T2V + I2V. "
      + "Gated HF accept + ComfyUI ≥ 0.32.",
    type: "ltx",
    maxFrames: 161,
    resolution: [768, 512],
    defaultSteps: 8,
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
  },
  // MiniMax H3: three precision rungs of one contract. The registry entry is
  // the source of truth for limits (backend/services/video_model_registry.py);
  // the page reads speed profiles, style embeddings, duration tiers and the
  // per-VRAM-class starting settings from /api/batch-video/models and keeps
  // these mirrors for offline rendering of the menu and for the tests.
  "minimax-h3-int8": {
    label: "MiniMax H3 Int8 (16GB)",
    description: "MiniMax H3 — video with native stereo audio in one pass. 24fps, ~5s. T2V, first-frame and first+last-frame I2V. ComfyUI ≥ 0.30.",
    type: "minimax",
    nativeFps: 24,
    maxFrames: 175,
    // 0.4 MPx is the official template's default (864×480 at 16:9); the native
    // 768×1344 canvas is the cap, not the starting point, on a 16GB card.
    resolution: [864, 480],
    aspectRatios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
    // The official ComfyUI template samples 20 steps (res_multistep / simple).
    // Below that the base model is under-resolved like Wan. A speed profile
    // (turbo LoRA) carries its own step count and floor, declared in the
    // registry and read from the API; the ids are mirrored here.
    defaultSteps: 20,
    minSteps: 20,
    speedProfiles: ["standard", "turbo-8", "turbo-4-768p"],
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
    maxPixelArea: 768 * 1344,
  },
  "minimax-h3-int8-full": {
    label: "MiniMax H3 Int8 unpruned (24GB+)",
    description: "MiniMax H3 with the full modulation weights and the int8 encoder. 24GB-class cards; unmeasured.",
    type: "minimax",
    nativeFps: 24,
    maxFrames: 175,
    resolution: [1344, 768],
    aspectRatios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
    defaultSteps: 20,
    minSteps: 20,
    speedProfiles: ["standard", "turbo-8", "turbo-4-768p"],
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
    maxPixelArea: 768 * 1344,
  },
  "minimax-h3-bf16": {
    label: "MiniMax H3 BF16 (48GB+)",
    description: "MiniMax H3 at full precision with the bf16 encoder (~110GB of weights). Workstation cards; unmeasured.",
    type: "minimax",
    nativeFps: 24,
    maxFrames: 175,
    resolution: [1344, 768],
    aspectRatios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
    defaultSteps: 20,
    minSteps: 20,
    speedProfiles: ["standard", "turbo-8", "turbo-4-768p"],
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
    maxPixelArea: 768 * 1344,
  },
  "hunyuan-t2v": {
    label: "HunyuanVideo 13B T2V (GGUF Q5)",
    description: "Tencent HunyuanVideo — cinematic motion, no content filter. 24fps, ~3s clips (~11GB VRAM)",
    type: "hunyuan",
    maxFrames: 129,
    resolution: [848, 480],
    defaultSteps: 20,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 16,
    maxPixelArea: 1_000_000,
  },
  "hunyuan-i2v": {
    label: "HunyuanVideo 13B I2V (GGUF Q5)",
    description: "HunyuanVideo image-to-video (v2) — follows the start frame closely. 24fps, ~3s (~11GB VRAM)",
    type: "hunyuan",
    maxFrames: 129,
    resolution: [848, 480],
    defaultSteps: 20,
    supportsT2V: false,
    supportsI2V: true,
    dimensionAlignment: 16,
    maxPixelArea: 1_000_000,
  },
};

/** Every family that renders video, derived from MODEL_OPTIONS so a new
 *  family cannot be left out of a hand-written list. 2026-08-28: the video
 *  page kept its own literal set, MiniMax H3 was not in it, and the model
 *  never appeared in the menu even though it was installed. */
export const GENERATION_TYPES = new Set(Object.values(MODEL_OPTIONS).map((m) => m.type));

export const DEFAULT_T2V_MODEL = "wan22-5b";
export const DEFAULT_I2V_MODEL = "wan22-5b";

export const isCogVideoXModel = (model) => MODEL_OPTIONS[model]?.type === "cogvideox";
export const isWanModel = (model) => MODEL_OPTIONS[model]?.type === "wan";
export const isLtxModel = (model) => MODEL_OPTIONS[model]?.type === "ltx";
export const isHunyuanModel = (model) => MODEL_OPTIONS[model]?.type === "hunyuan";
export const isMinimaxModel = (model) => MODEL_OPTIONS[model]?.type === "minimax";

/** Longer presets appear only when the registry declares a duration tier for
 *  them (a measured pixel-area cap at that length), so a 10 s or 15 s option
 *  cannot be offered before someone has rendered one. */
export const withDurationTiers = (presets, registryMeta) => {
  const tiers = registryMeta?.capabilities?.duration_tiers;
  if (!Array.isArray(tiers) || tiers.length === 0) return presets;
  const fps = registryMeta?.capabilities?.native_fps || 24;
  const known = new Set(Object.values(presets).map((p) => p.duration_frames));
  const longest = Math.max(...Object.values(presets).map((p) => p.duration_frames));
  const extra = {};
  for (const tier of tiers) {
    if (!tier?.frames || known.has(tier.frames) || tier.frames <= longest) continue;
    const seconds = Math.round(tier.frames / fps);
    extra[`tier_${tier.frames}`] = {
      label: `${seconds} s`,
      description: `~${seconds} seconds`,
      duration_frames: tier.frames,
      fps,
    };
  }
  return Object.keys(extra).length ? { ...presets, ...extra } : presets;
};

/** Duration presets for a model. The preset fps must match the model's native
 *  rate — muxing 24fps-native frames at 16fps plays every clip in slow motion. */
export const durationPresetsFor = (model, registryMeta) => {
  if (isMinimaxModel(model)) return withDurationTiers(MINIMAX_DURATION_PRESETS, registryMeta);
  if (isHunyuanModel(model)) return HUNYUAN_DURATION_PRESETS;
  if (isLtxModel(model)) return LTX_DURATION_PRESETS;
  if (isWanModel(model)) {
    return MODEL_OPTIONS[model]?.nativeFps === 24 ? WAN_5B_DURATION_PRESETS : WAN_DURATION_PRESETS;
  }
  return COGVIDEOX_DURATION_PRESETS;
};

/** Aspect ratios a model can actually render.
 *
 *  A model declaring `aspectRatios` offers only those; anything else is off its
 *  training distribution and the frame comes back warped rather than merely
 *  cropped. Models without the key are unconstrained. */
export const aspectRatiosFor = (model) => {
  const allowed = MODEL_OPTIONS[model]?.aspectRatios;
  if (!allowed?.length) return ASPECT_RATIO_PRESETS;
  return Object.fromEntries(
    allowed.filter((k) => ASPECT_RATIO_PRESETS[k]).map((k) => [k, ASPECT_RATIO_PRESETS[k]]),
  );
};

/** The selected ratio if the model supports it, otherwise its first supported one. */
export const resolveAspectRatio = (model, aspectRatio) => {
  const allowed = aspectRatiosFor(model);
  return allowed[aspectRatio] ? aspectRatio : Object.keys(allowed)[0];
};

export const snapDimensions = (width, height, model, registryMeta) => {
  const align =
    registryMeta?.dimension_alignment ??
    MODEL_OPTIONS[model]?.dimensionAlignment ??
    16;
  return {
    width: Math.round(width / align) * align,
    height: Math.round(height / align) * align,
  };
};

/** Redistribute a fixed pixel-area budget across an aspect ratio, snapped to
 *  the model's alignment. Keeps VRAM/compute constant while the frame reshapes:
 *  768×512 (3:2) ≈ 832×480 (16:9) ≈ 480×832 (9:16) ≈ 640×640 (1:1). */
export const fitAreaToRatio = (area, ratio, model, registryMeta) => {
  const align =
    registryMeta?.dimension_alignment ??
    MODEL_OPTIONS[model]?.dimensionAlignment ??
    16;
  const maxArea = registryMeta?.max_pixel_area ?? MODEL_OPTIONS[model]?.maxPixelArea ?? area;
  const budget = Math.min(area, maxArea);
  // height = width / ratio; width * height = budget → width = sqrt(budget * ratio)
  let width = Math.sqrt(budget * ratio);
  let height = width / ratio;
  width = Math.round(width / align) * align;
  height = Math.round(height / align) * align;
  return { width: Math.max(align, width), height: Math.max(align, height) };
};
