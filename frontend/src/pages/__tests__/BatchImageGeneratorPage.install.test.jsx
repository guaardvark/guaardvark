import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../contexts/UnifiedProgressContext", () => ({
  useUnifiedProgress: () => ({ activeProcesses: new Map(), unifiedJobs: new Map(), socketRef: { current: null } }),
  useImageProcesses: () => [],
}));
vi.mock("../../hooks/useJobsGate", () => ({ default: () => ({ gpuBusy: false, blockReason: null }) }));
vi.mock("../../components/layout/PageLayout", () => ({
  default: ({ children, actions }) => (
    <div>
      {actions}
      {children}
    </div>
  ),
}));
vi.mock("../../components/filmcrew/CharacterPicker", () => ({ default: () => null }));
vi.mock("../../components/images/BatchHistoryCard", () => ({ default: () => null }));
vi.mock("../../components/common/GpuGateBanner", () => ({ default: () => null }));

import BatchImageGeneratorPage from "../BatchImageGeneratorPage";

// Nothing installed: the recommended model is listed but not on disk.
const MODELS = {
  success: true,
  data: {
    models: [
      {
        id: "zimage-turbo",
        label: "Z-Image Turbo",
        name: "Z-Image Turbo",
        is_downloaded: false,
        availability: "downloadable",
        size_gb: 16,
        recommended: true,
      },
    ],
    unavailable_models: [],
    adapters: [],
  },
};

beforeEach(() => {
  global.fetch = vi.fn(async (url) => ({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => (String(url).endsWith("/batch-image/models") ? MODELS : {}),
    // The page reads the model list through apiClient.handleResponse, which reads text().
    text: async () => JSON.stringify(String(url).endsWith("/batch-image/models") ? MODELS : {}),
  }));
});

const renderPage = async () => {
  render(
    <MemoryRouter initialEntries={["/batch-images"]}>
      <BatchImageGeneratorPage embedded />
    </MemoryRouter>,
  );
  await act(async () => {});
};

describe("Image Gen model install", () => {
  it("offers Install when no image model is installed", async () => {
    await renderPage();
    expect(screen.getByText("No image model is installed yet.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Install" }).length).toBeGreaterThan(0);
    // Auto may still render through ComfyUI, so Start stays available.
    expect(screen.getByRole("button", { name: "Start Generation" })).not.toBeDisabled();
  });

  it("never asks the backend to download a model", async () => {
    await renderPage();
    fireEvent.change(screen.getByPlaceholderText(/Paste your complete prompt here/), {
      target: { value: "a red fox in snow" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Start Generation" }));
    });

    const call = global.fetch.mock.calls.find(([url]) => String(url).endsWith("/batch-image/generate/prompts"));
    expect(call).toBeDefined();
    const body = JSON.parse(call[1].body);
    expect(body.prompts).toEqual(["a red fox in snow"]);
    expect(body).not.toHaveProperty("allow_model_download");
  });
});
