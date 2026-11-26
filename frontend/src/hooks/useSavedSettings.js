// A page's remembered settings (utils/savedSettings) as two hooks: the values
// to open with, and saving them as they change.
import { useEffect, useState } from "react";

import { readSavedSettings, savedValue, writeSavedSettings } from "../utils/savedSettings";

/**
 * The settings to open the page with: each key of `defaults` holds the saved
 * value when one of the same type exists, else its default; `restored` is
 * true when anything was saved. Read once.
 */
export function useOpeningSettings(page, defaults) {
  const [opening] = useState(() => {
    const saved = readSavedSettings(page);
    // `restored` says whether the page has been used here before.
    const settings = { restored: Boolean(saved) };
    Object.entries(defaults).forEach(([key, fallback]) => {
      settings[key] = savedValue(saved, key, fallback);
    });
    return settings;
  });
  return opening;
}

/**
 * Saves `values` whenever one changes. Pass `ready` false until the page has
 * checked restored values against what is installed, so a value it is about
 * to replace is not written back first.
 */
export function useSaveSettings(page, values, ready = true) {
  const serialized = JSON.stringify(values);
  useEffect(() => {
    if (ready) writeSavedSettings(page, JSON.parse(serialized));
  }, [page, serialized, ready]);
}
