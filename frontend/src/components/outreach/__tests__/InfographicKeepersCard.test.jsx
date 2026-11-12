import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../../../api/outreachService", () => ({
  fetchOutreachInfographics: vi.fn(),
  setInfographicKeeper: vi.fn(),
  rereadInfographic: vi.fn(),
  outreachMediaUrl: (path) => path,
}));
vi.mock("../../images/ImageLightbox", () => ({
  default: ({ imageName }) => <div data-testid="lightbox">{imageName}</div>,
}));

import InfographicKeepersCard from "../InfographicKeepersCard";
import {
  fetchOutreachInfographics,
  rereadInfographic,
  setInfographicKeeper,
} from "../../../api/outreachService";

const item = (id, readback, extra = {}) => ({
  document_id: id,
  image_url: `/api/social-outreach/infographics/${id}/image`,
  headline: `Headline ${id}`,
  lines: ["One", "Two"],
  width: 1080,
  height: 1920,
  keeper: true,
  usable: readback.status === "passed",
  readback,
  ...extra,
});

const ITEMS = [
  item(1, { status: "passed", expected: ["One", "Two"], missing: [], extra: [] }),
  item(2, { status: "failed", expected: ["One", "Two"], missing: ["Two"], extra: ["Tow"], transcript: "One Tow" }),
  item(3, { status: "reading" }),
  item(4, { status: "unread" }),
];

describe("InfographicKeepersCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchOutreachInfographics.mockResolvedValue(ITEMS);
  });

  it("asks for keepers first and states each read-back as it is", async () => {
    render(<InfographicKeepersCard />);
    const passed = await screen.findByTestId("readback-1");
    expect(fetchOutreachInfographics).toHaveBeenCalledWith({ keepers: true });
    expect(passed).toHaveTextContent("all 2 lines asked for were found");

    const failed = within(screen.getByTestId("readback-2"));
    expect(failed.getByText(/did not match/)).toBeInTheDocument();
    expect(failed.getByText("Asked for but not found in the image:")).toBeInTheDocument();
    expect(failed.getByText("Two")).toBeInTheDocument();
    expect(failed.getByText("In the image but not asked for:")).toBeInTheDocument();
    expect(failed.getByText("Tow")).toBeInTheDocument();

    expect(screen.getByTestId("readback-3")).toHaveTextContent("Reading the text in the image now");
    expect(screen.getByTestId("readback-4")).toHaveTextContent("has not been read back yet");
  });

  it("says outreach can attach only the item the API calls usable", async () => {
    render(<InfographicKeepersCard />);
    await screen.findByTestId("readback-1");
    expect(screen.getAllByText("Outreach can attach this one.")).toHaveLength(1);
    expect(screen.getAllByText(/will not attach it until/)).toHaveLength(3);
    expect(screen.getByText("4 kept; outreach can attach 1 of them.")).toBeInTheDocument();
  });

  it("keeps the read button off while a read-back runs", async () => {
    render(<InfographicKeepersCard />);
    await screen.findByTestId("readback-1");
    const buttons = screen.getAllByRole("button", { name: /^Read (again|the text)$/ });
    expect(buttons).toHaveLength(4);
    expect(buttons[2]).toBeDisabled();
    expect(screen.getByRole("button", { name: "Read the text" })).toBeEnabled();
  });

  it("puts the answer of a keeper switch and a re-read into the tile", async () => {
    setInfographicKeeper.mockResolvedValue({ ...ITEMS[0], keeper: false, usable: false });
    rereadInfographic.mockResolvedValue({ ...ITEMS[3], readback: { status: "reading" } });
    render(<InfographicKeepersCard />);
    await screen.findByTestId("readback-1");

    fireEvent.click(screen.getAllByRole("checkbox")[0]);
    await waitFor(() => expect(setInfographicKeeper).toHaveBeenCalledWith(1, false));
    await waitFor(() => expect(screen.queryByText("Outreach can attach this one.")).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Read the text" }));
    await waitFor(() => expect(rereadInfographic).toHaveBeenCalledWith(4));
    await waitFor(() => expect(screen.getByTestId("readback-4")).toHaveAttribute("data-status", "reading"));
  });

  it("loads every infographic when All is picked and shows a page at a time", async () => {
    render(<InfographicKeepersCard />);
    await screen.findByTestId("readback-1");
    const many = Array.from({ length: 30 }, (_v, i) => item(100 + i, { status: "unread" }, { keeper: false }));
    fetchOutreachInfographics.mockResolvedValue(many);

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    await screen.findByTestId("readback-100");
    expect(fetchOutreachInfographics).toHaveBeenLastCalledWith({ keepers: false });
    expect(screen.getAllByRole("img")).toHaveLength(24);
    expect(screen.getByText("Showing 24 of 30")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getAllByRole("img")).toHaveLength(30);
  });

  it("shows the reason when the list cannot be read", async () => {
    fetchOutreachInfographics.mockRejectedValue(new Error("HTTP 500"));
    render(<InfographicKeepersCard />);
    expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  });
});
