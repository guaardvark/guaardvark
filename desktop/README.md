# Guaardvark desktop app

One window around the studio. Opening it starts Guaardvark if it is not running, shows each
startup step while that happens, and then shows the studio. Closing it stops what it started.

## Run it from a checkout (Linux)

```
cd desktop
npm install
npx install-electron          # downloads the Electron binary; `npm install` alone does not
linux/guaardvark-desktop.sh
```

To get a Guaardvark entry in the applications menu that runs the same launcher:

```
linux/install-shortcut.sh             # writes ~/.local/share/applications/guaardvark.desktop and the icon
linux/install-shortcut.sh --uninstall
```

The shortcut points at this checkout, so it stops working if the checkout moves; run the
installer again from the new place.

## What it does

- **One copy.** A second launch brings the first window forward and exits.
- **Already running.** If `GET <api>/api/health` answers 200 with a `status` and the web
  address answers, the studio opens at once and `start.sh` is not called.
- **Not running.** The loading screen appears and the app runs
  `start.sh --no-browser --progress-file <file>` in its own session, then follows the file
  ([PROTOCOL.md](PROTOCOL.md)): the step labels, the bar from `pct`, the current `detail` line
  and the elapsed time. On `ready` the window switches to the studio. Because `start.sh` has
  its own session, it finishes and Guaardvark keeps running even if the app is killed; the
  next launch finds it through the health check.
  A step that reports `fail` without the `failed` line after it is drawn as a warning and
  startup carries on. The app never runs two `start.sh` at once: a new start waits until an
  earlier one has exited.
- **First run on a platform that installs.** When the platform module is not installed and
  offers `planInstall()`, the window shows the plan (its steps, a permission prompt, a restart,
  the download) and waits for **Install**. If `install()` then stops with `RESTART_REQUIRED`,
  the window shows a restart screen with **Restart now** and **Later** in place of the failure
  screen. A launch with `--resume-setup` (the one Windows makes after that restart) carries on
  with setup without asking again.
- **Start failed.** The `failed` line's `label` and `detail`, with **Try again**, **Show log**
  (opens the `log` named in that line, or the start script's own output when it names none)
  and **Close**.
- **Closing the window.** If the app started Guaardvark, it shows "Stopping Guaardvark..." and
  runs `stop.sh`, then exits. If Guaardvark was already running, it asks; the default,
  **Keep it running**, closes only the window. An instance the app started and left running
  (for example after the app was killed) is recognised by the backend's boot id on the next
  launch and is stopped on close like any other it started.
- **Keep running after closing (Windows).** The app menu has *Keep Guaardvark running after
  closing* when the platform module has `getKeepAlive()`. It explains that the setting covers
  every Linux system on the PC and asks before changing anything or restarting the Linux layer.
- **Window.** Size and position are remembered. The address is always
  `http://localhost:<web port>`, the one a browser uses, because the studio keeps its settings
  per address. The app has its own browser profile, so a setting saved in a browser is set
  once more here.
- **Sign-in.** An install with an API key asks each new browser for it. The app signs its own
  window in instead: it reads `GUAARDVARK_API_KEY` from the install's `.env` (`getApiKey()` in
  the platform module) and sends it to `POST /api/auth/session` on the loopback backend, the
  route Settings → Access uses, which answers with the session cookie. The key is not logged,
  shown or saved by the app. If that fails, the studio asks for the key as it does in a browser.

## Network

The window talks to this computer only.

- Pages run with `contextIsolation` on, `nodeIntegration` off and the renderer sandbox on. The
  preload script gives the loading page five calls and gives studio pages nothing.
- Every request to a host other than `localhost`, `127.0.0.1` or `[::1]` is refused, and the
  app's resolver answers "not found" for every other name without a DNS lookup.
- A link that leaves the studio opens in your own browser. The window itself never navigates
  off this computer.
- Chromium's background services (component updates, the spelling dictionary download,
  reliability reports, sync, translation, crash upload) are off.

To check it yourself, start the app with `--log-net-log=<file>` and look for any host other
than loopback. One address does appear: Chromium tests whether the machine has an IPv6 route
by calling `connect()` on a UDP socket aimed at `2001:4860:4860::8888`. That call sends no
packet.

## The sandbox on Ubuntu

Chromium's sandbox needs either unprivileged user namespaces or a `chrome-sandbox` helper
owned by root with mode 4755. Ubuntu 24.04 and later restrict the first, and the Electron that
npm unpacks does not have the second, so `linux/guaardvark-desktop.sh` adds `--no-sandbox` on
those machines and leaves the sandbox on everywhere else. To keep it on there too:

```
sudo chown root node_modules/electron/dist/chrome-sandbox
sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
```

## Files

| File | What it is |
|------|------------|
| `main.js` | The app: window, request rules, startup and quit |
| `preload.js` | The loading page's calls into the app |
| `loading.html`, `loading.css`, `loading.js` | Loading, failure and stopping screens |
| `platform/index.js` | Picks the platform module |
| `platform/linux.js` | Linux: `start.sh`, `stop.sh`, the health check, the progress file |
| `linux/guaardvark-desktop.sh` | Launcher |
| `linux/install-shortcut.sh` | Applications menu shortcut |
| `build/` | App icons |
| `assets/icon-256.png` | The icon the loading screen shows |

## Settings

| Variable | Effect |
|----------|--------|
| `GUAARDVARK_INSTALL_ROOT` | The Guaardvark install to run. Default: the checkout this folder is in |
| `FLASK_PORT`, `VITE_PORT` | Ports, with the precedence `start.sh` uses: environment, then the install's `.env`, then 5000 and 5173 |
| `GUAARDVARK_DESKTOP_STATE_DIR` | Where the progress file and the start and stop output go. Default: `~/.local/state/guaardvark-desktop` |
| `GUAARDVARK_DESKTOP_PLATFORM_MODULE` | For development, ignored by a packaged app: a file that exports a platform object to use in place of this system's, so another system's flow can be driven against stand-in tools |
| `GUAARDVARK_DESKTOP_REQUEST_LOG` | For checks: a file that gets one line per request the window makes, marked `ALLOW` or `REFUSE` |
| `GUAARDVARK_DESKTOP_READONLY=1` | For checks: also refuses every request that is not a plain read, so the app can look at a studio someone is using without changing anything |

## For the other platform modules

`main.js` uses these beyond the interface in `platform/index.js`. All are optional; a module
without one simply does not get the screen or menu item that goes with it.

- `wasStartedByApp()`: true when the running instance is this app's to stop. Without it,
  `main.js` counts only a start made in the current run.
- `stop({ force: true })`: stop an instance the app did not start, after the person chose
  **Stop Guaardvark**.
- `planInstall()` -> `{ steps: [{ id, label }], needsAdmin, needsRestart, download: { from, bytes } }`
  for the screen before `install()`. It must not download or change anything.
- `install()` rejecting with `err.code === 'RESTART_REQUIRED'`, and `restartComputer()`.
- `isResumeLaunch(argv)`: this launch continues a setup the person already agreed to.
- `getApiKey()` -> the install's API key or `''`, read without logging it.
- `getKeepAlive()`, `setKeepAlive(on)` -> `{ needsWslRestart }`, `applyWslSettings()`.
