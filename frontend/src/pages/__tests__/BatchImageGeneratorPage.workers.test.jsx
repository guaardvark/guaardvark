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

// The worker renders one picture at a time on a CUDA card whatever Max Workers
// says (batch_image_generator: one pipeline on one card). The page reads the
// device from /batch-image/status and says so instead of showing a count that
// never applies.
const mockFetchWithDevice = (device) => {
  global.fetch = vi.fn(async (url) => {
    if (String(url).endsWith("/batch-image/status")) {
      return {
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => ({ success: true, data: { image_generator: { device } } }),
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

describe("Image Gen workers on a CUDA card", () => {
  beforeEach(() => {
    mockFetchWithDevice("cuda");
  });

  it("shows one-at-a-time instead of a worker count", async () => {
    await renderPage();
    expect(screen.getByText("One at a time")).toBeInTheDocument();
    expect(screen.queryByText(/Workers: \d/)).toBeNull();
  });

  it("disables Max Workers with a plain reason", async () => {
    await renderPage();
    fireEvent.click(screen.getByText("Advanced Settings"));
    expect(screen.getByText("Max Workers: 1")).toBeInTheDocument();
    expect(screen.getByText("One picture at a time on the graphics card.")).toBeInTheDocument();
  });
});

describe("Image Gen workers on a CPU box", () => {
  it("keeps the worker count", async () => {
    mockFetchWithDevice("cpu");
    await renderPage();
    expect(screen.getByText("Workers: 2")).toBeInTheDocument();
  });
});
