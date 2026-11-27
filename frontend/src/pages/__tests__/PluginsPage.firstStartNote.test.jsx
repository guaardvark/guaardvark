import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("../../api/pluginsService", () => ({
  listPlugins: vi.fn(),
  pluginGateRefusal: vi.fn(() => null),
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
  checkComfyUpdate: vi.fn(async () => ({})),
  startComfyUpdate: vi.fn(),
  getComfyUpdateStatus: vi.fn(async () => ({ state: "idle", log_tail: [] })),
  restoreComfyPrevious: vi.fn(),
  repairComfyUpdate: vi.fn(),
  installComfy: vi.fn(),
}));
vi.mock("../../components/settings/OllamaLifecycleSection", () => ({ default: () => null }));
vi.mock("../../components/settings/ComfyUILifecycleSection", () => ({ default: () => null }));
vi.mock("../../components/layout/PageLayout", () => ({ default: ({ children }) => <div>{children}</div> }));

import { PluginCard } from "../PluginsPage";

const audioFoundry = {
  id: "audio_foundry",
  name: "Audio Foundry",
  status: "starting",
  enabled: true,
  version: "0.2.0",
  type: "service",
  port: 8206,
  config: { timeout: 120, start_timeout: 1800 },
};

const renderCard = (plugin) =>
  render(<PluginCard plugin={plugin} onAction={vi.fn()} onConfigOpen={vi.fn()} showMessage={vi.fn()} />);

describe("Plugin card first-start note", () => {
  it("says a first start sets up environments while a long-start plugin is starting", () => {
    renderCard(audioFoundry);
    const note = screen.getByTestId("plugin-first-start-note-audio_foundry");
    expect(note).toHaveTextContent("The first start sets up its environments and can take several minutes.");
    expect(note).toHaveTextContent("The Logs panel shows its progress.");
  });

  it("is gone once the plugin is running", () => {
    renderCard({ ...audioFoundry, status: "running" });
    expect(screen.queryByTestId("plugin-first-start-note-audio_foundry")).not.toBeInTheDocument();
  });

  it("is not shown for a plugin without a declared start_timeout", () => {
    renderCard({ ...audioFoundry, id: "swarm", name: "Swarm", config: { timeout: 30 } });
    expect(screen.queryByTestId("plugin-first-start-note-swarm")).not.toBeInTheDocument();
  });
});
