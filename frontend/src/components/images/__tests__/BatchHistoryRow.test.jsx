import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import BatchHistoryRow from "../BatchHistoryRow";

const batch = { batch_id: "ImageBatch_1", display_name: "Night city", completed_images: 4, status: "completed" };

describe("BatchHistoryRow", () => {
  it("shows the name and image count, opens on click, and keeps retry and hide to their own buttons", () => {
    const onOpen = vi.fn();
    const onAdjustRetry = vi.fn();
    const onHide = vi.fn();
    render(
      <BatchHistoryRow
        batch={batch}
        dateStr="Nov 16"
        onOpen={onOpen}
        onLoad={vi.fn()}
        onHide={onHide}
        onDownload={vi.fn()}
        onAdjustRetry={onAdjustRetry}
      />,
    );
    expect(screen.getByText("Night city")).toBeInTheDocument();
    expect(screen.getByText("4 images")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Adjust & Retry" }));
    expect(onAdjustRetry).toHaveBeenCalledWith("ImageBatch_1");
    fireEvent.click(screen.getByRole("button", { name: "Clear batch from list" }));
    expect(onHide).toHaveBeenCalledWith("ImageBatch_1");
    expect(onOpen).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Night city"));
    expect(onOpen).toHaveBeenCalledWith(batch, 0);
  });

  it("loads an unfinished batch instead of opening its gallery", () => {
    const onLoad = vi.fn();
    render(
      <BatchHistoryRow
        batch={{ ...batch, status: "running" }}
        onOpen={vi.fn()}
        onLoad={onLoad}
        onHide={vi.fn()}
        onDownload={vi.fn()}
        onAdjustRetry={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText("Night city"));
    expect(onLoad).toHaveBeenCalledWith("ImageBatch_1");
    expect(screen.getByText("running")).toBeInTheDocument();
  });
});
