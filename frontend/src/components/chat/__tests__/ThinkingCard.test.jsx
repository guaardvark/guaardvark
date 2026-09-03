import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import ThinkingCard from "../ThinkingCard";

describe("ThinkingCard", () => {
  it("renders collapsed by default with the reasoning hidden", () => {
    render(<ThinkingCard text="Let me work this out." />);
    expect(screen.getByText("thinking")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /expand thinking/i })).toBeInTheDocument();
    const header = screen.getByTestId("thinking-card").querySelector("[aria-expanded]");
    expect(header).toHaveAttribute("aria-expanded", "false");
  });

  it("renders expanded when defaultExpanded is set and toggles on click", () => {
    render(<ThinkingCard text="Step one, then step two." defaultExpanded />);
    const header = screen.getByTestId("thinking-card").querySelector("[aria-expanded]");
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("thinking-body")).toHaveTextContent("Step one, then step two.");

    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "false");
  });

  it("follows the parent when defaultExpanded flips to collapsed", () => {
    const { rerender } = render(<ThinkingCard text="thinking..." streaming defaultExpanded />);
    const header = screen.getByTestId("thinking-card").querySelector("[aria-expanded]");
    expect(header).toHaveAttribute("aria-expanded", "true");

    rerender(<ThinkingCard text="thinking..." streaming defaultExpanded={false} />);
    expect(header).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the streaming state with a spinner and no elapsed chip", () => {
    render(<ThinkingCard text="partial" streaming elapsedMs={1200} defaultExpanded />);
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
    expect(screen.getByTestId("thinking-spinner")).toBeInTheDocument();
    expect(screen.queryByText("1.2s")).not.toBeInTheDocument();
  });

  it("shows elapsed seconds once streaming is done", () => {
    render(<ThinkingCard text="done" streaming={false} elapsedMs={2340} />);
    expect(screen.getByText("2.3s")).toBeInTheDocument();
    expect(screen.queryByTestId("thinking-spinner")).not.toBeInTheDocument();
    expect(screen.getByText("Reasoning")).toBeInTheDocument();
  });

  it("shows a placeholder when there is no reasoning text after completion", () => {
    render(<ThinkingCard text="" streaming={false} defaultExpanded />);
    expect(screen.getByTestId("thinking-body")).toHaveTextContent("(no reasoning recorded)");
  });
});
