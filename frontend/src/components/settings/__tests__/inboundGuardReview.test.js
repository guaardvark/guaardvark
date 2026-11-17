import { describe, expect, it } from "vitest";
import {
  approvalSummary,
  categoryOf,
  changeDate,
  decisionOf,
  formatExact,
  formatRelative,
  nextSelection,
  sizeText,
  toEntries,
  toggleAll,
  viewEntries,
} from "../inboundGuardReview";

const scan = (id, over = {}) => ({
  kind: "scan",
  key: `s${id}`,
  item: {
    id,
    subject: `item ${id}`,
    source: "self_code",
    verdict: "hold",
    top_severity: "medium",
    approve_effect: "record",
    what_approve_does: "record an approval only (the change already landed)",
    flagged_at: "2026-10-07T10:00:00.000Z",
    ...over,
  },
});

const git = (digest, over = {}) => ({
  kind: "git",
  key: `g${digest}`,
  item: {
    digest,
    subject: `merge ${digest}`,
    source: "git-merge",
    verdict: "hold",
    top_severity: "high",
    approve_effect: "merge_hook",
    what_approve_does: "lets this exact change through the merge hook",
    flagged_at: "2026-10-07T09:00:00-04:00",
    ...over,
  },
});

describe("selection", () => {
  const ordered = ["a", "b", "c", "d", "e"];

  it("toggles one row without a modifier", () => {
    const once = nextSelection(new Set(), ordered, "c", null);
    expect([...once]).toEqual(["c"]);
    expect([...nextSelection(once, ordered, "c", "c")]).toEqual([]);
  });

  it("shift selects the range from the anchor, either direction, keeping what was selected", () => {
    const down = nextSelection(new Set(["a"]), ordered, "d", "b", { shift: true });
    expect([...down].sort()).toEqual(["a", "b", "c", "d"]);
    const up = nextSelection(new Set(), ordered, "b", "e", { shift: true });
    expect([...up].sort()).toEqual(["b", "c", "d", "e"]);
  });

  it("shift without an anchor toggles", () => {
    expect([...nextSelection(new Set(), ordered, "b", null, { shift: true })]).toEqual(["b"]);
  });

  it("select-all picks every shown row, and clears them when all are picked", () => {
    const all = toggleAll(new Set(["x"]), ordered);
    expect(ordered.every((k) => all.has(k))).toBe(true);
    expect(all.has("x")).toBe(true);
    const none = toggleAll(all, ordered);
    expect(ordered.some((k) => none.has(k))).toBe(false);
  });
});

describe("approve-all summary", () => {
  const entries = [
    scan(1),
    scan(2, { verdict: "block", top_severity: "critical", source: "watch-audit" }),
    git("abc"),
    scan(3, { source: "watch", approve_effect: "accept", what_approve_does: "accept the file as it is" }),
  ];

  it("leaves blocks out unless asked and says how many", () => {
    const s = approvalSummary(entries);
    expect(s.count).toBe(3);
    expect(s.blocksLeftOut).toBe(1);
    expect(s.ids).toEqual([1, 3]);
    expect(s.gitDigests).toEqual(["abc"]);
    expect(s.highest).toBe("high");
    expect(s.byKind).toEqual({ "database items": 2, "git records": 1 });
    expect(s.effects).toEqual(
      expect.arrayContaining([
        { text: "record an approval only (the change already landed)", count: 1 },
        { text: "lets this exact change through the merge hook", count: 1 },
        { text: "accept the file as it is", count: 1 },
      ]),
    );
  });

  it("includes blocks only when ticked", () => {
    const s = approvalSummary(entries, { includeBlocks: true });
    expect(s.count).toBe(4);
    expect(s.blocksLeftOut).toBe(0);
    expect(s.ids).toEqual([1, 2, 3]);
    expect(s.highest).toBe("critical");
  });
});

describe("tabs, filters and sorting", () => {
  const entries = [
    scan(1),
    scan(2, { approve_effect: "apply", top_severity: "low", flagged_at: "2026-10-07T12:00:00.000Z" }),
    scan(3, { approve_effect: "accept", source: "watch" }),
    git("abc", { verdict: "block" }),
  ];

  it("groups by what approving does, and blocks on their own tab", () => {
    expect(categoryOf(entries[0].item)).toBe("landed");
    expect(viewEntries(entries, { tab: "waiting" }).map((e) => e.key)).toEqual(["gabc", "s2"]);
    expect(viewEntries(entries, { tab: "ondisk" }).map((e) => e.key)).toEqual(["s3"]);
    expect(viewEntries(entries, { verdict: "block" }).map((e) => e.key)).toEqual(["gabc"]);
    expect(viewEntries(entries, { verdict: "hold" }).map((e) => e.key)).not.toContain("gabc");
  });

  it("filters by severity and source, and sorts by the flagged instant", () => {
    expect(viewEntries(entries, { minSeverity: "medium" }).map((e) => e.key)).not.toContain("s2");
    expect(viewEntries(entries, { source: "watch" }).map((e) => e.key)).toEqual(["s3"]);
    // 09:00-04:00 is 13:00Z, so the git record is the newest of the four.
    expect(viewEntries(entries, { sort: "newest" }).map((e) => e.key)[0]).toBe("gabc");
    expect(viewEntries(entries, { sort: "severity" }).map((e) => e.key)[0]).toBe("gabc");
  });

  it("sorts by verdict with blocks first, and by size with the most lines first", () => {
    const sized = [
      scan(1, { added_lines: 10, files: 1 }),
      scan(2, { added_lines: 400, files: 2, verdict: "block" }),
      scan(3, { added_lines: 400, files: 9 }),
    ];
    expect(viewEntries(sized, { sort: "verdict" }).map((e) => e.key)[0]).toBe("s2");
    expect(viewEntries(sized, { sort: "size" }).map((e) => e.key)).toEqual(["s3", "s2", "s1"]);
    expect(sizeText(sized[0].item).text).toBe("1 file(s), +10");
    expect(sizeText({ source: "watch", files: 1, added_lines: 80 }).text).toBe("1 file(s) whole, 80 line(s)");
  });

  it("keeps approved git records out of the open list and alone in the decided one", () => {
    const data = { scans: [{ id: 1 }], git: [{ digest: "a", approved: true }, { digest: "b" }] };
    expect(toEntries(data).map((e) => e.key)).toEqual(["s1", "gb"]);
    expect(toEntries(data, { resolved: true }).map((e) => e.key)).toEqual(["s1", "ga"]);
  });

  it("never takes an own-decision item into a batch, blocks ticked or not", () => {
    const mixed = [scan(1), scan(2, { own_decision: true }), scan(3, { own_decision: true, verdict: "block" })];
    for (const includeBlocks of [false, true]) {
      const s = approvalSummary(mixed, { includeBlocks });
      expect(s.ids).toEqual([1]);
      expect(s.ownDecision).toBe(2);
      expect(s.blocks).toBe(0);
    }
  });

  it("names what happened to a decided record", () => {
    expect(decisionOf({ status: "approved" })).toBe("approved");
    expect(decisionOf({ approved: true })).toBe("approved");
    expect(decisionOf({ status: "rejected" })).toBe("rejected");
    expect(decisionOf({ status: "clear", verdict: "allow" })).toBe("allow");
    expect(decisionOf({ status: "clear", verdict: "hold" })).toBe("clear");
  });
});

describe("times", () => {
  const now = Date.parse("2026-10-07T14:00:00Z");

  it("reads an explicit offset as the same instant, wherever the browser is", () => {
    expect(formatRelative("2026-10-07T12:00:00.000Z", now)).toBe("2 h ago");
    expect(formatRelative("2026-10-07T08:00:00-04:00", now)).toBe("2 h ago");
    expect(formatExact("2026-10-07T12:00:00.000Z")).toBe(formatExact("2026-10-07T08:00:00-04:00"));
  });

  it("names the time zone and says so when a time is missing", () => {
    expect(formatExact("2026-10-07T12:00:00.000Z")).toMatch(/[A-Z]{2,}|GMT|UTC/);
    expect(formatRelative(null, now)).toBe("—");
    expect(formatRelative("garbage", now)).toBe("—");
    expect(formatExact("garbage")).toBe("");
    expect(formatRelative("2026-10-03T14:00:00Z", now)).toBe("4 days ago");
  });

  it("prefers the file's own write time, then the newest commit", () => {
    expect(changeDate({ file_mtime: "2026-10-01T00:00:00.000Z", file_path: "x.py" })).toMatchObject({
      label: "file written",
      detail: "x.py",
    });
    const commits = changeDate({ commits: { count: 3, newest_at: "2026-10-02T09:00:00+02:00", oldest_at: "2026-10-01T09:00:00+02:00" } });
    expect(commits.label).toBe("newest commit");
    expect(commits.detail).toMatch(/^3 commits, oldest /);
    expect(changeDate({ commits: null })).toBeNull();
  });
});
