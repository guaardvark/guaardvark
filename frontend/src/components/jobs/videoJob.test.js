import { describe, expect, it } from "vitest";
import { asNewJob, isVideoJob, readVideoJob, secondsOf, writeVideoJob } from "./videoJob";

const toolJob = {
  id: 1,
  type: "tool_call",
  workflow_config_parsed: {
    variant: "run",
    request: {
      tool_name: "generate_video",
      parameters: { prompt: "a fox", first_image: "/api/x.png", model: "wan22-14b-i2v", aspect_ratio: "16:9", duration_s: 5 },
    },
  },
};

const batchJob = {
  id: 2,
  type: "video_batch",
  workflow_config_parsed: {
    variant: "text",
    request: {
      prompts: ["one", "two"], model: "wan22-14b", width: 832, height: 480, duration_frames: 81, fps: 16,
      num_inference_steps: 25, seed: 7, guidance_scale: 3.5, ui_config: { model: "wan22-14b" },
    },
  },
};

const untouched = (job) => {
  const f = readVideoJob(job);
  return { prompts: f.prompts, model: f.model, aspect: f.aspect, seconds: undefined, steps: f.steps, style: f.style,
    speed: f.speed, seed: f.seed, negative: f.negative };
};

describe("videoJob", () => {
  it("knows a video job from another tool call", () => {
    expect(isVideoJob(toolJob)).toBe(true);
    expect(isVideoJob(batchJob)).toBe(true);
    expect(isVideoJob({ type: "tool_call", workflow_config_parsed: { request: { tool_name: "generate_image" } } })).toBe(false);
    expect(isVideoJob({ type: "content_generation" })).toBe(false);
  });

  it("writes a tool call's edits and keeps the arguments it does not show", () => {
    const request = writeVideoJob(toolJob, { ...untouched(toolJob), prompts: ["a red fox"], aspect: "", seconds: 4, steps: 22 });
    expect(request).toEqual({
      tool_name: "generate_video",
      parameters: { prompt: "a red fox", first_image: "/api/x.png", model: "wan22-14b-i2v", duration_s: 4, num_inference_steps: 22 },
    });
    expect(asNewJob(toolJob, request, "again")).toEqual({ tool: "generate_video", arguments: request.parameters, name: "again" });
  });

  it("reads a batch's shape and length from its canvas and frames", () => {
    const fields = readVideoJob(batchJob);
    expect(fields.aspect).toBe("16:9");
    expect(fields.size).toBe("832×480");
    expect(secondsOf(fields, null)).toBe(5.1);
    expect(fields.locked).toContain("model");
  });

  it("lets the backend size a batch's new shape and marks typed steps", () => {
    const request = writeVideoJob(batchJob, { ...untouched(batchJob), aspect: "9:16", seconds: 3, steps: 30 }, { native_fps: 16 });
    expect(request.width).toBeUndefined();
    expect(request.height).toBeUndefined();
    expect(request.aspect_ratio).toBe("9:16");
    expect(request.duration_frames).toBe(48);
    expect(request.num_inference_steps).toBe(30);
    expect(request.steps_explicit).toBe(true);
    expect(request.ui_config).toBeUndefined();
    expect(request.guidance_scale).toBe(3.5);
    expect(request.prompts).toEqual(["one", "two"]);
    expect(request.prompt_style).toBeUndefined();
  });
});
