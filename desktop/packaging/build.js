#!/usr/bin/env node
// Builds the Windows installer, Guaardvark-Setup-<version>-x64.exe, on Linux
// or Windows. Unsigned.
//
//   cd desktop && npm install && node packaging/build.js [--out <dir>] [--wsl-manifest <file>]
//                                                        [--wsl-image <dir>] [--dir-only]
//
// <version> is the repository's VERSION file, so the installer, the app's
// About data and the WSL image it downloads all carry the same number.
//
// --wsl-manifest <file> is this release's WSL image manifest
// (guaardvark-wsl-<version>-x64.json from packaging/wsl-image). Its sizes go
// into the app, so the install plan can show the download size and check
// the free disk space before anything is fetched. Without it the plan shows
// no size and the space is checked once the download starts.
//
// --wsl-image <dir> bundles a WSL image (manifest.json plus the files it
// lists) as resources/wsl-image, and the app then installs from it with no
// download (platform/wsl/image.js). Without it the app downloads the image
// from this version's GitHub release when the person chooses Install. NSIS
// cannot hold more than 2 GB, so only a small image fits.
//
// No wine: electron-builder normally runs the new installer under wine to
// get its uninstaller out. It also has a reader that takes the uninstaller
// out of the installer file in JavaScript, which it uses on macOS; this
// script selects that reader on every platform.

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const desktop = path.resolve(__dirname, '..');
const repo = path.resolve(desktop, '..');

function option(name) {
  const at = process.argv.indexOf(`--${name}`);
  return at > 0 ? process.argv[at + 1] : null;
}

function guaardvarkVersion() {
  const file = path.join(repo, 'VERSION');
  const version = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim() : '';
  if (!/^\d+\.\d+\.\d+$/.test(version)) {
    console.error(`VERSION must hold a plain x.y.z version; found ${JSON.stringify(version)} in ${file}`);
    process.exit(2);
  }
  return version;
}

async function main() {
  const version = guaardvarkVersion();
  const out = path.resolve(option('out') || path.join(desktop, 'dist'));
  // Build temporaries (a few hundred MB) beside the output, not in a
  // RAM-backed /tmp. Set before electron-builder loads.
  if (!process.env.TMPDIR) {
    process.env.TMPDIR = path.join(out, '.tmp');
    fs.mkdirSync(process.env.TMPDIR, { recursive: true });
  }
  const builder = require('electron-builder');
  const macosVersion = require('app-builder-lib/out/util/macosVersion');
  if (process.platform !== 'win32') macosVersion.isMacOsCatalina = () => true;

  const image = option('wsl-image');
  const manifestFile = option('wsl-manifest');
  const extraResources = [];
  // author becomes the CompanyName in the .exe's properties.
  const extraMetadata = { version, guaardvarkVersion: version, author: { name: 'Guaardvark' } };
  if (manifestFile) {
    const m = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
    if (m.version !== version) {
      console.error(`${manifestFile} is for version ${m.version}; VERSION is ${version}.`);
      process.exit(2);
    }
    extraMetadata.wslImage = { downloadBytes: m.parts.reduce((n, p) => n + p.size, 0), installedBytes: m.installedSize || null };
  } else if (!image) {
    console.warn('No --wsl-manifest: the install plan will not show the download size or check disk space up front.');
  }
  if (image) {
    const manifest = JSON.parse(fs.readFileSync(path.join(image, 'manifest.json'), 'utf8'));
    extraResources.push({ from: path.resolve(image), to: 'wsl-image' });
    extraMetadata.wslImageBytes = manifest.parts.reduce((n, p) => n + p.size, 0);
  }

  const files = await builder.build({
    projectDir: desktop,
    targets: builder.Platform.WINDOWS.createTarget(process.argv.includes('--dir-only') ? 'dir' : 'nsis', builder.Arch.x64),
    config: {
      extends: path.join(__dirname, 'electron-builder.yml'),
      directories: { output: out, buildResources: 'build' },
      extraMetadata,
      extraResources,
    },
    publish: 'never',
  });

  for (const file of files) {
    if (!file.endsWith('.exe') || file.includes('__uninstaller')) {
      console.log(`built: ${file}`);
      continue;
    }
    const hash = crypto.createHash('sha256');
    for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
    const digest = hash.digest('hex');
    fs.writeFileSync(`${file}.sha256`, `${digest}  ${path.basename(file)}\n`);
    console.log(`built: ${file}`);
    console.log(`size: ${fs.statSync(file).size} bytes`);
    console.log(`sha256: ${digest}`);
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
