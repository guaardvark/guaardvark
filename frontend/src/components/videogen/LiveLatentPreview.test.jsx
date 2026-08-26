import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import LiveLatentPreview from "./LiveLatentPreview";

describe("LiveLatentPreview", () => {
  it("renders nothing without a src", () => {
    const { container } = render(<LiveLatentPreview src={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the image and the not-final caption", () => {
    render(<LiveLatentPreview src="blob:preview" />);
    const img = screen.getByAltText("Live latent preview");
    expect(img).toHaveAttribute("src", "blob:preview");
    expect(screen.getByText("Live latent preview — not final quality")).toBeInTheDocument();
  });
});
