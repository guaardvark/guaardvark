// How a FileDesktop shows its library: the view (icons, thumbnails or list),
// the scope (the folder's own items, or every file below it), the kinds of
// file shown and the sort. The Files page and the Media page use the same
// desktop with their own defaults; each remembers how it was left.

import { useCallback, useState } from "react";

export const VIEW_MODES = ["icons", "thumbnails", "list"];
export const SCOPES = ["folders", "all"];
export const SORT_FIELDS = ["name", "date", "size", "type"];
// "other" is every file that is not a picture, a video or a sound.
export const MEDIA_KINDS = ["image", "video", "audio"];
export const ALL_KINDS = [...MEDIA_KINDS, "other"];

export const FILES_VIEW_DEFAULTS = Object.freeze({
  mode: "icons", scope: "folders", kinds: [], sort: { by: "name", dir: "asc" },
});
export const MEDIA_VIEW_DEFAULTS = Object.freeze({
  mode: "thumbnails", scope: "folders", kinds: [], sort: { by: "date", dir: "desc" },
});

// Card size and grid pitch of the two placed views, in px. A card is cardW
// wide and at most h tall, so neighbours one cell apart never overlap.
export const VIEW_GEOMETRY = Object.freeze({
  icons: Object.freeze({ w: 120, h: 100, pad: 20, cardW: 100, span: 108 }),
  thumbnails: Object.freeze({ w: 256, h: 220, pad: 20, cardW: 240, span: 248 }),
});
export const LIST_ROW_HEIGHT = 48;

export const geometryOf = (mode) => VIEW_GEOMETRY[mode === "thumbnails" ? "thumbnails" : "icons"];

/** The key icon places are kept under: each placed view and scope has its own. */
export const layoutKeyOf = (view) => `${view.mode === "thumbnails" ? "thumbnails" : "icons"}:${view.scope}`;

const clean = (view, defaults) => {
  const v = view && typeof view === "object" ? view : {};
  const sort = v.sort && typeof v.sort === "object" ? v.sort : {};
  return {
    mode: VIEW_MODES.includes(v.mode) ? v.mode : defaults.mode,
    scope: SCOPES.includes(v.scope) ? v.scope : defaults.scope,
    kinds: Array.isArray(v.kinds) ? v.kinds.filter((k) => ALL_KINDS.includes(k)) : [...defaults.kinds],
    sort: {
      by: SORT_FIELDS.includes(sort.by) ? sort.by : defaults.sort.by,
      dir: sort.dir === "desc" || sort.dir === "asc" ? sort.dir : defaults.sort.dir,
    },
  };
};

/**
 * The page's view, read from and written to localStorage under `storageKey`.
 * `legacyKey` holds the older icons/list choice, read once when nothing newer
 * is stored.
 */
export const useDesktopView = (storageKey, defaults, legacyKey = null) => {
  const [view, setViewState] = useState(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) return clean(JSON.parse(raw), defaults);
      const legacy = legacyKey ? localStorage.getItem(legacyKey) : null;
      if (legacy === "list" || legacy === "icons") return clean({ ...defaults, mode: legacy }, defaults);
    } catch (e) {
      // Unreadable storage: start from the defaults.
    }
    return clean(defaults, defaults);
  });
  const setView = useCallback((patch) => {
    setViewState((prev) => {
      const next = clean({ ...prev, ...(typeof patch === "function" ? patch(prev) : patch) }, defaults);
      try {
        localStorage.setItem(storageKey, JSON.stringify(next));
      } catch (e) {
        // Storage refused: the view still changes for this visit.
      }
      return next;
    });
  }, [storageKey, defaults]);
  return [view, setView];
};

/** What kind of file a listing row is: image, video, audio or other. */
export const kindOf = (file) => {
  if (file?.media && file.media !== "other") return file.media;
  const ext = String(file?.filename || "").split(".").pop().toLowerCase();
  if (["png", "jpg", "jpeg", "webp", "gif", "bmp"].includes(ext)) return "image";
  if (["mp4", "webm", "mov", "mkv"].includes(ext)) return "video";
  if (["wav", "mp3", "flac", "ogg", "m4a"].includes(ext)) return "audio";
  return "other";
};

/** Whether a file or folder name/title/prompt contains the search text. */
export const matchesSearch = (text, item) => {
  if (!text) return true;
  const needle = text.toLowerCase();
  return [item?.filename, item?.name, item?.title, item?.prompt]
    .some((v) => typeof v === "string" && v.toLowerCase().includes(needle));
};

/** A small thumbnail URL for a listing row; `size` is the long side in px. */
export const thumbnailUrl = (file, size) => {
  const stamp = encodeURIComponent(file.updated_at || file.uploaded_at || "");
  return `/api/files/thumbnail?document_id=${file.id}&size=${size}&v=${stamp}`;
};

/**
 * Places for every item on a placed view: saved places that still fit the
 * width are kept, the rest flow in `items` order into the first free cell.
 * A cell is free when no card would cover it; placed cards are bucketed by
 * row, so this stays fast with thousands of items.
 */
export const resolvePlaces = (items, saved, geom, width) => {
  const cols = Math.max(1, Math.floor((width - 40) / geom.w));
  const rows = new Map();
  const add = (pos) => {
    const r = Math.floor(pos.y / geom.h);
    if (!rows.has(r)) rows.set(r, []);
    rows.get(r).push(pos);
  };
  const covers = (x, y) => {
    const r = Math.floor(y / geom.h);
    for (let rr = r - 1; rr <= r + 1; rr++) {
      const list = rows.get(rr);
      if (!list) continue;
      for (const p of list) {
        if (Math.abs(p.x - x) < geom.span && Math.abs(p.y - y) < geom.h) return true;
      }
    }
    return false;
  };
  const places = {};
  for (const item of items) {
    const pos = saved[item.key];
    if (pos && pos.x + geom.span <= width) {
      places[item.key] = pos;
      add(pos);
    }
  }
  let next = 0;
  for (const item of items) {
    if (places[item.key]) continue;
    while (next < 1000000) {
      const cell = { x: (next % cols) * geom.w + geom.pad, y: Math.floor(next / cols) * geom.h + geom.pad };
      next++;
      if (!covers(cell.x, cell.y)) {
        places[item.key] = cell;
        add(cell);
        break;
      }
    }
  }
  return places;
};

/** Folders first, then files, in the view's sort field and direction. */
export const sortItems = (folders, files, sort) => {
  const byName = (a, b) => (a || "").localeCompare(b || "", undefined, { numeric: true, sensitivity: "base" });
  const time = (v) => (v ? new Date(v).getTime() || 0 : 0);
  const count = (f) => (f.document_count || 0) + (f.subfolder_count || 0);
  const sign = sort.dir === "desc" ? -1 : 1;
  const sortedFolders = [...folders];
  const sortedFiles = [...files];
  if (sort.by === "date") {
    sortedFolders.sort((a, b) => sign * (time(a.folder.updated_at || a.folder.created_at) - time(b.folder.updated_at || b.folder.created_at)));
    sortedFiles.sort((a, b) => sign * (time(a.updated_at || a.uploaded_at) - time(b.updated_at || b.uploaded_at)));
  } else if (sort.by === "size") {
    sortedFolders.sort((a, b) => sign * (count(a.folder) - count(b.folder)));
    sortedFiles.sort((a, b) => sign * ((a.size || 0) - (b.size || 0)));
  } else if (sort.by === "type") {
    sortedFolders.sort((a, b) => sign * byName(a.folder.name, b.folder.name));
    sortedFiles.sort((a, b) => sign * (byName(kindOf(a), kindOf(b)) || byName(a.filename, b.filename)));
  } else {
    sortedFolders.sort((a, b) => sign * byName(a.folder.name, b.folder.name));
    sortedFiles.sort((a, b) => sign * byName(a.filename, b.filename));
  }
  return { folders: sortedFolders, files: sortedFiles };
};
