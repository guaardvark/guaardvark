/**
 * Words for the voice session's state, shared by the mic buttons, their
 * popover and the Voice page.
 */
import { translate } from "../i18n";

// Getters, so each read follows the interface's language.
const CLICK_HINT = {
  get handsfree() { return translate("messages.voicePhase.clickHint.handsfree"); },
  get toggle() { return translate("messages.voicePhase.clickHint.toggle"); },
  get push() { return translate("messages.voicePhase.clickHint.push"); },
};

/**
 * @param {object} state voice session store state
 * @param {{systemName?: string, mode?: string}} context
 * @returns {{title: string, hint: string}}
 */
export function describeVoicePhase(state, { systemName = "Guaardvark", mode = "handsfree" } = {}) {
  const s = state || {};
  switch (s.phase) {
    case "requesting-permission":
      return { title: translate("messages.voicePhase.requestingPermission.title"), hint: translate("messages.voicePhase.requestingPermission.hint") };
    case "listening":
      return s.listenMode === "passive"
        ? { title: translate("messages.voicePhase.waitingForWake.title", { systemName }), hint: translate("messages.voicePhase.waitingForWake.hint") }
        : { title: translate("messages.voicePhase.listening.title"), hint: translate("messages.voicePhase.listening.hint") };
    case "capturing":
      return s.push
        ? { title: translate("messages.voicePhase.recording.title"), hint: translate("messages.voicePhase.recording.hint") }
        : { title: translate("messages.voicePhase.hearing.title"), hint: translate("messages.voicePhase.hearing.hint") };
    case "transcribing":
      return { title: translate("messages.voicePhase.transcribing"), hint: "" };
    case "sending":
      return { title: translate("messages.voicePhase.sending"), hint: "" };
    case "responding":
      return { title: translate("messages.voicePhase.responding.title"), hint: s.session ? translate("messages.voicePhase.responding.hint") : "" };
    case "speaking":
      return { title: translate("messages.voicePhase.speaking.title"), hint: translate("messages.voicePhase.speaking.hint") };
    case "denied":
      return { title: translate("messages.voicePhase.denied"), hint: s.error || "" };
    case "error":
      return { title: translate("messages.voicePhase.error"), hint: s.error || "" };
    default:
      return { title: translate("messages.voicePhase.micOff"), hint: CLICK_HINT[mode] || CLICK_HINT.handsfree };
  }
}

export const OUTCOME_LABELS = Object.freeze({
  get sent() { return translate("messages.voicePhase.outcomes.sent"); },
  get wake() { return translate("messages.voicePhase.outcomes.wake"); },
  get ignored() { return translate("messages.voicePhase.outcomes.ignored"); },
  get unsent() { return translate("messages.voicePhase.outcomes.unsent"); },
  get failed() { return translate("messages.voicePhase.outcomes.failed"); },
});
