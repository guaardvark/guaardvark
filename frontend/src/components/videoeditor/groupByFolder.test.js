import { describe, it, expect } from "vitest";
import groupByFolder, { mergeTiles } from "./groupByFolder";

const item = (overrides = {}) => ({
  id: 1,
  filename: "x.mp4",
  folder_id: null,
  folder: null,
  thumbnail_url: null,
  uploaded_at: "2026-05-05T08:00:00Z",
  ...overrides,
});

const folder = (id, name) => ({ id, name, path: `Folder ${name}` });

describe("groupByFolder", () => {
  it("returns empty result for non-array input", () => {
    expect(groupByFolder(null)).toEqual({ folders: [], ungrouped: [] });
    expect(groupByFolder(undefined)).toEqual({ folders: [], ungrouped: [] });
    expect(groupByFolder("not-an-array")).toEqual({ folders: [], ungrouped: [] });
  });

  it("treats items with no folder_id as ungrouped", () => {
    const items = [item({ id: 1 }), item({ id: 2 })];
    const result = groupByFolder(items);
    expect(result.folders).toHaveLength(0);
    expect(result.ungrouped).toHaveLength(2);
  });

  it("buckets all items into a single folder", () => {
    const f = folder(10, "VideoBatch_a");
    const items = [
      item({ id: 1, folder_id: 10, folder: f, uploaded_at: "2026-05-05T08:00:00Z" }),
      item({ id: 2, folder_id: 10, folder: f, uploaded_at: "2026-05-05T09:00:00Z" }),
      item({ id: 3, folder_id: 10, folder: f, uploaded_at: "2026-05-05T07:00:00Z" }),
    ];
    const result = groupByFolder(items);
    expect(result.folders).toHaveLength(1);
    expect(result.ungrouped).toHaveLength(0);
    expect(result.folders[0].folder.id).toBe(10);
    expect(result.folders[0].items).toHaveLength(3);
    // latest_timestamp must reflect the newest member
    expect(result.folders[0].latest_timestamp).toBe("2026-05-05T09:00:00Z");
  });

  it("mixes folders and ungrouped, preserving both", () => {
    const f1 = folder(10, "A");
    const f2 = folder(20, "B");
    const items = [
      item({ id: 1, folder_id: 10, folder: f1 }),
      item({ id: 2, folder_id: 20, folder: f2 }),
      item({ id: 3 }),
      item({ id: 4, folder_id: 10, folder: f1 }),
      item({ id: 5 }),
    ];
    const result = groupByFolder(items);
    expect(result.folders).toHaveLength(2);
    expect(result.ungrouped.map((i) => i.id).sort()).toEqual([3, 5]);
    const fA = result.folders.find((g) => g.folder.id === 10);
    expect(fA.items).toHaveLength(2);
  });

  it("treats folder_id-set-but-folder-null as ungrouped (defensive)", () => {
    // Data-consistency edge: serializer dropped the folder block but
    // kept folder_id. Better to show as ungrouped than crash.
    const items = [item({ id: 1, folder_id: 99, folder: null })];
    const result = groupByFolder(items);
    expect(result.folders).toHaveLength(0);
    expect(result.ungrouped).toHaveLength(1);
  });

  it("preview_thumbs takes the 3 most-recent items' urls", () => {
    const f = folder(10, "A");
    const mk = (id, ts, thumb) =>
      item({ id, folder_id: 10, folder: f, uploaded_at: ts, thumbnail_url: thumb });
    const items = [
      mk(1, "2026-05-01T00:00:00Z", "/t/1"),
      mk(2, "2026-05-02T00:00:00Z", "/t/2"),
      mk(3, "2026-05-03T00:00:00Z", "/t/3"),
      mk(4, "2026-05-04T00:00:00Z", "/t/4"),
    ];
    const result = groupByFolder(items);
    expect(result.folders[0].preview_thumbs).toEqual(["/t/4", "/t/3", "/t/2"]);
  });

  it("preview_thumbs preserves null entries when thumbnail_url is missing", () => {
    const f = folder(10, "A");
    const items = [
      item({ id: 1, folder_id: 10, folder: f, thumbnail_url: null }),
    ];
    const result = groupByFolder(items);
    expect(result.folders[0].preview_thumbs).toEqual([null]);
  });

  it("ignores null/falsy items in the input list", () => {
    const items = [null, item({ id: 1 }), undefined, item({ id: 2 })];
    expect(() => groupByFolder(items)).not.toThrow();
    expect(groupByFolder(items).ungrouped).toHaveLength(2);
  });
});

describe("mergeTiles", () => {
  it("interleaves folders and items by timestamp descending", () => {
    const f1 = folder(10, "Old");
    const f2 = folder(20, "New");
    const grouping = {
      folders: [
        { folder: f1, items: [], latest_timestamp: "2026-05-01T00:00:00Z", preview_thumbs: [] },
        { folder: f2, items: [], latest_timestamp: "2026-05-04T00:00:00Z", preview_thumbs: [] },
      ],
      ungrouped: [
        item({ id: 1, uploaded_at: "2026-05-03T00:00:00Z" }),
        item({ id: 2, uploaded_at: "2026-05-02T00:00:00Z" }),
      ],
    };
    const tiles = mergeTiles(grouping);
    // Order: New folder (2026-05-04), item 1 (2026-05-03), item 2 (2026-05-02), Old folder (2026-05-01)
    expect(tiles.map((t) => (t.kind === "folder" ? `f:${t.group.folder.id}` : `i:${t.item.id}`)))
      .toEqual(["f:20", "i:1", "i:2", "f:10"]);
  });

  it("handles empty grouping", () => {
    expect(mergeTiles({ folders: [], ungrouped: [] })).toEqual([]);
  });
});
