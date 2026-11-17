import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const listScans = vi.fn();
const approveBatch = vi.fn();
const rejectBatch = vi.fn();
const approveScan = vi.fn();
const history = vi.fn();
const clearHistory = vi.fn();

vi.mock("../../../api/inboundGuardService", () => ({
  inboundGuardService: {
    listScans: (...args) => listScans(...args),
    approveBatch: (...args) => approveBatch(...args),
    rejectBatch: (...args) => rejectBatch(...args),
    approveScan: (...args) => approveScan(...args),
    history: (...args) => history(...args),
    clearHistory: (...args) => clearHistory(...args),
  },
}));

import InboundGuardReviewDialog from "../InboundGuardReviewDialog";

const NOW = Date.parse("2026-10-07T14:00:00Z");

const scan = (id, over = {}) => ({
  id,
  subject: `scan item ${id}`,
  source: "posture",
  verdict: "hold",
  top_severity: "medium",
  mode_at_record: "observe",
  approve_effect: "record",
  what_approve_does: "record an approval only (the change already landed)",
  what_reject_does: "close it; nothing is undone",
  approve_note: "Posture checks compare settings and inventories after they change; nothing is held.",
  can_reject: true,
  findings: [{ rule: "posture.packages", pack: "posture", severity: "medium", path: "@posture/packages", line: null, excerpt: "torch", why: "a package was installed", origin: "posture" }],
  scanners_run: [{ name: "posture checks", by: "Guaardvark" }],
  flagged_at: "2026-10-07T12:00:00.000Z",
  ...over,
});

const gitItem = (digest, over = {}) => ({
  digest,
  subject: `merge ${digest} into main`,
  source: "git-merge",
  verdict: "hold",
  top_severity: "high",
  mode_at_record: "observe",
  approve_effect: "record",
  what_approve_does: "record an approval only (the change already landed)",
  what_reject_does: "a git record cannot be rejected here",
  can_reject: false,
  findings: [],
  scanners_run: [{ name: "core rules", by: "Guaardvark" }],
  flagged_at: "2026-10-07T09:00:00-04:00",
  commits: { count: 2, newest_at: "2026-10-07T08:00:00-04:00", oldest_at: "2026-10-06T08:00:00-04:00", range: "a..b" },
  ...over,
});

const DATA = {
  mode: "observe",
  git_mode: "observe",
  scans: [
    scan(1),
    scan(2, { flagged_at: "2026-10-07T11:00:00.000Z" }),
    scan(3, { verdict: "block", top_severity: "critical", source: "watch-audit", flagged_at: "2026-10-07T10:00:00.000Z" }),
  ],
  scans_total: 3,
  git: [gitItem("abc")],
  git_total: 1,
};

const theme = createTheme();

const renderDialog = async () => {
  render(
    <ThemeProvider theme={theme}>
      <InboundGuardReviewDialog open onClose={vi.fn()} onChanged={vi.fn()} />
    </ThemeProvider>,
  );
  await screen.findByTestId("row-s1");
};

const box = (key) => within(screen.getByTestId(`row-${key}`)).getByRole("checkbox");
const selectedCount = () => screen.getByText(/selected$/).textContent;

describe("InboundGuardReviewDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
    listScans.mockResolvedValue({ success: true, data: DATA });
    approveBatch.mockResolvedValue({ success: true, data: { approved: 3, skipped: 0, failed: 0, results: [] } });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("is titled for what it lists and shows when each row was flagged", async () => {
    await renderDialog();
    expect(screen.getByText("Inbound guard findings")).toBeInTheDocument();
    // 12:00Z and 09:00-04:00 are 2 h and 1 h before 14:00Z.
    expect(within(screen.getByTestId("row-s1")).getByText("2 h ago")).toBeInTheDocument();
    expect(within(screen.getByTestId("row-gabc")).getByText("1 h ago")).toBeInTheDocument();
    expect(within(screen.getByTestId("row-gabc")).getByText("2 h ago")).toBeInTheDocument(); // newest commit
    expect(within(screen.getByTestId("row-s1")).getByText("Records only")).toBeInTheDocument();
  });

  it("ticks a box without opening the row, and a plain click opens without selecting", async () => {
    await renderDialog();
    fireEvent.click(box("s1"));
    expect(box("s1")).toBeChecked();
    expect(selectedCount()).toBe("1 selected");
    expect(screen.getByText("Click a row to read what the guard found.")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("row-s2"));
    expect(box("s2")).not.toBeChecked();
    expect(screen.getByText(/Approving:/)).toBeInTheDocument();
    expect(screen.getByText("Posture check (written by Guaardvark's developers)")).toBeInTheDocument();
    expect(screen.getByText("What changed (quoted):")).toBeInTheDocument();
  });

  it("ctrl-click toggles a row, shift-click selects a range", async () => {
    await renderDialog();
    fireEvent.click(screen.getByTestId("row-gabc"), { ctrlKey: true });
    expect(box("gabc")).toBeChecked();
    fireEvent.click(screen.getByTestId("row-gabc"), { metaKey: true });
    expect(box("gabc")).not.toBeChecked();

    // Newest first: gabc (13:00Z), s1 (12:00Z), s2 (11:00Z), s3 (10:00Z).
    fireEvent.click(screen.getByTestId("row-s1"), { ctrlKey: true });
    fireEvent.click(screen.getByTestId("row-s3"), { shiftKey: true });
    expect(box("s1")).toBeChecked();
    expect(box("s2")).toBeChecked();
    expect(box("s3")).toBeChecked();
    expect(box("gabc")).not.toBeChecked();
    expect(selectedCount()).toBe("3 selected");
  });

  it("the header box selects every shown row and clears them again", async () => {
    await renderDialog();
    const all = screen.getByRole("checkbox", { name: "Select every row shown" });
    fireEvent.click(all);
    expect(selectedCount()).toBe("4 selected");
    fireEvent.click(all);
    expect(selectedCount()).toBe("0 selected");
  });

  it("approve all says what it covers and leaves blocks out unless ticked", async () => {
    await renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Approve all (4)" }));
    const confirm = await screen.findByRole("dialog", { name: /Approve 3 item/ });
    expect(within(confirm).getByText("3 of 4 shown")).toBeInTheDocument();
    expect(within(confirm).getByText("1 git records, 2 database items")).toBeInTheDocument();
    expect(within(confirm).getByText("high")).toBeInTheDocument();
    expect(within(confirm).getByText("1 left out")).toBeInTheDocument();
    expect(within(confirm).getByText("3 × record an approval only (the change already landed)")).toBeInTheDocument();

    fireEvent.click(within(confirm).getByRole("button", { name: "Approve 3" }));
    await waitFor(() => expect(approveBatch).toHaveBeenCalledTimes(1));
    expect(approveBatch).toHaveBeenCalledWith({ ids: [1, 2], gitDigests: ["abc"], note: "", includeBlocks: false });
  });

  it("includes the blocked rows only after the box is ticked", async () => {
    await renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Approve all (4)" }));
    const confirm = await screen.findByRole("dialog", { name: /Approve 3 item/ });
    fireEvent.click(within(confirm).getByRole("checkbox", { name: /Also approve the 1 blocked item/ }));
    const ticked = await screen.findByRole("dialog", { name: /Approve 4 item/ });
    expect(within(ticked).getByText("critical")).toBeInTheDocument();
    fireEvent.click(within(ticked).getByRole("button", { name: "Approve 4" }));
    await waitFor(() => expect(approveBatch).toHaveBeenCalledTimes(1));
    expect(approveBatch).toHaveBeenCalledWith({ ids: [1, 2, 3], gitDigests: ["abc"], note: "", includeBlocks: true });
  });

  it("reject selected leaves git records out", async () => {
    rejectBatch.mockResolvedValue({ success: true, data: { rejected: 1, skipped: 0, failed: 0, results: [] } });
    await renderDialog();
    fireEvent.click(box("gabc"));
    expect(screen.getByRole("button", { name: "Reject selected (0)" })).toBeDisabled();
    fireEvent.click(box("s1"));
    fireEvent.click(screen.getByRole("button", { name: "Reject selected (1)" }));
    const confirm = await screen.findByRole("dialog", { name: /Reject 1 item/ });
    fireEvent.click(within(confirm).getByRole("button", { name: "Reject 1" }));
    await waitFor(() => expect(rejectBatch).toHaveBeenCalledWith({ ids: [1], note: "" }));
  });

  it("the verdict filter lists only blocks, and Approve all then covers only what is shown", async () => {
    await renderDialog();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Verdict" }));
    fireEvent.click(await screen.findByRole("option", { name: "Blocked" }));
    expect(screen.getByTestId("row-s3")).toBeInTheDocument();
    expect(screen.queryByTestId("row-s1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve all (1)" })).toBeInTheDocument();
  });

  it("sorts by size, most lines first", async () => {
    listScans.mockResolvedValue({
      success: true,
      data: { ...DATA, git: [], git_total: 0, scans: [scan(1, { added_lines: 5, files: 1 }), scan(2, { added_lines: 900, files: 3 })], scans_total: 2 },
    });
    await renderDialog();
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Sort" }));
    fireEvent.click(await screen.findByRole("option", { name: "Size, most lines first" }));
    const rows = screen.getAllByTestId(/^row-s/).map((r) => r.getAttribute("data-testid"));
    expect(rows).toEqual(["row-s2", "row-s1"]);
    expect(within(screen.getByTestId("row-s2")).getByText("3 file(s), +900")).toBeInTheDocument();
  });

  it("reject all covers every shown row that can be rejected, after an in-page confirmation", async () => {
    rejectBatch.mockResolvedValue({ success: true, data: { rejected: 3, skipped: 0, failed: 0, results: [] } });
    await renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Reject all (3)" }));
    expect(rejectBatch).not.toHaveBeenCalled();
    const confirm = await screen.findByRole("dialog", { name: /Reject 3 items/ });
    expect(within(confirm).getByText("3 × close it; nothing is undone")).toBeInTheDocument();
    fireEvent.click(within(confirm).getByRole("button", { name: "Reject 3" }));
    await waitFor(() => expect(rejectBatch).toHaveBeenCalledWith({ ids: [1, 2, 3], note: "" }));
  });

  describe("a change that touches a way off the machine", () => {
    const OUTBOUND = { own_decision: true, own_decision_rules: ["net.new-destination"] };

    beforeEach(() => {
      listScans.mockResolvedValue({
        success: true,
        data: { ...DATA, git: [], git_total: 0, scans: [scan(1), scan(2, OUTBOUND)], scans_total: 2 },
      });
    });

    it("is left out of Approve all, and the confirmation and the result both say so", async () => {
      approveBatch.mockResolvedValue({ success: true, data: { approved: 1, skipped: 0, failed: 0, results: [] } });
      await renderDialog();
      expect(within(screen.getByTestId("row-s2")).getByText("Outbound: yours alone")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Approve all (2)" }));
      const confirm = await screen.findByRole("dialog", { name: /Approve 1 item\?/ });
      expect(within(confirm).getByText("1 of 2 shown")).toBeInTheDocument();
      expect(within(confirm).getByText("1 left out: approved one at a time, with a note")).toBeInTheDocument();
      fireEvent.click(within(confirm).getByRole("button", { name: "Approve 1" }));
      await waitFor(() => expect(approveBatch).toHaveBeenCalledWith({ ids: [1], gitDigests: [], note: "", includeBlocks: false }));
      expect(await screen.findByText(/1 left for you: it touches a way off the machine/)).toBeInTheDocument();
    });

    it("offers nothing to confirm when every row is one", async () => {
      await renderDialog();
      fireEvent.click(box("s2"));
      fireEvent.click(screen.getByRole("button", { name: "Approve selected (1)" }));
      const confirm = await screen.findByRole("dialog", { name: /Approve 0 items/ });
      expect(within(confirm).getByRole("button", { name: "Approve 0" })).toBeDisabled();
      expect(within(confirm).getByRole("button", { name: "Cancel" })).toBeEnabled();
    });

    it("is approved on its own only once a note is typed", async () => {
      approveScan.mockResolvedValue({ success: true, data: {} });
      await renderDialog();
      fireEvent.click(screen.getByTestId("row-s2"));
      const approve = screen.getByRole("button", { name: "Record approval" });
      expect(approve).toBeDisabled();
      fireEvent.change(screen.getByLabelText("Note (kept with your decision)"), { target: { value: "our own docs host" } });
      expect(approve).toBeEnabled();
      fireEvent.click(approve);
      await waitFor(() => expect(approveScan).toHaveBeenCalledWith(2, { note: "our own docs host", overrideBlock: false }));
    });
  });

  describe("the decided list", () => {
    const decided = scan(7, { status: "rejected", decided_at: "2026-10-07T13:00:00.000Z", decided_by: "operator", decision_note: "not ours" });

    beforeEach(() => {
      listScans.mockImplementation(async (status) => ({
        success: true,
        data: status === "resolved" ? { ...DATA, scans: [decided], scans_total: 1, git: [], git_total: 0 } : DATA,
      }));
      history.mockResolvedValue({
        success: true,
        data: { removes: { rejected: 1, cleared: 4, total: 5 }, keeps: { approved: 9, open: 3, in_use: 2, git_ledger: true } },
      });
      clearHistory.mockResolvedValue({
        success: true,
        data: { removed: { rejected: 1, cleared: 4, total: 5 }, keeps: { approved: 9, open: 3, in_use: 2 } },
      });
    });

    const openDecided = async () => {
      await renderDialog();
      fireEvent.click(screen.getByRole("radio", { name: "Decided" }));
      await screen.findByTestId("row-s7");
    };

    it("shows the decision, when, by whom and the note, and offers no batch decisions", async () => {
      await openDecided();
      expect(listScans).toHaveBeenLastCalledWith("resolved", 200, { git: true, gitLimit: 200 });
      const row = within(screen.getByTestId("row-s7"));
      expect(row.getByText("Rejected")).toBeInTheDocument();
      expect(row.getByText("1 h ago")).toBeInTheDocument();
      expect(row.getByText("operator")).toBeInTheDocument();
      expect(row.getByText("not ours")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Approve all/ })).not.toBeInTheDocument();
    });

    it("clears history only after saying exactly what goes and what stays", async () => {
      await openDecided();
      fireEvent.click(screen.getByRole("button", { name: "Clear history…" }));
      const confirm = await screen.findByRole("dialog", { name: "Clear the decided records?" });
      expect(clearHistory).not.toHaveBeenCalled();
      expect(within(confirm).getByText("Rejected records removed").nextSibling).toHaveTextContent("1");
      expect(within(confirm).getByText("Closed records removed").nextSibling).toHaveTextContent("4");
      expect(within(confirm).getByText(/9 approvals \(they are what lets the same change pass again\), 3 open items, 2 decided records still in use/)).toBeInTheDocument();
      fireEvent.click(within(confirm).getByRole("button", { name: "Remove 5 records" }));
      await waitFor(() => expect(clearHistory).toHaveBeenCalledTimes(1));
      expect(await screen.findByText(/Removed 5 decided records \(1 rejected, 4 closed\)\. 9 approvals and 3 open items were not touched\./)).toBeInTheDocument();
    });
  });
});
