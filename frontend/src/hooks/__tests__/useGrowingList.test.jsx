import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import useGrowingList, { growMarkerStyle } from "../useGrowingList";

const items = Array.from({ length: 60 }, (_, i) => `item ${i}`);

const List = () => {
  const { shown, markerRef, hasMore } = useGrowingList(items, { initial: 10, step: 20 });
  return (
    <div>
      {shown.map((item) => <span key={item}>{item}</span>)}
      {hasMore && <div ref={markerRef} style={growMarkerStyle} data-testid="marker" />}
    </div>
  );
};

afterEach(() => vi.unstubAllGlobals());

describe("useGrowingList", () => {
  it("draws the first items and more each time the end comes into view", () => {
    let reach;
    vi.stubGlobal("IntersectionObserver", class {
      constructor(callback) { reach = () => callback([{ isIntersecting: true }]); }
      observe() {}
      disconnect() {}
    });
    render(<List />);
    expect(screen.getAllByText(/^item/)).toHaveLength(10);

    act(() => reach());
    expect(screen.getAllByText(/^item/)).toHaveLength(30);
    act(() => reach());
    act(() => reach());
    expect(screen.getAllByText(/^item/)).toHaveLength(60);
    expect(screen.queryByTestId("marker")).toBeNull();
  });

  it("draws everything where the end of the list cannot be watched", () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    render(<List />);
    expect(screen.getAllByText(/^item/)).toHaveLength(60);
  });
});
