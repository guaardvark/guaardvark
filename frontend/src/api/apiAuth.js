// frontend/src/api/apiAuth.js
// How this browser is signed in to its Guaardvark, and what to say when a
// protected route refuses it.
//
// Protected backend routes (backend/utils/auth_guard.py) answer the Guaardvark
// machine itself while the install has no API key. Once it has one they answer
// the key in X-API-Key (command-line clients, the MCP server, scripts) or a
// browser signed in with it. Settings → Access sends the key once to
// POST /api/auth/session; the backend answers with an HttpOnly cookie holding a
// token derived from the key. Page scripts never keep the key and cannot read
// the cookie, so an injected script cannot take it. The browser sends the
// cookie by itself to the page's own origin, which is where the app's /api
// lives (the dev and preview servers and the Docker nginx proxy it to Flask).
//
// Only when the build points the API at another origin (VITE_API_BASE_URL,
// VITE_SOCKET_URL or VITE_API_URL naming another host or port) do requests to
// that backend need credentials: "include"; installBackendCredentials() adds
// it to those requests and to no others. It also passes the guard's refusals
// (a JSON body whose code is local_only or api_key_required) from axios to
// AUTH_REFUSED_EVENT; handleResponse does the same for fetch.
/* eslint-env browser */

export const API_KEY_SETTINGS_PATH = "/settings#settings-api-key";
export const AUTH_REFUSED_EVENT = "guaardvark:auth-refused";
export const SESSION_CHANGED_EVENT = "guaardvark:session-changed";
export const AUTH_REFUSAL_CODES = ["local_only", "outside_local_network", "api_key_required"];

const CHANNEL = "guaardvark-auth";
// Marks this tab's own channel messages, which it already handled through the
// window event.
const TAB_ID = Math.random().toString(36).slice(2);
const INSTALLED = Symbol.for("guaardvark.backendCredentials");

const defaultTargets = () => ({
  apiBase: import.meta.env.VITE_API_BASE_URL || "/api",
  backendUrls: [import.meta.env.VITE_SOCKET_URL, import.meta.env.VITE_API_URL],
});

const parse = (value, base) => {
  try {
    return new URL(String(value), base);
  } catch {
    return null;
  }
};

/**
 * Origins other than the page's own that the build names as the backend.
 * Empty in the usual setup, where /api is proxied on the page's origin.
 * `options` (for tests): { location, apiBase, backendUrls }.
 */
export function crossOriginBackends(options = {}) {
  const location = options.location || window.location;
  const { apiBase, backendUrls } = { ...defaultTargets(), ...options };
  return [...new Set(
    [apiBase, ...(backendUrls || [])]
      .map((u) => (u ? parse(u, location.href) : null))
      .filter((u) => u && (u.protocol === "http:" || u.protocol === "https:") && u.origin !== location.origin)
      .map((u) => u.origin),
  )];
}

/** True when `url` is this app's own backend. */
export function isBackendUrl(url, options = {}) {
  if (!url) return false;
  const location = options.location || window.location;
  const target = parse(url, location.href);
  if (!target || (target.protocol !== "http:" && target.protocol !== "https:")) return false;
  // A backend on an origin of its own: every path on it is the backend.
  if (crossOriginBackends(options).includes(target.origin)) return true;
  // The page's own origin also serves the app's files, so only the API path
  // there is the backend.
  if (target.origin !== location.origin) return false;
  const { apiBase } = { ...defaultTargets(), ...options };
  const api = parse(apiBase, location.href);
  const prefixes = ["/api"];
  if (api && api.origin === location.origin) prefixes.push(api.pathname.replace(/\/+$/, "") || "/api");
  return prefixes.some((p) => target.pathname === p || target.pathname.startsWith(`${p}/`));
}

/**
 * True when a request to `url` must ask for credentials to carry the sign-in
 * cookie: only a backend the build names on another origin. Same-origin
 * requests carry it already; other hosts never get it.
 */
export function needsCredentials(url, options = {}) {
  if (!url) return false;
  const location = options.location || window.location;
  const target = parse(url, location.href);
  return Boolean(target) && crossOriginBackends(options).includes(target.origin);
}

/** The guard's refusal code in a response body, or null. */
export function authRefusalCode(body) {
  const code = body && typeof body === "object" ? body.code : null;
  return AUTH_REFUSAL_CODES.includes(code) ? code : null;
}

/**
 * What to do about a refusal, in the words the UI shows. `rejected`: the
 * request carried a sign-in or key that is not accepted now.
 */
export function describeAuthRefusal(code, rejected = false, machine = "the Guaardvark machine") {
  if (code === "outside_local_network") {
    return `This works only on ${machine} and on devices on its local network, and this device is outside it. Create an API key in Settings → Access on ${machine}, then enter it in Settings → Access on this device.`;
  }
  if (code === "local_only") {
    return rejected
      ? `${machine} no longer has an API key, so this works only on ${machine} itself. Turn on Settings → Access → Network access there, or create a new key there and enter it in Settings → Access on this device.`
      : `This works only on ${machine} itself. To use it here, turn on Settings → Access → Network access on ${machine}, or create an API key there and enter it in Settings → Access on this device.`;
  }
  return rejected
    ? `This browser was signed in with a key ${machine} no longer uses. Enter the current key in Settings → Access.`
    : `Enter ${machine}'s API key in Settings → Access. It is shown when it is created, and ${machine} keeps it in its .env file as GUAARDVARK_API_KEY.`;
}

function dispatch(name, detail) {
  try {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  } catch {
    // No window (tests without jsdom).
  }
}

/** Tell the page (ApiKeyRefusalNotice) that a protected route refused it. */
export function announceRefusal(detail) {
  dispatch(AUTH_REFUSED_EVENT, detail);
}

const openChannel = () => {
  try {
    return typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(CHANNEL);
  } catch {
    return null;
  }
};

/**
 * This browser signed in or out, or the key changed: pages here and in the
 * browser's other tabs refresh what they show.
 */
export function notifySessionChanged() {
  dispatch(SESSION_CHANGED_EVENT, {});
  const channel = openChannel();
  if (channel) {
    channel.postMessage({ from: TAB_ID });
    channel.close();
  }
}

/** Run `callback` once whenever notifySessionChanged runs in any tab. Returns the unsubscribe. */
export function onSessionChanged(callback) {
  const local = () => callback();
  window.addEventListener(SESSION_CHANGED_EVENT, local);
  const channel = openChannel();
  if (channel) {
    channel.onmessage = (event) => {
      if (event?.data?.from !== TAB_ID) callback();
    };
  }
  return () => {
    window.removeEventListener(SESSION_CHANGED_EVENT, local);
    if (channel) channel.close();
  };
}

const requestUrl = (input) => {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return input?.url || "";
};

const axiosRequestUrl = (config) => {
  const url = config?.url || "";
  const base = config?.baseURL || "";
  if (!base || /^[a-z][a-z\d+.-]*:\/\//i.test(url)) return url;
  return `${base.replace(/\/+$/, "")}/${url.replace(/^\/+/, "")}`;
};

/**
 * Run once at startup. Always: axios refusals from this backend become the
 * advice above and are announced. Only when the backend is on another origin:
 * fetch and axios requests to it carry credentials. Safe to call twice.
 * `target` is the object whose fetch is wrapped (window); `options` as for
 * crossOriginBackends.
 */
export function installBackendCredentials({ axios, target = window, options } = {}) {
  const crossOrigin = crossOriginBackends(options).length > 0;

  if (crossOrigin && target && typeof target.fetch === "function" && !target.fetch[INSTALLED]) {
    const baseFetch = target.fetch;
    const fetchWithCredentials = (input, init) => {
      // A caller that chose its own credentials mode keeps it.
      if (init?.credentials === undefined && needsCredentials(requestUrl(input), options)) {
        return baseFetch.call(target, input, { ...(init || {}), credentials: "include" });
      }
      return baseFetch.call(target, input, init);
    };
    fetchWithCredentials[INSTALLED] = true;
    target.fetch = fetchWithCredentials;
  }

  if (axios && !axios[INSTALLED]) {
    if (crossOrigin) {
      axios.interceptors.request.use((config) => {
        if (config.withCredentials === undefined && needsCredentials(axiosRequestUrl(config), options)) {
          config.withCredentials = true;
        }
        return config;
      });
    }
    axios.interceptors.response.use(
      (response) => response,
      (error) => {
        const response = error?.response;
        const url = axiosRequestUrl(error?.config);
        const code = response && (response.status === 401 || response.status === 403)
          ? authRefusalCode(response.data)
          : null;
        if (code && isBackendUrl(url, options)) {
          // Call sites show response.data.error or error.message; both carry
          // the advice, and the server's own words stay in server_error.
          const rejected = Boolean(response.data.credential_rejected);
          const text = describeAuthRefusal(code, rejected, response.data.machine);
          response.data = { ...response.data, error: text, message: text, server_error: response.data.error };
          error.message = text;
          error.authRefused = code;
          announceRefusal({ code, rejected, url });
        }
        return Promise.reject(error);
      },
    );
    axios[INSTALLED] = true;
  }
}
