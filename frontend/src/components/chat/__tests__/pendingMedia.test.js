import { describe, expect, it } from "vitest";
import { applyMediaReady, mediaReadyFromStatus, pendingMediaOf } from "../pendingMedia";

const waiting = (overrides = {}) => ({
  id: "a1",
  role: "assistant",
  message_id: 42,
  content: "Making the video now.",
  generatedImages: [],
  pendingMedia: [{ batch_id: "B1", type: "video", studio_url: "/video?batch=B1" }],
  ...overrides,
});

describe("applyMediaReady", () => {
  it("swaps the placeholder for the clip", () => {
    const clip = { url: "/api/batch-video/video/B1/x.mp4", type: "video" };
    const [m] = applyMediaReady([waiting()], { message_id: 42, batch_id: "B1", media: [clip] });
    expect(m.generatedImages).toEqual([clip]);
    expect(m.pendingMedia).toEqual([]);
  });

  it("finds the reply by batch when the row id has not arrived", () => {
    const clip = { url: "/v.mp4", type: "video" };
    const [m] = applyMediaReady([waiting({ message_id: null })], { message_id: 7, batch_id: "B1", media: [clip] });
    expect(m.generatedImages).toEqual([clip]);
  });

  it("keeps a failed render on the reply with its reason", () => {
    const [m] = applyMediaReady([waiting()], { message_id: 42, batch_id: "B1", media: [], error: "Out of memory" });
    expect(m.pendingMedia).toEqual([expect.objectContaining({ batch_id: "B1", status: "failed", error: "Out of memory" })]);
  });

  it("leaves other chats' batches alone", () => {
    const list = [waiting()];
    expect(applyMediaReady(list, { batch_id: "B2", media: [] })).toBe(list);
  });

  it("reads pending clips saved under extra_data", () => {
    expect(pendingMediaOf({ extra_data: { pendingMedia: [{ batch_id: "B1" }] } })).toEqual([{ batch_id: "B1" }]);
  });
});

describe("mediaReadyFromStatus", () => {
  it("is null while the batch renders", () => {
    expect(mediaReadyFromStatus({ batch_id: "B1", status: "running", results: [] })).toBeNull();
  });

  it("names the clip and its poster when it finished", () => {
    const ready = mediaReadyFromStatus({
      batch_id: "B1",
      status: "completed",
      results: [{ success: true, video_path: "i/videos/a.mp4", thumbnail_path: "i/thumbnails/a.jpg" }],
    });
    expect(ready.media).toEqual([{
      url: "/api/batch-video/video/B1/i/videos/a.mp4",
      type: "video",
      alt: "Generated video",
      batch_id: "B1",
      poster: "/api/batch-video/video/B1/i/thumbnails/a.jpg",
    }]);
  });

  it("carries the failure when no clip came out", () => {
    const ready = mediaReadyFromStatus({
      batch_id: "B1",
      status: "error",
      results: [{ success: false, error: "boom", failure: { message: "The GPU ran out of memory" } }],
    });
    expect(ready.error).toBe("The GPU ran out of memory");
  });
});
