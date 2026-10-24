// frontend/src/utils/assetGuard.js
// Offline-first asset guard. A stale or hostile REMOTE image URL stored in chat
// history (e.g. an expired files.oaiusercontent.com link from an old ChatGPT paste)
// must NEVER cause the browser to phone out when a message is rendered. This blocks
// any media URL pointing at a public (non-local) host; local, LAN, relative, data:
// and blob: URLs pass through untouched.
/* eslint-env browser */
import { translate } from "../i18n";

export function isLocalAssetUrl(url) {
  if (!url || typeof url !== "string") return false;
  const u = url.trim();
  if (!u) return false;
  if (u.startsWith("data:") || u.startsWith("blob:")) return true;
  // Resolve before judging: "//host/x" and "/\host/x" look relative, but the
  // browser fetches both from another host.
  let host;
  try {
    host = new URL(u, window.location.origin).hostname;
  } catch {
    // The browser cannot resolve it either, so there is nothing to load.
    return false;
  }
  if (!host) return true;
  if (host === window.location.hostname) return true;
  if (host === "127.0.0.1" || host === "localhost" || host === "[::1]") return true;
  // Private LAN ranges — the app is intentionally reachable on the local network.
  // IPv4 literals only: "10.example.com" is a public name.
  const ip = host.match(/^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/);
  if (!ip) return false;
  const a = Number(ip[1]);
  const b = Number(ip[2]);
  return a === 10 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31);
}

const escapeXml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[c]);

const blockedPlaceholder = (title, note) =>
  "data:image/svg+xml;charset=utf8," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120">' +
      '<rect width="100%" height="100%" fill="#2b2b2b"/>' +
      '<text x="50%" y="46%" fill="#ff8a80" font-family="sans-serif" font-size="12" ' +
      `text-anchor="middle">${escapeXml(title)}</text>` +
      '<text x="50%" y="64%" fill="#9e9e9e" font-family="sans-serif" font-size="10" ' +
      `text-anchor="middle">${escapeXml(note)}</text>` +
    "</svg>"
  );

// Inline SVG shown in place of a blocked remote image. Pure data URI — no network.
// The constant is the English picture; remoteBlockedPlaceholder() draws it in
// the interface's language.
export const REMOTE_BLOCKED_PLACEHOLDER = blockedPlaceholder("remote image blocked", "(offline-first)");

export const remoteBlockedPlaceholder = () =>
  blockedPlaceholder(translate("messages.assetGuard.blocked"), translate("messages.assetGuard.offlineFirst"));

// The only safe accessor: returns the URL if it is local, else the offline
// placeholder so the <img>/<video> never issues an outbound request.
export function guardedMediaSrc(url) {
  return isLocalAssetUrl(url) ? url : remoteBlockedPlaceholder();
}

// ── HTML put into the page (sticky notes) ─────────────────────────────────
// Setting innerHTML loads every <img> in it at once, so remote media has to be
// taken out of the string first. The original URL is kept on the element
// (data-remote-*) and put back by unguardHtmlMedia before the HTML is saved,
// so a note's content is not changed by being shown.
const MEDIA_ATTRS = {
  IMG: ["src", "srcset"],
  SOURCE: ["src", "srcset"],
  VIDEO: ["src", "poster"],
  AUDIO: ["src"],
  INPUT: ["src"],
};
const REMOTE_PREFIX = "data-remote-";
const CSS_URL = /url\(\s*(['"]?)([^'")]+)\1\s*\)/gi;

function srcsetIsLocal(value) {
  return value
    .split(",")
    .map((part) => part.trim().split(/\s+/)[0])
    .filter(Boolean)
    .every(isLocalAssetUrl);
}

function mayHoldMedia(html) {
  return typeof html === "string" && /<(img|source|video|audio|input)\b|url\(/i.test(html);
}

export function guardHtmlMedia(html) {
  if (!mayHoldMedia(html) || typeof DOMParser === "undefined") return html;
  // A DOMParser document is inert: nothing in it is fetched.
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  let changed = false;
  doc.body.querySelectorAll("*").forEach((el) => {
    for (const attr of MEDIA_ATTRS[el.tagName] || []) {
      const value = el.getAttribute(attr);
      if (value == null) continue;
      const local = attr === "srcset" ? srcsetIsLocal(value) : isLocalAssetUrl(value);
      if (local) continue;
      el.setAttribute(REMOTE_PREFIX + attr, value);
      if (attr === "src" && el.tagName === "IMG") el.setAttribute("src", remoteBlockedPlaceholder());
      else el.removeAttribute(attr);
      changed = true;
    }
    const style = el.getAttribute("style");
    if (style && /url\(/i.test(style)) {
      const remote = [...style.matchAll(CSS_URL)].some((m) => !isLocalAssetUrl(m[2]));
      if (remote) {
        el.setAttribute(REMOTE_PREFIX + "style", style);
        el.setAttribute("style", style.replace(CSS_URL, (m, q, u) => (isLocalAssetUrl(u) ? m : "none")));
        changed = true;
      }
    }
  });
  return changed ? doc.body.innerHTML : html;
}

export function unguardHtmlMedia(html) {
  if (typeof html !== "string" || !html.includes(REMOTE_PREFIX) || typeof DOMParser === "undefined") {
    return html;
  }
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  doc.body.querySelectorAll("*").forEach((el) => {
    for (const { name, value } of [...el.attributes]) {
      if (!name.startsWith(REMOTE_PREFIX)) continue;
      el.setAttribute(name.slice(REMOTE_PREFIX.length), value);
      el.removeAttribute(name);
    }
  });
  return doc.body.innerHTML;
}
