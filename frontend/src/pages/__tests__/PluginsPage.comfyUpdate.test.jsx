import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

const api = {
  check: vi.fn(),
  start: vi.fn(),
  status: vi.fn(),
  restore: vi.fn(),
  repair: vi.fn(),
};

vi.mock("../../api/pluginsService", () => ({
  listPlugins: vi.fn(),
  startPlugin: vi.fn(),
  stopPlugin: vi.fn(),
  enablePlugin: vi.fn(),
  disablePlugin: vi.fn(),
  refreshPlugins: vi.fn(),
  updatePluginConfig: vi.fn(),
  getPluginLogs: vi.fn(async () => ({ success: true, data: { logs: "" } })),
  startVisionCamera: vi.fn(),
  stopVisionCamera: vi.fn(),
  getVisionCameraStatus: vi.fn(async () => ({ active: false })),
  checkComfyUpdate: (...args) => api.check(...args),
  startComfyUpdate: (...args) => api.start(...args),
  getComfyUpdateStatus: (...args) => api.status(...args),
  restoreComfyPrevious: (...args) => api.restore(...args),
  repairComfyUpdate: (...args) => api.repair(...args),
}));
vi.mock("../../components/settings/OllamaLifecycleSection", () => ({ default: () => null }));
vi.mock("../../components/settings/ComfyUILifecycleSection", () => ({ default: () => null }));
vi.mock("../../components/layout/PageLayout", () => ({ default: ({ children }) => <div>{children}</div> }));

import { PluginCard } from "../PluginsPage";

const comfyPlugin = {
  id: "comfyui",
  name: "ComfyUI",
  status: "running",
  enabled: true,
  version: "1.0.0",
  type: "service",
  port: 8188,
};

const IDLE = { state: "idle", phase: null, phase_label: null, percent: 0, log_tail: [] };

const availableCheck = {
  installed: "0.33.0",
  target: "0.39.0",
  available: true,
  busy: null,
  disk_free_gb: 120.4,
  package_changes: [
    { name: "comfy-aimdo", from: "0.4.13", to: "0.5.5" },
    { name: "torchaudio", from: "2.6.0", to: null },
  ],
  previous: null,
  keeps: ["models", "custom nodes", "your outputs"],
  download: "about 66 MB from GitHub, then packages from PyPI",
};

const renderCard = (plugin = comfyPlugin) => {
  const showMessage = vi.fn();
  render(<PluginCard plugin={plugin} onAction={vi.fn()} onConfigOpen={vi.fn()} showMessage={showMessage} />);
  return { showMessage };
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.status.mockResolvedValue(IDLE);
  api.check.mockResolvedValue({ ...availableCheck, available: false, target: "0.33.0" });
});

describe("ComfyUI card update section", () => {
  it("shows the installed ComfyUI version and no Update button when nothing is newer", async () => {
    renderCard();
    expect(await screen.findByText("ComfyUI 0.33.0")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Update to/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("comfyui-update-progress")).not.toBeInTheDocument();
  });

  it("asks the backend nothing for other plugins", async () => {
    renderCard({ ...comfyPlugin, id: "ollama", name: "Ollama" });
    expect(await screen.findByText("Ollama")).toBeInTheDocument();
    expect(api.check).not.toHaveBeenCalled();
    expect(api.status).not.toHaveBeenCalled();
    expect(screen.queryByText(/ComfyUI 0\./)).not.toBeInTheDocument();
  });

  it("offers Update to the newer version when one is available", async () => {
    api.check.mockResolvedValue(availableCheck);
    renderCard();
    const button = await screen.findByRole("button", { name: "Update to 0.39.0" });
    expect(button).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Restore/ })).not.toBeInTheDocument();
  });

  it("disables Update while ComfyUI is busy and says why on hover", async () => {
    api.check.mockResolvedValue({ ...availableCheck, busy: "ComfyUI is rendering a video right now" });
    renderCard();
    const button = await screen.findByRole("button", { name: "Update to 0.39.0" });
    expect(button).toBeDisabled();
    fireEvent.mouseOver(button.parentElement);
    expect(await screen.findByText("ComfyUI is rendering a video right now")).toBeInTheDocument();
  });

  it("offers a quieter Restore when an older copy was kept", async () => {
    api.check.mockResolvedValue({ ...availableCheck, available: false, installed: "0.39.0", previous: "0.33.0" });
    renderCard();
    expect(await screen.findByRole("button", { name: "Restore 0.33.0" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Update to/ })).not.toBeInTheDocument();
  });

  it("confirms with the versions, each package change, free disk and what is kept, then starts", async () => {
    api.check.mockResolvedValue(availableCheck);
    api.start.mockResolvedValue({
      state: "running",
      phase: "download",
      phase_label: "Downloading ComfyUI",
      percent: 5,
      from: "0.33.0",
      to: "0.39.0",
      log_tail: ["Fetching v0.39.0"],
    });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Update to 0.39.0" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Update ComfyUI to 0.39.0")).toBeInTheDocument();
    expect(within(dialog).getByText(/Downloads ComfyUI 0\.39\.0 from GitHub and its Python packages from PyPI/)).toBeInTheDocument();
    expect(within(dialog).getByText(/switches back to 0\.33\.0 by itself/)).toBeInTheDocument();
    expect(within(dialog).getByText("0.33.0 → 0.39.0")).toBeInTheDocument();
    expect(within(dialog).getByText("comfy-aimdo")).toBeInTheDocument();
    expect(within(dialog).getByText("0.4.13 → 0.5.5")).toBeInTheDocument();
    expect(within(dialog).getByText("torchaudio")).toBeInTheDocument();
    expect(within(dialog).getByText("2.6.0, removed")).toBeInTheDocument();
    expect(within(dialog).getByText("120.4 GB")).toBeInTheDocument();
    expect(within(dialog).getByText(/Not touched: models, custom nodes, your outputs/)).toBeInTheDocument();
    // An update switches back on its own, so the dialog does not claim it is final.
    expect(within(dialog).queryByText("This cannot be undone.")).toBeNull();
    expect(api.start).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Update now" }));
    await waitFor(() => expect(api.start).toHaveBeenCalledTimes(1));

    const progress = await screen.findByTestId("comfyui-update-progress");
    expect(within(progress).getByText(/Downloading ComfyUI \(5%\)/)).toBeInTheDocument();
    expect(progress.querySelector(".MuiLinearProgress-root")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Update to 0.39.0" })).not.toBeInTheDocument();
  });

  it("tells the person why the backend refused to start", async () => {
    api.check.mockResolvedValue(availableCheck);
    api.start.mockRejectedValue(new Error("Not enough free disk: 2.1 GB"));
    const { showMessage } = renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Update to 0.39.0" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Update now" }));
    await waitFor(() => expect(showMessage).toHaveBeenCalledWith("Not enough free disk: 2.1 GB", "error"));
    expect(screen.queryByTestId("comfyui-update-progress")).not.toBeInTheDocument();
  });

  it("shows progress for a job already running, then the rolled-back message with its reason", async () => {
    api.status
      .mockResolvedValueOnce({
        state: "running",
        phase: "pip",
        phase_label: "Installing Python packages",
        percent: 60,
        from: "0.33.0",
        to: "0.39.0",
        log_tail: ["Collecting comfy-aimdo==0.5.5", "Installing..."],
      })
      .mockResolvedValue({
        state: "rolled_back",
        phase: "done",
        phase_label: "Switched back",
        percent: 100,
        from: "0.33.0",
        to: "0.39.0",
        finished_at: "2026-10-11T09:00:00Z",
        message: "ComfyUI 0.39.0 did not start (port 8188 never answered), so 0.33.0 is back.",
        log_tail: ["Health check timed out", "Restored 0.33.0"],
      });
    api.check.mockResolvedValue({ ...availableCheck, busy: "An update is running" });
    renderCard();

    const progress = await screen.findByTestId("comfyui-update-progress");
    expect(within(progress).getByText(/Installing Python packages \(60%\)/)).toBeInTheDocument();
    fireEvent.click(within(progress).getByRole("button", { name: "Show details" }));
    expect(within(progress).getByTestId("comfyui-update-log")).toHaveTextContent("Collecting comfy-aimdo==0.5.5");

    const result = await screen.findByTestId("comfyui-update-result", {}, { timeout: 3000 });
    expect(within(result).getByText("Switched back")).toBeInTheDocument();
    expect(within(result).getByText(/port 8188 never answered/)).toBeInTheDocument();
    expect(screen.queryByTestId("comfyui-update-progress")).not.toBeInTheDocument();
    await waitFor(() => expect(api.check.mock.calls.length).toBeGreaterThanOrEqual(2));

    fireEvent.click(within(result).getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByTestId("comfyui-update-result")).not.toBeInTheDocument();
  });

  const repairCheck = {
    ...availableCheck,
    installed: "0.39.0",
    previous: "0.33.0",
    needs_repair: true,
    repair: {
      paths: ["plugins/comfyui/ComfyUI.updating", "plugins/comfyui/ComfyUI.previous"],
      message: "The last update stopped half way, so ComfyUI is not in one place. Repair puts the previous copy back.",
    },
  };

  it("offers only Repair, with the backend's words and the paths, when an update was cut short", async () => {
    api.check.mockResolvedValue(repairCheck);
    api.status.mockResolvedValue({ ...IDLE, state: "needs_repair", message: "ComfyUI needs repair" });
    renderCard();

    const repair = await screen.findByTestId("comfyui-update-repair");
    expect(within(repair).getByText(/The last update stopped half way/)).toBeInTheDocument();
    expect(within(repair).getByText(/ComfyUI\.updating, plugins\/comfyui\/ComfyUI\.previous/)).toHaveStyle({ fontFamily: "monospace" });
    expect(within(repair).getByRole("button", { name: "Repair" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Update to/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Restore/ })).not.toBeInTheDocument();
    expect(screen.queryByTestId("comfyui-update-result")).not.toBeInTheDocument();
  });

  it("confirms the repair in plain words, then shows its progress like an update", async () => {
    api.check.mockResolvedValue(repairCheck);
    api.status.mockResolvedValue({ ...IDLE, state: "needs_repair", message: "ComfyUI needs repair" });
    api.repair.mockResolvedValue({
      state: "running",
      phase: "restore",
      phase_label: "Moving models back",
      percent: 20,
      from: "0.39.0",
      to: "0.33.0",
      log_tail: ["Moving models/"],
    });
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Repair" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Put ComfyUI back the way it was")).toBeInTheDocument();
    expect(within(dialog).getByText(/Moves your models and settings back to the previous copy of ComfyUI and restarts that version/)).toBeInTheDocument();
    expect(within(dialog).getByText("0.39.0 → 0.33.0")).toBeInTheDocument();
    expect(within(dialog).getByText(/ComfyUI\.updating/)).toBeInTheDocument();
    expect(within(dialog).queryByText("This cannot be undone.")).toBeNull();
    expect(api.repair).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Repair now" }));
    await waitFor(() => expect(api.repair).toHaveBeenCalledTimes(1));

    const progress = await screen.findByTestId("comfyui-update-progress");
    expect(within(progress).getByText(/ComfyUI 0\.39\.0 → 0\.33\.0: Moving models back \(20%\)/)).toBeInTheDocument();
    expect(progress.querySelector(".MuiLinearProgress-root")).not.toBeNull();
    expect(screen.queryByTestId("comfyui-update-repair")).not.toBeInTheDocument();
  });
});
