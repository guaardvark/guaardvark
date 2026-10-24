/**
 * Speaking a whole chat reply: split it into short chunks so the first words
 * play soon, synthesize the next chunk while one plays, and turn a failure
 * into one plain sentence for the person.
 */
import { translate } from "../i18n";

const SENTENCE = /[^.!?]+[.!?]+["'’”)\]]*\s*|[^.!?]+$/g;

/**
 * Split cleaned text into chunks of up to `maxSentences` sentences and about
 * `maxChars` characters. A single sentence longer than `maxChars` stays whole:
 * the engines take long sentences, and a cut mid-sentence sounds wrong.
 */
export function chunkForSpeech(text, { maxSentences = 3, maxChars = 350 } = {}) {
  const cleaned = String(text || "").trim();
  if (!cleaned) return [];
  const sentences = (cleaned.match(SENTENCE) || [cleaned]).map((s) => s.trim()).filter(Boolean);
  const chunks = [];
  let current = [];
  let length = 0;
  for (const sentence of sentences) {
    const fits = current.length < maxSentences && length + sentence.length + 1 <= maxChars;
    if (current.length && !fits) {
      chunks.push(current.join(" "));
      current = [];
      length = 0;
    }
    current.push(sentence);
    length += sentence.length + 1;
  }
  if (current.length) chunks.push(current.join(" "));
  return chunks;
}

/**
 * Play `chunks` in order. `synthesize(chunk)` resolves to something `play`
 * accepts (an audio URL) and runs for the next chunk while the current one
 * plays; `isCancelled()` is checked between steps so a stop ends the reply.
 *
 * Resolves to {spoken, total, cancelled, error}: `spoken` counts chunks that
 * finished playing, and `error` is the failure that stopped the rest, if any.
 */
export async function speakInChunks(chunks, { synthesize, play, isCancelled = () => false }) {
  const total = chunks.length;
  let next = total ? synthesize(chunks[0]) : null;
  for (let i = 0; i < total; i++) {
    let audio;
    try {
      audio = await next;
    } catch (error) {
      return { spoken: i, total, cancelled: false, error };
    }
    if (isCancelled()) return { spoken: i, total, cancelled: true, error: null };
    next = i + 1 < total ? synthesize(chunks[i + 1]) : null;
    // The prefetch is awaited on the next turn; keep a rejection from being
    // reported as unhandled meanwhile.
    if (next) next.catch(() => {});
    if (audio) {
      try {
        await play(audio);
      } catch (error) {
        return { spoken: i, total, cancelled: false, error };
      }
    }
    if (isCancelled()) return { spoken: i + 1, total, cancelled: true, error: null };
  }
  return { spoken: total, total, cancelled: false, error: null };
}

// Voice settings are a dialog opened from the Chat section of Settings.
// Audio Foundry speaks a reply only with a Kokoro voice pack installed.
// The English text; describeSpeakError words it in the interface's language.
export const NO_VOICE_INSTALLED_MESSAGE =
  "No voice is installed, so replies can't be spoken. Install one in Voice Settings (Settings → Chat → Voice chat), or start the Audio Foundry plugin with a Kokoro voice pack installed.";

/**
 * One sentence for a failed reply, naming how far it got when it got anywhere.
 * Only the backend's own `no_voice_installed` code means no voice: a 503 can
 * also be a proxy or the task queue during a restart on a machine with voices.
 */
export function describeSpeakError(error, { spoken = 0, total = 0 } = {}) {
  if (error?.data?.code === "no_voice_installed") return translate("messages.replySpeech.noVoiceInstalled");
  const reason = error?.message;
  if (spoken > 0 && total > spoken) {
    return reason
      ? translate("messages.replySpeech.stoppedPartwayReason", { reason })
      : translate("messages.replySpeech.stoppedPartway");
  }
  return reason
    ? translate("messages.replySpeech.failedReason", { reason })
    : translate("messages.replySpeech.failed");
}
