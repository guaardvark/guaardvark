// frontend/src/utils/videoBatchStamp.js
//
// The date line under a Video Library card. Cards are named after the first
// prompt, so two batches of the same prompt looked identical with a relative
// "5m ago" under both; the line now carries the date and the time, the way
// Image Gen's Recent Batches cards do (BatchImageGeneratorPage.formatImageDate).
import { uiLocale } from "../i18n";

/**
 * @param {string|null|undefined} isoStr
 * @param {Date} [now]  for tests
 * @returns {string}  "Oct 10 7:42 PM", with the year when it is not this one; "" when unknown
 */
export function formatVideoStamp(isoStr, now = new Date()) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.toLocaleDateString(uiLocale(), {
    month: "short",
    day: "numeric",
    year: d.getFullYear() !== now.getFullYear() ? "numeric" : undefined,
  });
  const time = d.toLocaleTimeString(uiLocale(), { hour: "2-digit", minute: "2-digit" });
  return `${day} ${time}`;
}
