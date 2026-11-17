// The Kokoro voice catalog as the voice pickers read it (GET /api/audio-foundry/voices),
// and what a stored voice id means when Film Crew renders a character's lines.
//
// A render sends a catalog voice to Audio Foundry and leaves out anything else
// (backend/services/swarm/clients.py, _builtin_voice): an empty id or "default"
// means Audio Foundry's automatic voice, and an id that is not in the catalog is
// dropped, so the automatic voice speaks instead.

/** The catalog's groups from a /voices answer, or null when it has none. */
export function catalogGroups(data) {
  const groups = data?.kokoro?.groups;
  return Array.isArray(groups) && groups.length > 0 ? groups : null;
}

/**
 * Map of voice id to `{id, label, group, installed}`, or null without groups.
 * A voice in a language that is not installed also carries `language` and
 * `install`, the Manage-models row `{id, name}` that installs it.
 */
export function indexVoices(groups) {
  if (!groups) return null;
  const index = new Map();
  groups.forEach((group) => {
    (group.voices || []).forEach((voice) => {
      index.set(voice.id, {
        ...voice,
        group: group.label,
        language: group.language,
        install: group.install || null,
      });
    });
  });
  return index;
}

/** True when the voice's language still has to be installed (Japanese, Mandarin). */
export const needsLanguage = (voice) => voice?.language_installed === false;

/**
 * What `voiceId` means for a render, given the catalog `index`:
 *   default        nothing set ("" or "default"): Audio Foundry's automatic voice
 *   installed      a catalog voice whose voice pack is on this machine
 *   not_installed  a catalog voice whose pack is not installed yet (Audio
 *                  Studio → Manage models); Audio Foundry refuses it until then
 *   language_not_installed  a catalog voice whose language is its own Install
 *                  in Manage models and is not installed; refused until then
 *   invalid        not a catalog voice; renders drop it and use the automatic voice
 *   unknown        the catalog could not be loaded, so nothing is claimed
 * A voice without an `installed` flag (an older plugin) counts as installed.
 */
export function voiceStatus(voiceId, index) {
  const id = (voiceId || "").trim();
  if (!id || id === "default") return { state: "default", id: "" };
  if (!index) return { state: "unknown", id };
  const voice = index.get(id);
  if (!voice) return { state: "invalid", id };
  if (needsLanguage(voice)) return { state: "language_not_installed", id, voice };
  return { state: voice.installed === false ? "not_installed" : "installed", id, voice };
}
