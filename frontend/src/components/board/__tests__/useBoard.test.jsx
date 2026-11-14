import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useBoardGestures } from "../useBoard";

const stored = [{ i: "a", x: 0, y: 0, w: 35, h: 49 }];

const setup = (minimized) => {
  const state = { layout: stored, minimized };
  const { result } = renderHook(() =>
    useBoardGestures(
      () => state.layout,
      () => state.minimized,
    ),
  );
  return { state, gestures: result.current };
};

beforeEach(() => vi.useFakeTimers({ toFake: ["requestAnimationFrame"] }));
afterEach(() => vi.useRealTimers());

describe("useBoardGestures", () => {
  it("takes the reported height of an open card and keeps the stored one of a minimized card", () => {
    const open = setup({});
    open.gestures.onDragStart();
    expect(open.gestures.settle([{ i: "a", x: 3, y: 4, w: 35, h: 20 }], true)[0]).toMatchObject({ x: 3, y: 4, h: 20 });

    const min = setup({ a: true });
    min.gestures.onDragStart();
    expect(min.gestures.settle([{ i: "a", x: 3, y: 4, w: 35, h: 3 }], true)[0]).toMatchObject({ x: 3, y: 4, h: 49 });
  });

  it("reads a drag against the flags from before a toggle made on the same press", () => {
    // A double-click on the bar of a minimized card: the second press starts a
    // drag and expands the card, then the drag reports the bar it began with.
    const { state, gestures } = setup({ a: true });
    gestures.onDragStart();
    gestures.beginToggle("a");
    state.minimized = {};
    vi.runAllTimers();
    expect(gestures.settle([{ i: "a", x: 0, y: 0, w: 35, h: 3 }], true)[0].h).toBe(49);
  });

  it("ignores what the grid reports in the frame a toggle or preset is applied", () => {
    const { gestures } = setup({});
    gestures.suspendFrame();
    expect(gestures.settle([{ i: "a", x: 9, y: 9, w: 35, h: 20 }], true)).toBeNull();
    vi.runAllTimers();
    expect(gestures.settle([{ i: "a", x: 9, y: 9, w: 35, h: 20 }], false)[0]).toMatchObject({ x: 9, y: 9 });
  });
});
