import { describe, it, expect } from "vitest";
import {
  activeVideoJobs,
  buildFooterEntries,
  chipText,
  footerView,
  statusLine,
  typeLabel,
} from "../progressFooterModel";

const imageWaiting = {
  job_id: "ImageBatch_1",
  processType: "image_generation",
  status: "processing",
  progress: 0,
  message: "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free",
  timestamp: 200,
  additional_data: {
    batch_id: "ImageBatch_1",
    gpu_wait_reason: "Queued behind Video Gen — needs ~11.7 GB, 2.6 GB free",
  },
};

const videoJob = (over = {}) => ({
  id: "video_gen:VideoBatch_1",
  kind: "video_gen",
  native_id: "VideoBatch_1",
  status: "running",
  progress: 10,
  _receivedAt: 100,
  metadata: {
    batch_id: "VideoBatch_1",
    display_name: "Sunset pan",
    stage: "generate",
    current_item: "item-a",
    completed_videos: 0,
    failed_videos: 0,
    total_videos: 1,
    ...(over.metadata || {}),
  },
  ...Object.fromEntries(Object.entries(over).filter(([k]) => k !== "metadata")),
});

const denoising = (over = {}) => ({
  job_id: "item-a",
  processType: "video_render",
  status: "processing",
  progress: 40,
  message: "denoising 12/30",
  timestamp: 300,
  additional_data: { batch_id: "VideoBatch_1", stage: "denoising" },
  ...over,
});

describe("labels", () => {
  it("names video renders and video batches Video Gen", () => {
    expect(typeLabel("video_render")).toBe("Video Gen");
    expect(typeLabel("video_gen")).toBe("Video Gen");
    expect(typeLabel("image_generation")).toBe("Image Gen");
    expect(typeLabel("something_new")).toBe("something_new");
  });
});

describe("buildFooterEntries", () => {
  it("merges the two feeds and puts the running video before the waiting image", () => {
    const entries = buildFooterEntries([imageWaiting, denoising()], [videoJob()]);
    expect(entries.map((e) => e.type)).toEqual(["video_gen", "image_generation"]);
    expect(entries[0]).toMatchObject({ waiting: false, text: "denoising 12/30", progress: 40 });
    expect(entries[1]).toMatchObject({ waiting: true, waitKind: "gpu" });
  });

  it("folds a clip's steps into its batch by current item when the event has no batch id", () => {
    const step = denoising({ additional_data: { batch_id: "" } });
    const entries = buildFooterEntries([step], [videoJob()]);
    expect(entries).toHaveLength(1);
    expect(entries[0].text).toBe("denoising 12/30");
  });

  it("spreads a clip's steps over the whole batch", () => {
    const job = videoJob({ metadata: { completed_videos: 1, total_videos: 4 } });
    const [entry] = buildFooterEntries([denoising({ progress: 50 })], [job]);
    expect(entry.progress).toBeCloseTo(37.5);
    expect(entry.itemCount).toEqual({ current: 1, total: 4 });
  });

  it("treats a video batch in gpu_wait as waiting even while its status is running", () => {
    const job = videoJob({
      metadata: { stage: "gpu_wait", gpu_wait_reason: "Queued behind Image Gen — needs ~9.0 GB, 3.0 GB free" },
    });
    const running = { job_id: "ImageBatch_2", processType: "image_generation", status: "processing",
      progress: 30, message: "Generated image 3/10", timestamp: 50, additional_data: { batch_id: "ImageBatch_2" } };
    const entries = buildFooterEntries([running], [job]);
    expect(entries.map((e) => e.type)).toEqual(["image_generation", "video_gen"]);
    expect(entries[1]).toMatchObject({ waiting: true, text: "Queued behind Image Gen — needs ~9.0 GB, 3.0 GB free" });
  });

  it("shows a video batch's own stages, and a render with no batch on its own", () => {
    const [kf] = buildFooterEntries([], [videoJob({ metadata: { stage: "keyframe" } })]);
    expect(kf).toMatchObject({ waiting: false, text: "Keyframe" });
    const [queued] = buildFooterEntries([], [videoJob({ status: "pending", metadata: { stage: "queued" } })]);
    expect(queued).toMatchObject({ waiting: true, waitKind: "queue", text: "Queued" });
    const [loose] = buildFooterEntries([denoising({ additional_data: {} , job_id: "solo" })], []);
    expect(loose).toMatchObject({ type: "video_render", text: "denoising 12/30" });
  });

  it("drops finished processes", () => {
    expect(buildFooterEntries([{ ...imageWaiting, status: "complete" }], [])).toEqual([]);
  });
});

describe("activeVideoJobs", () => {
  it("keeps only video batches still in flight", () => {
    const jobs = new Map([
      ["video_gen:A", videoJob({ id: "video_gen:A" })],
      ["video_gen:B", videoJob({ id: "video_gen:B", status: "completed" })],
      ["unified:X", { id: "unified:X", kind: "unified", status: "running" }],
    ]);
    expect(activeVideoJobs(jobs, null).map((j) => j.id)).toEqual(["video_gen:A"]);
  });

  it("lets the seed drop a batch whose finishing event never arrived", () => {
    const jobs = new Map([["video_gen:A", videoJob({ id: "video_gen:A", _receivedAt: 100 })]]);
    expect(activeVideoJobs(jobs, { fetchedAt: 500, jobs: [] })).toEqual([]);
  });

  it("adds batches that started before the page loaded and prefers newer events", () => {
    const seeded = videoJob({ id: "video_gen:S", metadata: { stage: "queued" } });
    const fresh = videoJob({ id: "video_gen:S", _receivedAt: 900, metadata: { stage: "generate" } });
    expect(activeVideoJobs(new Map(), { fetchedAt: 500, jobs: [seeded] })).toHaveLength(1);
    const [job] = activeVideoJobs(new Map([["video_gen:S", fresh]]), { fetchedAt: 500, jobs: [seeded] });
    expect(job.metadata.stage).toBe("generate");
  });
});

describe("footerView and chips", () => {
  it("follows the running job and lists the rest as chips", () => {
    const view = footerView(buildFooterEntries([imageWaiting, denoising()], [videoJob()]));
    expect(view.statusText).toBe("Video Gen: denoising 12/30");
    expect(view.progress).toBe(40);
    expect(view.chips.map((c) => c.text)).toEqual(["Image: waiting for GPU"]);
    expect(view.entries).toHaveLength(2);
  });

  it("keeps one indexing job as its own line", () => {
    const view = footerView(buildFooterEntries(
      [{ job_id: "idx", processType: "indexing", status: "processing", progress: 20,
        message: "Indexing 2 of 10 files", timestamp: 1, additional_data: {} }],
      [videoJob()],
    ));
    expect(view.statusText).toBe("Indexing 2 of 10 files");
    expect(view.entries).toHaveLength(2);
  });

  it("folds indexing jobs that have not started into one line", () => {
    const waiting = (id) => ({
      job_id: id, processType: "indexing", status: "start", progress: 0,
      message: `Starting indexing... (Indexing document ${id}: f.py)`, timestamp: 1,
      additional_data: { document_id: id },
    });
    const view = footerView(buildFooterEntries([waiting("a"), waiting("b"), waiting("c")], []));
    expect(view.entries).toHaveLength(1);
    expect(view.statusText).toBe("Indexing: 3 documents waiting to start");
    expect(view.itemCount).toBeNull();
    expect(view.progress).toBe(0);
  });

  it("leads folded indexing jobs with the one that is moving and counts the rest", () => {
    const parked = {
      job_id: "parked", processType: "indexing", status: "start", progress: 0,
      message: "Starting indexing... (Indexing document 1: a.py)", timestamp: 1,
      additional_data: {},
    };
    const moving = {
      job_id: "moving", processType: "indexing", status: "processing", progress: 55,
      message: "Starting indexing for document 2", timestamp: 5, additional_data: {},
    };
    const view = footerView(buildFooterEntries(
      [parked, moving, { ...parked, job_id: "parked-2" }, denoising()],
      [videoJob()],
    ));
    expect(view.entries).toHaveLength(2);
    expect(view.statusText).toBe("Starting indexing for document 2 (1 running, 2 waiting)");
    expect(view.progress).toBe(55);
    expect(view.itemCount).toBeNull();
  });

  it("writes running chips with their percentage", () => {
    const view = footerView(buildFooterEntries(
      [denoising(), { job_id: "idx", processType: "indexing", status: "processing", progress: 20,
        message: "Indexing 2 of 10 files", timestamp: 1, additional_data: {} }],
      [videoJob()],
    ));
    expect(view.statusText).toBe("Indexing 2 of 10 files");
    expect(view.chips[0].text).toBe("Video: denoising 12/30 · 40%");
  });

  it("shows an image batch held at the job gate as waiting for the GPU, not starting", () => {
    const started = {
      job_id: "ImageBatch_3", processType: "image_generation", status: "start", progress: 0,
      message: "Starting image_generation... (Batch generation of 4 images)", timestamp: 400,
      additional_data: { batch_id: "ImageBatch_3", total_images: 4 },
    };
    const gateWait = "Queued behind Video Gen — needs ~8.8 GB, 2.0 GB free";
    const queued = {
      ...started, status: "processing", message: gateWait,
      additional_data: { ...started.additional_data, gpu_wait_reason: gateWait },
    };
    const view = footerView(buildFooterEntries([queued, denoising()], [videoJob()]));
    expect(view.chips.map((c) => c.text)).toEqual(["Image: waiting for GPU"]);
    expect(view.chips[0].waiting).toBe(true);
    expect(view.entries[1]).toMatchObject({ waitKind: "gpu", text: gateWait });
  });

  it("names a system-load wait and leads with a waiting job when nothing runs", () => {
    const sys = { ...imageWaiting, message: "Waiting for the system: RAM low",
      additional_data: { gpu_wait_reason: "Waiting for the system: RAM low" } };
    const [entry] = buildFooterEntries([sys], []);
    expect(chipText(entry)).toBe("Image: waiting for the system");
    expect(statusLine(entry)).toBe("Image Gen: Waiting for the system: RAM low");
    expect(footerView([])).toBe(null);
  });
});
