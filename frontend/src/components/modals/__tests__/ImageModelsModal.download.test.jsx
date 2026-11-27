import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import axios from "axios";
import ImageModelsModal from "../ImageModelsModal";
import { POLL_MS, resetModelDownloads, useModelDownloadsStore } from "../../../stores/useModelDownloadsStore";

vi.mock("axios");

const STATUS_URL = "/api/batch-image/models/download-status";

const zImage = { id: "z-image-turbo", name: "Z-Image Turbo", path: "Tongyi-MAI/Z-Image-Turbo", size_gb: 30.6, is_downloaded: false };
const packs = [
  { id: "qwen-edit", name: "Qwen edit", installed: false, download_gb: 20, tools: ["edit_image"] },
  { id: "kontext", name: "Kontext", installed: false, download_gb: 12, tools: ["edit_image"] },
];

let status;
const running = { is_downloading: true, current_model: zImage.path, status: "downloading", progress: 30, speed_mbps: 31, downloaded_gb: 9.2, total_gb: 30.6 };

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  resetModelDownloads();
  sessionStorage.clear();
  status = { is_downloading: false, status: "idle", current_model: null };
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(async (url) => {
    if (url === STATUS_URL) return { data: { success: true, data: status } };
    return { data: { success: true, data: { models: [zImage], adapters: [], encoders: [], editing: packs } } };
  });
});

afterEach(() => {
  resetModelDownloads();
  vi.useRealTimers();
});

const renderModal = (props = {}) => render(<ImageModelsModal open onClose={vi.fn()} showMessage={vi.fn()} {...props} />);

describe("ImageModelsModal while a model downloads", () => {
  it("closes with Escape and with Close, and says the download carries on", async () => {
    status = running;
    const onClose = vi.fn();
    renderModal({ onClose });
    expect(await screen.findByText("30% — 31 MB/s")).toBeInTheDocument();
    expect(screen.getByText(/Closing this window doesn't stop the download/)).toBeInTheDocument();

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    const close = screen.getByRole("button", { name: "Close" });
    expect(close).toBeEnabled();
    fireEvent.click(close);
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("picks the progress back up when reopened", async () => {
    status = running;
    const view = renderModal();
    expect(await screen.findByText("30% — 31 MB/s")).toBeInTheDocument();
    view.rerender(<ImageModelsModal open={false} onClose={vi.fn()} showMessage={vi.fn()} />);

    status = { ...running, progress: 55, downloaded_gb: 16.8 };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    view.rerender(<ImageModelsModal open onClose={vi.fn()} showMessage={vi.fn()} />);
    expect(await screen.findByText("55% — 31 MB/s")).toBeInTheDocument();
  });

  it("keeps installing the editing packs after the dialog is gone", async () => {
    axios.post.mockResolvedValue({ data: { success: true, data: {} } });
    const view = renderModal();
    fireEvent.click(await screen.findByRole("button", { name: /Install all/i }));
    await act(async () => {});
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenLastCalledWith("/api/batch-image/models/download", { model_path: "qwen-edit" });

    view.unmount();
    status = { is_downloading: false, status: "completed", current_model: "qwen-edit", delegated_to: "video" };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post).toHaveBeenLastCalledWith("/api/batch-image/models/download", { model_path: "kontext" });
    expect(useModelDownloadsStore.getState().queues.image).toEqual([]);
  });
});
