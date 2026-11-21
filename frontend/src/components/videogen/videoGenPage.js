// Small helpers the Video Gen page and its cards share.

import { translate } from "../../i18n";

export const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const stripGlyph = (label) => String(label || "").replace(/^[^\p{L}\p{N}]+/u, "").trim();

/** A batch status as a chip shows it; an unknown status shows as it came. */
export const batchStatusLabel = (status) => ({
  queued: translate("videoGenerator.status.queued"),
  pending: translate("videoGenerator.status.pending"),
  running: translate("videoGenerator.status.running"),
  processing: translate("videoGenerator.status.processing"),
  completed: translate("videoGenerator.status.completed"),
  error: translate("videoGenerator.status.error"),
  cancelled: translate("videoGenerator.status.cancelled"),
  paused: translate("videoGenerator.status.paused"),
})[status] || status || "";

/** The path part of a local or absolute path a response carries. */
export function PathFromUrl(path) {
  if (!path) return "";
  try {
    const url = new URL(path, window.location.origin);
    return url.pathname.replace(/^\/+/, "");
  } catch {
    return String(path).replace(/^\/+/, "");
  }
}

export function encodePathSegments(path) {
  if (!path) return "";
  return path
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

/** The compose cards dim and stop taking input while a batch is being queued. */
export const composeCardSx = (disabled) => ({
  display: "flex",
  flexDirection: "column",
  gap: 1.5,
  p: 1,
  opacity: disabled ? 0.5 : 1,
  pointerEvents: disabled ? "none" : "auto",
});

/** The status blocks were cards of their own; inside a card frame they are plain sections. */
export const statusSx = {
  p: 0.5,
  "& > .MuiCard-root": { boxShadow: "none", backgroundColor: "transparent", backgroundImage: "none", mb: 1 },
};
