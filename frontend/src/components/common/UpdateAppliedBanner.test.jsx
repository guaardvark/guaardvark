import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import UpdateAppliedBanner, { describeUpdate } from "./UpdateAppliedBanner";
import { useAppStore } from "../../stores/useAppStore";

let mockHealth = null;
vi.mock("../../contexts/HealthContext", () => ({
  useHealth: () => ({ healthData: { backend: mockHealth } }),
}));
vi.mock("../modals/RebootProgressModal", () => ({
  default: ({ open }) => (open ? <div>reboot modal open</div> : null),
}));

const health = (over = {}) => ({
  status: "ok",
  version: "3.0.0",
  boot_id: "boot-a",
  disk_version: "3.0.0",
  restart_required: false,
  restart_reason: null,
  update: null,
  ...over,
});

const frontendUpdate = (ts, files = 4) => ({
  ts,
  files,
  backend_changed: false,
  frontend_changed: true,
  deps_changed: false,
});

describe("describeUpdate", () => {
  it("is quiet for an older backend without boot_id", () => {
    expect(describeUpdate({ status: "ok" }, null)).toBeNull();
  });

  it("offers a restart when the GPU faulted, ahead of any update notice", () => {
    const notice = describeUpdate(
      health({
        gpu_fault: { at: "2026-10-23T23:19:52", where: "inference", error: "launch timed out" },
        restart_required: true,
        restart_reason: "backend code changed",
      }),
      { bootId: "boot-a", updateTs: null },
    );
    expect(notice.kind).toBe("restart");
    expect(notice.reason).toBe("gpu_fault");
    expect(notice.message).toBe(
      "The GPU stopped responding at 23:19. Image generation fails until Guaardvark restarts.",
    );
  });

  it("asks for a restart as soon as the backend says so", () => {
    const notice = describeUpdate(
      health({ restart_required: true, restart_reason: "backend code changed" }),
      null,
    );
    expect(notice.kind).toBe("restart");
    expect(notice.message).toContain("backend code changed");
  });
});

describe("UpdateAppliedBanner", () => {
  beforeEach(() => {
    mockHealth = null;
    window.localStorage.clear();
  });

  it("shows nothing while the backend is offline or current", () => {
    const { rerender } = render(<UpdateAppliedBanner />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    mockHealth = health();
    rerender(<UpdateAppliedBanner />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("offers Reload for a frontend update applied after the page loaded", () => {
    mockHealth = health();
    const { rerender } = render(<UpdateAppliedBanner />);
    mockHealth = health({ update: frontendUpdate(500, 12) });
    rerender(<UpdateAppliedBanner />);
    expect(screen.getByText(/updated \(12 files\)\. Reload this page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("stays quiet for an update the page already loaded after", () => {
    mockHealth = health({ update: frontendUpdate(500) });
    const { rerender } = render(<UpdateAppliedBanner />);
    rerender(<UpdateAppliedBanner />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("offers Restart behind a confirmation when the backend runs old code", () => {
    mockHealth = health({
      restart_required: true,
      restart_reason: "version 3.0.1 is on disk, 3.0.0 is running",
    });
    render(<UpdateAppliedBanner />);
    expect(screen.getByText(/Restart it to finish \(version 3\.0\.1 is on disk/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restart" }));
    expect(screen.queryByText("reboot modal open")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restart now" }));
    expect(screen.getByText("reboot modal open")).toBeInTheDocument();
  });

  it("offers Reload once the backend has restarted", () => {
    mockHealth = health();
    const { rerender } = render(<UpdateAppliedBanner />);
    mockHealth = health({ boot_id: "boot-b" });
    rerender(<UpdateAppliedBanner />);
    expect(screen.getByText(/restarted\. Reload this page/)).toBeInTheDocument();
  });

  it("dismissal holds for that boot and update, and a new one shows again", () => {
    mockHealth = health();
    const { rerender, unmount } = render(<UpdateAppliedBanner />);
    mockHealth = health({ update: frontendUpdate(500) });
    rerender(<UpdateAppliedBanner />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    unmount();

    mockHealth = health({ restart_required: true, restart_reason: "backend code changed", update: frontendUpdate(500) });
    render(<UpdateAppliedBanner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("tells the app store while it is up, so a tip card stays out of its way", () => {
    mockHealth = health({ restart_required: true, restart_reason: "backend code changed" });
    const { unmount } = render(<UpdateAppliedBanner />);
    expect(useAppStore.getState().updateNoticeVisible).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(useAppStore.getState().updateNoticeVisible).toBe(false);
    unmount();

    mockHealth = health({ restart_required: true, restart_reason: "dependencies changed", disk_version: "3.0.2" });
    const second = render(<UpdateAppliedBanner />);
    expect(useAppStore.getState().updateNoticeVisible).toBe(true);
    second.unmount();
    expect(useAppStore.getState().updateNoticeVisible).toBe(false);
  });
});
