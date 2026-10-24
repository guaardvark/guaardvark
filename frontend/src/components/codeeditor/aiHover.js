// The editor's optional AI hover: explain the word under the pointer with the
// model. Off by default because every explanation is a model request (and a
// chat session on the backend); when on, each word on a line is asked once.

import { translate } from "../../i18n";

export const AI_HOVER_STORAGE_KEY = "codeEditor_aiHover";

export function readAiHoverPreference() {
  try {
    return localStorage.getItem(AI_HOVER_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeAiHoverPreference(enabled) {
  try {
    localStorage.setItem(AI_HOVER_STORAGE_KEY, enabled ? "true" : "false");
  } catch {
    // Preference is a convenience; the toggle still works for this page view.
  }
}

/**
 * Build Monaco's provideHover for the AI explanation.
 *
 * `isEnabled()` is read on every hover so a toggle takes effect at once.
 * `explain(context)` is the model call; its promise is cached per
 * language/line/word (in-flight requests included) so re-hovering, or several
 * registered providers, share one request. Cancelled hovers return null.
 */
export function createAiHoverProvider({ isEnabled, explain, getTab, cache = new Map(), maxEntries = 200 }) {
  return async (model, position, token) => {
    if (!isEnabled()) return null;
    const word = model.getWordAtPosition(position);
    if (!word) return null;

    const tab = getTab() || {};
    const language = tab.language || "javascript";
    const line = model.getLineContent(position.lineNumber);
    const key = `${language}\u0000${line}\u0000${word.word}`;

    let pending = cache.get(key);
    if (!pending) {
      pending = explain({
        content: line,
        language,
        filePath: tab.filePath || "untitled",
        word: word.word,
        position,
      }, `Explain this ${language} code: ${word.word}`).catch((error) => {
        cache.delete(key);
        throw error;
      });
      if (cache.size >= maxEntries) cache.delete(cache.keys().next().value);
      cache.set(key, pending);
    }

    let explanation;
    try {
      explanation = await pending;
    } catch (error) {
      console.error("Hover provider error:", error);
      return null;
    }
    if (token?.isCancellationRequested || !explanation?.success) return null;

    return {
      range: {
        startLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endLineNumber: position.lineNumber,
        endColumn: word.endColumn,
      },
      contents: [
        { value: `**${word.word}**` },
        { value: explanation.analysis || translate("codeAssistant.hover.unavailable") },
      ],
    };
  };
}
