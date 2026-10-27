import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  DAYS_BEFORE_FIRST_ASK,
  MAX_ASKS,
  STAR_ASK_KEY,
  STAR_ASK_WIN_EVENT,
  localDay,
  markStarAskDone,
  markStarAsked,
  readStarAsk,
  recordDayOpened,
  recordWin,
  starAskDue,
} from "../starAskModel";

beforeEach(() => window.localStorage.clear());

describe("starAskModel", () => {
  it("reads defaults from nothing and from garbage", () => {
    expect(readStarAsk()).toEqual({ wins: 0, dayCount: 0, lastDay: "", asks: 0, dayCountAtLastAsk: 0, done: false });
    window.localStorage.setItem(STAR_ASK_KEY, "{not json");
    expect(readStarAsk().wins).toBe(0);
    window.localStorage.setItem(STAR_ASK_KEY, JSON.stringify({ wins: -2, dayCount: "x", done: "yes" }));
    expect(readStarAsk()).toMatchObject({ wins: 0, dayCount: 0, done: false });
  });

  it("counts each calendar day once", () => {
    recordDayOpened("2026-10-26");
    recordDayOpened("2026-10-26");
    recordDayOpened("2026-10-27");
    expect(readStarAsk()).toMatchObject({ dayCount: 2, lastDay: "2026-10-27" });
  });

  it("formats the local day as YYYY-MM-DD", () => {
    expect(localDay(new Date(2026, 0, 5))).toBe("2026-01-05");
  });

  it("counts only finished renders as wins, and announces them", () => {
    const onWin = vi.fn();
    window.addEventListener(STAR_ASK_WIN_EVENT, onWin);
    expect(recordWin("indexing")).toBeNull();
    recordWin("image_generation");
    recordWin("lora_train");
    window.removeEventListener(STAR_ASK_WIN_EVENT, onWin);
    expect(readStarAsk().wins).toBe(2);
    expect(onWin).toHaveBeenCalledTimes(2);
  });

  it("is due after one win or on the third day, not before", () => {
    expect(starAskDue()).toBe(false);
    for (let d = 1; d < DAYS_BEFORE_FIRST_ASK; d += 1) recordDayOpened(`2026-10-0${d}`);
    expect(starAskDue()).toBe(false);
    recordDayOpened("2026-10-09");
    expect(starAskDue()).toBe(true);
    window.localStorage.clear();
    recordWin("video_render");
    expect(starAskDue()).toBe(true);
  });

  it("asks at most twice, and never once done", () => {
    recordWin("image_generation");
    markStarAsked();
    expect(starAskDue()).toBe(false);
    for (let d = 10; d < 15; d += 1) recordDayOpened(`2026-10-${d}`);
    expect(starAskDue()).toBe(true);
    markStarAsked();
    for (let d = 15; d < 30; d += 1) recordDayOpened(`2026-10-${d}`);
    expect(readStarAsk().asks).toBe(MAX_ASKS);
    expect(starAskDue()).toBe(false);

    window.localStorage.clear();
    recordWin("image_generation");
    markStarAskDone();
    expect(starAskDue()).toBe(false);
    expect(recordWin("image_generation").wins).toBe(1);
  });
});
