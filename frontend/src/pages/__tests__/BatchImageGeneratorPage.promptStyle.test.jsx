import React from "react";
import { describe, it, expect, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
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

// The backend applies the content type and the anatomy phrasing only on the
// tag path; a prose model (and Auto while it resolves to one) skips both, so
// the page says so instead of offering controls that do nothing.
const mockModels = (autoPromptStyle) => {
  global.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.endsWith("/batch-image/models")) {
      const body = {
        success: true,
        data: {
          models: [
            { id: "zimage-turbo", label: "Z-Image", is_downloaded: true, prompt_style: "natural" },
            { id: "sd-xl", label: "SDXL", is_downloaded: true, prompt_style: "tags" },
          ],
          auto_prompt_style: autoPromptStyle,
          unavailable_models: [],
          adapters: [],
        },
      };
      // json() for older readers; text() for apiClient.handleResponse.
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => body,
        text: async () => JSON.stringify(body),
      };
    }
    return { ok: true, headers: { get: () => "application/json" }, json: async () => ({}) };
  });
};

const renderPage = async () => {
  render(
    <MemoryRouter initialEntries={["/batch-images"]}>
      <BatchImageGeneratorPage embedded />
    </MemoryRouter>,
  );
  await act(async () => {});
};

describe("Image Gen controls on a prose model (Auto resolves to Z-Image)", () => {
  it("replaces Content Type with a note and disables Enhance anatomy", async () => {
    mockModels("natural");
    await renderPage();
    expect(screen.queryByText("Content Type (for better quality)")).toBeNull();
    expect(screen.getByText(/Content Type is not used/)).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enhance anatomy" })).toBeDisabled();
    expect(screen.getByText("This model does not use anatomy enhancement.")).toBeInTheDocument();
  });
});

describe("Image Gen controls when Auto resolves to a tag-style model", () => {
  it("keeps Content Type and Enhance anatomy", async () => {
    mockModels("tags");
    await renderPage();
    expect(screen.getByText("Content Type (for better quality)")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Enhance anatomy" })).not.toBeDisabled();
  });
});
