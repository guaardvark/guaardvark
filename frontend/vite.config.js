import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createLogger, defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { NodeGlobalsPolyfillPlugin } from "@esbuild-plugins/node-globals-polyfill";
import { NodeModulesPolyfillPlugin } from "@esbuild-plugins/node-modules-polyfill";
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
  // but `npm run dev` from frontend/ must still pick up FLASK_PORT=5002 / VITE_PORT=5175.
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
  plugins: [react(), extensionsNodeModules()],
  resolve: {
    // `@` is core: extensions import it as `@/api/apiClient` instead of
    // counting `../` up to wherever core sits.
    alias: { "@": path.resolve(__dirname, "src") },
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
    esbuildOptions: {
      define: {
        global: "globalThis",
      },
      plugins: [
        NodeGlobalsPolyfillPlugin({
          buffer: true,
          process: true,
          global: true,
        }),
        NodeModulesPolyfillPlugin(),
      ],
    },
  },
  build: {
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      plugins: [rollupNodePolyFill()],
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          mui: ['@mui/material', '@mui/icons-material', '@emotion/react', '@emotion/styled'],
          routing: ['react-router-dom'],
          api: ['axios', 'socket.io-client'],
          utils: ['zustand', 'react-grid-layout', 'react-markdown', 'react-syntax-highlighter']
        }
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
