// The failure record the backend attaches to a failed render or a refused
// request (backend/services/job_types.py describe_failure), as text.

import { formatUiError } from "./uiError";
import { translate } from "../i18n";

/**
 * One line for a failure record: "Label: message Next: action".
 * @param {object} failure  {kind, label, message, action, retryable}
 * @returns {string}
 */
export function failureText(failure) {
  if (!failure) return "";
  let text = failure.message ? `${failure.label}: ${failure.message}` : failure.label || "";
  if (failure.action) text = translate("messages.renderFailure.withNext", { text, action: failure.action });
  if (failure.retryable) text = translate("messages.renderFailure.retryable", { text });
  return text;
}

/**
 * The failure a refused request carries (error.details.failure), else its message.
 * @param {object} body  a failed API response body
 * @returns {string}
 */
export function refusalText(body) {
  return failureText(body?.error?.details?.failure) || formatUiError(body?.error || body?.message);
}
