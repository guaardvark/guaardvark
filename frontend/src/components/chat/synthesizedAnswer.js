/**
 * chat:complete and the final ReACT step set synthesized: true when the
 * model spent its tool budget and the engine made one last call to answer
 * from the observations. Persisted history does not flatten that flag onto
 * extra_data; it lives on extra_data.steps[].synthesized (hydrated as
 * message.toolCalls). Live turns also set message.synthesized from the
 * complete payload.
 */

import { translate } from "../../i18n";

export const synthesizedLabel = () => translate("chat.synthesized.label");
export const synthesizedTooltip = () => translate("chat.synthesized.tooltip");

export function isSynthesizedStep(step) {
  return Boolean(step && step.synthesized === true);
}

export function stepsFromMessage(message) {
  if (!message) return [];
  if (Array.isArray(message.toolCalls) && message.toolCalls.length) return message.toolCalls;
  if (Array.isArray(message.extra_data?.steps)) return message.extra_data.steps;
  return Array.isArray(message.toolCalls) ? message.toolCalls : [];
}

export function isSynthesizedMessage(message) {
  if (!message) return false;
  if (message.synthesized === true) return true;
  if (message.extra_data?.synthesized === true) return true;
  return stepsFromMessage(message).some(isSynthesizedStep);
}
