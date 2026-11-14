import { describe, it, expect } from "vitest";
import { mergeSavedLayout } from "../dashboardSavedLayout";

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
