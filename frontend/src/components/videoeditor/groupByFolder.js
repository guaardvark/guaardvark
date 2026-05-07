// frontend/src/components/videoeditor/groupByFolder.js
//
// Pure grouping function for the Media Library panel. Splits a flat list
// of media Documents into:
//   - folders: one Group per distinct folder, with its items + a small
//     thumbnail strip + the most-recent timestamp for sort ordering.
//   - ungrouped: items whose folder_id is null OR whose folder field
//     didn't deserialize (data inconsistency — we don't crash on those).
//
// Caller decides how to merge folders+ungrouped for rendering. Sorting
// across the merged set lives in the renderer because grid vs. list may
// want different ordering (e.g. groups-first in list mode).
//
// This file is the unit-tested seam — the rest of the panel is presentation.

const PREVIEW_LIMIT = 3;

/**
 * @typedef {Object} MediaItem
 * @property {number} id
 * @property {string} filename
 * @property {number|null} folder_id
 * @property {{id:number, name:string, path:string}|null} folder
 * @property {string|null} thumbnail_url
 * @property {string} [uploaded_at]
 * @property {string} [updated_at]
 */

/**
 * @typedef {Object} Group
 * @property {{id:number, name:string, path:string}} folder
 * @property {MediaItem[]} items
 * @property {string} latest_timestamp
 * @property {string[]} preview_thumbs
 */

/**
 * @typedef {Object} GroupingResult
 * @property {Group[]} folders
 * @property {MediaItem[]} ungrouped
 */

const _itemTimestamp = (item) =>
  item?.updated_at || item?.uploaded_at || "";

const _maxTimestamp = (a, b) => (a > b ? a : b);

/**
 * Group a flat list of media items by folder.
 * @param {MediaItem[]} items
 * @returns {GroupingResult}
 */
export default function groupByFolder(items) {
  if (!Array.isArray(items)) {
    return { folders: [], ungrouped: [] };
  }

  // Single pass: bucket by folder_id, capture folder metadata from the
  // first item that carries it. If folder_id is set but folder is null,
  // we treat it as ungrouped — defensive against partial deserialization.
  const buckets = new Map();
  const ungrouped = [];

  for (const item of items) {
    if (!item) continue;
    const folder = item.folder;
    if (item.folder_id != null && folder && folder.id != null) {
      const key = folder.id;
      let bucket = buckets.get(key);
      if (!bucket) {
        bucket = {
          folder: { id: folder.id, name: folder.name, path: folder.path },
          items: [],
          latest_timestamp: "",
          preview_thumbs: [],
        };
        buckets.set(key, bucket);
      }
      bucket.items.push(item);
      bucket.latest_timestamp = _maxTimestamp(
        bucket.latest_timestamp,
        _itemTimestamp(item),
      );
    } else {
      ungrouped.push(item);
    }
  }

  // Build the preview thumb strip per folder. Take up to N most-recent
  // items by timestamp. Items without a thumbnail_url contribute null
  // so the renderer can decide whether to show a placeholder slot or
  // collapse the strip.
  const folders = Array.from(buckets.values()).map((bucket) => {
    const sortedItems = [...bucket.items].sort((a, b) =>
      _itemTimestamp(b).localeCompare(_itemTimestamp(a)),
    );
    const preview_thumbs = sortedItems
      .slice(0, PREVIEW_LIMIT)
      .map((it) => it.thumbnail_url || null);
    return {
      folder: bucket.folder,
      items: sortedItems,
      latest_timestamp: bucket.latest_timestamp,
      preview_thumbs,
    };
  });

  return { folders, ungrouped };
}

/**
 * Merge folders and ungrouped items into a single timestamp-descending
 * list of "tiles" for the renderer. Each tile is either
 *   { kind: 'folder', group }  or  { kind: 'item', item }.
 *
 * Pulled out as its own helper so list-mode can opt out (it puts folders
 * first, items second).
 *
 * @param {GroupingResult} grouping
 * @returns {Array<{kind:'folder'|'item', group?: Group, item?: MediaItem, ts: string}>}
 */
export function mergeTiles(grouping) {
  const { folders, ungrouped } = grouping;
  const tiles = [
    ...folders.map((g) => ({ kind: "folder", group: g, ts: g.latest_timestamp })),
    ...ungrouped.map((it) => ({ kind: "item", item: it, ts: _itemTimestamp(it) })),
  ];
  tiles.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
  return tiles;
}
