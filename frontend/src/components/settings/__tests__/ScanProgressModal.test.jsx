import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { beforeEach, describe, expect, it, vi } from "vitest";

const triggerRun = vi.fn();
const getRuns = vi.fn();
const listPendingFixes = vi.fn();
const getStatus = vi.fn();
const cancelScan = vi.fn();

vi.mock("../../../api/selfImprovementService", () => ({
  selfImprovementService: {
    triggerRun: (...args) => triggerRun(...args),
    getRuns: (...args) => getRuns(...args),
    listPendingFixes: (...args) => listPendingFixes(...args),
    getStatus: (...args) => getStatus(...args),
    cancelScan: (...args) => cancelScan(...args),
  },
}));

vi.mock("socket.io-client", () => ({
  io: () => ({
    on: vi.fn(),
    off: vi.fn(),
    disconnect: vi.fn(),
  }),
}));

import ScanProgressModal from "../ScanProgressModal";

const theme = createTheme();
const nowIso = () => new Date().toISOString();

function renderModal(props = {}) {
  const onClose = vi.fn();
  const onComplete = vi.fn();
  const onBackground = vi.fn();
  const result = render(
    <ThemeProvider theme={theme}>
      <ScanProgressModal
        open
        onClose={onClose}
        onComplete={onComplete}
        onBackground={onBackground}
        {...props}
      />
    </ThemeProvider>,
  );
  return { ...result, onClose, onComplete, onBackground };
}

describe("ScanProgressModal cancel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    triggerRun.mockResolvedValue({ success: true, data: { scan_id: 42, status: "dispatched" } });
    getRuns.mockResolvedValue({
      success: true,
      data: { runs: [{ id: 42, status: "running", timestamp: nowIso() }] },
    });
    listPendingFixes.mockResolvedValue({ success: true, data: [] });
    getStatus.mockResolvedValue({
      success: true,
      data: { last_run: { id: 42, status: "running" } },
    });
    cancelScan.mockResolvedValue({ success: true, data: { status: "cancelled" } });
  });

  it("Cancel posts to the scan cancel route and stays open in Cancelling", async () => {
    const { onClose, onBackground, onComplete } = renderModal();

    await waitFor(() => expect(triggerRun).toHaveBeenCalledTimes(1));
    const cancelBtn = await screen.findByRole("button", { name: "Cancel" });
    await act(async () => {
      fireEvent.click(cancelBtn);
    });

    await waitFor(() => expect(cancelScan).toHaveBeenCalledWith(42));
    expect(screen.getByText("Cancelling")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(onBackground).not.toHaveBeenCalled();
    expect(onComplete).not.toHaveBeenCalled();
  });

  it("shows Cancelled when the status route reports cancelled, without closing", async () => {
    getRuns
      .mockResolvedValueOnce({
        success: true,
        data: { runs: [{ id: 42, status: "running", timestamp: nowIso() }] },
      })
      .mockResolvedValue({
        success: true,
        data: { runs: [{ id: 42, status: "cancelled", timestamp: nowIso() }] },
      });
    getStatus
      .mockResolvedValueOnce({
        success: true,
        data: { last_run: { id: 42, status: "running" } },
      })
      .mockResolvedValue({
        success: true,
        data: { last_run: { id: 42, status: "cancelled" } },
      });

    const { onClose, onBackground, onComplete } = renderModal();
    await waitFor(() => expect(triggerRun).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    });
    await waitFor(() => expect(cancelScan).toHaveBeenCalledWith(42));

    await waitFor(
      () => {
        expect(screen.getByText("Cancelled")).toBeTruthy();
        expect(onComplete).toHaveBeenCalled();
      },
      { timeout: 6000 },
    );
    expect(onComplete.mock.calls.some((call) => call[0]?.status === "cancelled")).toBe(true);
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
    expect(onBackground).not.toHaveBeenCalled();
  });
});
