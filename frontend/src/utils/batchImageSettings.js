// Pure helpers for the Batch Image Generator page: model-family detection,
// per-family quality presets, and the prompt list that is actually submitted.
//
// Kept out of the page component so the Preview dialog and startGeneration
// share one prompt builder (they used to disagree about Look & Feel) and so
// the family logic has a single definition instead of four inline tests that
// disagreed about `auto` and exact-vs-fuzzy matching.

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
  return fam === 'zimage' || fam === 'qwen-image-21' ? 'natural' : 'tags';
}

// Qwen-Image 2.1 guidance range. The official ComfyUI template
// (image_qwen_image_2_1_t2i.json) runs cfg 1.0 and says to raise it only with a
// negative prompt; the slider stops at 10 so a stray drag cannot reach SD-era
// values. Nothing above 1.0 has been measured on this product's path.
export const QWEN_IMAGE_21_GUIDANCE_MAX = 10;

export const ZIMAGE_STEPS = 9;
export const ZIMAGE_GUIDANCE = 0;

// Steps each Z-Image preset legitimately uses, so the guard in the page corrects
// foreign values without fighting the model's own Fast / Standard / High-2K presets.
export const ZIMAGE_PRESET_STEPS = { fast: 6, standard: 9, 'high-2k': 9 };

const ZIMAGE_PRESETS = [
  { value: 'fast', label: 'Fast', steps: 6, guidance: 0.0, description: 'Quick draft' },
  { value: 'standard', label: 'Standard', steps: 9, guidance: 0.0, description: 'Official Turbo recipe (HF)' },
  // The 2K jump is its own explicitly-labelled preset: a preset that silently
  // rewrote the canvas to 2048x2048 put 16GB cards into memory crashes.
  { value: 'high-2k', label: 'High 2K (2048²)', steps: 9, guidance: 0.0, description: 'Official recipe at 2K canvas — heavy; 16GB cards may refuse' },
];

const PRESETS_BY_FAMILY = {
  flux: [
    { value: 'flux-fast', label: 'FLUX Fast', steps: 16, guidance: 3.0, description: 'Faster FLUX.1-dev stills' },
    { value: 'flux-quality', label: 'FLUX Max Quality', steps: 28, guidance: 3.5, description: 'Default max-quality FLUX.1-dev' },
    { value: 'flux-ultra', label: 'FLUX Ultra', steps: 40, guidance: 4.0, description: 'Highest steps — slow, peak detail' },
  ],
  zimage: ZIMAGE_PRESETS,
  // `auto` resolves to the product default (Z-Image) when nothing else fits,
  // so its presets mirror that family; guidance stays editable.
  auto: ZIMAGE_PRESETS,
  'krea-turbo': [
    { value: 'fast', label: 'Fast', steps: 6, guidance: 0.0, description: 'Krea turbo CFG-free' },
    { value: 'standard', label: 'Standard', steps: 8, guidance: 0.0, description: 'Balanced turbo' },
    { value: 'high', label: 'High Quality', steps: 12, guidance: 0.0, description: 'More steps' },
  ],
  'krea-raw': [
    { value: 'standard', label: 'Standard', steps: 40, guidance: 3.5, description: 'Krea raw quality' },
    { value: 'high', label: 'High Quality', steps: 52, guidance: 3.5, description: 'Default raw' },
    { value: 'ultra', label: 'Ultra', steps: 60, guidance: 3.5, description: 'Slow, peak detail' },
  ],
  // SDXL Turbo is a 1-4 step, CFG-free distillation; the backend validator
  // clamps to that range (settings_validator.py), so offering the SDXL Base
  // 20-35 step presets showed numbers that never ran.
  'sdxl-turbo': [
    { value: 'fast', label: 'Fast', steps: 1, guidance: 0.0, description: 'Single-step preview' },
    { value: 'standard', label: 'Standard', steps: 4, guidance: 0.0, description: 'Turbo recipe (4 steps, CFG-free)' },
  ],
  // Sana Sprint samples in 1-4 steps with an embedded guidance of 4.5; 2 steps
  // was the cleanest of 1/2/4 (media_model_registry, sana family row).
  sana: [
    { value: 'fast', label: 'Fast', steps: 1, guidance: 4.5, description: 'Single-step preview' },
    { value: 'standard', label: 'Standard', steps: 2, guidance: 4.5, description: 'Sana Sprint recipe (2 steps)' },
  ],
  // Qwen-Image 2.1 numbers come from the official ComfyUI template
  // (image_qwen_image_2_1_t2i.json): 25 steps, cfg 1.0, euler/simple, with the
  // note that the official pipeline uses 40-50 steps. Nothing below 25 is offered
  // because no faster count has been measured on a card.
  'qwen-image-21': [
    { value: 'standard', label: 'Standard', steps: 25, guidance: 1.0, description: 'Official ComfyUI template (25 steps, cfg 1)' },
    { value: 'high', label: 'High Quality', steps: 40, guidance: 1.0, description: 'Official pipeline range (40-50 steps), slower' },
  ],
  sdxl: [
    { value: 'fast', label: 'Fast', steps: 20, guidance: 6.0, description: 'Quick SDXL' },
    { value: 'standard', label: 'Standard', steps: 25, guidance: 7.0, description: 'Balanced SDXL' },
    { value: 'high', label: 'High Quality', steps: 35, guidance: 7.5, description: 'Final SDXL' },
  ],
  sd: [
    { value: 'fast', label: 'Fast', steps: 15, guidance: 7.0, description: 'Quick generation, good for testing' },
    { value: 'standard', label: 'Standard', steps: 20, guidance: 7.5, description: 'Balanced quality and speed' },
    { value: 'high', label: 'High Quality', steps: 30, guidance: 8.0, description: 'High quality, slower generation' },
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
  const size = m.size_gb ? ` ~${m.size_gb} GB` : '';
  return {
    value: m.id,
    label: m.recommended ? `${m.label} ⭐` : m.label,
    description: installed
      ? (m.description || '')
      : `${m.description || ''} (not installed — Install downloads${size})`.trim(),
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
