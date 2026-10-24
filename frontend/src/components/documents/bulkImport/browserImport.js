// Bulk Import from files the browser hands over (a drop, or Choose folder /
// Choose files). A browser never says where a dropped folder lives on disk, so
// these files are uploaded as copies through /files/upload, which keeps the
// folder structure the same way the Files page drop does.
//
// The rules match a server-folder import (scan_import_source in
// backend/celery_tasks_isolated.py): nothing inside a hidden folder, no hidden
// file, no credential or secret file. They are applied here on the names as
// dropped, and each upload sends import_rules=bulk so the server applies them
// again before secure_filename turns ".env" into "env".

import {
  ensureFolderPath, filesFolderApi, isSensitiveName, SENSITIVE_PATTERNS, storedFolderName,
} from "../../../utils/droppedFiles";
import { translate } from "../../../i18n";

export { isSensitiveName, SENSITIVE_PATTERNS };

// The upload route's own limit (MAX_CONTENT_LENGTH, 100 MB for the whole
// request), less room for the form fields around the file.
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024 - 64 * 1024;

/** The skip counters, in the shape the server reports as `skipped_private`. */
export const emptySkipped = () => ({
  hidden_dirs: 0, hidden_files: 0, sensitive_files: 0, symlinks: 0, internal_dirs: 0,
});

/**
 * Which rule keeps a file out, by its path relative to the import:
 * "hidden_dirs", "hidden_files", "sensitive_files", or null when it may come in.
 */
export const refusalKind = (relativePath) => {
  const parts = String(relativePath || "").split(/[\\/]+/).filter((p) => p && p !== ".");
  if (parts.length === 0) return "hidden_files";
  if (parts.slice(0, -1).some((p) => p.startsWith("."))) return "hidden_dirs";
  const name = parts[parts.length - 1];
  if (name.startsWith(".")) return "hidden_files";
  if (isSensitiveName(name)) return "sensitive_files";
  return null;
};

const readDirectory = async (dirEntry) => {
  const reader = dirEntry.createReader();
  const all = [];
  // readEntries returns batches (about 100 in Chrome) until an empty one.
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch || batch.length === 0) return all;
    all.push(...batch);
  }
};

// Like readEntryFiles in utils/droppedFiles.js, but a hidden folder is counted
// and never entered, as os.walk prunes it on the server: a dropped repository
// does not read every file under .git just to skip it.
const walkEntry = async (entry, basePath, out, skipped) => {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    out.push({ file, relativePath: basePath ? `${basePath}/${file.name}` : file.name });
    return;
  }
  if (!entry.isDirectory) return;
  if (entry.name.startsWith(".")) {
    skipped.hidden_dirs += 1;
    return;
  }
  const dirPath = basePath ? `${basePath}/${entry.name}` : entry.name;
  for (const child of await readDirectory(entry)) {
    await walkEntry(child, dirPath, out, skipped);
  }
};

/**
 * What a drop or a file picker brought, ready to upload: the files the rules
 * let in, what they kept out, and the files too large for one upload.
 *
 * @param {Array<{file: File, relativePath: string}>} candidates
 * @param {object} [skipped]  counts already made while walking
 */
export const stageFiles = (candidates, skipped = emptySkipped()) => {
  const files = [];
  const tooLarge = [];
  const hiddenDirs = new Set();
  let totalBytes = 0;
  for (const item of candidates) {
    const kind = refusalKind(item.relativePath);
    if (kind === "hidden_dirs") {
      // A file picker lists every file under a hidden folder; count the folder once.
      const parts = item.relativePath.split("/");
      const dir = parts.slice(0, parts.findIndex((p) => p.startsWith(".")) + 1).join("/");
      if (!hiddenDirs.has(dir)) {
        hiddenDirs.add(dir);
        skipped.hidden_dirs += 1;
      }
      continue;
    }
    if (kind) {
      skipped[kind] += 1;
      continue;
    }
    if (item.file.size > MAX_UPLOAD_BYTES) {
      tooLarge.push(item.relativePath);
      continue;
    }
    files.push(item);
    totalBytes += item.file.size;
  }
  return { files, skipped, tooLarge, totalBytes };
};

/** Stage a drop, from a snapshotDrop() taken inside the drop event. */
export const stageDrop = async (snapshot) => {
  const skipped = emptySkipped();
  const candidates = snapshot.looseFiles.map((file) => ({ file, relativePath: file.name }));
  for (const entry of snapshot.entries) {
    await walkEntry(entry, "", candidates, skipped);
  }
  if (candidates.length === 0 && snapshot.entries.length === 0) {
    candidates.push(...snapshot.fallbackFiles.map((file) => ({ file, relativePath: file.name })));
  }
  return stageFiles(candidates, skipped);
};

/** Stage the FileList of an <input type="file">, with or without webkitdirectory. */
export const stageFileList = (fileList) =>
  stageFiles(Array.from(fileList || []).map((file) => ({
    file,
    relativePath: file.webkitRelativePath || file.name,
  })));

/** The name a staged drop goes by on screen: the dropped folder, or a file count. */
export const stagedLabel = (files) => {
  const tops = new Set(files.map(({ relativePath }) => relativePath.split("/")[0]));
  if (tops.size === 1 && files[0]?.relativePath.includes("/")) return `"${[...tops][0]}"`;
  return translate("upload.bulk.fileCount", { count: files.length });
};

const dirOf = (relativePath) => {
  const i = relativePath.lastIndexOf("/");
  return i > 0 ? relativePath.slice(0, i) : "";
};

/**
 * Upload staged files one at a time under `baseFolder`, creating the folders a
 * dropped tree needs. A name already in its folder is left alone (the same rule
 * the server import follows), one failure does not stop the rest, and
 * `shouldStop()` is asked before each file.
 *
 * @param {object} args
 * @param {Array<{file: File, relativePath: string}>} args.files
 * @param {string} args.baseFolder   stored path of the folder to import into
 * @param {{client_id?, project_id?, website_id?}} args.links
 * @param {{get: Function, post: Function}} args.http  axios or compatible
 * @param {string} args.filesApiBase  e.g. "/api/files"
 * @param {function} [args.onProgress]  ({done, total, name})
 * @param {function} [args.shouldStop]
 * @returns {Promise<{imported, skipped, errors, queued_for_indexing, failures, stopped}>}
 */
export const uploadStaged = async ({
  files, baseFolder, links = {}, http, filesApiBase, onProgress, shouldStop,
}) => {
  const api = filesFolderApi(http, filesApiBase);
  const folderCache = new Map();
  const namesByFolder = new Map();
  const result = { imported: 0, skipped: 0, errors: 0, queued_for_indexing: 0, failures: [], stopped: false };

  const namesIn = async (folderPath) => {
    if (!namesByFolder.has(folderPath)) {
      const res = await http.get(`${filesApiBase}/browse`, { params: { path: folderPath, fields: "light" } });
      const docs = res.data?.data?.documents || [];
      namesByFolder.set(folderPath, new Set(docs.map((d) => d.filename)));
    }
    return namesByFolder.get(folderPath);
  };

  for (let i = 0; i < files.length; i += 1) {
    if (shouldStop?.()) {
      result.stopped = true;
      break;
    }
    const { file, relativePath } = files[i];
    onProgress?.({ done: i, total: files.length, name: file.name });
    try {
      const folderPath = await ensureFolderPath(dirOf(relativePath), baseFolder, api, folderCache);
      const existing = await namesIn(folderPath);
      if (existing.has(file.name) || existing.has(storedFolderName(file.name))) {
        result.skipped += 1;
        continue;
      }
      const form = new FormData();
      form.append("file", file);
      form.append("folder_path", folderPath);
      form.append("import_rules", "bulk");
      form.append("relative_path", relativePath);
      Object.entries(links).forEach(([key, value]) => {
        if (value) form.append(key, String(value));
      });
      const res = await http.post(`${filesApiBase}/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      result.imported += 1;
      if (res.data?.data?.job_id) result.queued_for_indexing += 1;
      existing.add(res.data?.data?.filename || file.name);
    } catch (err) {
      result.errors += 1;
      const reason = err.response?.data?.error?.message || err.response?.data?.message || err.message || translate("upload.bulk.uploadFailedReason");
      result.failures.push(`${relativePath}: ${reason}`);
    }
  }
  onProgress?.({ done: result.imported + result.skipped + result.errors, total: files.length, name: "" });
  return result;
};
