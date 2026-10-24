/**
 * Voice settings: one set of defaults and one reader/writer.
 *
 * The voice session (global mic), Settings → Voice and the Voice page all read
 * from here, so a default cannot differ between the screen that shows it and
 * the code that uses it. Values persist in localStorage under
 * VOICE_SETTINGS_KEY; writers announce changes with VOICE_SETTINGS_EVENT
 * because the browser's storage event only reaches other tabs.
 */

import { translate } from "../i18n";

export const VOICE_SETTINGS_KEY = "guaardvark_voiceSettings";
export const VOICE_SETTINGS_EVENT = "voiceSettingsChanged";

/** What a click on a mic button does. Holding a mic button always talks. */
export const ACTIVATION_MODES = Object.freeze(["push", "toggle", "handsfree"]);
export const DEFAULT_ACTIVATION_MODE = "handsfree";
// Getters, so each read follows the interface's language.
export const ACTIVATION_MODE_LABELS = Object.freeze({
  get push() { return translate("options.voiceActivation.push.label"); },
  get toggle() { return translate("options.voiceActivation.toggle.label"); },
  get handsfree() { return translate("options.voiceActivation.handsfree.label"); },
});
export const ACTIVATION_MODE_HELP = Object.freeze({
  get push() { return translate("options.voiceActivation.push.help"); },
  get toggle() { return translate("options.voiceActivation.toggle.help"); },
  get handsfree() { return translate("options.voiceActivation.handsfree.help"); },
});

/**
 * Defaults for every voice setting.
 *
 * silenceThreshold is the RMS level (0-1) the vad-processor worklet reports.
 * 0.03 sits between the two values in use before (0.02 in the listener code,
 * 0.05 in Settings); it has not been measured against a live microphone, so
 * the Voice page meter draws the line for setting it by ear. silenceTimeout
 * and maxSegmentDuration are on the Settings sliders' 500 ms / 5 s steps; a
 * shorter maximum split long requests into separate chat turns.
 */
export const VOICE_DEFAULTS = Object.freeze({
  voice: "libritts",
  ttsEnabled: true,
  micEnabled: true,
  showNarrateButtons: true,
  recordingQuality: "medium",
  recordingVolume: 1.0,
  playbackVolume: 1.0,
  playbackSpeed: 1.0,
  maxRecordingDuration: 60,
  autoGainControl: true,
  noiseSuppression: true,
  echoCancellation: true,
  silenceThreshold: 0.03,
  silenceTimeout: 1500,
  maxSegmentDuration: 30000,
  minSpeechDuration: 300,
  wakeWordEnabled: false,
  activeListeningDuration: 30000,
  bargeIn: false,
});

/** Slider bounds shared by Settings → Voice and the Voice page. */
export const VOICE_LIMITS = Object.freeze({
  silenceThreshold: { min: 0.01, max: 0.2, step: 0.01 },
  silenceTimeout: { min: 1000, max: 5000, step: 500 },
  maxSegmentDuration: { min: 10000, max: 60000, step: 5000 },
  minSpeechDuration: { min: 100, max: 2000, step: 100 },
  activeListeningDuration: { min: 10000, max: 120000, step: 5000 },
});

const NUMERIC_KEYS = Object.keys(VOICE_DEFAULTS).filter(
  (key) => typeof VOICE_DEFAULTS[key] === "number"
);
const BOOLEAN_KEYS = Object.keys(VOICE_DEFAULTS).filter(
  (key) => typeof VOICE_DEFAULTS[key] === "boolean"
);

function clampToLimits(key, value) {
  const limits = VOICE_LIMITS[key];
  if (!limits) return value;
  return Math.min(limits.max, Math.max(limits.min, value));
}

/**
 * Stored settings over the defaults. A stored value of the wrong type falls
 * back to its default rather than reaching the audio code.
 */
export function normalizeVoiceSettings(stored) {
  const source = stored && typeof stored === "object" ? stored : {};
  const merged = { ...VOICE_DEFAULTS, ...source };
  for (const key of NUMERIC_KEYS) {
    const n = Number(merged[key]);
    merged[key] = Number.isFinite(n) ? clampToLimits(key, n) : VOICE_DEFAULTS[key];
  }
  for (const key of BOOLEAN_KEYS) {
    if (typeof merged[key] !== "boolean") merged[key] = VOICE_DEFAULTS[key];
  }
  return merged;
}

function storageOrNull() {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** Current voice settings, defaults filled in. Never throws. */
export function readVoiceSettings() {
  const storage = storageOrNull();
  try {
    const raw = storage?.getItem(VOICE_SETTINGS_KEY);
    return normalizeVoiceSettings(raw ? JSON.parse(raw) : {});
  } catch {
    return normalizeVoiceSettings({});
  }
}

/** Merge `patch` into the stored settings and tell same-tab readers. */
export function updateVoiceSettings(patch) {
  const next = { ...readVoiceSettings(), ...(patch || {}) };
  const storage = storageOrNull();
  try {
    storage?.setItem(VOICE_SETTINGS_KEY, JSON.stringify(next));
  } catch {
    // Storage full or blocked: the change still applies for this page view.
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(VOICE_SETTINGS_EVENT));
  }
  return normalizeVoiceSettings(next);
}

/**
 * VAD parameters for the voice session from a settings object.
 * hysteresis keeps an utterance going while the level dips below the start
 * threshold; startConfirmMs ignores single clicks and taps.
 */
export function vadConfigFrom(settings) {
  const s = normalizeVoiceSettings(settings);
  return {
    threshold: s.silenceThreshold,
    hysteresis: 0.6,
    startConfirmMs: 60,
    silenceMs: s.silenceTimeout,
    minSpeechMs: s.minSpeechDuration,
    maxSegmentMs: s.maxSegmentDuration,
  };
}

export const isActivationMode = (mode) => ACTIVATION_MODES.includes(mode);
