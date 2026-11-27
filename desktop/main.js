// Guaardvark's desktop window. Starts Guaardvark if it is not running, shows
// each startup step, then shows the studio. Closing the window stops what the
// app started.

const { app, BrowserWindow, Menu, dialog, ipcMain, powerMonitor, protocol, screen, session, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

// The app's own screens (loading, install, restart, stopping) are served from
// app://guaardvark/ by a handler that hands out exactly the files listed
// here. They are not file:// pages, so the packaged app runs with the
// grantFileProtocolExtraPrivileges fuse off and nothing in the window can
// read a file from disk.
const APP_SCHEME = 'app';
const APP_HOST = 'guaardvark';
const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;
const APP_FILES = new Map([
  ['/loading.html', 'text/html; charset=utf-8'],
  ['/loading.css', 'text/css; charset=utf-8'],
  ['/loading.js', 'text/javascript; charset=utf-8'],
  ['/assets/icon-256.png', 'image/png'],
]);
const LOADING_URL = `${APP_ORIGIN}/loading.html`;
// Must happen before the app is ready.
protocol.registerSchemesAsPrivileged([{ scheme: APP_SCHEME, privileges: { standard: true, secure: true } }]);

function serveAppFiles() {
  protocol.handle(APP_SCHEME, async (request) => {
    let url;
    try {
      url = new URL(request.url);
    } catch {
      return new Response('', { status: 400 });
    }
    // The list is the whole of what is served: a path outside it, however it
    // is spelled, is not found.
    const type = url.host === APP_HOST && request.method === 'GET' ? APP_FILES.get(url.pathname) : undefined;
    if (!type) return new Response('', { status: 404 });
    try {
      const body = await fs.promises.readFile(path.join(__dirname, url.pathname));
      return new Response(body, { headers: { 'content-type': type, 'cache-control': 'no-store' } });
    } catch {
      return new Response('', { status: 404 });
    }
  });
}

function isAppPage(url) {
  try {
    const { protocol: scheme, host } = new URL(url);
    return scheme === `${APP_SCHEME}:` && host === APP_HOST;
  } catch {
    return false;
  }
}
const DEFAULT_BOUNDS = { width: 1440, height: 940 };
const MIN_SIZE = { width: 960, height: 640 };
const BACKGROUND = '#0b0c0f';

// The window talks to this computer only. These switches turn off Chromium's
// own background requests; lockDownSession() refuses whatever a page asks for
// outside loopback.
for (const name of ['disable-background-networking', 'disable-component-update', 'disable-domain-reliability',
  'disable-breakpad', 'disable-sync', 'disable-client-side-phishing-detection', 'no-pings', 'no-first-run',
  'no-default-browser-check']) {
  app.commandLine.appendSwitch(name);
}
app.commandLine.appendSwitch('disable-features', [
  'OptimizationHints', 'MediaRouter', 'DialMediaRouteProvider', 'Translate', 'AutofillServerCommunication',
  'CertificateTransparencyComponentUpdater', 'AnchorElementInteraction', 'NavigationPredictor', 'Prerender2',
].join(','));
// Chromium looks a link's host up in DNS as soon as the pointer goes down on
// it, before any request the filter could refuse. The app's own resolver
// therefore knows loopback names only; every other name fails without a lookup.
app.commandLine.appendSwitch('host-resolver-rules',
  'MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE *.localhost, EXCLUDE 127.0.0.1, EXCLUDE [::1]');
app.enableSandbox();
// Ties the window to the menu shortcut (linux/install-shortcut.sh) so the
// desktop shows one icon for both.
if (process.platform === 'linux') app.setDesktopName('guaardvark.desktop');

// Development only: GUAARDVARK_DESKTOP_PLATFORM_MODULE names a file that
// exports a platform object, so another system's module (built around
// stand-in tools) can be driven through the real window. A packaged app
// ignores it.
const platformOverride = !app.isPackaged && process.env.GUAARDVARK_DESKTOP_PLATFORM_MODULE;
const platform = platformOverride ? require(path.resolve(platformOverride)) : require('./platform').load();

let win = null;
let startedThisRun = false;   // start() was called in this run of the app
let onLoadingPage = false;
let attempt = 0;              // bumped by every launch(); stale results are dropped
let quitState = 'no';         // no | asking | stopping | done
let studioUrl = null;
let launching = false;        // a launch() is between its first and last step
let installApproved = false;  // the person pressed Install on the plan screen
let installChoice = {};       // what they chose there (the memory setting)
let reopenAfterStop = false;  // the app was opened again while it was stopping Guaardvark
let signInRefused = false;    // the backend turned the install's key down in this run

// What the loading page draws. Sent whole on every change, and handed to the
// page when it (re)loads, so the page keeps no state of its own.
let ui = { view: 'checking' };

// ---------------------------------------------------------------- requests

function isLoopbackHost(hostname) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname.endsWith('.localhost');
}

function isAllowedRequest(url) {
  try {
    const { protocol, hostname } = new URL(url);
    if (['devtools:', 'blob:', 'data:', 'about:', 'chrome-extension:'].includes(protocol)) return true;
    if (protocol === `${APP_SCHEME}:`) return hostname === APP_HOST;
    if (['http:', 'https:', 'ws:', 'wss:'].includes(protocol)) return isLoopbackHost(hostname);
    return false;
  } catch {
    return false;
  }
}

function isExternalLink(url) {
  try {
    const { protocol, hostname } = new URL(url);
    if (protocol === 'mailto:') return true;
    return (protocol === 'http:' || protocol === 'https:') && !isLoopbackHost(hostname);
  } catch {
    return false;
  }
}

// Two settings for checks only. GUAARDVARK_DESKTOP_REQUEST_LOG names a file
// that gets one line per request the window makes. GUAARDVARK_DESKTOP_READONLY=1
// refuses everything but plain reads, for looking at a studio someone is using.
const requestLog = process.env.GUAARDVARK_DESKTOP_REQUEST_LOG || null;
const readOnly = process.env.GUAARDVARK_DESKTOP_READONLY === '1';

function lockDownSession() {
  const ses = session.defaultSession;
  ses.webRequest.onBeforeRequest((details, callback) => {
    let allowed = isAllowedRequest(details.url);
    if (allowed && readOnly) {
      allowed = ['GET', 'HEAD', 'OPTIONS'].includes(details.method) && !/^wss?:/.test(details.url);
    }
    if (requestLog) {
      fs.appendFileSync(requestLog, `${allowed ? 'ALLOW ' : 'REFUSE'} ${details.method} ${details.url}\n`);
    }
    callback({ cancel: !allowed });
  });

  // Chromium fetches a spelling dictionary from a Google host the first time a
  // profile is used, whatever the window's own spellcheck setting says.
  // Spelling is off for the session and the download address points nowhere.
  ses.setSpellCheckerEnabled(false);
  ses.setSpellCheckerLanguages([]);
  ses.setSpellCheckerDictionaryDownloadURL('http://127.0.0.1:9/');

  // The studio records voice and copies text; nothing else is granted, and
  // only to pages served from this computer.
  const grantable = new Set(['media', 'clipboard-read', 'clipboard-sanitized-write', 'fullscreen', 'pointerLock']);
  const fromLoopback = (origin) => {
    try {
      return isLoopbackHost(new URL(origin).hostname);
    } catch {
      return false;
    }
  };
  ses.setPermissionRequestHandler((contents, permission, callback) => {
    callback(grantable.has(permission) && fromLoopback(contents.getURL()));
  });
  ses.setPermissionCheckHandler((_contents, permission, origin) => grantable.has(permission) && fromLoopback(origin));
}

// Links that leave the studio open in the person's own browser; the window
// itself never navigates off this computer.
function guardContents(contents) {
  contents.on('will-navigate', (event, url) => {
    // Only the app itself puts its own screens in the window; a page cannot
    // send the window to them.
    if (isAppPage(url) && !isAppPage(contents.getURL())) { event.preventDefault(); return; }
    if (isAllowedRequest(url)) return;
    event.preventDefault();
    if (isExternalLink(url)) shell.openExternal(url);
  });
  contents.on('will-redirect', (event, url) => {
    if (!isAllowedRequest(url)) event.preventDefault();
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (isExternalLink(url)) {
      shell.openExternal(url);
      return { action: 'deny' };
    }
    if (!isAllowedRequest(url) || isAppPage(url)) return { action: 'deny' };
    return {
      action: 'allow',
      overrideBrowserWindowOptions: {
        backgroundColor: BACKGROUND,
        autoHideMenuBar: true,
        icon: iconPath(),
        webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false },
      },
    };
  });
}

// ------------------------------------------------------------------ window

function iconPath() {
  for (const candidate of [path.join(__dirname, 'build', 'icon.png'), path.join(__dirname, 'assets', 'icon-256.png')]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return undefined;
}

const windowStateFile = () => path.join(app.getPath('userData'), 'window-state.json');

function loadWindowState() {
  let saved;
  try {
    saved = JSON.parse(fs.readFileSync(windowStateFile(), 'utf8'));
  } catch {
    return { ...DEFAULT_BOUNDS };
  }
  const { x, y, width, height, maximized } = saved;
  if (![width, height].every(Number.isInteger) || width < MIN_SIZE.width || height < MIN_SIZE.height) {
    return { ...DEFAULT_BOUNDS };
  }
  const state = { width, height, maximized: Boolean(maximized) };
  // The position is kept only while it still lands on a connected screen.
  if ([x, y].every(Number.isInteger)) {
    const visible = screen.getAllDisplays().some(({ workArea: a }) =>
      x < a.x + a.width - 100 && x + width > a.x + 100 && y >= a.y - 20 && y < a.y + a.height - 100);
    if (visible) Object.assign(state, { x, y });
  }
  return state;
}

function saveWindowState() {
  if (!win || win.isDestroyed()) return;
  const bounds = win.isMaximized() || win.isFullScreen() ? win.getNormalBounds() : win.getBounds();
  try {
    fs.writeFileSync(windowStateFile(), JSON.stringify({ ...bounds, maximized: win.isMaximized() }));
  } catch {
    // The window still works without a remembered size.
  }
}

function createWindow() {
  const state = loadWindowState();
  win = new BrowserWindow({
    ...state,
    minWidth: MIN_SIZE.width,
    minHeight: MIN_SIZE.height,
    backgroundColor: BACKGROUND,
    autoHideMenuBar: true,
    title: 'Guaardvark',
    icon: iconPath(),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      webviewTag: false,
    },
  });
  if (state.maximized) win.maximize();
  win.once('ready-to-show', () => win.show());

  let saveTimer = null;
  const scheduleSave = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveWindowState, 400);
  };
  win.on('resize', scheduleSave);
  win.on('move', scheduleSave);

  // The window is always called Guaardvark, whatever page it shows.
  win.on('page-title-updated', (event) => event.preventDefault());

  win.on('close', (event) => {
    saveWindowState();
    if (quitState === 'done') return;
    event.preventDefault();
    beginQuit();
  });
  win.on('closed', () => { win = null; });
  // Windows: logging off, shutting down or restarting.
  win.on('session-end', () => endForSessionEnd());

  win.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    // -3 is a load this app replaced with another one.
    if (!isMainFrame || code === -3 || quitState !== 'no' || !studioUrl || !url.startsWith(studioUrl)) return;
    showFailure({
      label: 'The studio page did not open',
      detail: `${url} did not answer (${description}).`,
    });
  });
}

// Only a platform that can keep Guaardvark running without the window (the
// Windows module) has the keep-alive calls.
const hasKeepAlive = typeof platform.getKeepAlive === 'function' && typeof platform.setKeepAlive === 'function';
const hasMemory = typeof platform.getMemory === 'function' && typeof platform.setMemory === 'function';

// The memory limit WSL gives Guaardvark: a checkbox while Guaardvark's own
// setting applies or can be offered; a plain line when the person set one.
function memoryItems() {
  if (!hasMemory) return [];
  const mem = platform.getMemory();
  if (mem.userValue) return [{ label: `Memory for Linux: ${mem.userValue} (your setting)`, enabled: false }];
  if (!mem.recommendedGB) return [];
  return [{
    label: `Memory for Guaardvark: ${mem.recommendedGB} GB`,
    type: 'checkbox',
    checked: Boolean(mem.on),
    click: () => changeMemory(),
  }];
}

function buildMenu() {
  const keepAliveItems = hasKeepAlive ? [
    {
      label: 'Keep Guaardvark running after closing',
      type: 'checkbox',
      checked: Boolean(platform.getKeepAlive()),
      click: () => changeKeepAlive(),
    },
    ...memoryItems(),
    { type: 'separator' },
  ] : [];
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'Guaardvark',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => win && win.webContents.reload() },
        { type: 'separator' },
        ...keepAliveItems,
        { label: 'Quit', accelerator: 'CmdOrCtrl+Q', click: () => beginQuit() },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
        { role: 'toggleDevTools' },
      ],
    },
  ]));
}

// Turning keep-alive on or off rewrites a Windows setting every Linux system
// on the PC shares, and applying it at once restarts all of them, so nothing
// is changed before the person has read that and chosen.
async function changeKeepAlive() {
  const turnOn = !platform.getKeepAlive();
  buildMenu();   // the click already flipped the tick; draw the real state until it changes
  if (!win || win.isDestroyed() || quitState !== 'no') return;
  const { response } = await dialog.showMessageBox(win, {
    type: 'question',
    title: 'Guaardvark',
    message: turnOn ? 'Keep Guaardvark running after this window closes?' : 'Stop Guaardvark when this window closes?',
    detail: [
      turnOn
        ? 'Guaardvark will stay available in your browser, and work in progress will carry on, after you close this window.'
        : 'Closing this window will stop Guaardvark again.',
      turnOn
        ? 'For that, Windows is told not to shut its Linux layer down when it is idle. The setting covers every Linux system on this PC, not only Guaardvark.'
        : 'Windows goes back to shutting its Linux layer down when it is idle. The setting covers every Linux system on this PC, not only Guaardvark.',
      'The change takes effect when the Linux layer restarts. Restarting it now stops every Linux system that is running on this PC, Guaardvark included; Guaardvark starts again straight away.',
    ].join('\n\n'),
    buttons: ['Change it and restart the Linux layer now', 'Change it at the next Windows restart', 'Cancel'],
    defaultId: 1,
    cancelId: 2,
    noLink: true,
  });
  if (response === 2) return;
  let result;
  try {
    result = await platform.setKeepAlive(turnOn);
  } catch (err) {
    dialog.showMessageBox(win, {
      type: 'error',
      title: 'Guaardvark',
      message: 'The setting could not be changed.',
      detail: String(err.message || err),
      buttons: ['OK'],
    });
    return;
  } finally {
    buildMenu();
  }
  if (response !== 0 || !result || !result.needsWslRestart || typeof platform.applyWslSettings !== 'function') return;
  setUi({ view: 'starting', startedAt: Date.now(), steps: [], label: 'Restarting the Linux layer', detail: '' });
  await showLoadingPage();
  await platform.applyWslSettings();
  launch();
}

// Same shape as keep-alive: a Windows setting every Linux system on the PC
// shares, applied only when the Linux layer restarts.
async function changeMemory() {
  const mem = platform.getMemory();
  const turnOn = !mem.on;
  buildMenu();
  if (!win || win.isDestroyed() || quitState !== 'no') return;
  const { response } = await dialog.showMessageBox(win, {
    type: 'question',
    title: 'Guaardvark',
    message: turnOn ? `Let Guaardvark use up to ${mem.recommendedGB} GB of memory?` : `Go back to Windows' own memory limit for Linux (${mem.defaultGB} GB)?`,
    detail: [
      turnOn
        ? `Windows gives its Linux layer half of this PC's ${mem.totalGB} GB unless told otherwise. Image and video models often need more; this leaves 8 GB for Windows.`
        : 'Large image and video models may no longer fit.',
      'The setting covers every Linux system on this PC, not only Guaardvark.',
      'The change takes effect when the Linux layer restarts. Restarting it now stops every Linux system that is running on this PC, Guaardvark included; Guaardvark starts again straight away.',
    ].join('\n\n'),
    buttons: ['Change it and restart the Linux layer now', 'Change it at the next Windows restart', 'Cancel'],
    defaultId: 1,
    cancelId: 2,
    noLink: true,
  });
  if (response === 2) return;
  let result;
  try {
    result = await platform.setMemory(turnOn);
  } catch (err) {
    dialog.showMessageBox(win, { type: 'error', title: 'Guaardvark', message: 'The setting could not be changed.', detail: String(err.message || err), buttons: ['OK'] });
    return;
  } finally {
    buildMenu();
  }
  if (result && result.message) {
    await dialog.showMessageBox(win, { type: 'info', title: 'Guaardvark', message: 'Your .wslconfig was edited since Guaardvark changed it.', detail: result.message, buttons: ['OK'] });
  }
  if (response !== 0 || !result || !result.needsWslRestart || typeof platform.applyWslSettings !== 'function') return;
  setUi({ view: 'starting', startedAt: Date.now(), steps: [], label: 'Restarting the Linux layer', detail: '' });
  await showLoadingPage();
  await platform.applyWslSettings();
  launch();
}

// ------------------------------------------------------------ loading page

function setUi(next) {
  ui = next;
  if (win && !win.isDestroyed() && onLoadingPage) win.webContents.send('gv:ui', ui);
}

async function showLoadingPage() {
  if (!win || win.isDestroyed() || onLoadingPage) return;
  onLoadingPage = true;
  await win.loadURL(LOADING_URL);
}

// An install with an API key asks every new browser profile for it once. This
// window belongs to the person running the install, so the app signs its own
// profile in through the same route Settings > Access uses: the key goes from
// the install's .env to the loopback backend, which answers with the session
// cookie. The key is not logged, shown or stored by the app. Any failure
// leaves the studio to ask for the key as it would in a browser.
async function signInOwnWindow(url) {
  if (typeof platform.getApiKey !== 'function') return;
  try {
    if (!isLoopbackHost(new URL(url).hostname)) return;
    const ses = session.defaultSession;
    const status = await ses.fetch(`${url}/api/auth/status`, { credentials: 'include', cache: 'no-store', signal: AbortSignal.timeout(5000) });
    if (!status.ok) return;
    const { key_required: keyRequired, session_ok: signedIn } = await status.json();
    if (!keyRequired || signedIn || signInRefused) return;
    const key = await platform.getApiKey();
    if (!key) return;
    // One try per run: a key the backend refuses (the .env changed without a
    // restart, say) is not sent again.
    signInRefused = true;
    const answer = await ses.fetch(`${url}/api/auth/session`, {
      method: 'POST',
      credentials: 'include',
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key }),
    });
    signInRefused = !answer.ok;
  } catch {
    // The studio's own sign-in prompt covers it.
  }
}

async function showStudio(url) {
  if (!win || win.isDestroyed()) return;
  studioUrl = url;
  await signInOwnWindow(url);
  if (!win || win.isDestroyed()) return;
  onLoadingPage = false;
  try {
    await win.loadURL(url);
  } catch {
    // did-fail-load draws the failure.
  }
}

// The failed line names the step startup stopped at (`at`); without that it
// is the step that was running. Steps that only warned stay warnings.
async function showFailure(evt) {
  const steps = (ui.steps || []).map((s) => (s.state === 'running' || s.step === evt.at ? { ...s, state: 'failed' } : s));
  // No step named and none running: the step that warned last is where it stopped.
  const last = steps[steps.length - 1];
  if (last && last.state === 'warned' && !steps.some((s) => s.state === 'failed')) steps[steps.length - 1] = { ...last, state: 'failed' };
  setUi({
    view: 'failed',
    label: evt.label || 'Guaardvark did not start',
    detail: evt.detail || '',
    steps,
  });
  await showLoadingPage();
}

// Folds one progress event into the list of steps the page draws.
function applyProgress(evt) {
  if (!evt || typeof evt !== 'object' || ui.view !== 'starting') return;
  const steps = ui.steps.slice();
  const next = { ...ui, steps };
  // A step's `fail` is a warning while startup carries on; only the terminal
  // `failed` line turns it into the failure (showFailure).
  const stateName = { start: 'running', ok: 'done', skip: 'skipped', fail: 'warned' }[evt.state];
  if (stateName && evt.step && evt.step !== 'ready' && evt.step !== 'failed') {
    const at = steps.findIndex((s) => s.step === evt.step);
    const entry = { step: evt.step, label: evt.label || (at >= 0 ? steps[at].label : evt.step), state: stateName };
    if (at >= 0) steps[at] = entry; else steps.push(entry);
  }
  if (evt.state === 'start' && evt.label) next.label = evt.label;
  if (evt.state === 'start' || evt.detail !== undefined) next.detail = evt.detail || '';
  const pct = Number(evt.pct);
  if (Number.isFinite(pct)) next.pct = Math.max(next.pct ?? 0, Math.min(100, Math.max(0, pct)));
  if (evt.step === 'ready') Object.assign(next, { pct: 100, label: 'Opening the studio', detail: '' });
  setUi(next);
}

// ----------------------------------------------------------------- startup

// One launch at a time: start.sh must never run twice at once, and a second
// click on a button must not begin a second pass.
async function launch() {
  if (launching) return;
  launching = true;
  try {
    await runLaunch();
  } finally {
    launching = false;
  }
}

async function runLaunch() {
  const mine = ++attempt;
  const current = () => mine === attempt && quitState === 'no';
  try {
    if (!(await platform.isInstalled())) {
      // A platform that can say what installing involves gets a screen that
      // shows it and waits for Install; nothing is downloaded or changed
      // before that. Coming back from the restart setup asked for counts as
      // already agreed.
      const resumed = typeof platform.isResumeLaunch === 'function' && platform.isResumeLaunch(process.argv);
      if (typeof platform.planInstall === 'function' && !installApproved && !resumed) {
        const plan = await platform.planInstall();
        if (!current()) return;
        setUi({ view: 'install', plan: describePlan(plan) });
        await showLoadingPage();
        return;
      }
      setUi({ view: 'starting', startedAt: Date.now(), steps: [], label: 'Setting up Guaardvark', detail: '' });
      await showLoadingPage();
      await platform.install((evt) => current() && applyProgress(evt), installChoice);
    }
    if (!current()) return;

    // An earlier run of the app may have left a stop still going.
    if (typeof platform.pendingWork === 'function' && platform.pendingWork()) {
      setUi({ view: 'starting', startedAt: Date.now(), steps: [], label: 'Waiting for Guaardvark to finish stopping', detail: 'It starts again as soon as that is done.' });
      await showLoadingPage();
      await platform.waitForPendingWork();
      if (!current()) return;
    }

    if (await platform.isRunning()) {
      if (current()) await showStudio(platform.describe().webUrl);
      return;
    }
    if (!current()) return;

    setUi({ view: 'starting', startedAt: Date.now(), steps: [], label: 'Starting Guaardvark', detail: '' });
    await showLoadingPage();
    startedThisRun = true;
    const { webUrl } = await platform.start((evt) => current() && applyProgress(evt));
    if (current()) await showStudio(webUrl);
  } catch (err) {
    if (!current()) return;
    if (err.code === 'RESTART_REQUIRED') {
      setUi({ view: 'restart', detail: '' });
      await showLoadingPage();
      return;
    }
    await showFailure(err.event || { label: 'Guaardvark did not start', detail: String(err.message || err) });
  }
}

function sizeText(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  // As Windows shows sizes: 1 GB = 1024^3 bytes.
  return bytes >= 2 ** 30 ? `${(bytes / 2 ** 30).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 2 ** 20))} MB`;
}

// The plan screen's text, from whatever planInstall() returned: its steps and
// what the person should expect (a permission prompt, a restart, a download).
function describePlan(plan = {}) {
  const notes = [];
  if (plan.download) {
    const size = sizeText(plan.download.bytes);
    const from = plan.download.from ? ` from ${plan.download.from}` : '';
    notes.push(size ? `Downloads ${size}${from}.` : `Downloads Guaardvark's files${from}.`);
  }
  if (plan.needsAdmin) notes.push('Your computer will ask for permission to make changes.');
  if (plan.needsRestart) notes.push('One restart is needed part-way through. Setup carries on by itself after you sign in.');
  const mem = plan.memory;
  if (mem && mem.userValue) notes.push(`Your own memory setting for Linux (${mem.userValue}) stays as it is.`);
  // Checked before anything is downloaded: the graphics card and driver,
  // and the free space. A warning lets the person go on; too little space
  // does not.
  const warnings = [];
  const gpu = plan.gpu;
  if (gpu && gpu.status === 'none') {
    warnings.push('No NVIDIA graphics card was found. On Windows, Guaardvark needs an NVIDIA card to make pictures and video. Chat still works, on the processor.');
  } else if (gpu && gpu.status === 'old-driver') {
    warnings.push(`Your NVIDIA driver is version ${gpu.driver}. Update your NVIDIA driver to ${gpu.minDriver} or newer (nvidia.com/drivers), then run Guaardvark again. If you continue now, setup also downloads a version of PyTorch that works with this driver.`);
  }
  const disk = plan.disk;
  let blocked = null;
  if (disk && !disk.enough) {
    blocked = `Not enough free disk space on ${disk.drive}: Guaardvark needs about ${disk.text.needed} (the download, its Linux system and the first AI models) and ${disk.text.free} is free. Free some space, then open Guaardvark again.`;
  } else if (disk && disk.text.free) {
    notes.push(`Needs about ${disk.text.needed} of free space on ${disk.drive}; ${disk.text.free} is free.`);
  }
  return {
    steps: (plan.steps || []).map((s) => ({ step: s.id, label: s.label || s.id, state: 'pending' })),
    notes,
    warnings: blocked ? [blocked, ...warnings] : warnings,
    blocked: Boolean(blocked),
    installLabel: warnings.length ? 'Continue anyway' : 'Install',
    // Offered when the PC has more memory than WSL gives Linux by default.
    memory: mem && mem.offered ? { label: mem.label, on: Boolean(mem.defaultOn) } : null,
  };
}

async function restartComputer() {
  if (typeof platform.restartComputer !== 'function' || ui.view !== 'restart') return;
  const ok = await platform.restartComputer();
  if (ok) { finishQuit(); return; }
  setUi({ view: 'restart', detail: 'The restart did not begin. Restart from the Start menu; Guaardvark opens again after you sign in.' });
}

// -------------------------------------------------------------------- quit

function ownsInstance() {
  if (typeof platform.wasStartedByApp === 'function') return platform.wasStartedByApp();
  return startedThisRun;
}

async function beginQuit() {
  if (quitState !== 'no') return;
  let force = false;

  if (!ownsInstance()) {
    // Guaardvark was already running when the window opened. It may be in use
    // from a browser or doing work; keeping it running is the default.
    const stillRunning = win && !win.isDestroyed() && studioUrl && await platform.isRunning();
    if (stillRunning && !ownsInstance()) {
      quitState = 'asking';
      const { response } = await dialog.showMessageBox(win, {
        type: 'question',
        title: 'Guaardvark',
        message: 'Guaardvark was already running when this window opened.',
        detail: 'Closing the window leaves it running, so it stays available in your browser and any work in progress continues.',
        buttons: ['Keep it running', 'Stop Guaardvark', 'Cancel'],
        defaultId: 0,
        cancelId: 2,
        noLink: true,
      });
      if (response === 2) { quitState = 'no'; return; }
      force = response === 1;
    }
    if (!force) { finishQuit(); return; }
  }

  quitState = 'stopping';
  // With keep-alive on, stop() only lets go of the instance; nothing is
  // stopped, so there is no stopping screen to show.
  const keeping = !force && hasKeepAlive && platform.getKeepAlive();
  if (!keeping && win && !win.isDestroyed()) {
    setUi({ view: 'stopping' });
    onLoadingPage = false;
    try {
      await showLoadingPage();
    } catch {
      // The stop below does not depend on the page.
    }
  }
  try {
    await platform.stop({ force });
  } finally {
    if (reopenAfterStop && win && !win.isDestroyed()) {
      // Opened again while stopping: carry on as a fresh launch in this window.
      reopenAfterStop = false;
      quitState = 'no';
      // A launch that was cut short by the quit may still be unwinding.
      const again = () => (launching ? setTimeout(again, 200) : launch());
      again();
    } else {
      finishQuit();
    }
  }
}

// Chromium writes cookies and page storage to disk on a timer; a quit inside
// that interval would lose a sign-in or a setting made just before it.
async function flushSession() {
  const ses = session.defaultSession;
  try {
    ses.flushStorageData();
    await ses.cookies.flushStore();
  } catch {
    // Quitting matters more than the flush.
  }
}

async function finishQuit() {
  quitState = 'done';
  saveWindowState();
  await flushSession();
  app.quit();
}

// The desktop is logging out or shutting down, or the app was sent a
// termination signal. Nobody is there to answer a question and the system is
// waiting, so: save the session, stop Guaardvark only if this app started it
// and it is not set to keep running, and leave within the time allowed
// whatever happens. A stop.sh that needs longer carries on by itself.
const SESSION_END_BUDGET_MS = process.platform === 'win32' ? 4000 : 25000;
let sessionEnding = false;

async function endForSessionEnd() {
  if (sessionEnding || quitState === 'done') return;
  sessionEnding = true;
  const leave = () => { quitState = 'done'; app.exit(0); };
  setTimeout(leave, SESSION_END_BUDGET_MS);
  saveWindowState();
  await flushSession();
  const keeping = hasKeepAlive && platform.getKeepAlive();
  // A stop already under way (the window was closing) is left to finish.
  if (quitState !== 'stopping' && ownsInstance() && !keeping) {
    quitState = 'stopping';
    if (win && !win.isDestroyed()) {
      setUi({ view: 'stopping' });
      onLoadingPage = false;
      showLoadingPage().catch(() => {});
    }
    try {
      await platform.stop();
    } catch {
      // Leaving is still right.
    }
    leave();
  } else if (quitState !== 'stopping') {
    leave();
  }
}

// --------------------------------------------------------------------- app

function fromLoadingPage(event) {
  return Boolean(event.senderFrame && isAppPage(event.senderFrame.url));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!win || win.isDestroyed()) return;
    if (quitState === 'stopping' && !reopenAfterStop) {
      reopenAfterStop = true;
      setUi({ view: 'stopping', reopening: true });
      if (!onLoadingPage) showLoadingPage().catch(() => {});
    }
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
  });

  app.on('web-contents-created', (_event, contents) => guardContents(contents));

  app.on('before-quit', (event) => {
    if (quitState === 'done') return;
    event.preventDefault();
    beginQuit();
  });

  // The quit path above decides when the app ends.
  app.on('window-all-closed', () => {});

  ipcMain.handle('gv:get-ui', (event) => (fromLoadingPage(event) ? ui : null));
  ipcMain.on('gv:retry', (event) => { if (fromLoadingPage(event) && quitState === 'no') launch(); });
  ipcMain.on('gv:install', (event, choice) => {
    if (!fromLoadingPage(event) || quitState !== 'no' || ui.view !== 'install' || ui.plan.blocked) return;
    installApproved = true;
    installChoice = choice && typeof choice.memory === 'boolean' ? { memory: choice.memory } : {};
    launch();
  });
  ipcMain.on('gv:restart', (event) => { if (fromLoadingPage(event) && quitState === 'no') restartComputer(); });
  ipcMain.on('gv:show-log', (event) => { if (fromLoadingPage(event)) shell.openPath(platform.logPath()); });
  ipcMain.on('gv:quit', (event) => { if (fromLoadingPage(event)) beginQuit(); });

  app.whenReady().then(() => {
    // Registered here, not earlier: Chromium installs its own handlers for
    // these signals while it starts, and they turn a signal into an ordinary
    // quit, which would stop to ask a question nobody is there to answer.
    for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(signal, () => endForSessionEnd());
    // Linux and macOS: the system is shutting down or restarting.
    powerMonitor.on('shutdown', (event) => {
      event.preventDefault();
      endForSessionEnd();
    });
    serveAppFiles();
    lockDownSession();
    buildMenu();
    createWindow();
    launch();
  });
}
