// frontend/src/utils/videoPreviewPrompts.js
//
// The prompts "Preview enhanced" sends to /batch-video/enhance-preview: the
// same strings Add to queue sends, one per clip, so the preview shows what
// will be sent rather than the first line alone. Mirrors handleGenerate on
// VideoGeneratorPage: Text input sends one prompt per line with Look & Feel
// appended to each; Image and References input send the one description,
// with Look & Feel appended when there is one.
import { translate } from "../i18n";

// The preview route takes one prompt per call; the enhancer is a pure
// function, so a batch is previewed line by line up to this many.
export const PREVIEW_LINE_CAP = 20;

/**
 * @param {object} args
 * @param {"text"|"image"|"references"} args.inputMode
 * @param {string} args.promptsText
 * @param {string} [args.lookAndFeel]
 * @param {boolean} [args.storyboardMode]  Text input: the box is one concept
 * @returns {{prompts: string[], total: number, capped: boolean}}
 */
export function promptsToPreview({ inputMode, promptsText, lookAndFeel = "", storyboardMode = false }) {
  const lf = (lookAndFeel || "").trim();
  if (inputMode === "text" && !storyboardMode) {
    const lines = (promptsText || "").split("\n").map((p) => p.trim()).filter(Boolean);
    const all = lf ? lines.map((p) => `${p}, ${lf}`) : lines;
    return { prompts: all.slice(0, PREVIEW_LINE_CAP), total: all.length, capped: all.length > PREVIEW_LINE_CAP };
  }
  const one = (promptsText || "").trim();
  if (!one) return { prompts: [], total: 0, capped: false };
  return { prompts: [lf ? `${one}, ${lf}` : one], total: 1, capped: false };
}

/**
 * The label over the preview box.
 *
 * @param {object} args
 * @param {number} args.shown  prompts previewed
 * @param {number} args.total  prompts the batch will send
 * @param {boolean} [args.verbatim]  Settings > Verbatim prompts is on
 * @param {boolean} [args.enhanceOff]  Enhance prompt is off
 * @param {boolean} [args.storyboard]  the Director will expand this concept
 */
export function previewLabel({ shown, total, verbatim = false, enhanceOff = false, storyboard = false }) {
  // Which "as typed" reason applies, if any: each label is one whole sentence.
  const mode = verbatim ? "Verbatim" : enhanceOff ? "EnhanceOff" : "Enhanced";
  if (storyboard) {
    if (mode === "Verbatim") return translate("messages.videoPreview.conceptVerbatim");
    if (mode === "EnhanceOff") return translate("messages.videoPreview.conceptEnhanceOff");
    return translate("messages.videoPreview.conceptEnhanced");
  }
  if (total <= 1) {
    if (mode === "Verbatim") return translate("messages.videoPreview.promptVerbatim");
    if (mode === "EnhanceOff") return translate("messages.videoPreview.promptEnhanceOff");
    return translate("messages.videoPreview.promptEnhanced");
  }
  const first = shown < total;
  if (mode === "Verbatim") {
    return first
      ? translate("messages.videoPreview.promptsVerbatimFirst", { shown, count: total })
      : translate("messages.videoPreview.promptsVerbatimAll", { count: total });
  }
  if (mode === "EnhanceOff") {
    return first
      ? translate("messages.videoPreview.promptsEnhanceOffFirst", { shown, count: total })
      : translate("messages.videoPreview.promptsEnhanceOffAll", { count: total });
  }
  return first
    ? translate("messages.videoPreview.promptsEnhancedFirst", { shown, count: total })
    : translate("messages.videoPreview.promptsEnhancedAll", { count: total });
}

/** One block per clip, numbered when there is more than one. */
export function joinPreviews(previews) {
  if (previews.length <= 1) return previews[0] || "";
  return previews.map((p, i) => `${i + 1}. ${p}`).join("\n\n");
}
