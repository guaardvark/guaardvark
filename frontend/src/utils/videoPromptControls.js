// frontend/src/utils/videoPromptControls.js
//
// Whether the Motion chips and the Prompt style on the Video Generator reach
// the render, so each control says when it does nothing.
//
// What the render does (backend):
// - Text and Image input: the prompt enhancer is the only reader of the motion
//   strength and the style, and it runs only with Enhance prompt on and
//   Settings > Verbatim prompts off (comfyui_video_generator). The batch
//   worker also turns the Director off under Verbatim; the Director and the
//   Storyboard step read the style on their own, Storyboard even under Verbatim
//   (batch_video_generator).
// - References input: the board compiles before queuing when Enhance prompt is
//   on; the compiler keeps the motion strength under Verbatim and drops the
//   style under Verbatim or Exact text (batch_video_generation_api).

import { translate } from "../i18n";

/**
 * @param {object} args
 * @param {boolean} args.enhancePrompt
 * @param {boolean} args.verbatimPrompts  Settings > Verbatim prompts, as the backend reports it
 * @param {"text"|"image"|"references"} args.inputMode
 * @param {boolean} [args.fidelityMode]  the Exact text chip
 * @param {boolean} [args.directorMode]
 * @param {boolean} [args.storyboardMode]
 * @returns {{motion: {active: boolean, note: string}, style: {active: boolean, note: string}}}
 */
export function promptControlsState({
  enhancePrompt,
  verbatimPrompts,
  inputMode,
  fidelityMode = false,
  directorMode = false,
  storyboardMode = false,
}) {
  const references = inputMode === "references";

  let motion;
  if (!enhancePrompt) motion = { active: false, note: translate("messages.videoPromptControls.enhanceOff") };
  else if (verbatimPrompts && !references) motion = { active: false, note: translate("messages.videoPromptControls.verbatim") };
  else motion = { active: true, note: references ? translate("messages.videoPromptControls.viaReferenceCompiler") : translate("messages.videoPromptControls.viaPromptEnhancer") };

  const storyboardReads = storyboardMode && inputMode === "text";
  const directorReads = directorMode && !verbatimPrompts && !references;
  const enhancerReads = enhancePrompt && !verbatimPrompts && !(references && fidelityMode);
  let style;
  if (enhancerReads || storyboardReads || directorReads) {
    style = { active: true, note: "" };
  } else if (verbatimPrompts) {
    style = { active: false, note: translate("messages.videoPromptControls.styleVerbatim") };
  } else if (!enhancePrompt) {
    style = { active: false, note: translate("messages.videoPromptControls.styleEnhanceOff") };
  } else {
    style = { active: false, note: translate("messages.videoPromptControls.styleExactText") };
  }
  return { motion, style };
}
