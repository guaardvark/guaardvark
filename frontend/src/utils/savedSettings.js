// A generation page's settings (model, size, quality and the rest), kept in
// this browser so the page opens the way it was left, across reloads and
// restarts. Prompts, picked files and seeds are per job and are not kept.

const PREFIX = "guaardvark.savedSettings.";

/** The saved settings for a page, or null when there are none. */
export function readSavedSettings(page) {
  try {
    const raw = window.localStorage.getItem(PREFIX + page);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

export function writeSavedSettings(page, values) {
  try {
    window.localStorage.setItem(PREFIX + page, JSON.stringify(values));
  } catch {
    // Storage blocked or full: the page still works, it only forgets on reload.
  }
}

export function clearSavedSettings(page) {
  try {
    window.localStorage.removeItem(PREFIX + page);
  } catch {
    // Nothing was kept.
  }
}

/** `saved[key]` when it has the type of `fallback`, else `fallback`. */
export function savedValue(saved, key, fallback) {
  const value = saved?.[key];
  if (value === undefined || value === null) return fallback;
  if (fallback !== null && fallback !== undefined && typeof value !== typeof fallback) return fallback;
  return value;
}
