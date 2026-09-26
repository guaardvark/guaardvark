import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import RenderFailureNote from "./RenderFailureNote";
import { refusalText } from "../../utils/renderFailure";

const OOM = {
  kind: "oom", label: "Out of GPU memory", retryable: false, retry_after_s: [],
  message: "ComfyUI ran out of GPU memory in KSamplerAdvanced (node 10): Allocation failed",
  action: "Lower the size, length or steps, or turn off upscale and LoRAs.",
};

describe("RenderFailureNote", () => {
  it("shows the kind, the message and the next step", () => {
    render(<RenderFailureNote failure={OOM} error="raw" />);
    const note = screen.getByText(/Out of GPU memory: ComfyUI ran out of GPU memory/);
    expect(note).toHaveTextContent("Next: Lower the size, length or steps");
    expect(note).toHaveAttribute("data-failure-kind", "oom");
  });

  it("says when a retry can help", () => {
    render(<RenderFailureNote failure={{ ...OOM, kind: "vram_busy", label: "GPU busy", retryable: true }} />);
    expect(screen.getByText(/Retrying later can succeed/)).toBeInTheDocument();
  });

  it("falls back to the raw error for older results", () => {
    render(<RenderFailureNote error={{ message: "ComfyUI generation timed out or failed" }} />);
    expect(screen.getByText("ComfyUI generation timed out or failed")).toBeInTheDocument();
  });

  it("renders nothing without an error", () => {
    const { container } = render(<RenderFailureNote />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("refusalText", () => {
  it("reads the failure a refused request carries", () => {
    const body = { error: { code: "BAD_REQUEST", message: "Wan is not installed.", details: { failure: {
      kind: "model_not_installed", label: "Model not installed", message: "Wan is not installed.",
      action: "Open Manage Video Models and install it.", retryable: false } } } };
    expect(refusalText(body)).toBe("Model not installed: Wan is not installed. Next: Open Manage Video Models and install it.");
  });

  it("keeps the plain message when there is no record", () => {
    expect(refusalText({ error: { code: "BAD_REQUEST", message: "No prompts provided" } })).toBe("No prompts provided");
  });
});
