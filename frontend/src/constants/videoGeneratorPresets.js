import { translate } from "../i18n";

export const QUALITY_PRESETS = {
  fast: {
    get label() { return translate("options.videoPresets.quality.fast.label"); },
    get description() { return translate("options.videoPresets.quality.fast.description"); },
    num_inference_steps: 10,
    width: 720,
    height: 480,
  },
  standard: {
    get label() { return translate("options.videoPresets.quality.standard.label"); },
    get description() { return translate("options.videoPresets.quality.standard.description"); },
    num_inference_steps: 30,
    width: 720,
    height: 480,
  },
  high: {
    get label() { return translate("options.videoPresets.quality.high.label"); },
    get description() { return translate("options.videoPresets.quality.high.description"); },
    num_inference_steps: 40,
    width: 720,
    height: 480,
  },
  maximum: {
    get label() { return translate("options.videoPresets.quality.maximum.label"); },
    get description() { return translate("options.videoPresets.quality.maximum.description"); },
    num_inference_steps: 50,
    width: 720,
    height: 480,
  },
};

export const COGVIDEOX_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.short"); }, get description() { return translate("options.videoPresets.duration.about", { count: 3 }); }, duration_frames: 24, fps: 8 },
  medium: { get label() { return translate("options.videoPresets.duration.medium"); }, get description() { return translate("options.videoPresets.duration.about", { count: 4 }); }, duration_frames: 33, fps: 8 },
  long: { get label() { return translate("options.videoPresets.duration.long"); }, get description() { return translate("options.videoPresets.duration.about", { count: 6 }); }, duration_frames: 49, fps: 8 },
};

/** CogVideoX 1.5 I2V renders its card's 5 s length at 16fps; the card's 10 s
 *  (161 frames) is not offered until it has been measured on a 16 GB card. */
export const COGVIDEOX15_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.seconds", { seconds: 5 }); }, get description() { return translate("options.videoPresets.duration.exact", { count: 5 }); }, duration_frames: 81, fps: 16 },
};

/** Wan 2.2 14B (T2V / I2V) is 16fps-native. */
export const WAN_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.short"); }, get description() { return translate("options.videoPresets.duration.about", { count: 2 }); }, duration_frames: 33, fps: 16 },
  medium: { get label() { return translate("options.videoPresets.duration.medium"); }, get description() { return translate("options.videoPresets.duration.about", { count: 3 }); }, duration_frames: 49, fps: 16 },
  long: { get label() { return translate("options.videoPresets.duration.long"); }, get description() { return translate("options.videoPresets.duration.about", { count: 5 }); }, duration_frames: 81, fps: 16 },
};

/** Wan 2.2 TI2V-5B is 24fps-native (official template: 121 frames @ 24fps); frames 4n+1. */
export const WAN_5B_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.short"); }, get description() { return translate("options.videoPresets.duration.about", { count: 2 }); }, duration_frames: 49, fps: 24 },
  medium: { get label() { return translate("options.videoPresets.duration.medium"); }, get description() { return translate("options.videoPresets.duration.about", { count: 3 }); }, duration_frames: 73, fps: 24 },
  long: { get label() { return translate("options.videoPresets.duration.long"); }, get description() { return translate("options.videoPresets.duration.about", { count: 5 }); }, duration_frames: 121, fps: 24 },
};

/** LTX-2.3 / 2.5 frame counts must be 8n+1. 161 @ 16fps ≈ 10s (16GB Ada target). */
export const LTX_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.short"); }, get description() { return translate("options.videoPresets.duration.about", { count: 4 }); }, duration_frames: 65, fps: 16 },
  medium: { get label() { return translate("options.videoPresets.duration.medium"); }, get description() { return translate("options.videoPresets.duration.about", { count: 6 }); }, duration_frames: 97, fps: 16 },
  long: { get label() { return translate("options.videoPresets.duration.long"); }, get description() { return translate("options.videoPresets.duration.about", { count: 10 }); }, duration_frames: 161, fps: 16 },
};

/** HunyuanVideo frame counts must be 4n+1; native 24fps. 73 @ 24fps ≈ 3s (official template). */
// MiniMax H3 samples on a 17k+5 frame grid at 24 fps; 124 (~5 s) is the
// official template's default and the low end of the trained range. The 10 s
// and 15 s presets come from the registry's duration tiers (measured on a
// 16 GB card at 480p), appended by durationPresetsFor().
export const MINIMAX_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.short"); }, get description() { return translate("options.videoPresets.duration.about", { count: 3 }); }, duration_frames: 73, fps: 24 },
  medium: { get label() { return translate("options.videoPresets.duration.medium"); }, get description() { return translate("options.videoPresets.duration.about", { count: 5 }); }, duration_frames: 124, fps: 24 },
  long: { get label() { return translate("options.videoPresets.duration.long"); }, get description() { return translate("options.videoPresets.duration.about", { count: 7 }); }, duration_frames: 175, fps: 24 },
};

export const HUNYUAN_DURATION_PRESETS = {
  short: { get label() { return translate("options.videoPresets.duration.short"); }, get description() { return translate("options.videoPresets.duration.about", { count: 2 }); }, duration_frames: 49, fps: 24 },
  medium: { get label() { return translate("options.videoPresets.duration.medium"); }, get description() { return translate("options.videoPresets.duration.about", { count: 3 }); }, duration_frames: 73, fps: 24 },
  long: { get label() { return translate("options.videoPresets.duration.long"); }, get description() { return translate("options.videoPresets.duration.about", { count: 4 }); }, duration_frames: 97, fps: 24 },
};

export const MOTION_PRESETS = {
  subtle: { get label() { return translate("options.videoPresets.motion.subtle.label"); }, get description() { return translate("options.videoPresets.motion.subtle.description"); }, motion_strength: 0.5 },
  normal: { get label() { return translate("options.videoPresets.motion.normal.label"); }, get description() { return translate("options.videoPresets.motion.normal.description"); }, motion_strength: 1.0 },
  dynamic: { get label() { return translate("options.videoPresets.motion.dynamic.label"); }, get description() { return translate("options.videoPresets.motion.dynamic.description"); }, motion_strength: 1.5 },
  intense: { get label() { return translate("options.videoPresets.motion.intense.label"); }, get description() { return translate("options.videoPresets.motion.intense.description"); }, motion_strength: 2.0 },
};

export const OUTPUT_QUALITY_TIERS = {
  draft: { get label() { return translate("options.videoPresets.outputTiers.draft.label"); }, get description() { return translate("options.videoPresets.outputTiers.draft.description"); }, interpolation: 1, upscale: false },
  standard: { get label() { return translate("options.videoPresets.outputTiers.standard.label"); }, get description() { return translate("options.videoPresets.outputTiers.standard.description"); }, interpolation: 2, upscale: false },
  cinema: { get label() { return translate("options.videoPresets.outputTiers.cinema.label"); }, get description() { return translate("options.videoPresets.outputTiers.cinema.description"); }, interpolation: 2, upscale: true },
};

export const KEYFRAME_MODEL_OPTIONS = {
  "from-lora": {
    get label() { return translate("options.videoPresets.keyframeModels.fromLora.label"); },
    get description() { return translate("options.videoPresets.keyframeModels.fromLora.description"); },
  },
  "flux-schnell": { label: "FLUX.1-schnell", get description() { return translate("options.videoPresets.keyframeModels.fluxSchnell.description"); } },
  "flux-dev-lora": { label: "FLUX-dev + LoRA", get description() { return translate("options.videoPresets.keyframeModels.fluxDevLora.description"); } },
  sdxl: { label: "SDXL", get description() { return translate("options.videoPresets.keyframeModels.sdxl.description"); } },
  "sdxl-lora": { label: "SDXL + LoRA", get description() { return translate("options.videoPresets.keyframeModels.sdxlLora.description"); } },
};

export const DEFAULT_KEYFRAME_MODEL = "flux-schnell";

/** Wan 2.2 5B sampling profiles (backend WAN5B_SAMPLER_PROFILES). */
export const WAN5B_SAMPLER_PROFILES = {
  official: { get label() { return translate("options.videoPresets.samplerProfiles.official.label"); }, get description() { return translate("options.videoPresets.samplerProfiles.official.description"); } },
  adaptive: { get label() { return translate("options.videoPresets.samplerProfiles.adaptive.label"); }, get description() { return translate("options.videoPresets.samplerProfiles.adaptive.description"); } },
};

// Family defaults, mirroring the backend workflow signatures. A model whose
// backend default differs from its family carries `defaultGuidance` in
// MODEL_OPTIONS below; keep the two in step or the UI will send a value the
// backend never chose for itself.
// MiniMax H3 runs without CFG (BasicGuider); the value is display-only.
export const MODEL_DEFAULT_GUIDANCE = { wan: 3.5, cogvideox: 6.0, ltx: 1.0, hunyuan: 6.0, minimax: 1.0 };

export const ASPECT_RATIO_PRESETS = {
  "16:9": { label: "16:9", get description() { return translate("options.videoPresets.aspectRatios.widescreen"); }, ratio: 16 / 9 },
  "9:16": { label: "9:16", get description() { return translate("options.videoPresets.aspectRatios.portraitVertical"); }, ratio: 9 / 16 },
  "1:1": { label: "1:1", get description() { return translate("options.videoPresets.aspectRatios.square"); }, ratio: 1 },
  "4:3": { label: "4:3", get description() { return translate("options.videoPresets.aspectRatios.standard"); }, ratio: 4 / 3 },
  "3:2": { label: "3:2", get description() { return translate("options.videoPresets.aspectRatios.classic"); }, ratio: 3 / 2 },
  "21:9": { label: "21:9", get description() { return translate("options.videoPresets.aspectRatios.ultraWide"); }, ratio: 21 / 9 },
  "3:4": { label: "3:4", get description() { return translate("options.videoPresets.aspectRatios.portrait"); }, ratio: 3 / 4 },
};

export const PROMPT_STYLES = {
  cinematic: { get label() { return translate("options.videoPresets.promptStyles.cinematic.label"); }, get description() { return translate("options.videoPresets.promptStyles.cinematic.description"); } },
  realistic: { get label() { return translate("options.videoPresets.promptStyles.realistic.label"); }, get description() { return translate("options.videoPresets.promptStyles.realistic.description"); } },
  artistic: { get label() { return translate("options.videoPresets.promptStyles.artistic.label"); }, get description() { return translate("options.videoPresets.promptStyles.artistic.description"); } },
  anime: { get label() { return translate("options.videoPresets.promptStyles.anime.label"); }, get description() { return translate("options.videoPresets.promptStyles.anime.description"); } },
  "3d_animation": { get label() { return translate("options.videoPresets.promptStyles.animation3d.label"); }, get description() { return translate("options.videoPresets.promptStyles.animation3d.description"); } },
  stop_motion: { get label() { return translate("options.videoPresets.promptStyles.stopMotion.label"); }, get description() { return translate("options.videoPresets.promptStyles.stopMotion.description"); } },
  hand_drawn: { get label() { return translate("options.videoPresets.promptStyles.handDrawn.label"); }, get description() { return translate("options.videoPresets.promptStyles.handDrawn.description"); } },
  western_cartoon: { get label() { return translate("options.videoPresets.promptStyles.westernCartoon.label"); }, get description() { return translate("options.videoPresets.promptStyles.westernCartoon.description"); } },
  none: { get label() { return translate("options.videoPresets.promptStyles.none.label"); }, get description() { return translate("options.videoPresets.promptStyles.none.description"); } },
};

export const VIDEO_SIZE_PRESETS = {
  small: { get label() { return translate("options.videoPresets.sizes.small.label"); }, get description() { return translate("options.videoPresets.sizes.small.description"); }, baseSize: 512 },
  medium: { get label() { return translate("options.videoPresets.sizes.medium.label"); }, description: "576px", baseSize: 576 },
  large: { get label() { return translate("options.videoPresets.sizes.large.label"); }, get description() { return translate("options.videoPresets.sizes.large.description"); }, baseSize: 720 },
  hd: { label: "HD", get description() { return translate("options.videoPresets.sizes.hd.description"); }, baseSize: 1280 },
  fullhd: { label: "Full HD", get description() { return translate("options.videoPresets.sizes.fullhd.description"); }, baseSize: 1920 },
};

export const MODEL_OPTIONS = {
  "wan22-5b": {
    get label() { return translate("options.videoPresets.models.wan225b.label"); },
    get description() { return translate("options.videoPresets.models.wan225b.description"); },
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
    get description() { return translate("options.videoPresets.models.wan2214b.description"); },
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
    speedProfiles: ["standard", "lightx2v-4"],
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 32,
    maxPixelArea: 1_000_000,
  },
  "wan22-14b-i2v": {
    label: "Wan 2.2 14B I2V (GGUF Q5)",
    get description() { return translate("options.videoPresets.models.wan2214bI2v.description"); },
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
    speedProfiles: ["standard", "lightx2v-4"],
    supportsT2V: false,
    supportsI2V: true,
    dimensionAlignment: 32,
    maxPixelArea: 1_000_000,
  },
  "cogvideox-5b": {
    label: "CogVideoX 5B",
    get description() { return translate("options.videoPresets.models.cogvideox5b.description"); },
    type: "cogvideox",
    nativeFps: 8,
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    minSteps: 50,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 16,
  },
  "cogvideox-5b-i2v": {
    label: "CogVideoX 1.5 5B I2V",
    get description() { return translate("options.videoPresets.models.cogvideox5bI2v.description"); },
    type: "cogvideox",
    // CogVideoX 1.5's card: short side 768, long side up to 1360, 16fps,
    // 5 s. The registry entry carries the same numbers.
    nativeFps: 16,
    maxFrames: 81,
    resolution: [1360, 768],
    canvas: { shortSide: 768, maxLongSide: 1360 },
    maxPixelArea: 1360 * 768,
    aspectRatios: ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2"],
    durationPresets: COGVIDEOX15_DURATION_PRESETS,
    defaultSteps: 50,
    minSteps: 50,
    supportsT2V: false,
    supportsI2V: true,
    dimensionAlignment: 16,
  },
  "ltx23-distilled-fp8": {
    label: "LTX-2.3 Distilled FP8 (16GB)",
    get description() { return translate("options.videoPresets.models.ltx23.description"); },
    type: "ltx",
    nativeFps: 16,
    maxFrames: 161,
    resolution: [768, 512],
    defaultSteps: 8,
    minSteps: 8,
    supportsT2V: true,
    supportsI2V: true,
    dimensionAlignment: 32,
  },
  "ltx25-distilled-int8": {
    label: "LTX-2.5 Distilled Int8 (16GB)",
    get description() { return translate("options.videoPresets.models.ltx25.description"); },
    type: "ltx",
    nativeFps: 16,
    maxFrames: 161,
    resolution: [768, 512],
    defaultSteps: 8,
    minSteps: 8,
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
    get description() { return translate("options.videoPresets.models.minimaxH3Int8.description"); },
    type: "minimax",
    nativeFps: 24,
    maxFrames: 362,
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
    get description() { return translate("options.videoPresets.models.minimaxH3Int8Full.description"); },
    type: "minimax",
    nativeFps: 24,
    maxFrames: 362,
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
  // The reference build takes no text-only or first-frame request: it is
  // offered under the References input, from capabilities.modes "ref2v".
  "minimax-h3-ref2va-int8": {
    label: "MiniMax H3 Reference Int8 (16GB)",
    get description() { return translate("options.videoPresets.models.minimaxH3Reference.description"); },
    type: "minimax",
    nativeFps: 24,
    maxFrames: 362,
    resolution: [864, 480],
    aspectRatios: ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
    defaultSteps: 20,
    minSteps: 20,
    speedProfiles: ["standard", "turbo-4"],
    supportsT2V: false,
    supportsI2V: false,
    dimensionAlignment: 32,
    maxPixelArea: 768 * 1344,
  },
  "minimax-h3-bf16": {
    label: "MiniMax H3 BF16 (48GB+)",
    get description() { return translate("options.videoPresets.models.minimaxH3Bf16.description"); },
    type: "minimax",
    nativeFps: 24,
    maxFrames: 362,
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
    get description() { return translate("options.videoPresets.models.hunyuanT2v.description"); },
    type: "hunyuan",
    nativeFps: 24,
    maxFrames: 129,
    resolution: [848, 480],
    defaultSteps: 20,
    minSteps: 20,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 16,
    maxPixelArea: 1_000_000,
  },
  "hunyuan-i2v": {
    label: "HunyuanVideo 13B I2V (GGUF Q5)",
    get description() { return translate("options.videoPresets.models.hunyuanI2v.description"); },
    type: "hunyuan",
    nativeFps: 24,
    maxFrames: 129,
    resolution: [848, 480],
    defaultSteps: 20,
    minSteps: 20,
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

/** Family of a model: shipped MODEL_OPTIONS first, then the registry row. */
export const familyType = (model, registryMeta) =>
  MODEL_OPTIONS[model]?.type || registryMeta?.type || null;

export const isCogVideoXModel = (model, registryMeta) => familyType(model, registryMeta) === "cogvideox";
export const isWanModel = (model, registryMeta) => familyType(model, registryMeta) === "wan";
export const isLtxModel = (model, registryMeta) => familyType(model, registryMeta) === "ltx";
export const isHunyuanModel = (model, registryMeta) => familyType(model, registryMeta) === "hunyuan";
export const isMinimaxModel = (model, registryMeta) => familyType(model, registryMeta) === "minimax";

/** Short name for the "what will run" chip. Keyed by MODEL_OPTIONS.type so a
 *  new family cannot fall through to CogVideoX the way Hunyuan did. Unknown
 *  types show the model's own label. */
const FAMILY_CHIP_LABEL = {
  ltx: (id) => (String(id).startsWith("ltx25") ? "LTX-2.5" : "LTX-2.3"),
  minimax: () => "MiniMax H3",
  wan: () => "Wan 2.2",
  hunyuan: () => "HunyuanVideo",
  cogvideox: () => "CogVideoX",
};

export function modelChipLabel(modelId, registryMeta) {
  const opt = MODEL_OPTIONS[modelId];
  const type = familyType(modelId, registryMeta);
  const named = FAMILY_CHIP_LABEL[type];
  if (named) return named(modelId);
  return opt?.label || registryMeta?.name || modelId || translate("options.videoPresets.unknownModel");
}

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
      get label() { return translate("options.videoPresets.duration.seconds", { seconds }); },
      get description() { return translate("options.videoPresets.duration.about", { count: seconds }); },
      duration_frames: tier.frames,
      fps,
    };
  }
  return Object.keys(extra).length ? { ...presets, ...extra } : presets;
};

/** Duration presets for a model. The preset fps must match the model's native
 *  rate — muxing 24fps-native frames at 16fps plays every clip in slow motion. */
export const durationPresetsFor = (model, registryMeta) => {
  if (MODEL_OPTIONS[model]?.durationPresets) return MODEL_OPTIONS[model].durationPresets;
  if (isMinimaxModel(model, registryMeta)) return withDurationTiers(MINIMAX_DURATION_PRESETS, registryMeta);
  if (isHunyuanModel(model, registryMeta)) return HUNYUAN_DURATION_PRESETS;
  if (isLtxModel(model, registryMeta)) return LTX_DURATION_PRESETS;
  if (isWanModel(model, registryMeta)) {
    const fps = MODEL_OPTIONS[model]?.nativeFps ?? registryMeta?.capabilities?.native_fps;
    return fps === 24 ? WAN_5B_DURATION_PRESETS : WAN_DURATION_PRESETS;
  }
  return COGVIDEOX_DURATION_PRESETS;
};

/** Aspect ratios a model can actually render.
 *
 *  A model declaring `aspectRatios` offers only those; anything else is off its
 *  training distribution and the frame comes back warped rather than merely
 *  cropped. Models without the key are unconstrained. */
export const aspectRatiosFor = (model, registryMeta) => {
  const allowed = MODEL_OPTIONS[model]?.aspectRatios || registryMeta?.capabilities?.aspect_ratios;
  if (!allowed?.length) return ASPECT_RATIO_PRESETS;
  return Object.fromEntries(
    allowed.filter((k) => ASPECT_RATIO_PRESETS[k]).map((k) => [k, ASPECT_RATIO_PRESETS[k]]),
  );
};

/** The selected ratio if the model supports it, otherwise its first supported one. */
export const resolveAspectRatio = (model, aspectRatio, registryMeta) => {
  const allowed = aspectRatiosFor(model, registryMeta);
  return allowed[aspectRatio] ? aspectRatio : Object.keys(allowed)[0];
};

/** Largest seed the box accepts: the range the backend draws random seeds from. */
export const MAX_SEED = 2 ** 31 - 1;

/** The Seed box's text as a seed, or null for a new random seed per clip. */
export const parseSeed = (text) => {
  const t = String(text ?? "").trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n <= MAX_SEED ? n : null;
};

/** The size on a model's declared canvas: the short side fixed, the long side
 *  following the aspect up to its cap, snapped to the model's grid. */
export const canvasDimensions = (canvas, ratio, align = 16) => {
  const { shortSide, maxLongSide } = canvas;
  const wide = ratio >= 1 ? ratio : 1 / ratio;
  let longSide = Math.max(shortSide, Math.round(Math.min(shortSide * wide, maxLongSide) / align) * align);
  if (longSide > maxLongSide) longSide = Math.floor(maxLongSide / align) * align;
  return ratio >= 1 ? { width: longSide, height: shortSide } : { width: shortSide, height: longSide };
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
