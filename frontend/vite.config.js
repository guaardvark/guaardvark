import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createLogger, defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import rollupNodePolyFill from "rollup-plugin-polyfill-node";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");
const EXTENSIONS_ROOT = path.join(REPO_ROOT, "extensions");
// Real paths of extension folders (they may be symlinks into private checkouts),
// so the dev server is allowed to read them.
const extensionRealPaths = () => {
  try {
    return fs.readdirSync(EXTENSIONS_ROOT)
      .filter((name) => !name.startsWith("_") && !name.startsWith("."))
      .map((name) => fs.realpathSync(path.join(EXTENSIONS_ROOT, name)));
  } catch { return []; }
};

// Extension frontends live in ../extensions/<id>/frontend, outside this
// package, so a bare import like "@mui/material" from one of them has no
// node_modules above it. Re-resolve such imports as if they came from src/,
// so an extension uses core's dependencies without a copy of node_modules.
const extensionsNodeModules = () => ({
  name: "extensions-node-modules",
  enforce: "pre",
  async resolveId(source, importer, options) {
    // Extension folders may be symlinks; Vite hands us the real path, so key
    // on "outside this package and not a dependency" rather than the folder.
    if (!importer || importer.startsWith(__dirname) || importer.includes("/node_modules/")) return null;
    if (source.startsWith(".") || source.startsWith("/") || source.startsWith("@/") || source.startsWith("\0")) return null;
    const asIfFromCore = path.join(__dirname, "src", "__extension_import__.js");
    return this.resolve(source, asIfFromCore, { ...options, skipSelf: true });
  },
});

// Present while the Interconnector writes synced files. Hot updates in that
// window mix old and new modules, so they are held and the page reloads once after.
const SYNC_MARKER = path.join(REPO_ROOT, "data", "dep_reconciler", ".sync_in_progress");
// The sync clears the marker in a finally block; an older one was left by a killed process.
const SYNC_MARKER_STALE_MS = 10 * 60 * 1000;
const HELD_PAYLOADS = new Set(["update", "full-reload", "error"]);

const syncInProgress = () => {
  try {
    return Date.now() - fs.statSync(SYNC_MARKER).mtimeMs < SYNC_MARKER_STALE_MS;
  } catch {
    return false;
  }
};

const pauseHmrDuringSync = () => {
  let holding = false;
  let hold = () => {};
  return {
    name: "pause-hmr-during-sync",
    apply: "serve",
    configureServer(server) {
      if (typeof server.ws?.send !== "function") return;
      // server.ws is also the client environment's hot channel, so this sees
      // module updates, the HTML page reload and the dep optimizer's reload.
      const send = server.ws.send;
      let poll = null;
      hold = () => {
        holding = true;
        if (poll) return;
        server.config.logger.info("sync in progress: holding hot updates", { timestamp: true });
        poll = setInterval(() => {
          if (syncInProgress()) return;
          clearInterval(poll);
          poll = null;
          holding = false;
          server.config.logger.info("sync finished: reloading the page", { timestamp: true });
          send({ type: "full-reload", path: "*" });
        }, 500);
        poll.unref?.();
      };
      server.ws.send = (...args) => {
        const payload = args[0];
        if (HELD_PAYLOADS.has(payload?.type) && (holding || syncInProgress())) {
          hold();
          return;
        }
        send(...args);
      };
      server.httpServer?.once("close", () => poll && clearInterval(poll));
      // A config change mid-sync restarts this server; keep holding until the end.
      if (syncInProgress()) hold();
    },
    hotUpdate() {
      if (!holding && !syncInProgress()) return;
      hold();
      return [];
    },
  };
};

// Socket.IO disconnects (page refresh, backend restart, transport retry) reset the
// proxied TCP socket; Vite logs that as "ws proxy error: ECONNRESET" even though
// the client reconnects fine via polling/websocket. Filter the benign noise only.
const BENIGN_PROXY_RESET = /ECONN(RESET|ABORTED)|EPIPE|socket hang up/i;
const viteLogger = createLogger();
const logError = viteLogger.error.bind(viteLogger);
viteLogger.error = (msg, options) => {
  const text = typeof msg === "string" ? msg : String(msg);
  if (text.includes("ws proxy") && BENIGN_PROXY_RESET.test(text)) return;
  logError(msg, options);
};

function resolvePorts(mode) {
  // Repo .env lives at the repo root (not frontend/.env). start.sh exports these,
  // but `npm run dev` from frontend/ must still pick up FLASK_PORT / VITE_PORT from it.
  const rootEnv = loadEnv(mode, REPO_ROOT, "");
  const flaskPort =
    process.env.FLASK_PORT ||
    process.env.FLASK_RUN_PORT ||
    rootEnv.FLASK_PORT ||
    "5000";
  const vitePort = process.env.VITE_PORT || rootEnv.VITE_PORT || "5173";
  return { flaskPort, vitePort };
}

function buildProxy(flaskPort) {
  const target = `http://127.0.0.1:${flaskPort}`;
  const shared = {
    target,
    changeOrigin: true,
    secure: false,
    xfwd: true,
    configure: (proxy) => {
      proxy.on("error", (err, _req, res) => {
        // Honest status when the backend is unreachable. Without this, Vite's
        // built-in proxy error handler writes a hardcoded HTTP 500 for every
        // ECONNREFUSED — which reads as "the server crashed" when the backend is
        // simply down. Writing the response here (for the /api path, where `res`
        // is a ServerResponse) sets res.headersSent, so Vite's handler skips its
        // 500. A 502 + machine-readable body lets the client show "backend
        // offline" instead of a misleading wall of 500s. The /socket.io WS path
        // passes a raw socket (no writeHead) — left to reconnect on its own.
        if (res && typeof res.writeHead === "function" && !res.headersSent) {
          res.writeHead(502, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "backend_offline" }));
        }
        if (BENIGN_PROXY_RESET.test(err?.message || "")) return;
        logError(`[vite] http proxy error: ${err?.message || err}`);
      });
      proxy.on("proxyReqWs", (_proxyReq, _req, socket) => {
        socket.on("error", (err) => {
          if (BENIGN_PROXY_RESET.test(err?.message || "")) return;
          logError(`[vite] ws proxy socket error: ${err?.message || err}`);
        });
      });
    },
  };
  return {
    "/api": shared,
    "/socket.io": { ...shared, ws: true },
  };
}

function resolveAllowedHosts(rootEnv) {
  // Extra hostnames/IPs allowed to reach the dev server (comma-separated).
  // Set VITE_ALLOWED_HOSTS to your LAN IP (e.g. "192.168.1.108") to reach the UI
  // from another device, or "all" to skip the host check entirely (trusted nets only).
  const extraAllowedHosts = (process.env.VITE_ALLOWED_HOSTS || rootEnv.VITE_ALLOWED_HOSTS || "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean);
  if (extraAllowedHosts.includes("all")) {
    return "all";
  }
  // Always allow the machine's own hostname (and its Bonjour/.local form) so the
  // box serves the UI under its own name even when start.sh's LAN detection comes
  // up empty (#41: a Mac reached via raw hostname/IP got an opaque Vite "403
  // Forbidden / Blocked request" with no hint). `.local` already covers *.local
  // Bonjour names; this adds the bare hostname (e.g. "vogon") and a normalized
  // "<host>.local" so neither form locks the operator out of their own machine.
  const selfHost = (os.hostname() || "").trim().toLowerCase();
  const selfHosts = selfHost
    ? [selfHost, selfHost.endsWith(".local") ? selfHost : `${selfHost}.local`]
    : [];
  return [
    "localhost",
    "127.0.0.1",
    ".local",
    ...selfHosts,
    ...extraAllowedHosts,
  ];
}

// Package -> output chunk. Everything else under node_modules stays in the
// default vendor split.
const VENDOR_CHUNKS = Object.fromEntries([
  ...['react', 'react-dom'].map((n) => [n, 'vendor']),
  ...['@mui/material', '@mui/icons-material', '@emotion/react', '@emotion/styled'].map((n) => [n, 'mui']),
  ['react-router-dom', 'routing'],
  ...['axios', 'socket.io-client'].map((n) => [n, 'api']),
  ...['zustand', 'react-grid-layout', 'react-markdown', 'react-syntax-highlighter'].map((n) => [n, 'utils']),
]);

export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, REPO_ROOT, "");
  const { flaskPort, vitePort } = resolvePorts(mode);
  const allowedHosts = resolveAllowedHosts(rootEnv);
  // Shared by both the dev (`server`) and production-preview (`preview`) servers.
  // The frontend calls a relative "/api" and connects the socket to the page
  // origin, so whichever server serves the page must proxy these to Flask.
  // `xfwd: true` forwards the originating client IP as X-Forwarded-For — the backend
  // auth_guard relies on it to still recognize a LAN device (proxied via loopback)
  // as remote, so proxying the UI does not silently bypass the host check.
  const proxy = buildProxy(flaskPort);

  return {
  customLogger: viteLogger,
  plugins: [react(), extensionsNodeModules(), pauseHmrDuringSync()],
  resolve: {
    // `@` is core: extensions import it as `@/api/apiClient` instead of
    // counting `../` up to wherever core sits.
    alias: [
      { find: /^@\//, replacement: `${path.resolve(__dirname, "src")}/` },
      // @mui/icons-material 5.x has no exports map, so `@mui/icons-material/Add`
      // resolves to its CommonJS file. Vite 8's pre-bundler hands a default
      // import of that file the module object, not the component ("Element type
      // is invalid ... got: object"). Point every icon import at the ESM build.
      { find: /^@mui\/icons-material(?!\/esm)(\/.*)?$/, replacement: "@mui/icons-material/esm$1" },
    ],
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.js',
    include: [
      'src/**/*.{test,spec}.{js,jsx,ts,tsx}',
      '../extensions/*/frontend/**/*.{test,spec}.{js,jsx,ts,tsx}',
    ],
    coverage: {
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'src/test/'],
    },
  },
  optimizeDeps: {
    include: [
      '@emotion/react',
      '@emotion/styled',
      '@mui/material',
      '@mui/material/Tooltip',
      '@mui/material/Popper',
      '@popperjs/core',
      '@mui/icons-material',
      'react',
      'react-dom',
      'react/jsx-runtime'
    ],
    // Vite 8 pre-bundles with Rolldown, which takes no esbuild plugins: the
    // esbuild-only Node polyfill plugins made every dependency scan fail and
    // left the dev server with no pre-bundled deps at all. The build keeps its
    // rollup polyfill below; `global` is defined once at the top level.
  },
  define: {
    global: "globalThis",
  },
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      plugins: [rollupNodePolyFill()],
      // Rolldown's timing report names the terser pass on every build. That
      // cost is expected for this bundle, so the report is only noise.
      checks: { bundlerTimings: false },
      output: {
        // Function form: Vite 8 bundles with Rolldown, which does not accept
        // the object form ("manualChunks is not a function"). Same groups.
        manualChunks: (id) => {
          // MUI's locale packs load only when a language other than English
          // is picked (src/i18n/muiLocale.js), so they stay their own chunk.
          if (/[\\/]@mui[\\/]material[\\/]locale[\\/]/.test(id)) return undefined;
          const m = id.match(/[\\/]node_modules[\\/]((?:@[^\\/]+[\\/])?[^\\/]+)/);
          if (!m) return undefined;
          return VENDOR_CHUNKS[m[1]];
        },
      }
    },
    sourcemap: false,
    minify: 'terser',
    terserOptions: {
      compress: {
        drop_console: false,
        drop_debugger: true,
        pure_funcs: ['console.debug'],  // Only strip debug, keep error/warn/log
      },
    },
  },
  // Web workers are bundled separately and do not inherit build.rollupOptions.
  worker: {
    rolldownOptions: {
      checks: { bundlerTimings: false },
    },
  },
  server: {
    host: '0.0.0.0',
    port: parseInt(vitePort, 10),
    strictPort: true,
    allowedHosts,
    proxy,
    // Extension frontends live outside frontend/ (extensions/<id>/frontend).
    fs: { allow: [REPO_ROOT, ...extensionRealPaths()] },
  },
  // `start.sh` serves the production build via `vite preview`, which does NOT
  // share the `server:` block above — so host allowlist + API/WS proxy must be
  // repeated here, or LAN clients get "Blocked request" and /api + sockets 404.
  preview: {
    host: '0.0.0.0',
    port: parseInt(vitePort, 10),
    strictPort: true,
    allowedHosts,
    proxy,
  },
};
});
