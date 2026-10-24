import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import MessageItem from "../MessageItem";
import { isSynthesizedMessage, synthesizedLabel } from "../synthesizedAnswer";

vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (sel) => {
    const state = { systemLogo: null, activeLessonId: null };
    return typeof sel === "function" ? sel(state) : state;
  },
}));

vi.mock("../../common/NarrateButton", () => ({
  default: () => null,
}));

const renderMsg = (message) =>
  render(
    <ThemeProvider theme={createTheme()}>
      <MessageItem message={message} sessionId="session_1" />
    </ThemeProvider>,
  );

const assistant = (overrides) => ({
  role: "assistant",
  content: "It runs on your own GPU.",
  isUnifiedChat: true,
  timestamp: "2026-09-19T12:00:00Z",
  ...overrides,
});

describe("isSynthesizedMessage", () => {
  it("reads the live flag, extra_data, and a synthesized step", () => {
    expect(isSynthesizedMessage({ synthesized: true })).toBe(true);
    expect(isSynthesizedMessage({ extra_data: { synthesized: true } })).toBe(true);
    expect(isSynthesizedMessage({
      toolCalls: [{ iteration: 3, tool_calls: [], synthesized: true, thoughts: "done" }],
    })).toBe(true);
    expect(isSynthesizedMessage({
      extra_data: { steps: [{ iteration: 3, tool_calls: [], synthesized: true }] },
    })).toBe(true);
    expect(isSynthesizedMessage({
      isUnifiedChat: true,
      toolCalls: [{ iteration: 1, tool_calls: [{ tool_name: "search" }] }],
    })).toBe(false);
  });
});

describe("MessageItem synthesized chip", () => {
  it("renders the chip when synthesized is set and not otherwise", () => {
    const { rerender } = renderMsg(assistant({ synthesized: true }));
    expect(screen.getByTestId("synthesized-chip")).toHaveTextContent(synthesizedLabel());

    rerender(
      <ThemeProvider theme={createTheme()}>
        <MessageItem message={assistant({ synthesized: false })} sessionId="session_1" />
      </ThemeProvider>,
    );
    expect(screen.queryByTestId("synthesized-chip")).not.toBeInTheDocument();
  });

  it("renders the chip from a persisted synthesized step without a top-level flag", () => {
    renderMsg(assistant({
      synthesized: undefined,
      toolCalls: [
        {
          iteration: 1,
          thoughts: "searching",
          tool_calls: [{ tool_name: "search_knowledge_base", success: true, output_preview: "ok" }],
        },
        {
          iteration: 3,
          thoughts: "Iteration limit reached — answered from the tool results gathered.",
          tool_calls: [],
          synthesized: true,
        },
      ],
    }));
    expect(screen.getByTestId("synthesized-chip")).toBeInTheDocument();
    expect(screen.getByTestId("synthesized-step")).toHaveTextContent(
      "Iteration limit reached — answered from the tool results gathered.",
    );
  });

  it("does not render a synthesized step row for ordinary tool steps", () => {
    renderMsg(assistant({
      toolCalls: [{
        iteration: 1,
        thoughts: "looking it up",
        tool_calls: [{ tool_name: "search_knowledge_base", success: true, output_preview: "ok" }],
      }],
    }));
    expect(screen.queryByTestId("synthesized-chip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("synthesized-step")).not.toBeInTheDocument();
    expect(screen.getByText(/looking it up/)).toBeInTheDocument();
  });
});
