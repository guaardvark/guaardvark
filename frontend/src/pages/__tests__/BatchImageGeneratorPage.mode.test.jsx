import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
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

beforeEach(() => {
  global.fetch = vi.fn(async () => ({ ok: true, json: async () => ({}) }));
});

const renderAt = async (url) => {
  render(
    <MemoryRouter initialEntries={[url]}>
      <BatchImageGeneratorPage embedded />
    </MemoryRouter>,
  );
  await act(async () => {});
};

describe("Image Gen ?mode=", () => {
  it("opens bulk input for ?mode=bulk", async () => {
    await renderAt("/batch-images?mode=bulk");
    expect(screen.getByRole("button", { name: "Bulk Input" })).toHaveClass("MuiButton-contained");
    expect(screen.getByRole("button", { name: "Single Prompt" })).toHaveClass("MuiButton-outlined");
  });

  it("opens single prompt without the parameter", async () => {
    await renderAt("/batch-images");
    expect(screen.getByRole("button", { name: "Single Prompt" })).toHaveClass("MuiButton-contained");
  });
});
