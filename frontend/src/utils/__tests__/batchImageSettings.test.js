import { describe, it, expect } from "vitest";
import { promptStyleFor, imageModelOption } from "../batchImageSettings";

// The backend's /batch-image/models answer wins; the family table only covers
// the first paint before it arrives.
describe("promptStyleFor", () => {
  const options = [
    imageModelOption({ id: "zimage-turbo", label: "Z-Image", prompt_style: "natural" }),
    imageModelOption({ id: "sd-xl", label: "SDXL", prompt_style: "tags" }),
    imageModelOption({ id: "no-answer-xl", label: "Unknown" }),
  ];

  it("uses the backend's style for a listed model", () => {
    expect(promptStyleFor("zimage-turbo", options)).toBe("natural");
    expect(promptStyleFor("sd-xl", options)).toBe("tags");
  });

  it("falls back to the family table when the list has no answer", () => {
    expect(promptStyleFor("no-answer-xl", options)).toBe("tags");
    expect(promptStyleFor("zimage-turbo", [])).toBe("natural");
    expect(promptStyleFor("qwen-image-21", [])).toBe("natural");
    expect(promptStyleFor("user-qwen-image-21-foo", [])).toBe("natural");
    expect(promptStyleFor("flux-dev", [])).toBe("tags");
  });

  it("takes the backend's answer for Auto, natural until it arrives", () => {
    expect(promptStyleFor("auto", options, "tags")).toBe("tags");
    expect(promptStyleFor("auto", options, null)).toBe("natural");
  });
});
