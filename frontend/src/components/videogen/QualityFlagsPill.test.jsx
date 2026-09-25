import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import QualityFlagsPill from "./QualityFlagsPill";

describe("QualityFlagsPill", () => {
  it("renders nothing for a clean clip", () => {
    const { container } = render(<QualityFlagsPill quality={{ flagged: false, flags: [] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names a single flag", () => {
    render(<QualityFlagsPill quality={{ flagged: true, flags: [
      { code: "washed_out", message: "washed out (low contrast, lifted blacks): luma spread 13 of 255" },
    ] }} />);
    expect(screen.getByText("Washed out")).toBeInTheDocument();
  });

  it("counts several flags", () => {
    render(<QualityFlagsPill quality={{ flagged: true, flags: [
      { code: "black_tiles", message: "black tiles" }, { code: "frozen", message: "frozen" },
    ] }} />);
    expect(screen.getByText("2 quality flags")).toBeInTheDocument();
  });

  it("falls back to the reasons of an older batch", () => {
    render(<QualityFlagsPill quality={{ flagged: true, flag_reasons: ["low_vlm_score:3"] }} />);
    expect(screen.getByText("Review")).toBeInTheDocument();
  });
});
