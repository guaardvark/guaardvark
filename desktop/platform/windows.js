// Windows: Guaardvark runs inside its own WSL 2 distribution named
// "Guaardvark", driven through wsl.exe. Implements the interface in
// ./index.js, plus the Windows-only calls listed at the bottom (restart,
// keep-alive) that the window uses for its setup screens.
//
// Setup, in order (each step reports a plain-language progress event):
//   Windows build -> NVIDIA driver -> WSL present? (turn it on as
//   administrator; restart; carry on after the restart) -> the Guaardvark
//   distribution (import the image, or as a fallback install Ubuntu and run
//   the public installer inside it) -> first start -> GPU visible in Linux.
//
// Paths on the Windows side live under %LOCALAPPDATA%\Guaardvark:
//   wsl\         the distribution's disk (ext4.vhdx)
//   downloads\   image parts while they download
//   logs\        setup.log (every wsl.exe call) and start.log (start.sh output)
//   setup-state.json

const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const detect = require('./wsl/detect');
const image = require('./wsl/image');
const keepAlive = require('./wsl/wslconfig');
const { createRunner, defaultTools } = require('./wsl/exec');
const { runElevated } = require('./wsl/elevate');
const { classify, event } = require('./wsl/labels');
const { lines } = require('./wsl/decode');
const { createStateStore, systemBootTime, registerResume, clearResume, RESUME_FLAG } = require('./wsl/state');

const DISTRO = 'Guaardvark';
// Store WSL 2.x: systemd and GPU passthrough are both there.
const MIN_WSL = [2, 0, 0];
// `wsl --install <distro> --name --location` came with tar-based distributions.
const MIN_WSL_NAMED_INSTALL = [2, 4, 4];
// The default Ubuntu of `wsl --install`, the route proven on Windows 11.
const FALLBACK_DISTRO = 'Ubuntu';
const FALLBACK_USER = 'guaardvark';
const PUBLIC_INSTALLER = 'https://guaardvark.com/install.sh';
const DEFAULT_PORTS = { api: 5000, web: 5173 };

// Prints the default user, $HOME, the install root and whether start.sh is
// there. GUAARDVARK_ROOT lets an image put the checkout somewhere else.
const PROBE_SCRIPT = [
  'root="${GUAARDVARK_ROOT:-$HOME/guaardvark}"',
  'printf "%s\\n" "$(id -un)" "$HOME" "$root"',
  'if [ -x "$root/start.sh" ]; then echo has-start; else echo no-start; fi',
  'for k in FLASK_PORT VITE_PORT; do sed -n "s/^$k=\\([0-9]\\{1,5\\}\\).*/$k=\\1/p" "$root/.env" 2>/dev/null | tail -n 1; done',
].join('\n');

// Root steps for the Ubuntu fallback: a normal user with passwordless sudo
// (start.sh installs system packages and runs with no terminal to ask on),
// systemd on, and that user as the default.
const PREPARE_UBUNTU_SCRIPT = [
  'set -e',
  `id -u ${FALLBACK_USER} >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo ${FALLBACK_USER}`,
  `printf '%s ALL=(ALL) NOPASSWD:ALL\\n' ${FALLBACK_USER} > /etc/sudoers.d/90-guaardvark`,
  'chmod 0440 /etc/sudoers.d/90-guaardvark',
  `printf '[boot]\\nsystemd=true\\n\\n[user]\\ndefault=%s\\n' ${FALLBACK_USER} > /etc/wsl.conf`,
].join('\n');

// Streams the progress file and exits after the terminal line. tail stops
// on its own once this shell is gone (--pid).
const TAIL_SCRIPT = [
  'while IFS= read -r line; do',
  '  printf "%s\\n" "$line"',
  '  case $line in *\'"step"\'*\'"ready"\'*|*\'"step"\'*\'"failed"\'*) exit 0 ;; esac',
  'done < <(exec tail --pid=$$ -n +1 -F "$1" 2>/dev/null)',
].join('\n');

function restartRequiredError(evt) {
  const err = new Error(evt.detail || evt.label);
  err.code = 'RESTART_REQUIRED';
  err.event = evt;
  return err;
}

function failedError(evt) {
  const err = new Error(evt.detail || evt.label);
  err.code = 'FAILED';
  err.event = evt;
  return err;
}

// The packaging step stamps guaardvarkVersion (and wslImageBytes when it
// bundles an image) into the app's package.json; a checkout falls back to
// the repository's VERSION file.
function packageField(name) {
  try {
    return require('../package.json')[name] ?? null;
  } catch {
    return null;
  }
}

function readGuaardvarkVersion() {
  const stamped = packageField('guaardvarkVersion');
  if (stamped) return stamped;
  try {
    return fs.readFileSync(path.join(__dirname, '..', '..', 'VERSION'), 'utf8').trim();
  } catch {
    return null;
  }
}

function gb(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
}

function create(opts = {}) {
  const env = opts.env || {};
  const localAppData = opts.localAppData || process.env.LOCALAPPDATA || os.homedir();
  const userProfile = opts.userProfile || process.env.USERPROFILE || os.homedir();
  const appDir = path.join(localAppData, 'Guaardvark');
  const dirs = {
    distro: path.join(appDir, 'wsl'),
    downloads: path.join(appDir, 'downloads'),
    logs: path.join(appDir, 'logs'),
  };
  const setupLog = path.join(dirs.logs, 'setup.log');
  const startLog = path.join(dirs.logs, 'start.log');
  const tools = { ...defaultTools(), ...(opts.tools || {}) };
  const bootTime = opts.bootTime || systemBootTime;
  const exePath = opts.exePath || process.execPath;
  const osRelease = opts.osRelease || os.release();
  const resourcesPath = opts.resourcesPath !== undefined ? opts.resourcesPath : process.resourcesPath;
  const version = opts.guaardvarkVersion || readGuaardvarkVersion();
  const fetchImpl = opts.fetch || image.pickFetch();
  const store = createStateStore(path.join(appDir, 'setup-state.json'));

  function log(text) {
    try {
      fs.mkdirSync(dirs.logs, { recursive: true });
      fs.appendFileSync(setupLog, `[${new Date().toISOString()}] ${text}\n`);
    } catch { /* logging never stops setup */ }
  }

  const runner = createRunner({ tools, env, log });
  const session = { started: false, holder: null, tail: null, starter: null, lastLog: null };

  function uncPath(linuxPath) {
    return linuxPath ? `\\\\wsl.localhost\\${DISTRO}${linuxPath.replace(/\//g, '\\')}` : null;
  }

  function urls() {
    const st = store.read();
    const ports = { ...DEFAULT_PORTS, ...(st.ports || {}) };
    return {
      webUrl: st.webUrl || `http://localhost:${ports.web}`,
      apiUrl: st.apiUrl || `http://localhost:${ports.api}`,
    };
  }

  function inDistro(script, args = [], { user, timeoutMs, input, login = true } = {}) {
    return runner.run('wsl', ['-d', DISTRO, ...(user ? ['-u', user] : []), '--exec', 'bash', login ? '-lc' : '-c', script, 'bash', ...args], { timeoutMs, input });
  }

  function makeReporter(report) {
    return (evt) => {
      log(`event ${JSON.stringify(evt)}`);
      try { report?.(evt); } catch (e) { log(`report() threw: ${e.message}`); }
      return evt;
    };
  }

  function fail(emit, step, label, detail, extra = {}) {
    emit(event(step, 'fail', { label, detail }));
    const evt = emit(event('failed', 'fail', { label, detail, log: setupLog, ...extra }));
    throw failedError(evt);
  }

  function failFromWsl(emit, step, fallbackLabel, res) {
    const text = `${res.stdout || ''}\n${res.stderr || ''}\n${res.output || ''}`.trim();
    const known = classify(text);
    const detail = known ? known.detail : (lines(text).slice(-3).join(' ') || `wsl.exe exited with ${res.code}`);
    fail(emit, step, known ? known.label : fallbackLabel, detail, { code: res.code });
  }

  // ---------------------------------------------------------------- setup

  async function ensureWsl(emit) {
    emit(event('wsl_check', 'start'));
    let ws = await detect.wslState(runner);
    log(`wsl state: ${ws.state} version=${ws.version ? ws.version.join('.') : '-'} problem=${ws.problem ? ws.problem.kind : '-'}`);

    if (ws.state === 'broken' && ws.problem.kind === 'virtualization') {
      fail(emit, 'virtualization', ws.problem.label, ws.problem.detail);
    }

    if (ws.state === 'absent' || (ws.state === 'broken' && ws.problem.kind === 'not_installed')) {
      emit(event('wsl_check', 'info', { detail: 'Windows Subsystem for Linux is not turned on yet.' }));
      const virt = await detect.virtualization(runner);
      if (virt.known && !virt.ok) {
        emit(event('virtualization', 'info', {
          detail: 'Virtualization looks turned off in this PC\'s firmware. Guaardvark can turn on WSL now, but Linux will not start until virtualization is on: during the restart, open the firmware settings (BIOS/UEFI) and turn on Intel VT-x or AMD SVM.',
        }));
      }
      emit(event('wsl_enable', 'start', { detail: 'Windows will ask for permission to make changes. Choose Yes.' }));
      const res = await runElevated(runner, 'wsl', ['--install', '--no-distribution'], path.join(dirs.logs, 'wsl-install.txt'));
      log(`elevated wsl --install --no-distribution -> ${res.code}\n${res.output}`);
      if (res.declined) {
        fail(emit, 'wsl_enable', 'Permission was not given',
          'Guaardvark needs administrator permission once, to turn on Windows Subsystem for Linux. Choose Install again and answer Yes when Windows asks.');
      }
      if (!(res.code === 0 || res.restartRequested)) failFromWsl(emit, 'wsl_enable', 'Windows Subsystem for Linux could not be turned on', res);
      emit(event('wsl_enable', 'ok'));

      // Turning WSL on enables Windows features that only work after a
      // restart. Exit codes and the pending-restart key say so; a plain
      // update of an already enabled WSL needs none.
      const after = await detect.wslState(runner);
      const needsRestart = res.restartRequested || (await detect.rebootPending(runner))
        || after.state !== 'ready' || /reboot|restart/i.test(res.output);
      if (needsRestart) return requestRestart(emit);
      ws = after;
    }

    if (ws.state === 'broken') failFromWsl(emit, 'wsl_check', 'Windows Subsystem for Linux reported a problem', { stdout: ws.statusText, code: null });

    if (!detect.versionAtLeast(ws.version, MIN_WSL)) {
      emit(event('wsl_update', 'start', { detail: `WSL ${ws.version.join('.')} is older than Guaardvark needs. Windows will ask for permission.` }));
      const res = await runElevated(runner, 'wsl', ['--update'], path.join(dirs.logs, 'wsl-update.txt'));
      if (res.declined) fail(emit, 'wsl_update', 'Permission was not given', 'Updating Windows Subsystem for Linux needs administrator permission. Choose Install again and answer Yes.');
      if (res.code !== 0) failFromWsl(emit, 'wsl_update', 'Windows Subsystem for Linux could not be updated', res);
      ws = await detect.wslState(runner);
      emit(event('wsl_update', 'ok', { detail: ws.version ? `WSL ${ws.version.join('.')}` : '' }));
    }
    if (!ws.version) failFromWsl(emit, 'wsl_check', 'Windows Subsystem for Linux does not answer', { stdout: ws.statusText || ws.reason || '', code: null });
    emit(event('wsl_check', 'ok', { detail: `WSL ${ws.version.join('.')}` }));
    return ws;
  }

  async function requestRestart(emit) {
    store.write({ restartRequestedAt: Date.now(), pendingStep: 'wsl_enable' });
    const resumeRegistered = await registerResume(runner, exePath);
    const evt = emit(event('restart_needed', 'info', {
      action: 'restart',
      detail: 'Windows needs to restart to finish turning on Windows Subsystem for Linux. Save your work, then restart. Guaardvark opens again after you sign in and carries on.',
      resumeRegistered,
    }));
    throw restartRequiredError(evt);
  }

  async function obtainImage(emit) {
    const src = image.locate({ env: { ...process.env, ...env }, resourcesPath, version });
    if (src.kind === 'local') {
      log(`image: local ${src.dir}`);
      return { manifest: src.manifest, files: src.manifest.parts.map((p) => path.join(src.dir, p.name)), downloaded: false };
    }
    if (!version) return null;
    let manifest;
    try {
      manifest = await image.fetchManifest(src.url, fetchImpl);
    } catch (e) {
      fail(emit, 'wsl_image_download', 'The download could not start', `Check the internet connection and try again. (${e.message})`);
    }
    if (!manifest) {
      log(`image: no manifest at ${src.url}; using the Ubuntu fallback`);
      return null;
    }
    const total = manifest.parts.reduce((n, p) => n + p.size, 0);
    const need = total + (manifest.installedSize || total * 3);
    emit(event('disk_space', 'start'));
    const free = image.freeBytes(appDir);
    if (free !== null && free < need) {
      fail(emit, 'disk_space', 'Not enough free disk space',
        `Guaardvark needs about ${gb(need)} free on the drive with your user folder; ${gb(free)} is free. Free some space and try again.`);
    }
    emit(event('disk_space', 'ok', { detail: free !== null ? `${gb(free)} free` : '' }));

    fs.mkdirSync(dirs.downloads, { recursive: true });
    emit(event('wsl_image_download', 'start', { detail: `About ${gb(total)} from GitHub` }));
    let done = 0;
    let lastEmit = 0;
    const files = [];
    for (const part of manifest.parts) {
      const dest = path.join(dirs.downloads, part.name);
      const url = new URL(part.name, src.url).toString();
      await image.downloadPart(url, dest, part, {
        fetchImpl,
        onBytes: (n) => {
          done += n;
          const now = Date.now();
          if (now - lastEmit > 1000 || done === total) {
            lastEmit = now;
            emit(event('wsl_image_download', 'info', { detail: `${gb(done)} of ${gb(total)}`, pct: Math.round((done / total) * 100) }));
          }
        },
      }).catch((e) => fail(emit, 'wsl_image_download', 'The download stopped', e.message));
      files.push(dest);
    }
    emit(event('wsl_image_download', 'ok'));
    if (files.length > 1 && manifest.sha256) {
      emit(event('wsl_image_verify', 'start'));
      if (!(await image.verifyJoined(files, manifest.sha256))) {
        for (const f of files) fs.rmSync(f, { force: true });
        fail(emit, 'wsl_image_verify', 'The download is damaged', 'The downloaded files did not match their checksum and were deleted. Try again.');
      }
      emit(event('wsl_image_verify', 'ok'));
    }
    return { manifest, files, downloaded: true };
  }

  async function importImage(emit, img) {
    // A disk left by an earlier attempt that never registered would make the
    // import fail; this folder only ever holds Guaardvark's distribution.
    fs.rmSync(path.join(dirs.distro, 'ext4.vhdx'), { force: true });
    fs.mkdirSync(dirs.distro, { recursive: true });
    const total = img.files.reduce((n, f) => n + fs.statSync(f).size, 0);
    emit(event('wsl_import', 'start', { detail: `Unpacking ${gb(total)}. This takes a few minutes.` }));
    let res;
    if (img.files.length === 1) {
      res = await runner.run('wsl', ['--import', DISTRO, dirs.distro, img.files[0], '--version', '2']);
    } else {
      let fed = 0;
      let lastEmit = 0;
      const input = image.joinedStream(img.files, (n) => {
        fed += n;
        if (Date.now() - lastEmit > 1000) {
          lastEmit = Date.now();
          emit(event('wsl_import', 'info', { pct: Math.round((fed / total) * 100) }));
        }
      });
      // "-" reads the tar from standard input (wsl --import docs).
      res = await runner.run('wsl', ['--import', DISTRO, dirs.distro, '-', '--version', '2'], { input });
    }
    if (res.code !== 0) failFromWsl(emit, 'wsl_import', "Guaardvark's Linux system could not be set up", res);
    emit(event('wsl_import', 'ok'));
    if (img.downloaded) for (const f of img.files) fs.rmSync(f, { force: true });
  }

  async function installViaUbuntu(emit, ws) {
    if (!detect.versionAtLeast(ws.version, MIN_WSL_NAMED_INSTALL)) {
      emit(event('wsl_update', 'start', { detail: 'Windows will ask for permission.' }));
      const res = await runElevated(runner, 'wsl', ['--update'], path.join(dirs.logs, 'wsl-update.txt'));
      if (res.declined || res.code !== 0) failFromWsl(emit, 'wsl_update', 'Windows Subsystem for Linux could not be updated', res);
      emit(event('wsl_update', 'ok'));
    }
    fs.rmSync(path.join(dirs.distro, 'ext4.vhdx'), { force: true });
    fs.mkdirSync(dirs.distro, { recursive: true });
    emit(event('ubuntu_install', 'start', { detail: 'Downloading Ubuntu from Microsoft. This takes a few minutes.' }));
    let res = await runner.run('wsl', ['--install', FALLBACK_DISTRO, '--name', DISTRO, '--location', dirs.distro, '--no-launch', '--web-download']);
    if (res.code !== 0) failFromWsl(emit, 'ubuntu_install', 'Ubuntu could not be installed', res);
    emit(event('ubuntu_install', 'ok'));

    emit(event('ubuntu_prepare', 'start'));
    res = await inDistro(PREPARE_UBUNTU_SCRIPT, [], { user: 'root', login: false, timeoutMs: 300000 });
    if (res.code !== 0) failFromWsl(emit, 'ubuntu_prepare', 'Ubuntu could not be prepared', res);
    // wsl.conf (systemd, default user) is read when the distribution starts.
    await runner.run('wsl', ['--terminate', DISTRO], { timeoutMs: 60000 });
    emit(event('ubuntu_prepare', 'ok'));
  }

  async function runPublicInstaller(emit) {
    emit(event('guaardvark_download', 'start', { detail: 'Getting Guaardvark from GitHub' }));
    const res = await inDistro(`curl -fsSL ${PUBLIC_INSTALLER} | GUAARDVARK_NO_START=1 bash`, [], { timeoutMs: 1800000 });
    if (res.code !== 0) failFromWsl(emit, 'guaardvark_download', 'Guaardvark could not be downloaded', res);
    emit(event('guaardvark_download', 'ok'));
  }

  async function probe(emit) {
    const res = await inDistro(PROBE_SCRIPT, [], { timeoutMs: 180000 });
    if (res.code !== 0) failFromWsl(emit, 'wsl_first_boot', "Guaardvark's Linux system did not start", res);
    const out = lines(res.stdout);
    const [user, home, root, start] = out;
    const ports = {};
    for (const l of out.slice(4)) {
      const m = /^(FLASK_PORT|VITE_PORT)=(\d+)$/.exec(l);
      if (m) ports[m[1] === 'FLASK_PORT' ? 'api' : 'web'] = Number(m[2]);
    }
    return { user, home, root, hasStart: start === 'has-start', ports };
  }

  async function install(report) {
    const emit = makeReporter(report);
    log(`---- install (Guaardvark ${version || 'unknown version'})`);

    emit(event('windows_version', 'start'));
    const build = detect.windowsBuild(osRelease);
    if (build !== null && build < detect.MIN_WINDOWS_BUILD) {
      fail(emit, 'windows_version', 'This Windows is too old',
        `Guaardvark needs Windows 11, or Windows 10 version 21H2 or newer (build ${detect.MIN_WINDOWS_BUILD}). This PC has build ${build}. Update Windows and try again.`);
    }
    emit(event('windows_version', 'ok', { detail: build ? `Windows build ${build}` : '' }));

    const st = store.read();
    if (st.restartRequestedAt) {
      if (bootTime() < st.restartRequestedAt) {
        const evt = emit(event('restart_needed', 'info', {
          action: 'restart',
          detail: 'Windows has not restarted since Windows Subsystem for Linux was turned on. Restart the PC to carry on.',
        }));
        throw restartRequiredError(evt);
      }
      log('restart done; carrying on');
      store.write({ restartRequestedAt: undefined, pendingStep: undefined, restartedAt: Date.now() });
      await clearResume(runner);
    }

    emit(event('nvidia_driver', 'start'));
    const gpu = await detect.nvidia(runner);
    if (gpu.present) {
      const g = gpu.gpus[0];
      emit(event('nvidia_driver', 'ok', { detail: `${g.name}, driver ${g.driver}${g.memoryMiB ? `, ${Math.round(g.memoryMiB / 1024)} GB` : ''}` }));
    } else {
      emit(event('nvidia_driver', 'info', {
        detail: 'No NVIDIA driver found. Guaardvark still runs, but pictures and video need an NVIDIA card with a current Windows driver. Linux needs no driver of its own.',
      }));
    }

    const ws = await ensureWsl(emit);

    let source = 'existing';
    const names = await detect.distros(runner);
    if (names.includes(DISTRO)) {
      const versions = await detect.distroVersions(runner);
      if (versions[DISTRO] === 1) {
        fail(emit, 'wsl_import', "Guaardvark's Linux system runs on WSL 1",
          `It needs WSL 2. In PowerShell run: wsl --set-version ${DISTRO} 2`);
      }
      emit(event('wsl_import', 'skip', { detail: 'Already set up' }));
    } else {
      const img = await obtainImage(emit);
      if (img) {
        await importImage(emit, img);
        source = 'image';
      } else {
        await installViaUbuntu(emit, ws);
        source = 'ubuntu';
      }
    }

    emit(event('wsl_first_boot', 'start'));
    let info = await probe(emit);
    if (!info.hasStart && source !== 'image') {
      await runPublicInstaller(emit);
      info = await probe(emit);
    }
    if (info.user === 'root') {
      fail(emit, 'wsl_first_boot', "Guaardvark's Linux system has no user account",
        'Its default user is root, and Guaardvark does not run as root. Remove it (wsl --unregister Guaardvark) and run setup again.');
    }
    if (!info.hasStart) {
      fail(emit, 'wsl_first_boot', 'Guaardvark is missing from its Linux system', `No start.sh in ${info.root}.`);
    }
    emit(event('wsl_first_boot', 'ok', { detail: `${info.user} at ${info.root}` }));

    emit(event('distro_gpu', 'start'));
    const smi = await inDistro('PATH=/usr/lib/wsl/lib:$PATH nvidia-smi -L', [], { timeoutMs: 60000 });
    if (smi.code === 0 && smi.stdout.trim()) {
      emit(event('distro_gpu', 'ok', { detail: lines(smi.stdout)[0] }));
    } else {
      emit(event('distro_gpu', 'info', { detail: 'Linux does not see an NVIDIA card. Guaardvark will run without one.' }));
    }

    const ports = { ...DEFAULT_PORTS, ...info.ports };
    store.write({
      installed: true,
      installedAt: new Date().toISOString(),
      source,
      user: info.user,
      home: info.home,
      root: info.root,
      ports,
      webUrl: undefined,
      apiUrl: undefined,
    });
    emit(event('wsl_installed', 'ok'));
  }

  // ---------------------------------------------------------------- run

  async function isInstalled() {
    if (!store.read().installed) return false;
    return (await detect.distros(runner)).includes(DISTRO);
  }

  function httpOk(url, timeoutMs = 2500) {
    return new Promise((resolve) => {
      const req = http.get(url, { timeout: timeoutMs }, (res) => {
        const chunks = [];
        res.on('data', (d) => chunks.push(d));
        res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8') }));
      });
      req.on('timeout', () => { req.destroy(); resolve(null); });
      req.on('error', () => resolve(null));
    });
  }

  async function isRunning() {
    const { webUrl, apiUrl } = urls();
    const health = await httpOk(`${apiUrl}/api/health`);
    if (!health || health.status !== 200) return false;
    try {
      if (!('status' in JSON.parse(health.body))) return false;
    } catch {
      return false;
    }
    const web = await httpOk(webUrl);
    return !!web && web.status < 500;
  }

  function startHolder() {
    // An open wsl.exe session keeps the distribution running; WSL stops it
    // about 15 s after the last one ends. This one lasts as long as the app.
    const holder = runner.start('wsl', ['-d', DISTRO, '--exec', 'sleep', 'infinity']);
    const out = [];
    holder.stdout.on('data', (d) => out.push(d));
    holder.stderr.on('data', (d) => out.push(d));
    holder.on('exit', (code) => {
      log(`session holder exited (${code})`);
      if (session.holder === holder) session.holder = null;
      holder.output = Buffer.concat(out);
    });
    return holder;
  }

  function start(report) {
    const emit = makeReporter(report);
    const st = store.read();
    if (!st.installed) return Promise.reject(new Error('Guaardvark is not set up yet; install() comes first.'));
    log('---- start');
    fs.mkdirSync(dirs.logs, { recursive: true });
    fs.writeFileSync(startLog, '');
    session.lastLog = startLog;
    session.started = true;

    emit(event('wsl_attach', 'start'));
    if (!session.holder) session.holder = startHolder();

    const progressFile = `${st.home}/.cache/guaardvark-desktop/progress-${Date.now()}.jsonl`;
    return new Promise((resolve, reject) => {
      let settled = false;
      let attached = false;
      const logStream = fs.createWriteStream(startLog, { flags: 'a' });

      const settle = (ok, value) => {
        if (settled) return;
        settled = true;
        if (session.tail) { session.tail.kill(); session.tail = null; }
        if (ok) {
          store.write({ webUrl: value.webUrl, apiUrl: value.apiUrl });
          resolve(value);
        } else {
          reject(value);
        }
      };
      const failWith = (evt) => {
        if (evt.log && evt.log.startsWith('/')) {
          session.lastLog = uncPath(evt.log);
          evt = { ...evt, windowsLog: session.lastLog };
        }
        emit(evt);
        settle(false, failedError(evt));
      };

      const starter = runner.start('wsl', ['-d', DISTRO, '--exec', 'bash', '-lc',
        'mkdir -p "$(dirname "$2")" && cd "$1" && exec ./start.sh --no-browser --progress-file "$2"',
        'bash', st.root, progressFile]);
      session.starter = starter;
      starter.stdout.pipe(logStream, { end: false });
      starter.stderr.pipe(logStream, { end: false });
      starter.on('error', (e) => failWith(event('failed', 'fail', { label: 'Guaardvark could not start', detail: e.message, log: startLog })));
      starter.on('exit', (code) => {
        log(`start.sh exited (${code})`);
        session.starter = null;
        if (settled) return;
        if (code !== 0) {
          // Give the tail a moment to deliver start.sh's own "failed" line.
          setTimeout(() => {
            if (settled) return;
            const tailText = fs.existsSync(startLog) ? lines(fs.readFileSync(startLog, 'utf8')).slice(-3).join(' ') : '';
            const known = classify(tailText);
            failWith(event('failed', 'fail', {
              label: known ? known.label : 'Guaardvark could not start',
              detail: known ? known.detail : (tailText || `start.sh exited with ${code}`),
              log: startLog,
            }));
          }, 3000);
        } else {
          // start.sh finished without a terminal line (an older start.sh
          // ignores --progress-file): ask the studio itself.
          setTimeout(async () => {
            if (settled) return;
            if (await isRunning()) {
              const u = urls();
              emit({ t: Date.now() / 1000, step: 'ready', state: 'ok', label: 'Guaardvark is ready', url: u.webUrl, api: u.apiUrl });
              settle(true, u);
            } else {
              failWith(event('failed', 'fail', { label: 'Guaardvark did not say it was ready', detail: 'start.sh finished, but the studio does not answer.', log: startLog }));
            }
          }, 20000);
        }
      });

      const tail = runner.start('wsl', ['-d', DISTRO, '--exec', 'bash', '-c', TAIL_SCRIPT, 'bash', progressFile]);
      session.tail = tail;
      let buf = '';
      tail.stdout.on('data', (d) => {
        buf += d.toString('utf8');
        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          let evt;
          try { evt = JSON.parse(line); } catch { log(`progress line is not JSON: ${line}`); continue; }
          if (!attached) { attached = true; emit(event('wsl_attach', 'ok')); }
          if (evt.step === 'ready') {
            emit(evt);
            settle(true, { webUrl: evt.url || urls().webUrl, apiUrl: evt.api || urls().apiUrl });
          } else if (evt.step === 'failed') {
            failWith(evt);
          } else {
            emit(evt);
          }
        }
      });
    });
  }

  // Stops only what this app started, unless { force: true }: the window
  // passes that after the person chose to stop an instance that was already
  // running when the app opened (kept alive in the background, or started
  // from a terminal).
  async function stop({ force = false } = {}) {
    if (!session.started && !force) {
      log('stop: this app did not start Guaardvark; leaving it running');
      return;
    }
    const st = store.read();
    if (session.tail) { session.tail.kill(); session.tail = null; }
    if (!force && st.keepAlive && keepAlive.isKeepAlive(userProfile)) {
      log('stop: keep-alive is on; Guaardvark keeps running in the background');
    } else {
      log('---- stop');
      const res = await inDistro('cd "$1" && ./stop.sh', [st.root], { timeoutMs: 180000 });
      fs.appendFileSync(startLog, `\n--- stop.sh (exit ${res.code})\n${res.stdout}${res.stderr}`);
      await runner.run('wsl', ['--terminate', DISTRO], { timeoutMs: 60000 });
    }
    if (session.holder) { session.holder.kill(); session.holder = null; }
    session.started = false;
  }

  function logPath() {
    return session.lastLog || setupLog;
  }

  // True when this app ran start.sh in the distribution during this session.
  function wasStartedByApp() {
    return session.started;
  }

  function describe() {
    const st = store.read();
    return { installRoot: st.root ? uncPath(st.root) : null, ...urls() };
  }

  // ---------------------------------------------------------------- Windows-only

  // What install() will do, without changing anything or using the network,
  // for the screen that asks before it starts.
  async function planInstall() {
    const st = store.read();
    const ws = await detect.wslState(runner);
    const names = ws.state === 'ready' ? await detect.distros(runner) : [];
    const src = image.locate({ env: { ...process.env, ...env }, resourcesPath, version });
    const steps = [];
    const enable = ws.state !== 'ready';
    if (enable) steps.push('wsl_enable', 'restart_needed');
    if (!names.includes(DISTRO)) steps.push(src.kind === 'download' ? 'wsl_image_download' : null, 'wsl_import');
    steps.push('wsl_first_boot');
    return {
      steps: steps.filter(Boolean).map((id) => ({ id, label: event(id, 'start').label })),
      needsAdmin: enable,
      needsRestart: enable || !!st.restartRequestedAt,
      download: src.kind === 'download' && !names.includes(DISTRO) ? { from: 'GitHub', url: src.url, bytes: opts.imageBytes || null } : null,
      bundledImageBytes: src.kind === 'local' ? (opts.imageBytes || packageField('wslImageBytes')) : null,
    };
  }

  async function restartComputer() {
    // No /f: programs with unsaved work still get to ask.
    const res = await runner.run('shutdown', ['/r', '/t', '0']);
    return res.code === 0;
  }

  function isResumeLaunch(argv = process.argv) {
    return argv.includes(RESUME_FLAG);
  }

  function getKeepAlive() {
    return !!store.read().keepAlive && keepAlive.isKeepAlive(userProfile);
  }

  // on=true: closing the window leaves Guaardvark running. Writes the two
  // WSL idle timeouts (every distribution on the PC) and takes effect after
  // applyWslSettings(), which restarts all of WSL; ask before calling it.
  function setKeepAlive(on) {
    const changed = keepAlive.setKeepAlive(userProfile, !!on);
    store.write({ keepAlive: !!on });
    log(`keep-alive ${on ? 'on' : 'off'} (${changed ? 'changed' : 'unchanged'} ${keepAlive.configPath(userProfile)})`);
    return { changed, needsWslRestart: changed, file: keepAlive.configPath(userProfile) };
  }

  async function applyWslSettings() {
    const res = await runner.run('wsl', ['--shutdown'], { timeoutMs: 120000 });
    session.holder = null;
    return res.code === 0;
  }

  return {
    describe,
    isInstalled,
    install,
    isRunning,
    start,
    stop,
    logPath,
    wasStartedByApp,
    planInstall,
    restartComputer,
    isResumeLaunch,
    getKeepAlive,
    setKeepAlive,
    applyWslSettings,
    DISTRO,
  };
}

// index.js loads this module on Windows and uses it as the platform
// interface; create() builds another instance with other paths or tools.
module.exports = process.platform === 'win32' ? Object.assign(create(), { create }) : { create };
