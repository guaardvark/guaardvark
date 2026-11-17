import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import AgentThinkingTrail from "../AgentThinkingTrail";

const makeSteps = (n) =>
  Array.from({ length: n }, (_, i) => ({
    iteration: i + 1,
    label: `label ${i + 1}`,
    reasoning: `reasoning ${i + 1}`,
  }));

// jsdom lays nothing out, so the scroll geometry is supplied by hand.
const setGeometry = (el, { scrollHeight, clientHeight }) => {
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: clientHeight });
};

describe("AgentThinkingTrail", () => {
  it("renders nothing without steps", () => {
    const { container } = render(<AgentThinkingTrail steps={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("starts collapsed and toggles from the title, by click or keyboard", () => {
    render(<AgentThinkingTrail steps={makeSteps(2)} />);
    const header = screen.getByRole("button");
    expect(header).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(header);
    expect(header).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(header, { key: "Enter" });
    expect(header).toHaveAttribute("aria-expanded", "false");
  });

  it("lists every step with its label and reasoning, and marks user notes", () => {
    const steps = [...makeSteps(2), { kind: "note", iteration: 3, reasoning: "use the other tab" }];
    render(<AgentThinkingTrail steps={steps} />);
    const body = screen.getByTestId("agent-thinking-trail-body");
    expect(body).toHaveTextContent("label 1");
    expect(body).toHaveTextContent("reasoning 2");
    expect(body).toHaveTextContent("use the other tab");
  });

  it("puts all steps in one scrolling area with a bounded height", () => {
    render(<AgentThinkingTrail steps={makeSteps(3)} />);
    const style = window.getComputedStyle(screen.getByTestId("agent-thinking-trail-body"));
    expect(style.overflowY).toBe("auto");
    expect(style.maxHeight).not.toBe("");
    expect(style.maxHeight).not.toBe("none");
  });

  it("follows the newest step while streaming", () => {
    const { rerender } = render(<AgentThinkingTrail steps={makeSteps(3)} streaming />);
    fireEvent.click(screen.getByRole("button"));
    const body = screen.getByTestId("agent-thinking-trail-body");
    setGeometry(body, { scrollHeight: 900, clientHeight: 300 });
    rerender(<AgentThinkingTrail steps={makeSteps(4)} streaming />);
    expect(body.scrollTop).toBe(900);
  });

  it("stops following once the reader scrolls up, and resumes at the bottom", () => {
    const { rerender } = render(<AgentThinkingTrail steps={makeSteps(3)} streaming />);
    fireEvent.click(screen.getByRole("button"));
    const body = screen.getByTestId("agent-thinking-trail-body");
    setGeometry(body, { scrollHeight: 900, clientHeight: 300 });

    body.scrollTop = 100;
    fireEvent.scroll(body);
    setGeometry(body, { scrollHeight: 1200, clientHeight: 300 });
    rerender(<AgentThinkingTrail steps={makeSteps(4)} streaming />);
    expect(body.scrollTop).toBe(100);

    body.scrollTop = 900;
    fireEvent.scroll(body);
    setGeometry(body, { scrollHeight: 1500, clientHeight: 300 });
    rerender(<AgentThinkingTrail steps={makeSteps(5)} streaming />);
    expect(body.scrollTop).toBe(1500);
  });

  it("leaves a finished trail where the reader put it", () => {
    const { rerender } = render(<AgentThinkingTrail steps={makeSteps(3)} />);
    fireEvent.click(screen.getByRole("button"));
    const body = screen.getByTestId("agent-thinking-trail-body");
    setGeometry(body, { scrollHeight: 900, clientHeight: 300 });
    rerender(<AgentThinkingTrail steps={makeSteps(4)} />);
    expect(body.scrollTop).toBe(0);
  });
});
