/**
 * US phone number display formatting.
 *
 * The phone columns are free text and hold everything from imported legacy
 * strings to international numbers, so nothing here rewrites what it cannot
 * confidently recognise: anything that is not a plain US 10- or 11-digit
 * number is passed through untouched.
 */

/** Digits only, for `tel:` hrefs and comparisons. Keeps a leading "+". */
export function phoneDigits(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  const plus = raw.startsWith("+") ? "+" : "";
  return plus + raw.replace(/\D/g, "");
}

/**
 * Format a stored phone number for display.
 *
 * @param {string} value
 * @returns {string} "(555) 123-4567", or `value` when it is not a US number
 */
export function formatPhone(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  // An explicit country code other than +1 is somebody else's format.
  if (raw.startsWith("+") && !raw.startsWith("+1")) return raw;
  const digits = raw.replace(/\D/g, "");
  const local = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits;
  if (local.length !== 10) return raw;
  return `(${local.slice(0, 3)}) ${local.slice(3, 6)}-${local.slice(6)}`;
}

/**
 * Format while the user types, so the field never fights the caret.
 *
 * Grows punctuation as digits arrive and stops at ten, but leaves anything
 * that starts with "+" alone so an international number stays typeable.
 *
 * @param {string} value — the raw input value
 * @returns {string}
 */
export function formatPhoneInput(value) {
  const raw = String(value ?? "");
  if (raw.trim().startsWith("+")) return raw;
  const digits = raw.replace(/\D/g, "");
  // A leading 1 is a country code, not the first digit of the area code.
  const local = digits.length > 10 && digits.startsWith("1") ? digits.slice(1) : digits;
  const capped = local.slice(0, 10);
  if (capped.length === 0) return "";
  if (capped.length < 4) return capped;
  if (capped.length < 7) return `(${capped.slice(0, 3)}) ${capped.slice(3)}`;
  return `(${capped.slice(0, 3)}) ${capped.slice(3, 6)}-${capped.slice(6)}`;
}
