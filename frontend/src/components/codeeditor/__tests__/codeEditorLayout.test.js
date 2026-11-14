import { describe, it, expect } from "vitest";
import { boundToBoard, savedEditorLayout } from "../codeEditorLayout";

const mins = { cardMinGridW: 30, cardMinGridH: 18 };
const defaults = [
  { i: "filetree", x: 0, y: 0, w: 35, h: 37, minW: 30, minH: 18, isDraggable: true, isResizable: true },
  { i: "editor", x: 35, y: 0, w: 105, h: 74, minW: 30, minH: 18, isDraggable: true, isResizable: true },
];

describe("savedEditorLayout", () => {
  it("puts saved places and sizes on the default cards and keeps a card with no default", () => {
    const saved = [
      { i: "editor", x: 40, y: 2, w: 100, h: 60 },
      { i: "wordpress", x: 1, y: 2, w: 40, h: 20 },
    ];
    const layout = savedEditorLayout(defaults, saved, {}, undefined, mins);
    expect(layout[0]).toMatchObject({ i: "filetree", x: 0, y: 0, w: 35, h: 37 });
    expect(layout[1]).toMatchObject({ i: "editor", x: 40, y: 2, w: 100, h: 60, minH: 18 });
    expect(layout[2]).toMatchObject({ i: "wordpress", x: 1, y: 2, w: 40, h: 20, minW: 30, minH: 18, isResizable: true });
  });

  it("opens a card an older save held at its bar height to the size kept beside the layout", () => {
    const saved = [
      { i: "filetree", x: 0, y: 0, w: 35, h: 5, minH: 5, maxH: 5, isResizable: false },
      { i: "editor", x: 35, y: 0, w: 105, h: 5, minH: 5, maxH: 5, isResizable: false },
    ];
    const layout = savedEditorLayout(defaults, saved, { filetree: true, editor: true }, { filetree: { w: 35, h: 30 } }, mins);
    // filetree: the kept size. editor: nothing kept, so its default size.
    expect(layout[0]).toMatchObject({ i: "filetree", w: 35, h: 30, minH: 18, isResizable: true });
    expect(layout[1]).toMatchObject({ i: "editor", w: 105, h: 74, minH: 18, isResizable: true });
    expect(layout[0].maxH).toBeUndefined();
    expect(layout[1].maxH).toBeUndefined();
  });

  it("drops a height limit left over from a minimize on a card that is open", () => {
    const layout = savedEditorLayout(defaults, [{ i: "editor", x: 35, y: 0, w: 105, h: 74, maxH: 5 }], {}, {}, mins);
    expect(layout[1].maxH).toBeUndefined();
  });
});

describe("boundToBoard", () => {
  it("keeps cards inside the columns and above the last row", () => {
    const layout = [
      { i: "a", x: 170, y: -3, w: 30, h: 40 },
      { i: "b", x: 0, y: 60, w: 200, h: 90 },
    ];
    expect(boundToBoard(layout, 175, 74)).toEqual([
      { i: "a", x: 145, y: 0, w: 30, h: 40 },
      { i: "b", x: 0, y: 0, w: 175, h: 74 },
    ]);
    expect(boundToBoard(layout, 175, null)[1]).toMatchObject({ y: 60, h: 90 });
  });
});
