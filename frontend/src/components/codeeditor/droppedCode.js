// frontend/src/components/codeeditor/droppedCode.js
// Files dropped from the computer onto the Code Editor open as unsaved tabs.
// The browser hands over a copy only: Save keeps it with the editor's other
// browser-stored files, and the file on disk is never written.

import { translate } from "../../i18n";
import { collectDroppedFiles } from "../../utils/droppedFiles";
import { getLanguageFromFilename } from "../../utils/languageDetector";
import { TAB_SOURCE, TAB_LOAD } from "./tabOrigin";

// Saved tabs share the browser's localStorage, about 5 MB per site in all.
export const MAX_DROPPED_FILE_BYTES = 2 * 1024 * 1024;
// One drop opens at most this many tabs.
export const MAX_DROPPED_FILES = 20;

/** An editor tab holding a dropped file's text, unsaved until Save. */
export function droppedTab(name, text) {
  return {
    id: Math.random().toString(36).slice(2, 11),
    filePath: name,
    language: getLanguageFromFilename(name),
    source: TAB_SOURCE.LOCAL,
    isNew: true,
    content: text,
    // Nothing in Guaardvark holds this text yet, so Save has something to write.
    savedContent: null,
    loadState: TAB_LOAD.LOADED,
    loadError: null,
    isModified: true,
  };
}

/**
 * Read a drop into editor tabs.
 *
 * @param {ReturnType<import("../../utils/droppedFiles").snapshotDrop>} snapshot
 * @returns {Promise<{tabs: object[], skipped: Array<{name: string, reason: string}>}>}
 */
export async function readDroppedCodeFiles(snapshot) {
  const folders = snapshot.entries.filter((entry) => entry.isDirectory).map((entry) => entry.name);
  const files = await collectDroppedFiles({
    ...snapshot,
    entries: snapshot.entries.filter((entry) => !entry.isDirectory),
    // A dropped folder also shows up in `files`, as an empty File.
    fallbackFiles: folders.length ? [] : snapshot.fallbackFiles,
  });

  const skipped = folders.map((name) => ({ name, reason: translate("codeAssistant.drop.reasons.folder") }));
  const tabs = [];
  for (const { file } of files) {
    if (tabs.length >= MAX_DROPPED_FILES) {
      skipped.push({ name: file.name, reason: translate("codeAssistant.drop.reasons.tooMany", { max: MAX_DROPPED_FILES }) });
      continue;
    }
    if (file.size > MAX_DROPPED_FILE_BYTES) {
      skipped.push({ name: file.name, reason: translate("codeAssistant.drop.reasons.tooLarge", { size: MAX_DROPPED_FILE_BYTES / (1024 * 1024) }) });
      continue;
    }
    let text;
    try {
      text = await file.text();
    } catch {
      skipped.push({ name: file.name, reason: translate("codeAssistant.drop.reasons.unreadable") });
      continue;
    }
    // Images, archives, PDFs and other binaries carry NUL bytes; text does not.
    if (text.includes("\u0000")) {
      skipped.push({ name: file.name, reason: translate("codeAssistant.drop.reasons.notText") });
      continue;
    }
    tabs.push(droppedTab(file.name, text));
  }
  return { tabs, skipped };
}

/** The line shown after a drop: what opened, where Save puts it, and what was left out. */
export function dropNotice({ tabs, skipped }) {
  const parts = [];
  if (tabs.length) {
    const names = tabs.map((tab) => tab.filePath).join(", ");
    parts.push(translate("codeAssistant.drop.opened", { count: tabs.length, names }));
  }
  if (skipped.length) {
    const files = skipped
      .map((s) => translate("codeAssistant.drop.skippedFile", { name: s.name, reason: s.reason }))
      .join(", ");
    parts.push(translate("codeAssistant.drop.notOpened", { files }));
  }
  if (!parts.length) return null;
  return { severity: skipped.length ? "warning" : "info", text: parts.join(" ") };
}
