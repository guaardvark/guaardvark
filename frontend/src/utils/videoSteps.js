// frontend/src/utils/videoSteps.js
//
// Where the step count on the Video Generator comes from, so the Steps chips
// and the Steps tile say the same thing the render does.
//
// A model that declares speed profiles (registry `capabilities.speed_profiles`)
// renders at the active profile's own step count: a turbo LoRA is trained for
// its steps, and the quality preset's number is not used. The chips are then
// disabled with that reason instead of staying live and labelling the tile.
// A step count a person typed in Advanced still wins over the profile.
import { translate } from "../i18n";

/**
 * @param {object} args
 * @param {{steps?: number, label?: string}|null} [args.profileSpec]  the active speed profile
 * @param {boolean} [args.stepsTyped]  a step count typed in Advanced
 * @returns {{fixed: boolean, steps: number|null, label: string}}
 */
export function stepsFromProfile({ profileSpec, stepsTyped = false } = {}) {
  const steps = Number(profileSpec?.steps);
  if (!profileSpec || !Number.isFinite(steps) || steps <= 0 || stepsTyped) {
    return { fixed: false, steps: null, label: "" };
  }
  const label = String(profileSpec.label || "").trim();
  // Profile labels carry the count ("Turbo (8 steps)"); add it when one does not.
  const shown = label && /\d+\s*steps?/i.test(label) ? label : translate("messages.videoSteps.profileSteps", { label: label || translate("messages.videoSteps.speedProfile"), steps });
  return { fixed: true, steps, label: shown };
}

/**
 * The line under the Steps tile.
 *
 * @param {object} args
 * @param {boolean} args.lowVramClamp  Low VRAM is on AND the clamp applies to this model
 * @param {{fixed: boolean, label: string}} args.profile  from stepsFromProfile
 * @param {string} [args.presetLabel]  the selected quality chip's label
 */
export function stepsTileNote({ lowVramClamp, profile, presetLabel }) {
  if (lowVramClamp) return translate("messages.videoSteps.lowVramClamp");
  if (profile?.fixed) return translate("messages.videoSteps.setBy", { label: profile.label });
  return presetLabel || translate("messages.videoSteps.preset");
}
