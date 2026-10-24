// Reading files and folders dropped from the operating system.
//
// The browser empties DataTransfer.items as soon as the drop handler yields, so
// a handler that awaits between items only ever sees the first one. Take the
// snapshot synchronously inside the event, then walk it asynchronously.

/**
 * Capture every entry and file a drop carries, without awaiting.
 *
 * @param {DataTransfer} dataTransfer The drop event's dataTransfer.
 * @returns {{entries: FileSystemEntry[], looseFiles: File[], fallbackFiles: File[]}}
 *   `entries` for items the browser exposes as file-system entries (files and
 *   folders), `looseFiles` for file items without one, and `fallbackFiles`
 *   from `dataTransfer.files` for when neither yields anything.
 */
export function snapshotDrop(dataTransfer) {
  const entries = [];
  const looseFiles = [];
  const items = dataTransfer?.items;
  if (items) {
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      if (!item || item.kind !== 'file') continue;
      const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null;
      if (entry) {
        entries.push(entry);
      } else {
        const file = typeof item.getAsFile === 'function' ? item.getAsFile() : null;
        if (file) looseFiles.push(file);
      }
    }
  }
  const fallbackFiles = Array.from(dataTransfer?.files || []);
  return { entries, looseFiles, fallbackFiles };
}

// Mirrors SENSITIVE_PATTERNS in backend/utils/path_safety.py; matched against
// the lower-cased file name.
export const SENSITIVE_PATTERNS = [
  ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx",
  "*.ppk", "*.jks", "*.keystore",
  "id_rsa*", "id_dsa*", "id_ed25519*", "id_ecdsa*",
  ".netrc", ".pgpass", ".npmrc", ".pypirc",
  "credentials", "credentials.*", "mcp_servers.json", ".git-credentials",
  ".*secret*", "*.secret", "client_secret*.json",
];

const globToRegExp = (glob) =>
  new RegExp(`^${glob.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".")}$`);
const SENSITIVE_RES = SENSITIVE_PATTERNS.map(globToRegExp);

export const isSensitiveName = (name) => {
  const lower = String(name || "").toLowerCase();
  return SENSITIVE_RES.some((re) => re.test(lower));
};

// Mirror BUILD_DIR_NAMES and BUILD_ARTIFACT_EXTENSIONS in backend/utils/file_kinds.py.
export const BUILD_DIR_NAMES = new Set([
  "__pycache__", "node_modules", "venv", ".venv", "dist", ".git",
  ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".next",
]);
const BUILD_ARTIFACT_EXTENSIONS = new Set([
  ".pyc", ".pyo", ".class", ".o", ".obj", ".so", ".dll", ".dylib", ".a", ".lib", ".exe",
]);

/** A folder a folder import never enters: hidden, or generated or installed. */
export const isPrunedFolder = (name) => {
  const n = String(name || "");
  return n.startsWith(".") || BUILD_DIR_NAMES.has(n);
};

/** A file a folder import never takes: hidden, a credential, or compiled output. */
export const isPrunedFile = (name) => {
  const n = String(name || "");
  const dot = n.lastIndexOf(".");
  const ext = dot > 0 ? n.slice(dot).toLowerCase() : "";
  return n.startsWith(".") || isSensitiveName(n) || BUILD_ARTIFACT_EXTENSIONS.has(ext);
};

async function readAllDirectoryEntries(dirEntry) {
  const reader = dirEntry.createReader();
  const all = [];
  // readEntries returns batches (about 100 in Chrome) until an empty one.
  for (;;) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch || batch.length === 0) return all;
    all.push(...batch);
  }
}

/**
 * Every file under a dropped entry, with its path relative to the drop.
 *
 * A dropped folder `Photos` holding `a.jpg` yields `Photos/a.jpg`; a dropped
 * file yields its bare name.
 *
 * @param {FileSystemEntry} entry
 * @param {string} [basePath]
 * @returns {Promise<Array<{file: File, relativePath: string}>>}
 */
export async function readEntryFiles(entry, basePath = '', prune = null) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    if (prune && isPrunedFile(file.name)) {
      prune.files += 1;
      return [];
    }
    return [{ file, relativePath: basePath ? `${basePath}/${file.name}` : file.name }];
  }
  if (!entry.isDirectory) return [];
  if (prune && isPrunedFolder(entry.name)) {
    prune.folders += 1;
    return [];
  }
  const dirPath = basePath ? `${basePath}/${entry.name}` : entry.name;
  const out = [];
  for (const child of await readAllDirectoryEntries(entry)) {
    out.push(...(await readEntryFiles(child, dirPath, prune)));
  }
  return out;
}

/**
 * Resolve a snapshot from {@link snapshotDrop} into files with relative paths.
 *
 * With `prune` (an object `{folders: 0, files: 0}` that is counted into), the
 * walk leaves out what a folder import never takes: hidden and dependency or
 * build folders (`.git`, `node_modules`, `__pycache__`, `venv`, `dist`, ...),
 * hidden files, credential files and compiled output. A pruned folder is never
 * entered.
 *
 * @param {ReturnType<typeof snapshotDrop>} snapshot
 * @param {{prune?: {folders: number, files: number}}} [options]
 * @returns {Promise<Array<{file: File, relativePath: string}>>}
 */
export async function collectDroppedFiles(snapshot, { prune = null } = {}) {
  const out = [];
  for (const file of snapshot.looseFiles) {
    if (prune && isPrunedFile(file.name)) prune.files += 1;
    else out.push({ file, relativePath: file.name });
  }
  for (const entry of snapshot.entries) {
    out.push(...(await readEntryFiles(entry, '', prune)));
  }
  if (out.length === 0 && !(prune && (prune.folders || prune.files))) {
    return snapshot.fallbackFiles.map((file) => ({ file, relativePath: file.name }));
  }
  return out;
}

/** "left out 2 folders and 5 files (hidden, build output or credentials)" */
export function prunedNote(prune) {
  const parts = [];
  if (prune?.folders) parts.push(`${prune.folders} folder${prune.folders === 1 ? '' : 's'}`);
  if (prune?.files) parts.push(`${prune.files} file${prune.files === 1 ? '' : 's'}`);
  return parts.length ? `left out ${parts.join(' and ')} (hidden, build output or credentials)` : '';
}

/** True when a drag carries files from outside the page. */
export function dragCarriesFiles(event) {
  return Array.from(event?.dataTransfer?.types || []).includes('Files');
}

/**
 * The name the server stores a new folder under.
 *
 * Mirrors werkzeug's `secure_filename`, which the folder route applies: accents
 * folded to ASCII, runs of whitespace joined with `_`, anything outside
 * `A-Za-z0-9_.-` dropped, and leading or trailing `.`/`_` trimmed. "Weekend in
 * Lisbon" is stored as "Weekend_in_Lisbon".
 *
 * @param {string} name
 * @returns {string}
 */
export function storedFolderName(name) {
  return String(name ?? '')
    .normalize('NFKD')
    .replace(/[^ -~\t\n\r\v\f]/g, '')
    .replace(/\//g, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .join('_')
    .replace(/[^A-Za-z0-9_.-]/g, '')
    .replace(/^[._]+|[._]+$/g, '');
}

/**
 * Folder calls on the Files API, in the shape {@link ensureFolderPath} takes.
 *
 * @param {{get: Function, post: Function}} http  axios or a compatible client
 * @param {string} filesApiBase  e.g. "/api/files"
 */
export function filesFolderApi(http, filesApiBase) {
  return {
    listFolders: async (path) => {
      const res = await http.get(`${filesApiBase}/browse`, { params: { path, fields: 'light' } });
      return res.data?.data?.folders || [];
    },
    createFolder: async (name, parentPath) => {
      const res = await http.post(`${filesApiBase}/folder`, { name, parent_path: parentPath });
      return res.data?.data;
    },
  };
}

const isConflict = (err) => err?.response?.status === 409;

/**
 * Make sure a nested folder path exists under `baseFolder`, creating what is
 * missing, and return the stored path of the deepest folder.
 *
 * Existing folders are matched by their dropped name or by the name the server
 * stores it under, so every file of a dropped "Weekend in Lisbon" lands in the
 * same "Weekend_in_Lisbon" folder. Pass one `cache` Map for a whole drop so each
 * folder is looked up once.
 *
 * @param {string} relativePath  folder path relative to `baseFolder`, `/`-separated
 * @param {string} baseFolder    stored path to start from ("/" for the root)
 * @param {{listFolders: Function, createFolder: Function}} api  see {@link filesFolderApi}
 * @param {Map<string, string>} [cache]
 * @returns {Promise<string>}
 */
export async function ensureFolderPath(relativePath, baseFolder, api, cache = new Map()) {
  if (!relativePath || relativePath === '/') return baseFolder;
  let current = baseFolder;
  for (const part of relativePath.split('/').filter(Boolean)) {
    const key = `${current}\n${part}`;
    if (cache.has(key)) {
      current = cache.get(key);
      continue;
    }
    const stored = storedFolderName(part);
    const find = (folders) => folders.find((f) => f.name === part || (stored && f.name === stored));
    let folder = find(await api.listFolders(current));
    if (!folder) {
      try {
        folder = await api.createFolder(part, current);
      } catch (err) {
        if (!isConflict(err)) throw err;
        folder = find(await api.listFolders(current));
        if (!folder) throw err;
      }
    }
    cache.set(key, folder.path);
    current = folder.path;
  }
  return current;
}
