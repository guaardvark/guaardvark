// The interface's language. Strings live in src/locales/<language>/<area>.json:
// a file per area of the interface, the same files in every language, keyed
// as t("<area>.<key>"). English ships with the page and stands in for any
// string a language lacks; another language loads when it is picked. A
// language is offered as soon as its folder exists, so adding one is adding
// files (see docs/LOCALIZATION.md and scripts/translate_locales.py).
import i18n from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";

export { default as Trans } from "./Trans";

const ENGLISH_FILES = import.meta.glob("../locales/en/*.json", { eager: true, import: "default" });
// _sources.json is the translation script's record, not strings.
const OTHER_FILES = import.meta.glob(
  ["../locales/*/*.json", "!../locales/en/*.json", "!../locales/*/_*.json"],
  { import: "default" },
);

const areaOf = (path) => path.split("/").pop().replace(/\.json$/, "");
const languageOf = (path) => path.split("/").slice(-2)[0];

// Right-to-left scripts need the layout mirrored before they can be offered.
const RIGHT_TO_LEFT = new Set(["ar", "fa", "he", "ur"]);

export const LANGUAGE_STORAGE_KEY = "guaardvark_uiLanguage";

/** Languages the picker offers, English first. */
export const SUPPORTED_LANGUAGES = Object.freeze([
  "en",
  ...[...new Set(Object.keys(OTHER_FILES).map(languageOf))]
    .filter((code) => !RIGHT_TO_LEFT.has(code.split("-")[0]))
    .sort(),
]);

export function normalizeLanguage(language) {
  return SUPPORTED_LANGUAGES.includes(language) ? language : "en";
}

/** A language's own name for itself: "Deutsch", "日本語". */
export function languageName(code) {
  try {
    const name = new Intl.DisplayNames([code], { type: "language" }).of(code);
    return name ? name.charAt(0).toLocaleUpperCase(code) + name.slice(1) : code;
  } catch {
    return code;
  }
}

function readStoredLanguage() {
  try {
    return normalizeLanguage(localStorage.getItem(LANGUAGE_STORAGE_KEY));
  } catch {
    return "en";
  }
}

const english = Object.fromEntries(
  Object.entries(ENGLISH_FILES).map(([path, strings]) => [areaOf(path), strings]),
);

i18n.use(initReactI18next).init({
  resources: { en: { translation: english } },
  lng: "en",
  fallbackLng: "en",
  initAsync: false,
  interpolation: { escapeValue: false },
  returnNull: false,
  returnEmptyString: false,
  react: { useSuspense: false },
  saveMissing: import.meta.env.DEV,
  missingKeyHandler: (_languages, _namespace, key) => {
    console.warn(`[i18n] no English string for "${key}"`);
  },
});

const loaded = new Set(["en"]);

async function loadLanguage(code) {
  if (loaded.has(code)) return;
  const files = Object.entries(OTHER_FILES).filter(([path]) => languageOf(path) === code);
  const areas = await Promise.all(
    files.map(async ([path, load]) => [areaOf(path), await load()]),
  );
  for (const [area, strings] of areas) {
    i18n.addResourceBundle(code, "translation", { [area]: strings }, true, true);
  }
  loaded.add(code);
}

function updateDocumentLanguage(language) {
  if (typeof document !== "undefined") {
    document.documentElement.lang = language || "en";
  }
}

i18n.on("languageChanged", updateDocumentLanguage);
updateDocumentLanguage("en");

let requested = "en";

async function applyLanguage(language) {
  const next = normalizeLanguage(language);
  requested = next;
  await loadLanguage(next);
  // A pick made while this one loaded wins, however the loads finish.
  if (requested !== next) return;
  await i18n.changeLanguage(next);
}

/** Switch the interface language and remember it in this browser. */
export async function setLanguage(language) {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, normalizeLanguage(language));
  } catch {
    // Storage blocked: the switch lasts until the page reloads.
  }
  return applyLanguage(language);
}

const stored = readStoredLanguage();
if (stored !== "en") {
  applyLanguage(stored).catch((error) => console.warn("[i18n] could not load", stored, error));
}

if (typeof window !== "undefined") {
  // Follow a switch made in another tab; a null key means storage was cleared.
  window.addEventListener("storage", (event) => {
    if (event.key === LANGUAGE_STORAGE_KEY || event.key === null) {
      applyLanguage(event.key === null ? "en" : event.newValue).catch(() => {});
    }
  });
}

/** t() for code outside components (constants, services, toasts). Stable, so a
 * callback that holds it does not restart when the language changes. */
export const translate = (key, options) => i18n.t(key, options);

/** For components: re-renders on a language change and hands back the stable
 * translate(), so memoized callbacks keep their identity. */
export function useUiTranslation() {
  const { i18n: instance } = useTranslation();
  return { t: translate, language: instance.resolvedLanguage || "en" };
}

/** The locale dates and numbers are written in: the browser's own while the
 * interface is English, so an English page keeps today's formats. */
export function uiLocale() {
  const language = i18n.resolvedLanguage || "en";
  return language === "en" ? undefined : language;
}

export function formatNumber(value, options) {
  return new Intl.NumberFormat(uiLocale(), options).format(value);
}

export function formatDateTime(value, options) {
  return new Intl.DateTimeFormat(uiLocale(), options).format(new Date(value));
}

export default i18n;
