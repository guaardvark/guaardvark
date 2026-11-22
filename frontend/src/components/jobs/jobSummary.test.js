import { describe, expect, it } from "vitest";
import { jobKind, jobSummary, outputThumb } from "./jobSummary";

const t = (key, values) => (values ? `${key}:${Object.values(values).join(",")}` : key);

const clip = {
  id: 1,
  type: "tool_call",
  name: "Generate video",
  workflow_config_parsed: {
    request: {
      tool_name: "generate_video",
      parameters: { prompt: "The dancers turn", first_image: "/api/x.png", model: "wan22-14b-i2v", aspect_ratio: "16:9", duration_s: 5 },
    },
  },
};

describe("jobSummary", () => {
  it("titles a job nothing named by its prompt and shows its first frame", () => {
    const summary = jobSummary(clip, t);
    expect(summary.kind).toBe("video");
    expect(summary.title).toBe("The dancers turn");
    expect(summary.details).toEqual(["wan22-14b-i2v", "16:9", "jobs.summary.seconds:5"]);
    expect(summary.thumb).toBe("/api/x.png");
  });

  it("keeps a name somebody gave", () => {
    expect(jobSummary({ ...clip, name: "cut54 take 1" }, t).title).toBe("cut54 take 1");
  });

  it("shows what a started job made in place of its first frame", () => {
    const ran = { ...clip, handler_config: { handoff: { kind: "video_batch", batch_id: "VideoBatch_1" } } };
    expect(outputThumb(clip)).toBeNull();
    expect(jobSummary(ran, t).thumb).toMatch(/batch-video\/preview\/VideoBatch_1$/);
  });

  it("files jobs under the kind their filter chip names", () => {
    expect(jobKind({ type: "social_outreach_reddit" })).toBe("outreach");
    expect(jobKind({ type: "image_batch" })).toBe("images");
    expect(jobKind({ type: "content_generation" })).toBe("content_generation");
  });
});
