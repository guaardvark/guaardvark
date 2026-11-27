import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import axios from "axios";
import {
  POLL_MS,
  resetModelDownloads,
  useModelDownloadsStore,
  useModelsChanged,
} from "../useModelDownloadsStore";

vi.mock("axios");

const IMAGE_STATUS = "/api/batch-image/models/download-status";
const VIDEO_STATUS = "/api/batch-video/models/download-status";
const AUDIO_STATUS = "/api/audio-foundry/models/download-status";

// What each status route answers, in order; the last answer repeats.
let answers;
const answer = (url, ...bodies) => {
  answers[url] = bodies;
};
const envelope = (data) => ({ success: true, data });

const store = () => useModelDownloadsStore.getState();
const tick = async (n = 1) => {
  for (let i = 0; i < n; i += 1) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
  }
};

const running = (model, extra = {}) => ({
  is_downloading: true,
  current_model: model,
  status: "downloading",
  progress: 40,
  downloaded_gb: 4,
  total_gb: 10,
  ...extra,
});

let notify;

beforeEach(() => {
  vi.useFakeTimers();
  resetModelDownloads();
  sessionStorage.clear();
  answers = {};
  notify = vi.fn();
  store().setNotifier(notify);
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(async (url) => {
    const list = answers[url];
    if (!list) throw new Error(`no answer for ${url}`);
    const body = list.length > 1 ? list.shift() : list[0];
    return { data: body };
  });
});

afterEach(() => {
  resetModelDownloads();
  vi.useRealTimers();
});

describe("useModelDownloadsStore", () => {
  it("follows a started download after its dialog is gone and announces the end", async () => {
    answer(IMAGE_STATUS, envelope(running("z-image")), envelope({ is_downloading: false, status: "completed", current_model: "z-image" }));

    store().markStarted("image", { current_model: "z-image", status: "starting" }, "Z-Image Turbo");
    expect(store().statuses.image.is_downloading).toBe(true);

    await tick();
    expect(store().statuses.image.progress).toBe(40);
    expect(notify).not.toHaveBeenCalled();

    await tick();
    expect(store().statuses.image.is_downloading).toBe(false);
    expect(notify).toHaveBeenCalledWith("Z-Image Turbo is installed.", "success");
    expect(store().versions.image).toBe(1);
    expect(store().names.image).toBeNull();

    // Polling stops once nothing runs.
    const reads = axios.get.mock.calls.length;
    await tick(3);
    expect(axios.get.mock.calls.length).toBe(reads);
  });

  it("names the reason when a download fails", async () => {
    answer(VIDEO_STATUS, envelope({ is_downloading: false, status: "failed", error: "stalled", current_model: "wan" }));
    store().markStarted("video", { current_model: "wan" }, "Wan 2.2");
    await tick();
    expect(notify).toHaveBeenCalledWith("Wan 2.2 did not install: stalled", "error");
    expect(store().versions.video).toBe(1);
  });

  it("stays quiet about a run that ended before it was watched", async () => {
    answer(IMAGE_STATUS, envelope({ is_downloading: false, status: "completed", current_model: "old" }));
    await act(() => store().refresh("image"));
    expect(notify).not.toHaveBeenCalled();
    expect(store().versions.image).toBe(0);
  });

  it("picks up a download that was running before the page loaded", async () => {
    answer(IMAGE_STATUS, envelope({ is_downloading: false, status: "idle" }));
    answer(VIDEO_STATUS, envelope(running("ltx")), envelope({ is_downloading: false, status: "completed", current_model: "ltx" }));
    answer("/api/voice/models/download-status", envelope({ is_downloading: false, status: "idle" }));
    answer(AUDIO_STATUS, { success: true, is_downloading: false, status: "idle" });

    await act(() => store().refreshAll());
    expect(store().statuses.video.is_downloading).toBe(true);
    await tick();
    // No display name after a reload: the toast names the model id.
    expect(notify).toHaveBeenCalledWith("ltx is installed.", "success");
  });

  it("ignores a read that was sent before the download started", async () => {
    let release;
    axios.get.mockImplementationOnce(
      () => new Promise((resolve) => {
        release = () => resolve({ data: envelope({ is_downloading: false, status: "idle" }) });
      }),
    );
    const stale = store().refresh("image");
    store().markStarted("image", { current_model: "z-image" }, "Z-Image Turbo");
    release();
    await act(() => stale);
    expect(store().statuses.image.is_downloading).toBe(true);
  });

  it("works through an install-all list one item at a time, dialog or not", async () => {
    axios.post.mockResolvedValue({ data: { success: true } });
    answer(
      IMAGE_STATUS,
      envelope(running("pack-a")),
      envelope({ is_downloading: false, status: "completed", current_model: "pack-a" }),
      envelope(running("pack-b")),
      envelope({ is_downloading: false, status: "completed", current_model: "pack-b" }),
    );

    await act(async () => {
      store().installAll("image", [
        { body: { model_path: "pack-a" }, name: "Pack A", status: { current_model: "pack-a" } },
        { body: { model_path: "pack-b" }, name: "Pack B", status: { current_model: "pack-b" } },
      ]);
    });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenLastCalledWith("/api/batch-image/models/download", { model_path: "pack-a" });

    await tick();
    expect(axios.post).toHaveBeenCalledTimes(1);
    await tick();
    expect(notify).toHaveBeenCalledWith("Pack A is installed.", "success");
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(axios.post).toHaveBeenLastCalledWith("/api/batch-image/models/download", { model_path: "pack-b" });

    await tick(2);
    expect(notify).toHaveBeenCalledWith("Pack B is installed.", "success");
    expect(store().queues.image).toEqual([]);
  });

  it("skips an item that is already installed and goes on", async () => {
    axios.post
      .mockResolvedValueOnce({ data: { success: true, data: { already_installed: true } } })
      .mockResolvedValueOnce({ data: { success: true } });
    answer(AUDIO_STATUS, { success: true, ...running(null), current_id: "fx" });

    await act(async () => {
      store().installAll("audio", [
        { body: { id: "voice" }, name: "Voice", status: {} },
        { body: { id: "fx" }, name: "FX", status: { current_id: "fx" } },
      ]);
    });
    expect(axios.post).toHaveBeenCalledTimes(2);
    expect(store().versions.audio).toBe(1);
    expect(store().statuses.audio.current_id).toBe("fx");
  });

  it("drops the rest of the list when another download holds the downloader", async () => {
    axios.post.mockRejectedValueOnce({ response: { status: 409 } });
    await act(async () => {
      store().installAll("image", [
        { body: { model_path: "a" }, name: "A", status: {} },
        { body: { model_path: "b" }, name: "B", status: {} },
      ]);
    });
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(store().queues.image).toEqual([]);
    expect(notify).toHaveBeenCalledWith("A download is already in progress.", "warning");
  });

  it("keeps an install-all list across a reload of the tab", async () => {
    axios.post.mockResolvedValue({ data: { success: true } });
    answer(IMAGE_STATUS, envelope(running("pack-a")));
    await act(async () => {
      store().installAll("image", [
        { body: { model_path: "pack-a" }, name: "Pack A", status: {} },
        { body: { model_path: "pack-b" }, name: "Pack B", status: {} },
      ]);
    });
    const saved = JSON.parse(sessionStorage.getItem("guaardvark-model-downloads"));
    expect(saved.state.queues.image.map((i) => i.name)).toEqual(["Pack B"]);
    expect(saved.state.names.image).toBe("Pack A");
  });

  it("counts a run handed to the video downloader once", async () => {
    const mirrored = { ...running("qwen-edit-pack"), delegated_to: "video" };
    answer(IMAGE_STATUS, envelope(mirrored), envelope({ is_downloading: false, status: "completed", current_model: "qwen-edit-pack", delegated_to: "video" }));
    answer(
      VIDEO_STATUS,
      envelope(running("qwen-edit-vae")),
      envelope(running("qwen-edit-vae")),
      envelope({ is_downloading: false, status: "completed", current_model: "qwen-edit-vae" }),
    );

    store().markStarted("image", { current_model: "qwen-edit-pack" }, "Qwen edit");
    await act(() => store().refresh("video"));
    await tick();
    expect(store().videoMirrored).toBe(true);

    await tick();
    const toasts = notify.mock.calls.map(([msg]) => msg);
    expect(toasts).toEqual(["Qwen edit is installed."]);
    // Video lists change too: the video downloader fetched the files.
    expect(store().versions.video).toBeGreaterThanOrEqual(1);
    expect(store().videoMirrored).toBe(false);
  });
});

describe("useModelsChanged", () => {
  it("calls back when a download of its kind ends, not on mount", async () => {
    const onChange = vi.fn();
    renderHook(() => useModelsChanged("voice", onChange));
    expect(onChange).not.toHaveBeenCalled();

    answer("/api/voice/models/download-status", envelope({ is_downloading: false, status: "completed", current_model: "tiny.en" }));
    store().markStarted("voice", { current_model: "tiny.en" }, "Whisper tiny");
    await tick();
    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
