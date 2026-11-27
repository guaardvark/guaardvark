// Where the Guaardvark WSL image comes from, and getting it onto disk.
//
// The image is a root filesystem tarball (packaging/wsl-image/) described by
// a manifest:
//
//   { "schema": 1, "version": "3.7.0", "arch": "x64", "format": "tar.gz",
//     "size": <bytes>, "sha256": "<whole image>", "installedSize": <bytes>,
//     "parts": [ { "name": "<file>", "size": <bytes>, "sha256": "<part>" } ] }
//
// GitHub release assets are limited to 2 GB each, so a larger image is
// published as parts that are joined in order. Part names are relative to
// the manifest's own location.
//
// Sources, first match wins:
//   1. GUAARDVARK_WSL_IMAGE: a local image file or manifest (offline installs).
//   2. Bundled: <resources>/wsl-image/manifest.json beside the app. The
//      installer carries no image today; adding the files there through
//      electron-builder's extraResources is all it takes.
//   3. Download: the manifest of this release on GitHub Releases. Nothing is
//      fetched until install() runs, which the app starts from its visible
//      Install step.

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');

const RELEASES = 'https://github.com/guaardvark/guaardvark/releases/download';

function releaseManifestUrl(version, arch = 'x64') {
  return `${RELEASES}/v${version}/guaardvark-wsl-${version}-${arch}.json`;
}

function pickFetch() {
  if (!process.versions.electron) return globalThis.fetch;
  try {
    // Electron's net.fetch goes through Chromium's network stack, which
    // honours the Windows proxy settings.
    const { net } = require('electron');
    if (net && typeof net.fetch === 'function') return net.fetch.bind(net);
  } catch { /* plain Node */ }
  return globalThis.fetch;
}

function localManifest(file) {
  if (/\.json$/i.test(file)) {
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
    return { kind: 'local', manifest, dir: path.dirname(file) };
  }
  const size = fs.statSync(file).size;
  return { kind: 'local', manifest: { schema: 1, size, parts: [{ name: path.basename(file), size }] }, dir: path.dirname(file) };
}

// Resolves where the image comes from without touching the network.
function locate({ env = process.env, resourcesPath, version }) {
  if (env.GUAARDVARK_WSL_IMAGE) return localManifest(env.GUAARDVARK_WSL_IMAGE);
  if (resourcesPath) {
    const bundled = path.join(resourcesPath, 'wsl-image', 'manifest.json');
    if (fs.existsSync(bundled)) return localManifest(bundled);
  }
  const url = env.GUAARDVARK_WSL_IMAGE_MANIFEST_URL || releaseManifestUrl(version);
  return { kind: 'download', url };
}

async function fetchManifest(url, fetchImpl = pickFetch()) {
  const res = await fetchImpl(url, { redirect: 'follow' });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`The image description could not be downloaded (HTTP ${res.status}).`);
  const manifest = await res.json();
  if (!Array.isArray(manifest.parts) || !manifest.parts.length) throw new Error('The image description lists no files.');
  return manifest;
}

async function hashFile(file, upTo = Infinity) {
  const hash = crypto.createHash('sha256');
  if (upTo > 0) {
    const stream = fs.createReadStream(file, upTo === Infinity ? {} : { end: upTo - 1 });
    for await (const chunk of stream) hash.update(chunk);
  }
  return hash;
}

// Downloads one part to dest, resuming a partial dest.part, and checks its
// size and sha256. onBytes(n) is called with each chunk's length.
async function downloadPart(url, dest, part, { fetchImpl = pickFetch(), onBytes, signal } = {}) {
  if (fs.existsSync(dest) && fs.statSync(dest).size === part.size) {
    if (!part.sha256 || (await hashFile(dest)).digest('hex') === part.sha256.toLowerCase()) {
      onBytes?.(part.size);
      return dest;
    }
    fs.rmSync(dest);
  }
  const partial = `${dest}.part`;
  let have = fs.existsSync(partial) ? fs.statSync(partial).size : 0;
  if (have > part.size) { fs.rmSync(partial); have = 0; }
  const hash = await hashFile(partial, have);
  const headers = have > 0 ? { Range: `bytes=${have}-` } : {};
  const res = await fetchImpl(url, { headers, redirect: 'follow', signal });
  if (have > 0 && res.status === 200) {
    // The server ignored the range; start over.
    fs.rmSync(partial);
    return downloadPart(url, dest, part, { fetchImpl, onBytes, signal });
  }
  if (!(res.status === 200 || res.status === 206)) throw new Error(`Download failed (HTTP ${res.status}) for ${path.basename(dest)}.`);
  onBytes?.(have);
  const out = fs.createWriteStream(partial, { flags: have > 0 ? 'a' : 'w' });
  try {
    for await (const chunk of Readable.fromWeb ? Readable.fromWeb(res.body) : res.body) {
      hash.update(chunk);
      if (!out.write(chunk)) await new Promise((r) => out.once('drain', r));
      onBytes?.(chunk.length);
    }
  } finally {
    await new Promise((r) => out.end(r));
  }
  const size = fs.statSync(partial).size;
  if (size !== part.size) throw new Error(`Download of ${path.basename(dest)} stopped early (${size} of ${part.size} bytes). Try again; it resumes where it stopped.`);
  const digest = hash.digest('hex');
  if (part.sha256 && digest !== part.sha256.toLowerCase()) {
    fs.rmSync(partial);
    throw new Error(`${path.basename(dest)} did not match its checksum and was deleted. Try again.`);
  }
  fs.renameSync(partial, dest);
  return dest;
}

// Checks the joined parts against the image's sha256 without writing them out.
async function verifyJoined(files, sha256, onBytes) {
  if (!sha256) return true;
  const hash = crypto.createHash('sha256');
  for (const file of files) {
    for await (const chunk of fs.createReadStream(file)) {
      hash.update(chunk);
      onBytes?.(chunk.length);
    }
  }
  return hash.digest('hex') === sha256.toLowerCase();
}

// One stream of the parts in order, for `wsl --import <name> <dir> -`.
function joinedStream(files, onBytes) {
  async function* chunks() {
    for (const file of files) {
      for await (const chunk of fs.createReadStream(file)) {
        onBytes?.(chunk.length);
        yield chunk;
      }
    }
  }
  return Readable.from(chunks());
}

function freeBytes(dir) {
  try {
    fs.mkdirSync(dir, { recursive: true });
    const s = fs.statfsSync(dir);
    return s.bavail * s.bsize;
  } catch {
    return null;
  }
}

module.exports = {
  RELEASES,
  releaseManifestUrl,
  locate,
  fetchManifest,
  downloadPart,
  verifyJoined,
  joinedStream,
  freeBytes,
  pickFetch,
};
