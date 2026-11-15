import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import useStableCallbacks from "../useStableCallbacks";

describe("useStableCallbacks", () => {
  it("keeps each function's identity and runs the latest version", () => {
    const { result, rerender } = renderHook(({ n }) => useStableCallbacks({ value: () => n, add: (x) => n + x }), {
      initialProps: { n: 1 },
    });
    const first = result.current;
    expect(first.value()).toBe(1);

    rerender({ n: 5 });
    expect(result.current).toBe(first);
    expect(result.current.value).toBe(first.value);
    expect(first.value()).toBe(5);
    expect(first.add(2)).toBe(7);
  });
});
