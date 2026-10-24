import { translate } from "../i18n";

/**
 * A server path as a person reads it: the home folder written as ~, so the
 * account name stays off screen. Paths outside the home folder are unchanged.
 */
export const displayPath = (path, home) => {
  if (!path || !home) return path;
  const base = home.replace(/\/+$/, "");
  if (path === base) return "~";
  return path.startsWith(`${base}/`) ? `~${path.slice(base.length)}` : path;
};

/** A browse root's label: "Home" for the home folder, ~/… under it. */
export const rootLabel = (root, home) =>
  home && root.replace(/\/+$/, "") === home.replace(/\/+$/, "") ? translate("messages.paths.home") : displayPath(root, home);

/** The last part of a server path (its file name), for screens that should not show the path. */
export const fileNameOf = (path) => (path ? String(path).split(/[\\/]/).pop() : "");
