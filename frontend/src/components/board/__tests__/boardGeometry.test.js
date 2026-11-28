import { describe, it, expect } from "vitest";
import {
  barRows,
  barsPreset,
  compactPreset,
  gridLayout,
  mergeReported,
  narrowReflow,
  nextMode,
  repackRows,
  resolveOverlaps,
} from "../boardGeometry";

const grid = {
  CONTAINER_PADDING_PX: 4,
  CARD_MARGIN_PX: 8,
  COLS_COUNT: 175,
  ROW_HEIGHT_PX: 10,
  cardGridW: 35,
  cardGridH: 49,
  cardMinGridW: 30,
};

describe("board geometry", () => {
  it("gives a minimized card the fewest rows that hold its 40px bar", () => {
    // 3 rows of 10px with two 8px margins between them is 46px.
    expect(barRows(10, 8)).toBe(3);
    expect(barRows(40, 8)).toBe(1);
  });

  it("packs the compact preset in reading order at 0.71 of a card", () => {
    const layout = compactPreset(["a", "b", "c", "d", "e", "f", "g", "h"], grid, 1750);
    expect(layout[0]).toMatchObject({ i: "a", x: 0, y: 0, w: 25, h: 35, minW: 30, isResizable: true });
    // 1750px over 250px cards is seven a row; the eighth starts the next row.
    expect(layout[6]).toMatchObject({ x: 150, y: 0 });
    expect(layout[7]).toMatchObject({ x: 0, y: 35 });
    expect(compactPreset(["a"], grid, 1750, { isResizable: false })[0].isResizable).toBe(false);
  });

  it("stacks the bars preset down the right edge, 300px wide", () => {
    const layout = barsPreset(["a", "b"], grid, 1750, 50);
    expect(layout).toMatchObject([
      { i: "a", x: 145, y: 0, w: 30, h: 5 },
      { i: "b", x: 145, y: 5, w: 30, h: 5 },
    ]);
    expect(barsPreset(["a", "b"], grid, 1750, 150)[1]).toMatchObject({ y: 15, h: 15 });
  });

  it("shows a minimized card at bar height and never a minimum above the size", () => {
    const stored = [
      { i: "a", x: 0, y: 0, w: 35, h: 49, minW: 30 },
      { i: "b", x: 40, y: 0, w: 20, h: 12, minW: 30, minH: 18 },
    ];
    const shown = gridLayout(stored, { a: true }, 3);
    expect(shown[0]).toMatchObject({ i: "a", w: 35, h: 3, minH: 3, maxH: 3 });
    expect(shown[1]).toMatchObject({ i: "b", h: 12, minW: 20, minH: 12 });
    expect(stored[0].h).toBe(49);
  });

  it("keeps a minimized card's stored height when the grid reports its bar", () => {
    const stored = [
      { i: "a", x: 0, y: 0, w: 35, h: 49, minW: 30 },
      { i: "b", x: 40, y: 0, w: 35, h: 49, minW: 30 },
    ];
    const reported = [
      { i: "a", x: 5, y: 6, w: 35, h: 3, minH: 3, maxH: 3 },
      { i: "b", x: 40, y: 0, w: 50, h: 20 },
    ];
    expect(mergeReported(reported, stored, { a: true })).toEqual([
      { i: "a", x: 5, y: 6, w: 35, h: 49, minW: 30 },
      { i: "b", x: 40, y: 0, w: 50, h: 20, minW: 30 },
    ]);
  });

  it("calls a board narrow once a card would render under 300px, and says how many fit a row", () => {
    expect(narrowReflow(grid, 1750, 35).isNarrow).toBe(false);
    const phone = narrowReflow(grid, 700, 35);
    expect(phone.isNarrow).toBe(true);
    expect(phone.cardsPerRow).toBe(2);
    expect(narrowReflow(grid, 0, 35).isNarrow).toBe(false);
  });

  it("re-packs rows in reading order, each row as tall as its tallest card, and locks them", () => {
    const layout = [
      { i: "c", x: 0, y: 50, w: 35, h: 10 },
      { i: "a", x: 0, y: 0, w: 35, h: 20 },
      { i: "b", x: 40, y: 0, w: 35, h: 30 },
    ];
    expect(repackRows(layout, 175, 2, 49)).toEqual([
      { i: "a", x: 0, y: 0, w: 87, h: 20, minW: 1, isDraggable: false, isResizable: false },
      { i: "b", x: 87, y: 0, w: 87, h: 30, minW: 1, isDraggable: false, isResizable: false },
      { i: "c", x: 0, y: 30, w: 87, h: 10, minW: 1, isDraggable: false, isResizable: false },
    ]);
  });

  it("steps a card sideways when only its edges cross its neighbours", () => {
    const layout = [
      { i: "photos", x: 0, y: 0, w: 55, h: 78 },
      { i: "settings", x: 53, y: 7, w: 55, h: 78 },
      { i: "result", x: 104, y: 0, w: 65, h: 78 },
    ];
    expect(resolveOverlaps(layout, 175)).toEqual([
      layout[0],
      { i: "settings", x: 55, y: 7, w: 55, h: 78 },
      { i: "result", x: 110, y: 0, w: 65, h: 78 },
    ]);
  });

  it("drops a card laid on another to the first free place under it", () => {
    const layout = [
      { i: "active", x: 0, y: 0, w: 107, h: 49 },
      { i: "templates", x: 110, y: 0, w: 65, h: 45 },
      { i: "history", x: 58, y: 27, w: 57, h: 22 },
    ];
    expect(resolveOverlaps(layout, 175)).toEqual([layout[0], layout[1], { i: "history", x: 58, y: 49, w: 57, h: 22 }]);
  });

  it("drops a card whose sideways step would leave the board", () => {
    const layout = [
      { i: "a", x: 0, y: 0, w: 90, h: 40 },
      { i: "b", x: 88, y: 0, w: 87, h: 40 },
    ];
    expect(resolveOverlaps(layout, 175)).toEqual([layout[0], { i: "b", x: 88, y: 40, w: 87, h: 40 }]);
  });

  it("keeps the list in its order, which is how cards stack", () => {
    const layout = [
      { i: "low", x: 0, y: 30, w: 30, h: 20 },
      { i: "high", x: 10, y: 0, w: 30, h: 40 },
      { i: "apart", x: 100, y: 0, w: 30, h: 20 },
    ];
    expect(resolveOverlaps(layout, 175)).toEqual([{ i: "low", x: 0, y: 40, w: 30, h: 20 }, layout[1], layout[2]]);
  });

  it("leaves the card just placed where it is and moves the ones under it", () => {
    const layout = [
      { i: "active", x: 0, y: 0, w: 107, h: 49 },
      { i: "templates", x: 22, y: 7, w: 65, h: 45 },
    ];
    expect(resolveOverlaps(layout, 175, "templates")).toEqual([{ i: "active", x: 0, y: 52, w: 107, h: 49 }, layout[1]]);
  });

  it("returns a layout without overlaps as it is", () => {
    const layout = [
      { i: "a", x: 0, y: 0, w: 35, h: 49 },
      { i: "b", x: 35, y: 0, w: 35, h: 49 },
      { i: "c", x: 0, y: 49, w: 70, h: 49 },
    ];
    expect(resolveOverlaps(layout, 175)).toBe(layout);
  });

  it("cycles modes", () => {
    expect(nextMode(["normal", "compact", "bars"], "compact")).toBe("bars");
    expect(nextMode(["normal", "compact", "bars"], "bars")).toBe("normal");
  });
});
