import { describe, expect, it } from "vitest";
import { moveBefore } from "./ScheduledJobsCard";

describe("moveBefore", () => {
  it("moves a job up to the place of the one it is dragged over", () => {
    expect(moveBefore([1, 2, 3, 4], 4, 2)).toEqual([1, 4, 2, 3]);
  });
  it("moves a job down to the place of the one it is dragged over", () => {
    expect(moveBefore([1, 2, 3, 4], 1, 3)).toEqual([2, 3, 1, 4]);
  });
  it("leaves the order alone for a job it does not hold", () => {
    expect(moveBefore([1, 2, 3], 9, 2)).toEqual([1, 2, 3]);
    expect(moveBefore([1, 2, 3], 2, 2)).toEqual([1, 2, 3]);
  });
});
