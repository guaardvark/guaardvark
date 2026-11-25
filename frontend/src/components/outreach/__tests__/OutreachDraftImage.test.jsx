import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

vi.mock("../../images/ImageLightbox", () => ({
  default: ({ imageUrl }) => <div data-testid="lightbox">{imageUrl}</div>,
}));

import OutreachDraftImage from "../OutreachDraftImage";

const IMAGE = "/api/social-outreach/infographics/7/image";

describe("OutreachDraftImage", () => {
  it("renders nothing for a draft without an image", () => {
    const { container } = render(<OutreachDraftImage draft={{ id: 1, status: "drafted", image_url: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the image with its alt text, says it will be attached, and opens larger", () => {
    render(<OutreachDraftImage draft={{ id: 1, status: "drafted", image_url: IMAGE, image_alt: "Five local tools" }} />);
    const img = screen.getByRole("img", { name: "Five local tools" });
    expect(img.getAttribute("src")).toContain("/social-outreach/infographics/7/image");
    expect(screen.getByText("This infographic will be attached to the post.")).toBeInTheDocument();
    expect(screen.queryByTestId("lightbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open the infographic larger" }));
    expect(screen.getByTestId("lightbox")).toHaveTextContent("/social-outreach/infographics/7/image");
  });

  it("does not promise an attachment once the draft has left the queue", () => {
    render(<OutreachDraftImage draft={{ id: 1, status: "posted", image_url: IMAGE }} />);
    expect(screen.queryByText("This infographic will be attached to the post.")).not.toBeInTheDocument();
    expect(screen.getByText("The infographic picked for this draft.")).toBeInTheDocument();
  });
});
