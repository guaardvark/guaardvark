import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// The page lays its cards out on the shared board, which reads the grid from the layout context.
vi.mock("../../contexts/LayoutContext", () => {
  const layout = {
    gridSettings: {
      CONTAINER_PADDING_PX: 4,
      CARD_MARGIN_PX: 8,
      COLS_COUNT: 175,
      ROW_HEIGHT_PX: 10,
      cardGridW: 35,
      cardGridH: 49,
      cardMinGridW: 30,
    },
  };
  return { useDashboardWidth: () => 1750, useLayout: () => layout };
});
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

const PROMPT = "a red fox in fresh snow";
let jobStatus;

const reply = (body) => ({ ok: true, json: async () => body });

beforeEach(() => {
  localStorage.clear();
  jobStatus = {
    batch_id: "b1",
    status: "cancelled",
    completed_images: 0,
    failed_images: 0,
    retry_data: { params: { ui_config: { inputMode: "single", batchItems: PROMPT } } },
  };
  global.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/batch-image/queue")) {
      return reply({ data: { queue: [{ batch_id: "b1", status: "queued", total_images: 1, completed_images: 0 }] } });
    }
    if (u.includes("/batch-image/cancel/b1")) return reply({ success: true });
    if (u.includes("/batch-image/status/b1")) return reply({ data: jobStatus });
    return reply({});
  });
});

const renderPage = async () => {
  render(
    <MemoryRouter initialEntries={["/batch-images"]}>
      <BatchImageGeneratorPage embedded />
    </MemoryRouter>,
  );
  await act(async () => {});
};

const promptBox = () => screen.getByPlaceholderText(/Paste your complete prompt here/);

const cancelQueued = async () => {
  const button = await screen.findByRole("button", { name: "cancel batch" });
  await act(async () => {
    fireEvent.click(button);
  });
  await act(async () => {});
};

describe("Image Gen cancel before start", () => {
  it("puts the cancelled job's prompt back in an empty form", async () => {
    await renderPage();
    await cancelQueued();
    expect(promptBox()).toHaveValue(PROMPT);
  });

  it("leaves a prompt typed for the next job alone", async () => {
    await renderPage();
    fireEvent.change(promptBox(), { target: { value: "next job" } });
    await cancelQueued();
    expect(promptBox()).toHaveValue("next job");
  });

  it("does not restore a job that already made an image", async () => {
    jobStatus.completed_images = 1;
    await renderPage();
    await cancelQueued();
    expect(promptBox()).toHaveValue("");
  });
});
