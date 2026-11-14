// The newest pictures and clips in the library, for the dashboard's Images and
// Videos cards. Items are the library's own document rows, so the Media page's
// details window and file menu work on them unchanged.

import { useCallback, useEffect, useState } from "react";
import { BASE_URL, handleResponse } from "../../api/apiClient";
import { isImageFile, isVideoFile } from "../documents/fileUtils.jsx";
import { translate } from "../../i18n";

/** How many thumbnails a card asks for. */
export const MEDIA_LIMIT = 24;

/** A finished render shows up on the card within this long. */
const REFRESH_MS = 60000;

const ofKind = { image: isImageFile, video: isVideoFile };

const stamp = (doc) => encodeURIComponent(doc.updated_at || "");

/** A small WebP still of a picture or clip. */
export const thumbnailUrl = (doc, size = 320) =>
  `${BASE_URL}/files/thumbnail?document_id=${doc.id}&size=${size}&v=${stamp(doc)}`;

/** The file itself, served inline so <video> can play it. */
export const mediaUrl = (doc) => `${BASE_URL}/files/document/${doc.id}/download?v=${stamp(doc)}`;

/** What it was made from, when the generator recorded a prompt. */
export const mediaPrompt = (doc) => {
  const prompt = doc?.metadata?.prompt;
  return typeof prompt === "string" ? prompt : "";
};

/** Rows of one kind, in the order the feed gave them (newest first). */
export function mediaOfKind(documents, kind) {
  const keep = ofKind[kind];
  return (Array.isArray(documents) ? documents : []).filter((doc) => keep(doc?.filename || ""));
}

export async function loadRecentMedia(kind) {
  const data = await handleResponse(await fetch(`${BASE_URL}/files/recent?kind=${kind}&limit=${MEDIA_LIMIT}`));
  return mediaOfKind(data?.data?.documents, kind);
}

/**
 * The latest media of one kind: loaded on mount, on `refresh()`, and again every
 * minute while the tab is showing. A background reload keeps what is on screen
 * if it fails.
 * @param {"image"|"video"} kind
 */
export function useRecentMedia(kind) {
  const [state, setState] = useState({ items: [], loading: true, error: null });

  const load = useCallback(
    async ({ quiet = false } = {}) => {
      if (!quiet) setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const items = await loadRecentMedia(kind);
        setState({ items, loading: false, error: null });
      } catch (err) {
        if (quiet) return;
        setState({
          items: [],
          loading: false,
          error: translate(`dashboard.media.loadFailed.${kind}`, { message: err?.message || err }),
        });
      }
    },
    [kind],
  );

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") load({ quiet: true });
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  return { ...state, refresh: load };
}
