import { describe, expect, it } from "vitest";
import { buildPlanRequest, getKeptRangeDecorations, getPlanInputs } from "./buildPlanRequest";

const timeline = {
  bin: [
    { clipId: "doc1", documentId: 1, filename: "a.mp4", kind: "video" },
    { clipId: "doc2", documentId: 2, filename: "b.wav", kind: "audio", isMasterSong: true },
    { clipId: "doc3", documentId: 3, filename: "c.png", kind: "image" },
  ],
};

describe("buildPlanRequest", () => {
  it("extracts video clips and the master song from the bin", () => {
    const inputs = getPlanInputs(timeline);

    expect(inputs.videoCount).toBe(1);
    expect(inputs.hasMasterSong).toBe(true);
    expect(inputs.canPlan).toBe(true);
    expect(inputs.masterSong.documentId).toBe(2);
  });

  it("builds the backend plan payload from explicit workflow state", () => {
    const request = buildPlanRequest({
      timeline,
      masterSong: timeline.bin[1],
      scanMode: "motion",
      styleRecipeName: "Music Video",
      clipOverrides: { doc1: { mood: "energetic" } },
      seed: 123,
    });

    expect(request).toEqual({
      bin_clips: [{ clip_id: "doc1", document_id: 1 }],
      song_document_id: 2,
      scan_mode: "motion",
      style_recipe_name: "Music Video",
      seed: 123,
      clip_overrides: { doc1: { mood: "energetic" } },
    });
  });

  it("derives kept-range decorations without mutating timeline state", () => {
    expect(getKeptRangeDecorations({
      kept_ranges_by_clip: {
        doc1: [[0, 1.5], [3, 4]],
      },
    })).toEqual({
      doc1: {
        keptRanges: [[0, 1.5], [3, 4]],
        durationSeconds: 4,
      },
    });
  });
});
