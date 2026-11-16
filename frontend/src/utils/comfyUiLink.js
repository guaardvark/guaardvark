// frontend/src/utils/comfyUiLink.js
//
// The address the "ComfyUI" button on the Video page opens. The backend
// reports where it talks to ComfyUI (/gpu/comfyui/status: comfyui_url, from
// the plugin manifest's port or GUAARDVARK_COMFYUI_URL); that address names
// the backend's own machine as localhost, which from another computer on the
// network is the viewer's machine. The same goes for the names a container
// uses for its host (host.containers.internal, host.docker.internal): they
// resolve only inside the container, never in the browser. So a loopback or
// container-host name is swapped for the host the page was opened on, and the
// port and protocol are kept. An address that already names a machine is
// opened as given.

export const DEFAULT_COMFYUI_PORT = 8188;

const LOOPBACK = new Set([
  "localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0",
  "host.containers.internal", "host.docker.internal",
]);

/**
 * @param {string} pageHostname  window.location.hostname
 * @param {string|null|undefined} comfyuiUrl  what the backend reports, if known
 * @returns {string}
 */
export function comfyUiLink(pageHostname, comfyuiUrl) {
  let port = DEFAULT_COMFYUI_PORT;
  let protocol = "http:";
  if (comfyuiUrl) {
    try {
      const url = new URL(comfyuiUrl);
      if (!LOOPBACK.has(url.hostname)) return url.origin;
      if (url.port) port = Number(url.port);
      protocol = url.protocol;
    } catch {
      // Not a URL; the default port and the page's host stand.
    }
  }
  const host = (pageHostname || "localhost").trim() || "localhost";
  // An IPv6 literal needs brackets in a URL.
  const shown = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
  return `${protocol}//${shown}:${port}`;
}
