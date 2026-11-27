import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";

vi.mock("axios", () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }));
vi.mock("./AddImageModelDialog", () => ({ default: () => null }));

import axios from "axios";
import ImageModelsModal from "./ImageModelsModal";

// A machine whose Ubuntu sees 15 GB (the WSL default on a 32 GB PC): Z-Image's
// 21 GB need cannot be admitted, SDXL's 10 GB can.
const LIST = {
  success: true,
  data: {
    models: [
      { id: "zimage-turbo", name: "Z-Image Turbo", path: "Tongyi-MAI/Z-Image-Turbo", is_downloaded: false,
        size_gb: 30.6, ram_gb: 21, vram_mb: 11000, fits_ram: false, recommended: false },
      { id: "sd-xl", name: "SDXL Base", path: "stabilityai/stable-diffusion-xl-base-1.0", is_downloaded: false,
        size_gb: 6.9, ram_gb: 10, vram_mb: 8000, fits_ram: true, recommended: true },
    ],
    adapters: [],
    encoders: [],
    editing: [],
    machine: { total_ram_gb: 15, ram_floor_gb: 1.5, wsl: true },
  },
};

const IDLE = { success: true, data: { is_downloading: false, status: "idle" } };

beforeEach(() => {
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(async (url) => ({ data: String(url).endsWith("/models") ? LIST : IDLE }));
  axios.post.mockResolvedValue({ data: { success: true, data: { model_path: "Tongyi-MAI/Z-Image-Turbo" } } });
});

const openModal = async () => {
  render(<ImageModelsModal open onClose={() => {}} showMessage={() => {}} />);
  await act(async () => {});
};

const rowOf = (name) => screen.getByText(name).closest("li");

describe("Manage Image Models: RAM need", () => {
  it("shows each model's RAM and VRAM need beside its download size", async () => {
    await openModal();
    const zimage = within(rowOf("Z-Image Turbo"));
    expect(zimage.getByText("30.6 GB")).toBeInTheDocument();
    expect(zimage.getByText("21 GB RAM")).toBeInTheDocument();
    expect(zimage.getByText("10.7 GB VRAM")).toBeInTheDocument();
    expect(within(rowOf("SDXL Base")).getByText("10 GB RAM")).toBeInTheDocument();
  });

  it("warns before installing a model this system cannot hold, and installs on Install anyway", async () => {
    await openModal();
    fireEvent.click(within(rowOf("Z-Image Turbo")).getByRole("button", { name: "Install" }));
    expect(screen.getByText("Z-Image Turbo needs more RAM than this system has")).toBeInTheDocument();
    expect(screen.getByText(/memory=<size>GB under \[wsl2\]/)).toBeInTheDocument();
    expect(screen.getByText("21 GB of RAM plus a 1.5 GB floor")).toBeInTheDocument();
    expect(screen.getByText("Ubuntu sees")).toBeInTheDocument();
    expect(axios.post).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Install anyway" }));
    });
    expect(axios.post).toHaveBeenCalledWith("/api/batch-image/models/download",
      { model_path: "Tongyi-MAI/Z-Image-Turbo" });
  });

  it("installs a model that fits without a warning", async () => {
    await openModal();
    await act(async () => {
      fireEvent.click(within(rowOf("SDXL Base")).getByRole("button", { name: "Install" }));
    });
    expect(screen.queryByText(/needs more RAM than this system has/)).toBeNull();
    expect(axios.post).toHaveBeenCalledTimes(1);
  });
});
