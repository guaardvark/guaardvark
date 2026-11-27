// Linux: Guaardvark runs from a checkout. start.sh brings it up and reports its
// steps through the progress file (desktop/PROTOCOL.md); stop.sh takes it down.

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const DEFAULT_API_PORT = 5000;
const DEFAULT_WEB_PORT = 5173;
const HEALTH_TIMEOUT_MS = 2500;
const TAIL_INTERVAL_MS = 200;
const STOP_TIMEOUT_MS = 180000;
// How long after start.sh exits a terminal line may still be read from the file.
const EXIT_GRACE_MS = 1500;

let startChild = null;      // the start.sh process this run launched, until it exits
let starting = false;       // a start() call is in flight
let startedByApp = false;   // this app (this run or an earlier one) brought Guaardvark up
let lastFailureLog = null;

function installRoot() {
  return path.resolve(process.env.GUAARDVARK_INSTALL_ROOT || path.join(__dirname, '..', '..'));
}

// Where the progress file, the start/stop output and the "started by the app"
// marker live. Outside the checkout so a read-only or shared install works.
function stateDir() {
  const base = process.env.GUAARDVARK_DESKTOP_STATE_DIR
    || path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(), '.local', 'state'), 'guaardvark-desktop');
  fs.mkdirSync(base, { recursive: true });
  return base;
}

const PROGRESS_PREFIX = 'start-progress-';
const outputLog = () => path.join(stateDir(), 'start-output.log');
const markerFile = () => path.join(stateDir(), 'started.json');

function readDotEnv(root) {
  const values = {};
  let text;
  try {
    text = fs.readFileSync(path.join(root, '.env'), 'utf8');
  } catch {
    return values;
  }
  for (const line of text.split('\n')) {
    const m = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/.exec(line);
    if (m) values[m[1]] = m[2].replace(/^(['"])(.*)\1$/, '$2');
  }
  return values;
}

function port(name, dotEnv, fallback) {
  const n = Number.parseInt(process.env[name] || dotEnv[name] || '', 10);
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : fallback;
}

// The same precedence start.sh uses: the environment, then the install's .env,
// then the defaults. The host stays "localhost" so the window shares the origin
// (and with it the saved settings) of the studio opened in a browser.
function describe() {
  const root = installRoot();
  const dotEnv = readDotEnv(root);
  const apiPort = port('FLASK_PORT', dotEnv, DEFAULT_API_PORT);
  const webPort = port('VITE_PORT', dotEnv, DEFAULT_WEB_PORT);
  return {
    installRoot: root,
    webUrl: `http://localhost:${webPort}`,
    apiUrl: `http://localhost:${apiPort}`,
  };
}

function get(url) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: HEALTH_TIMEOUT_MS }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { if (body.length < 65536) body += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body }));
    });
    req.on('timeout', () => req.destroy());
    req.on('error', () => resolve(null));
  });
}

async function health() {
  const { apiUrl } = describe();
  const res = await get(`${apiUrl}/api/health`);
  if (!res || res.status !== 200) return null;
  try {
    const json = JSON.parse(res.body);
    return json && typeof json === 'object' && 'status' in json ? json : null;
  } catch {
    return null;
  }
}

function readMarker() {
  try {
    return JSON.parse(fs.readFileSync(markerFile(), 'utf8'));
  } catch {
    return null;
  }
}

function clearMarker() {
  fs.rmSync(markerFile(), { force: true });
}

async function isInstalled() {
  const root = installRoot();
  return fs.existsSync(path.join(root, 'start.sh')) && fs.existsSync(path.join(root, 'stop.sh'));
}

async function install(report) {
  if (await isInstalled()) return;
  const evt = {
    step: 'failed',
    state: 'fail',
    label: 'Guaardvark is not installed here',
    detail: `No start.sh in ${installRoot()}. The desktop app runs from inside a Guaardvark checkout.`,
  };
  if (report) report(evt);
  throw Object.assign(new Error(evt.label), { event: evt });
}

async function isRunning() {
  const { webUrl } = describe();
  const [api, web] = await Promise.all([health(), get(webUrl)]);
  const running = Boolean(api && web && web.status < 500);
  if (running && !starting) {
    // An instance this app started before (the app was closed without stopping
    // it, or crashed) is still this app's to stop. The backend's boot id tells
    // it apart from one that was restarted by hand since.
    const marker = readMarker();
    startedByApp = Boolean(marker && marker.installRoot === installRoot()
      && marker.bootId && marker.bootId === api.boot_id);
  }
  return running;
}

function wasStartedByApp() {
  return startedByApp;
}

// Reads the lines appended to the progress file since the last call. A partial
// last line stays in the buffer until its newline arrives.
function makeTail(file) {
  let offset = 0;
  let pending = '';
  return function readNew() {
    let fd;
    try {
      fd = fs.openSync(file, 'r');
    } catch {
      return [];
    }
    try {
      const size = fs.fstatSync(fd).size;
      if (size < offset) { offset = 0; pending = ''; }
      if (size === offset) return [];
      const buf = Buffer.alloc(size - offset);
      fs.readSync(fd, buf, 0, buf.length, offset);
      offset = size;
      pending += buf.toString('utf8');
    } finally {
      fs.closeSync(fd);
    }
    const lines = pending.split('\n');
    pending = lines.pop();
    const events = [];
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const evt = JSON.parse(line);
        if (evt && typeof evt === 'object') events.push(evt);
      } catch {
        // A line that is not JSON is not a progress event.
      }
    }
    return events;
  };
}

// The protocol wants a file no other start.sh run has used, so each launch
// gets its own and the ones from earlier launches are cleared out.
function newProgressFile() {
  const dir = stateDir();
  for (const name of fs.readdirSync(dir)) {
    if (name.startsWith(PROGRESS_PREFIX)) fs.rmSync(path.join(dir, name), { force: true });
  }
  return path.join(dir, `${PROGRESS_PREFIX}${Date.now()}.jsonl`);
}

function isStartScript(pid, root) {
  try {
    return fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').includes(path.join(root, 'start.sh'));
  } catch {
    return false;
  }
}

// The start.sh still running for this install, if any: the one this run
// launched, or the one an earlier run of the app recorded before it went away.
function earlierStartPid() {
  if (startChild) return startChild.pid;
  const marker = readMarker();
  const root = installRoot();
  if (marker && marker.installRoot === root && Number.isInteger(marker.pid) && isStartScript(marker.pid, root)) {
    return marker.pid;
  }
  return null;
}

// A second start.sh ends the first one's services (its first step is
// stop.sh, which also removes start.sh's own lock), so the app never runs
// two: a new start waits here until the earlier script has exited.
async function waitForEarlierStart(report) {
  if (!earlierStartPid()) return false;
  const evt = { step: 'previous_start', label: 'Waiting for the earlier start to finish' };
  if (report) report({ ...evt, state: 'start' });
  while (earlierStartPid()) await new Promise((resolve) => setTimeout(resolve, 300));
  if (report) report({ ...evt, state: 'ok' });
  return true;
}

async function start(report) {
  if (starting) throw new Error('Guaardvark is already starting');
  starting = true;
  try {
    if (await waitForEarlierStart(report)) {
      // The script waited for may have brought Guaardvark up (the app was
      // closed or killed while it ran). Then there is nothing left to start.
      const { installRoot: root, webUrl, apiUrl } = describe();
      const [api, web] = await Promise.all([health(), get(webUrl)]);
      if (api && web && web.status < 500) {
        startedByApp = true;
        fs.writeFileSync(markerFile(), JSON.stringify({ installRoot: root, pid: null, t: Date.now() / 1000, bootId: api.boot_id || null }));
        return { webUrl, apiUrl };
      }
    }
    return await runStartScript(report);
  } finally {
    starting = false;
  }
}

function runStartScript(report) {
  const { installRoot: root, webUrl, apiUrl } = describe();
  const file = newProgressFile();
  const log = outputLog();
  const out = fs.openSync(log, 'w');
  lastFailureLog = null;

  // Its own session and process group: if the app dies, the stack keeps
  // running and the next launch finds it through the health check.
  const child = spawn('bash', [path.join(root, 'start.sh'), '--no-browser', '--progress-file', file], {
    cwd: root,
    detached: true,
    stdio: ['ignore', out, out],
    env: { ...process.env, GUAARDVARK_PROGRESS_FILE: file },
  });
  fs.closeSync(out);
  child.unref();
  startChild = child;
  startedByApp = true;
  fs.writeFileSync(markerFile(), JSON.stringify({ installRoot: root, pid: child.pid, t: Date.now() / 1000, bootId: null }));

  return new Promise((resolve, reject) => {
    const readNew = makeTail(file);
    let exited = null;
    let exitedAt = 0;
    let settled = false;

    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearInterval(timer);
      fn(value);
    };

    const fail = (evt) => {
      lastFailureLog = evt.log && fs.existsSync(evt.log) ? evt.log : log;
      finish(reject, Object.assign(new Error(evt.label || 'Guaardvark did not start'), { event: evt }));
    };

    const succeed = async (evt) => {
      const api = await health();
      const marker = { installRoot: root, pid: child.pid, t: Date.now() / 1000, bootId: (api && api.boot_id) || null };
      fs.writeFileSync(markerFile(), JSON.stringify(marker));
      finish(resolve, { webUrl: evt.url || webUrl, apiUrl: evt.api || apiUrl });
    };

    let checkingAfterExit = false;
    const poll = async () => {
      if (settled || checkingAfterExit) return;
      for (const evt of readNew()) {
        if (report) report(evt);
        if (evt.step === 'ready') { succeed(evt); return; }
        if (evt.step === 'failed') { fail(evt); return; }
      }
      if (exited && Date.now() - exitedAt > EXIT_GRACE_MS) {
        // start.sh ended without a terminal line. One that exited cleanly and
        // left Guaardvark answering (a start.sh without the progress file)
        // still counts as started.
        if (exited.code === 0) {
          checkingAfterExit = true;
          const [api, web] = await Promise.all([health(), get(webUrl)]);
          if (api && web && web.status < 500) { succeed({ step: 'ready' }); return; }
        }
        const evt = {
          step: 'failed',
          state: 'fail',
          label: 'Guaardvark did not start',
          detail: exited.code === 0
            ? 'The start script ended without saying it was ready.'
            : `The start script stopped early (exit ${exited.signal || exited.code}).`,
          log,
        };
        if (report) report(evt);
        fail(evt);
      }
    };

    const timer = setInterval(poll, TAIL_INTERVAL_MS);
    const gone = () => { if (startChild === child) startChild = null; };
    child.on('error', (err) => { gone(); exited = { code: 1, signal: null, err }; exitedAt = Date.now() - EXIT_GRACE_MS; });
    child.on('exit', (code, signal) => { gone(); exited = { code, signal }; exitedAt = Date.now(); });
  });
}

// Ends a start.sh run that is still going, with everything it launched in its
// own process group.
function endStartInProgress() {
  if (!startChild) return;
  try {
    process.kill(-startChild.pid, 'SIGTERM');
  } catch {
    // Already gone.
  }
}

// Stops Guaardvark if this app started it. `force` also stops an instance the
// app found running, for when the person asked for that.
function stop({ force = false } = {}) {
  if (!startedByApp && !force) return Promise.resolve(false);
  endStartInProgress();
  const root = installRoot();
  return new Promise((resolve) => {
    const out = fs.openSync(path.join(stateDir(), 'stop-output.log'), 'w');
    const child = spawn('bash', [path.join(root, 'stop.sh')], { cwd: root, stdio: ['ignore', out, out] });
    fs.closeSync(out);
    const done = () => {
      clearTimeout(timer);
      startedByApp = false;
      clearMarker();
      resolve(true);
    };
    const timer = setTimeout(() => { child.kill('SIGKILL'); }, STOP_TIMEOUT_MS);
    child.on('error', done);
    child.on('exit', done);
  });
}

function logPath() {
  return lastFailureLog || outputLog();
}

module.exports = {
  describe, isInstalled, install, isRunning, start, stop, logPath,
  wasStartedByApp,
};
