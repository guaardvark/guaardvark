// frontend/src/components/codeeditor/tabOrigin.js
// Where an editor tab came from decides where a save, rename or delete goes.
// The origin is the `source` tag set when the tab is opened (FileTreeCard,
// CodeEditorPage, WordPressPagesCard); it is never inferred from the file name,
// because a library document and a repo file can share one.

import { translate } from "../../i18n";

export const TAB_SOURCE = {
  // Files kept in the browser's localStorage (new tabs land here).
  LOCAL: "local",
  // A document in the library (Files page), addressed by its document id.
  DOCUMENT: "document",
  // A file under the repo root, addressed by its repo-relative path.
  FILESYSTEM: "filesystem",
  // A read-only view of the running checkout.
  LIVE_REPO: "live_repo",
  WORDPRESS: "wordpress",
};

// A tab's content is only trusted once it has loaded. Placeholder and error
// text never go into `content` (Save would write them over the real file);
// they are shown beside the editor from `loadState` and `loadError` instead.
export const TAB_LOAD = {
  LOADING: "loading",
  LOADED: "loaded",
  FAILED: "failed",
};

export const tabDisplayName = (tab) => (tab?.filePath || translate("codeEditor.tabs.untitled")).split("/").pop();

/** A tab whose content is still being fetched. */
export function loadingTab(tab) {
  return { ...tab, content: "", savedContent: null, loadState: TAB_LOAD.LOADING, loadError: null, isModified: false };
}

/** A tab whose content arrived; `savedContent` is what Save compares against. */
export function loadedTab(tab, content) {
  const text = typeof content === "string" ? content : "";
  return { ...tab, content: text, savedContent: text, loadState: TAB_LOAD.LOADED, loadError: null, isModified: false };
}

/** A tab whose content could not be fetched; it holds nothing and cannot be saved. */
export function failedTab(tab, error) {
  const message = (error && error.message) || (typeof error === "string" && error) || translate("codeEditor.tabs.loadFailed");
  return { ...tab, content: "", savedContent: null, loadState: TAB_LOAD.FAILED, loadError: message, isModified: false };
}

/** True when the tab holds something other than what was loaded or last saved. */
export function isDirty(tab) {
  if (!tab) return false;
  if (typeof tab.savedContent === "string") return tab.content !== tab.savedContent;
  // Tabs opened before load tracking existed carry no baseline; the modified
  // flag is the only signal they have.
  return Boolean(tab.isModified);
}

/**
 * Why a tab must not be saved right now, or null when saving is fine.
 * @returns {{reason: "none"|"loading"|"failed"|"clean", message: string}|null}
 */
export function saveRefusal(tab) {
  if (!tab) return { reason: "none", message: translate("codeEditor.tabs.noTabToSave") };
  const name = tabDisplayName(tab);
  const state = tab.loadState || TAB_LOAD.LOADED;
  if (state === TAB_LOAD.LOADING) {
    return { reason: "loading", message: translate("codeEditor.tabs.stillLoading", { name }) };
  }
  if (state === TAB_LOAD.FAILED) {
    return {
      reason: "failed",
      message: translate("codeEditor.tabs.notLoaded", {
        name,
        error: tab.loadError || translate("codeEditor.tabs.unknownError"),
      }),
    };
  }
  if (!isDirty(tab)) return { reason: "clean", message: translate("codeEditor.tabs.noChanges", { name }) };
  return null;
}

/**
 * Resolve the save target of a tab.
 * @returns {{kind: "none"|"readonly"|"error"|"document"|"repo"|"local", documentId?: number, path?: string|null, message?: string}}
 */
export function saveTargetFor(tab) {
  if (!tab) return { kind: "none" };
  if (tab.readOnly || tab.source === TAB_SOURCE.LIVE_REPO) {
    return {
      kind: "readonly",
      message: translate("codeEditor.tabs.liveRepoReadOnly"),
    };
  }
  if (tab.source === TAB_SOURCE.DOCUMENT) {
    if (tab.documentId === null || tab.documentId === undefined || tab.documentId === "") {
      return {
        kind: "error",
        message: translate("codeEditor.tabs.documentNoId"),
      };
    }
    return { kind: "document", documentId: tab.documentId };
  }
  if (tab.source === TAB_SOURCE.FILESYSTEM) {
    if (!tab.filePath || !String(tab.filePath).trim()) {
      return { kind: "error", message: translate("codeEditor.tabs.repoNoPath") };
    }
    return { kind: "repo", path: tab.filePath };
  }
  return { kind: "local", path: tab.filePath || null };
}
