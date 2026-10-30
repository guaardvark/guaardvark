// Pure helpers for the Batch Image Generator page: model-family detection,
// per-family quality presets, and the prompt list that is actually submitted.
//
// Kept out of the page component so the Preview dialog and startGeneration
// share one prompt builder (they used to disagree about Look & Feel) and so
// the family logic has a single definition instead of four inline tests that
// disagreed about `auto` and exact-vs-fuzzy matching.
import { translate } from "../i18n";

/**
 * Sampling family for a catalog model id. `auto` is its own family: the
 * backend router may pick Z-Image, SDXL (for text-bearing prompts) or another
 * downloaded model, so the UI must not lock the guidance slider for it.
 */
export function modelFamily(model) {
  const m = String(model || 'auto').toLowerCase();
  if (m === 'auto') return 'auto';
  // Shipped `qwen-image-21` and user `user-qwen-image-21-<slug>`. Tested before
  // the substring families so a slug cannot pull the id elsewhere; the editing
  // model `qwen-image-edit` does not contain this token and is unaffected.
  if (m.includes('qwen-image-21')) return 'qwen-image-21';
  // Shipped `moss-image` and user `user-moss-image-<slug>`; tested before FLUX
  // because a MOSS repack can carry the FLUX.2 VAE's name.
  if (m.includes('moss-image')) return 'moss-image';
  if (m.includes('flux')) return 'flux';
  if (m.includes('zimage') || m.includes('z-image')) return 'zimage';
  if (m.includes('krea')) return m.includes('raw') ? 'krea-raw' : 'krea-turbo';
  if (m.includes('sana')) return 'sana';
  if (m.includes('xl')) return m.includes('turbo') ? 'sdxl-turbo' : 'sdxl';
  return 'sd';
}

/** Z-Image proper (CFG-distilled). `auto` is deliberately NOT included. */
export const isZimageModel = (model) => modelFamily(model) === 'zimage';

/**
 * Stills paths with no negative prompt: Z-Image and Krea 2 Turbo (CFG-free)
 * and Sana Sprint (its pipeline takes none). Krea Raw still uses one; `auto`
 * may route to SDXL so it stays editable. Whether anatomy phrasing applies is
 * a different question, answered by promptStyleFor: the backend skips it for
 * prose models only, and Krea Turbo and Sana still take it.
 */
export function ignoresNegativeAndAnatomy(model) {
  const fam = modelFamily(model);
  return fam === 'zimage' || fam === 'krea-turbo' || fam === 'sana';
}

/**
 * How the model reads a prompt: 'natural' (an LLM text encoder trained on
 * prose; Z-Image) or 'tags' (CLIP-era models that take comma tags). The backend
 * skips the content-type and anatomy tag stuffing for natural models, so the
 * page hides those controls for them. The answer comes from /batch-image/models
 * (`prompt_style` per row, `auto_prompt_style` for Auto); the family table is
 * the fallback for the first paint, before the list has arrived.
 */
export function promptStyleFor(model, modelOptions = [], autoPromptStyle = null) {
  const m = String(model || 'auto');
  if (m === 'auto') return autoPromptStyle || 'natural';
  const opt = (modelOptions || []).find((o) => o.value === m);
  if (opt?.promptStyle) return opt.promptStyle;
  const fam = modelFamily(m);
  return fam === 'zimage' || fam === 'qwen-image-21' || fam === 'moss-image' ? 'natural' : 'tags';
}

// Qwen-Image 2.1 guidance range. The official ComfyUI template
// (image_qwen_image_2_1_t2i.json) runs cfg 1.0 and says to raise it only with a
// negative prompt. The slider stops at 5, the upper end of the registry's
// cfg_range for qwen-image-21 (media_model_registry.IMAGE_MODEL_LIMITS):
// compared at 1024x1024 with a negative prompt, cfg 3 and 5 render clean and
// cfg 9 crushes the shadows to black.
export const QWEN_IMAGE_21_GUIDANCE_MAX = 5;

// MOSS-Image guidance range: the model's user guide gives cfg 3.0-6.0, the
// official ComfyUI template runs 2.5. The slider stops at 6, the upper end of
// the registry's cfg_range for moss-image (media_model_registry.IMAGE_MODEL_LIMITS).
export const MOSS_IMAGE_GUIDANCE_MAX = 6;

export const ZIMAGE_STEPS = 9;
export const ZIMAGE_GUIDANCE = 0;

// Steps each Z-Image preset legitimately uses, so the guard in the page corrects
// foreign values without fighting the model's own Fast / Standard / High-2K presets.
export const ZIMAGE_PRESET_STEPS = { fast: 6, standard: 9, 'high-2k': 9 };

const ZIMAGE_PRESETS = [
  { value: 'fast', get label() { return translate('options.imagePresets.labels.fast'); }, steps: 6, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.zimage.fast'); } },
  { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 9, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.zimage.standard'); } },
  // The 2K jump is its own explicitly-labelled preset: a preset that silently
  // rewrote the canvas to 2048x2048 put 16GB cards into memory crashes.
  { value: 'high-2k', get label() { return translate('options.imagePresets.labels.high2k'); }, steps: 9, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.zimage.high2k'); } },
];

const PRESETS_BY_FAMILY = {
  flux: [
    { value: 'flux-fast', get label() { return translate('options.imagePresets.labels.fluxFast'); }, steps: 16, guidance: 3.0, get description() { return translate('options.imagePresets.descriptions.flux.fluxFast'); } },
    { value: 'flux-quality', get label() { return translate('options.imagePresets.labels.fluxQuality'); }, steps: 28, guidance: 3.5, get description() { return translate('options.imagePresets.descriptions.flux.fluxQuality'); } },
    { value: 'flux-ultra', get label() { return translate('options.imagePresets.labels.fluxUltra'); }, steps: 40, guidance: 4.0, get description() { return translate('options.imagePresets.descriptions.flux.fluxUltra'); } },
  ],
  zimage: ZIMAGE_PRESETS,
  // `auto` resolves to the product default (Z-Image) when nothing else fits,
  // so its presets mirror that family; guidance stays editable.
  auto: ZIMAGE_PRESETS,
  'krea-turbo': [
    { value: 'fast', get label() { return translate('options.imagePresets.labels.fast'); }, steps: 6, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.kreaTurbo.fast'); } },
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 8, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.kreaTurbo.standard'); } },
    { value: 'high', get label() { return translate('options.imagePresets.labels.high'); }, steps: 12, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.kreaTurbo.high'); } },
  ],
  'krea-raw': [
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 40, guidance: 3.5, get description() { return translate('options.imagePresets.descriptions.kreaRaw.standard'); } },
    { value: 'high', get label() { return translate('options.imagePresets.labels.high'); }, steps: 52, guidance: 3.5, get description() { return translate('options.imagePresets.descriptions.kreaRaw.high'); } },
    { value: 'ultra', get label() { return translate('options.imagePresets.labels.ultra'); }, steps: 60, guidance: 3.5, get description() { return translate('options.imagePresets.descriptions.kreaRaw.ultra'); } },
  ],
  // SDXL Turbo is a 1-4 step, CFG-free distillation; the backend validator
  // clamps to that range (settings_validator.py), so offering the SDXL Base
  // 20-35 step presets showed numbers that never ran.
  'sdxl-turbo': [
    { value: 'fast', get label() { return translate('options.imagePresets.labels.fast'); }, steps: 1, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.sdxlTurbo.fast'); } },
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 4, guidance: 0.0, get description() { return translate('options.imagePresets.descriptions.sdxlTurbo.standard'); } },
  ],
  // Sana Sprint samples in 1-4 steps with an embedded guidance of 4.5; 2 steps
  // was the cleanest of 1/2/4 (media_model_registry, sana family row).
  sana: [
    { value: 'fast', get label() { return translate('options.imagePresets.labels.fast'); }, steps: 1, guidance: 4.5, get description() { return translate('options.imagePresets.descriptions.sana.fast'); } },
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 2, guidance: 4.5, get description() { return translate('options.imagePresets.descriptions.sana.standard'); } },
  ],
  // Qwen-Image 2.1 numbers come from the official ComfyUI template
  // (image_qwen_image_2_1_t2i.json): 25 steps, cfg 1.0, euler/simple, with the
  // note that the official pipeline uses 40-50 steps. Nothing below 25 is offered
  // because no faster count has been measured on a card.
  'qwen-image-21': [
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 25, guidance: 1.0, get description() { return translate('options.imagePresets.descriptions.qwenImage21.standard'); } },
    { value: 'high', get label() { return translate('options.imagePresets.labels.high'); }, steps: 40, guidance: 1.0, get description() { return translate('options.imagePresets.descriptions.qwenImage21.high'); } },
  ],
  // MOSS-Image numbers come from the official ComfyUI template
  // (image_moss_image_t2i.json: 25 steps, cfg 2.5, er_sde/simple) and the
  // model's user guide (50 steps, cfg 3.0).
  'moss-image': [
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 25, guidance: 2.5, get description() { return translate('options.imagePresets.descriptions.mossImage.standard'); } },
    { value: 'high', get label() { return translate('options.imagePresets.labels.high'); }, steps: 50, guidance: 3.0, get description() { return translate('options.imagePresets.descriptions.mossImage.high'); } },
  ],
  sdxl: [
    { value: 'fast', get label() { return translate('options.imagePresets.labels.fast'); }, steps: 20, guidance: 6.0, get description() { return translate('options.imagePresets.descriptions.sdxl.fast'); } },
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 25, guidance: 7.0, get description() { return translate('options.imagePresets.descriptions.sdxl.standard'); } },
    { value: 'high', get label() { return translate('options.imagePresets.labels.high'); }, steps: 35, guidance: 7.5, get description() { return translate('options.imagePresets.descriptions.sdxl.high'); } },
  ],
  sd: [
    { value: 'fast', get label() { return translate('options.imagePresets.labels.fast'); }, steps: 15, guidance: 7.0, get description() { return translate('options.imagePresets.descriptions.sd.fast'); } },
    { value: 'standard', get label() { return translate('options.imagePresets.labels.standard'); }, steps: 20, guidance: 7.5, get description() { return translate('options.imagePresets.descriptions.sd.standard'); } },
    { value: 'high', get label() { return translate('options.imagePresets.labels.high'); }, steps: 30, guidance: 8.0, get description() { return translate('options.imagePresets.descriptions.sd.high'); } },
  ],
};

/** Quality presets offered for a model. */
export function qualityPresetsForModel(model) {
  return PRESETS_BY_FAMILY[modelFamily(model)] || PRESETS_BY_FAMILY.sd;
}

// The preset whose steps/guidance match the family's recommended recipe.
const DEFAULT_PRESET_BY_FAMILY = {
  flux: 'flux-quality',
  zimage: 'standard',
  auto: 'standard',
  'krea-turbo': 'standard',
  'krea-raw': 'high',
  'sdxl-turbo': 'standard',
  sana: 'standard',
  'qwen-image-21': 'standard',
  'moss-image': 'standard',
  sdxl: 'standard',
  sd: 'standard',
};

/**
 * Preset to select after switching to `model`. Keeps `current` when the new
 * family offers it; otherwise the family default. Without this a value from
 * another family (e.g. 'flux-quality' after leaving FLUX) sat on the Select as
 * an out-of-range value and the "Quality" chip described settings not in use.
 */
export function resolveQualityPreset(model, current) {
  const presets = qualityPresetsForModel(model);
  if (current && presets.some((p) => p.value === current)) return current;
  return DEFAULT_PRESET_BY_FAMILY[modelFamily(model)] || 'standard';
}

// Legacy batches stored prompts with the old " (i+1)" copy counter baked in.
// Strip it when rehydrating so re-runs don't compound the marker.
// Mirrors _TRAILING_COUNTER in backend/services/image_prompt_sanitize.py.
export const stripCopyCounter = (p) =>
  String(p ?? '').replace(/(?:\s*\(\d{1,3}\))+\s*$/, '').trim() || String(p ?? '');

/** Highest quantity the page accepts; the backend caps the prompt list to match. */
export const MAX_QUANTITY = 100;

/** Largest image seed: the 2**32 space the backend draws image seeds from. */
export const IMAGE_MAX_SEED = 2 ** 32 - 1;

/** The Seed box's text as a seed, or null for a new seed per image. */
export const parseImageSeed = (text) => {
  const t = String(text ?? '').trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n <= IMAGE_MAX_SEED ? n : null;
};

export function clampQuantity(value) {
  const n = parseInt(value, 10);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(n, MAX_QUANTITY);
}

/**
 * The exact prompt list that will be submitted for single/bulk mode.
 *
 * - single: the whole textarea is ONE prompt (line breaks preserved).
 * - bulk: one prompt per non-empty line.
 * Look & Feel, when set, is appended to every prompt as ", <look>". Quantity
 * repeats each prompt verbatim; the backend keys images by position, so no
 * copy counter is added to the text.
 */
export function buildFinalPrompts({ inputMode, batchItems, lookAndFeel, quantity }) {
  const look = String(lookAndFeel || '').trim();
  const withLook = (p) => (look ? `${p}, ${look}` : p);

  let base;
  if (inputMode === 'single') {
    const single = String(batchItems || '').trim();
    base = single ? [single] : [];
  } else {
    base = String(batchItems || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
  }

  const copies = clampQuantity(quantity);
  const out = [];
  base.forEach((p) => {
    const finalPrompt = withLook(p);
    for (let i = 0; i < copies; i++) out.push(finalPrompt);
  });
  return out;
}

/**
 * Picker option for one row of /batch-image/models. A model that is not on
 * disk says so: it installs from Install (Manage models), never from Generate.
 */
export function imageModelOption(m) {
  const installed = m.is_downloaded !== false;
  return {
    value: m.id,
    label: m.recommended ? `${m.label} ⭐` : m.label,
    description: installed
      ? (m.description || '')
      : (m.size_gb
        ? translate('messages.imageModels.notInstalledSize', { description: m.description || '', size: m.size_gb })
        : translate('messages.imageModels.notInstalled', { description: m.description || '' })).trim(),
    installed,
    recommended: !!m.recommended,
    name: m.name || m.label || m.id,
    sizeGb: m.size_gb || 0,
    promptStyle: m.prompt_style || null,
  };
}

/**
 * What Generate is waiting on for `value`, or null when nothing is.
 *
 * A picked model that is not installed blocks Generate: the run would be
 * refused. Auto chooses among installed models, so it never blocks; when no
 * listed model is installed it names the recommended one so the page can
 * offer Install (a ComfyUI route may still render without it).
 */
export function modelInstallNeed(options, value) {
  if (!value || value === 'auto') {
    const listed = options.filter((o) => o.value !== 'auto');
    if (listed.length === 0 || listed.some((o) => o.installed)) return null;
    return { option: listed.find((o) => o.recommended) || listed[0], blocks: false };
  }
  const option = options.find((o) => o.value === value);
  return option && option.installed === false ? { option, blocks: true } : null;
}
