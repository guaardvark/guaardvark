import { translate } from "../i18n";

/**
 * Coerce an API error payload to a string safe to render as a React child.
 *
 * The backend's standard error envelope is `error: {code, message, ...}`
 * (backend/utils/response_utils.py); rendering that object raw crashes React
 * with "Objects are not valid as a React child". Accepts strings, Error
 * instances, and nested {message|error|detail} shapes.
 *
 * @param {*} err - Anything an error handler might have captured.
 * @returns {string} A human-readable message, or "" for empty input.
 */
export function formatUiError(err) {
  if (err == null || err === "") return "";
  if (typeof err === "string") return err;
  if (typeof err === "number" || typeof err === "boolean") return String(err);
  if (typeof err === "object") {
    if (typeof err.message === "string" && err.message) return err.message;
    if (typeof err.error === "string" && err.error) return err.error;
    if (typeof err.error === "object" && err.error) return formatUiError(err.error);
    if (typeof err.detail === "string" && err.detail) return err.detail;
    try {
      return JSON.stringify(err);
    } catch {
      return translate("messages.uiError.generic");
    }
  }
  return String(err);
}
