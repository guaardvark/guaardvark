import { describe, expect, it } from "vitest";
import { asNewImageJob, isImageJob, readImageJob, sizeChoices, writeImageJob } from "./imageJob";

const toolJob = {
  id: 1,
  type: "tool_call",
  workflow_config_parsed: {
    variant: "run",
    request: { tool_name: "generate_image", parameters: { prompt: "a boat", subject_ids: [3], wait_for_result: false } },
  },
};

const batchJob = {
  id: 2,
  type: "image_batch",
  workflow_config_parsed: {
    variant: "prompts",
    request: {
      prompts: ["one", "two"], model: "zimage-turbo", width: 2688, height: 1472, steps: 9, steps_explicit: false,
      guidance: 0, adapters: [{ id: "a" }], negative_prompt: "", ui_config: { quantity: 1 },
    },
  },
};

const untouched = (job) => {
  const f = readImageJob(job);
  return { prompts: f.prompts, model: f.model, width: f.width, height: f.height, steps: f.steps, style: f.style,
    seed: f.seed, negative: f.negative };
};

describe("imageJob", () => {
  it("knows an image job from another tool call", () => {
    expect(isImageJob(toolJob)).toBe(true);
    expect(isImageJob(batchJob)).toBe(true);
    expect(isImageJob({ type: "tool_call", workflow_config_parsed: { request: { tool_name: "generate_video" } } })).toBe(false);
  });

  it("reads a tool call's defaults and keeps the arguments it does not show", () => {
    const fields = readImageJob(toolJob);
    expect([fields.model, fields.width, fields.height, fields.style, fields.cast]).toEqual(["auto", 1024, 1024, "realistic", 1]);
    const request = writeImageJob(toolJob, { ...untouched(toolJob), prompts: ["a red boat"], width: 1280, height: 768, steps: 12 });
    expect(request.parameters).toEqual({
      prompt: "a red boat", subject_ids: [3], wait_for_result: false, width: 1280, height: 768, style: "realistic", steps: 12,
    });
    expect(asNewImageJob(toolJob, request, "again").tool).toBe("generate_image");
  });

  it("marks typed steps on a batch and drops the page snapshot", () => {
    const request = writeImageJob(batchJob, { ...untouched(batchJob), steps: 14, seed: 42, negative: " blurry " });
    expect(request.steps).toBe(14);
    expect(request.steps_explicit).toBe(true);
    expect(request.seed).toBe(42);
    expect(request.negative_prompt).toBe("blurry");
    expect(request.ui_config).toBeUndefined();
    expect(request.adapters).toEqual([{ id: "a" }]);
    expect(request.guidance).toBe(0);
  });

  it("offers the job's own size and nothing past the model's largest picture", () => {
    const fields = readImageJob(batchJob);
    expect(sizeChoices(fields, null)).toContainEqual([2688, 1472]);
    const small = sizeChoices({ ...fields, width: 768, height: 768 }, { max_pixels: 768 * 768 });
    expect(small).toEqual([[768, 768]]);
  });
});
