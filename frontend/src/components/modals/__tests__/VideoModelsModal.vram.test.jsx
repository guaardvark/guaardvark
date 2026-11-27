import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import axios from "axios";
import VideoModelsModal from "../VideoModelsModal";

vi.mock("axios");

const row = (over) => ({
  type: "wan",
  description: "",
  size_gb: 21,
  install_size_gb: 27.55,
  is_downloaded: false,
  is_ready: false,
  missing_files: [],
  incomplete_files: {},
  requires: [],
  ...over,
});

const listing = {
  total_vram_mb: 12282,
  models: [
    row({ id: "wan22-14b", name: "Wan 14B T2V", vram_mb: 11000, min_vram_gb: 16, below_card_floor: true }),
    row({ id: "wan22-5b", name: "Wan 5B", vram_mb: 11000, min_vram_gb: 6, below_card_floor: false }),
    row({ id: "flux-dev", name: "FLUX dev", type: "flux", vram_mb: 12000, min_vram_gb: 0, below_card_floor: false }),
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  axios.get.mockImplementation((url) =>
    Promise.resolve({
      data: url.endsWith("/download-status")
        ? { success: true, data: { is_downloading: false, status: "idle" } }
        : { success: true, data: listing },
    }),
  );
  axios.post.mockResolvedValue({ data: { success: true } });
});

describe("VideoModelsModal VRAM chip", () => {
  it("leads with the floor and shows typical use beside it", async () => {
    render(<VideoModelsModal open onClose={() => {}} showMessage={() => {}} />);
    expect(await screen.findByText("Needs 16 GB VRAM")).toBeInTheDocument();
    expect(screen.getByText("Needs 6 GB VRAM")).toBeInTheDocument();
    expect(screen.getAllByText("typically uses ~11 GB").length).toBe(2);
    // No floor known: the chip says what it typically uses, nothing more.
    expect(screen.getByText("Uses ~12 GB VRAM")).toBeInTheDocument();
    expect(screen.queryByText("11GB VRAM")).not.toBeInTheDocument();
  });

  it("marks a model the card is under and warns before downloading it", async () => {
    render(<VideoModelsModal open onClose={() => {}} showMessage={() => {}} />);
    expect(await screen.findByText("Needs more than this card's 12 GB")).toBeInTheDocument();

    const installs = screen.getAllByRole("button", { name: /Install/ });
    fireEvent.click(installs[0]);
    expect(await screen.findByText("Wan 14B T2V needs a bigger graphics card")).toBeInTheDocument();
    expect(screen.getByText(/at least 16 GB of VRAM and this card has 12 GB/)).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Install anyway" }));
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/api/batch-video/models/download", { model_id: "wan22-14b" }),
    );
  });

  it("downloads a model that fits without asking", async () => {
    render(<VideoModelsModal open onClose={() => {}} showMessage={() => {}} />);
    await screen.findByText("Needs 6 GB VRAM");
    fireEvent.click(screen.getAllByRole("button", { name: /Install/ })[1]);
    await waitFor(() =>
      expect(axios.post).toHaveBeenCalledWith("/api/batch-video/models/download", { model_id: "wan22-5b" }),
    );
    expect(screen.queryByText(/needs a bigger graphics card/)).not.toBeInTheDocument();
  });
});
