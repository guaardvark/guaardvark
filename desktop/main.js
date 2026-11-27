// Guaardvark's desktop window. Starts Guaardvark if it is not running, shows
// each startup step, then shows the studio. Closing the window stops what the
// app started.

const { app, BrowserWindow, Menu, dialog, ipcMain, screen, session, shell } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const LOADING_PAGE = path.join(__dirname, 'loading.html');
const LOADING_URL = pathToFileURL(LOADING_PAGE).href;
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

const platform = require('./platform').load();

let win = null;
let startedThisRun = false;   // start() was called in this run of the app
let onLoadingPage = false;
let attempt = 0;              // bumped by every launch(); stale results are dropped
let quitState = 'no';         // no | asking | stopping | done
let studioUrl = null;

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
    if (protocol === 'file:') return url.startsWith(pathToFileURL(__dirname).href);
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
    if (!isAllowedRequest(url) || url.startsWith('file:')) return { action: 'deny' };
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

  win.webContents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    // -3 is a load this app replaced with another one.
    if (!isMainFrame || code === -3 || quitState !== 'no' || !studioUrl || !url.startsWith(studioUrl)) return;
    showFailure({
      label: 'The studio page did not open',
      detail: `${url} did not answer (${description}).`,
    });
  });
}

function buildMenu() {
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {
      label: 'Guaardvark',
      submenu: [
        { label: 'Reload', accelerator: 'CmdOrCtrl+R', click: () => win && win.webContents.reload() },
        { type: 'separator' },
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

// ------------------------------------------------------------ loading page

function setUi(next) {
  ui = next;
  if (win && !win.isDestroyed() && onLoadingPage) win.webContents.send('gv:ui', ui);
}

async function showLoadingPage() {
  if (!win || win.isDestroyed() || onLoadingPage) return;
  onLoadingPage = true;
  await win.loadFile(LOADING_PAGE);
}

async function showStudio(url) {
  if (!win || win.isDestroyed()) return;
  studioUrl = url;
  onLoadingPage = false;
  try {
    await win.loadURL(url);
  } catch {
    // did-fail-load draws the failure.
  }
}

async function showFailure(evt) {
  setUi({
    view: 'failed',
    label: evt.label || 'Guaardvark did not start',
    detail: evt.detail || '',
    steps: (ui.steps || []).map((s) => (s.state === 'running' ? { ...s, state: 'failed' } : s)),
  });
  await showLoadingPage();
}

// Folds one progress event into the list of steps the page draws.
function applyProgress(evt) {
  if (!evt || typeof evt !== 'object' || ui.view !== 'starting') return;
  const steps = ui.steps.slice();
  const next = { ...ui, steps };
  const stateName = { start: 'running', ok: 'done', skip: 'skipped', fail: 'failed' }[evt.state];
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

async function launch() {
  const mine = ++attempt;
  const current = () => mine === attempt && quitState === 'no';
  try {
    if (!(await platform.isInstalled())) {
      setUi({ view: 'starting', startedAt: Date.now(), steps: [], label: 'Installing Guaardvark', detail: '' });
      await showLoadingPage();
      await platform.install((evt) => current() && applyProgress(evt));
    }
    if (!current()) return;

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
    await showFailure(err.event || { label: 'Guaardvark did not start', detail: String(err.message || err) });
  }
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
  if (win && !win.isDestroyed()) {
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
    finishQuit();
  }
}

function finishQuit() {
  quitState = 'done';
  saveWindowState();
  app.quit();
}

// --------------------------------------------------------------------- app

function fromLoadingPage(event) {
  return Boolean(event.senderFrame && event.senderFrame.url.startsWith(LOADING_URL));
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!win || win.isDestroyed()) return;
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
  ipcMain.on('gv:show-log', (event) => { if (fromLoadingPage(event)) shell.openPath(platform.logPath()); });
  ipcMain.on('gv:quit', (event) => { if (fromLoadingPage(event)) beginQuit(); });

  app.whenReady().then(() => {
    lockDownSession();
    buildMenu();
    createWindow();
    launch();
  });
}
