import { describe, it, expect } from "vitest";
import { appendCards, mergeSavedLayout } from "../dashboardSavedLayout";
import { restoreMinimizedSizes } from "../../board";

const defaults = [
  { i: "approvals", x: 0, y: 0, w: 35, h: 49, minW: 30 },
  { i: "imggen", x: 35, y: 0, w: 70, h: 49, minW: 30 },
  { i: "videogen", x: 105, y: 0, w: 70, h: 49, minW: 30 },
  { i: "project", x: 0, y: 49, w: 35, h: 49, minW: 30 },
];

const at = (layout, id) => layout.find((item) => item.i === id);

describe("mergeSavedLayout", () => {
  it("keeps every saved place and size when no card is new", () => {
    const saved = defaults.map((item, n) => ({ i: item.i, x: n, y: n * 2, w: 40, h: 20 }));
    const merged = mergeSavedLayout(defaults, saved);
    expect(merged.map(({ i, x, y, w, h }) => ({ i, x, y, w, h }))).toEqual(saved);
    expect(at(merged, "project").minW).toBe(30);
  });

  it("puts cards added since the save in a top row and moves the saved ones down by it", () => {
    const saved = [
      { i: "project", x: 82, y: 0, w: 35, h: 18 },
      { i: "imggen", x: 3, y: 12, w: 30, h: 23 },
    ];
    const merged = mergeSavedLayout(defaults, saved);
    expect(at(merged, "approvals")).toMatchObject({ x: 0, y: 0, w: 35, h: 49 });
    expect(at(merged, "videogen")).toMatchObject({ x: 35, y: 0, w: 70, h: 49 });
    expect(at(merged, "project")).toMatchObject({ x: 82, y: 49, w: 35, h: 18 });
    expect(at(merged, "imggen")).toMatchObject({ x: 3, y: 61, w: 30, h: 23 });
  });

  it("gives the new row the height asked for", () => {
    const merged = mergeSavedLayout(defaults, [{ i: "project", x: 0, y: 4, w: 35, h: 18 }], { addedRowH: 24 });
    expect(at(merged, "videogen")).toMatchObject({ y: 0, h: 24 });
    expect(at(merged, "project").y).toBe(28);
  });

  it("keeps the saved stacking order, with added cards last", () => {
    const saved = [
      { i: "project", x: 0, y: 0, w: 35, h: 50 },
      { i: "imggen", x: 3, y: 12, w: 30, h: 23 },
    ];
    expect(mergeSavedLayout(defaults, saved).map((item) => item.i)).toEqual(["project", "imggen", "approvals", "videogen"]);
  });

  it("keeps a saved item for a card this version does not have", () => {
    const saved = [...defaults.map(({ i, x, y, w, h }) => ({ i, x, y, w, h })), { i: "legacy", x: 5, y: 6, w: 7, h: 8 }];
    const merged = mergeSavedLayout(defaults, saved, { extraItemProps: { isDraggable: true } });
    expect(at(merged, "legacy")).toEqual({ isDraggable: true, i: "legacy", x: 5, y: 6, w: 7, h: 8 });
  });
});

describe("restoreMinimizedSizes", () => {
  it("gives a minimized card back the size an older save kept beside the layout", () => {
    const layout = [
      { i: "chat", x: 38, y: 3, w: 35, h: 18 },
      { i: "tasks", x: 2, y: 3, w: 35, h: 50 },
    ];
    const restored = restoreMinimizedSizes(layout, { chat: true, tasks: false }, { chat: { w: 35, h: 49 }, tasks: { w: 1, h: 1 } });
    expect(restored).toEqual([
      { i: "chat", x: 38, y: 3, w: 35, h: 49 },
      { i: "tasks", x: 2, y: 3, w: 35, h: 50 },
    ]);
    expect(restoreMinimizedSizes(layout, { chat: true }, undefined)).toEqual(layout);

    // A card pinned at its bar height opens to the fallback size with the pin lifted.
    const pinned = [{ i: "editor", x: 0, y: 0, w: 105, h: 5, minH: 5, maxH: 5 }];
    expect(restoreMinimizedSizes(pinned, { editor: true }, {}, () => ({ w: 105, h: 74 }))).toEqual([
      { i: "editor", x: 0, y: 0, w: 105, h: 74 },
    ]);
  });
});

describe("appendCards", () => {
  const grid = { cardGridW: 35, cardGridH: 49, COLS_COUNT: 175 };
  const core = [
    { i: "approvals", x: 0, y: 0, w: 35, h: 49 },
    { i: "files", x: 105, y: 98, w: 70, h: 73.5 },
  ];

  it("places extension cards in rows under the lowest core card, wrapping at the board's width", () => {
    const cards = [
      { id: "acme-orders", w: 2, h: 1 },
      { id: "acme-stock", w: 2, h: 1.5 },
      { id: "acme-notes", w: 2, h: 1 },
    ];
    expect(appendCards(core, cards, grid).slice(2)).toEqual([
      { i: "acme-orders", x: 0, y: 171.5, w: 70, h: 49 },
      { i: "acme-stock", x: 70, y: 171.5, w: 70, h: 74 },
      // 140 + 70 passes 175 columns: a new row, under the taller of the two above.
      { i: "acme-notes", x: 0, y: 245.5, w: 70, h: 49 },
    ]);
  });

  it("returns the layout itself when there is nothing to add", () => {
    expect(appendCards(core, [], grid)).toBe(core);
  });
});
