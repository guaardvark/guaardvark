// The failure record the backend attaches to a failed render or a refused
// request (backend/services/job_types.py describe_failure), as text.

import { formatUiError } from "./uiError";

/**
 * One line for a failure record: "Label: message Next: action".
 * @param {object} failure  {kind, label, message, action, retryable}
 * @returns {string}
 */
export function failureText(failure) {
  if (!failure) return "";
  let text = failure.message ? `${failure.label}: ${failure.message}` : failure.label || "";
  if (failure.action) text += ` Next: ${failure.action}`;
  if (failure.retryable) text += " (Retrying later can succeed.)";
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
