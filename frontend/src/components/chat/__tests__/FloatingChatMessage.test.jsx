import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import FloatingChatMessage from "../FloatingChatMessage";

describe("FloatingChatMessage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  describe("User prompts", () => {
    it("renders user prompt text and copy button", () => {
      const msg = {
        id: "u1",
        role: "user",
        content: "How do I configure the router?",
        timestamp: "2026-09-10T12:00:00Z",
      };
      render(<FloatingChatMessage message={msg} />);

      expect(screen.getByText("How do I configure the router?")).toBeInTheDocument();
      const copyBtn = screen.getByRole("button", { name: "Copy message" });
      expect(copyBtn).toBeInTheDocument();
      expect(copyBtn).toHaveClass("msg-user-copy");
    });

    it("copies user prompt text to clipboard and shows feedback", async () => {
      const writeTextMock = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText: writeTextMock },
      });

      const msg = {
        id: "u1",
        role: "user",
        content: "What is the capital of France?",
        timestamp: "2026-09-10T12:00:00Z",
      };
      render(<FloatingChatMessage message={msg} />);

      const copyBtn = screen.getByRole("button", { name: "Copy message" });
      await act(async () => {
        fireEvent.click(copyBtn);
      });

      expect(writeTextMock).toHaveBeenCalledWith("What is the capital of France?");

      // Should show check icon (success)
      expect(copyBtn.querySelector("[data-testid='CheckIcon']")).not.toBeNull();

      // After 1500ms, should revert back
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      expect(copyBtn.querySelector("[data-testid='ContentCopyIcon']")).not.toBeNull();
    });

    it("does not render copy button when prompt content is empty", () => {
      const msg = {
        id: "u2",
        role: "user",
        content: "",
        timestamp: "2026-09-10T12:00:00Z",
      };
      render(<FloatingChatMessage message={msg} />);
      expect(screen.queryByRole("button", { name: "Copy message" })).toBeNull();
    });
  });

  describe("Responses (assistant and system)", () => {
    it("renders assistant response and copy button in action row", () => {
      const msg = {
        id: "a1",
        role: "assistant",
        content: "Paris is the capital of France.",
        timestamp: "2026-09-10T12:00:01Z",
      };
      render(<FloatingChatMessage message={msg} />);

      expect(screen.getByText("Paris is the capital of France.")).toBeInTheDocument();
      const copyBtn = screen.getByRole("button", { name: "Copy message" });
      expect(copyBtn).toBeInTheDocument();
      expect(copyBtn).toHaveClass("msg-response-copy");
    });

    it("copies assistant response text to clipboard and shows feedback", async () => {
      const writeTextMock = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText: writeTextMock },
      });

      const msg = {
        id: "a1",
        role: "assistant",
        content: "Here is your solution: run `npm start`.",
        timestamp: "2026-09-10T12:00:01Z",
      };
      render(<FloatingChatMessage message={msg} />);

      const copyBtn = screen.getByRole("button", { name: "Copy message" });
      await act(async () => {
        fireEvent.click(copyBtn);
      });

      expect(writeTextMock).toHaveBeenCalledWith("Here is your solution: run `npm start`.");
      expect(copyBtn.querySelector("[data-testid='CheckIcon']")).not.toBeNull();

      act(() => {
        vi.advanceTimersByTime(1500);
      });

      expect(copyBtn.querySelector("[data-testid='ContentCopyIcon']")).not.toBeNull();
    });

    it("renders system error messages and allows copying", async () => {
      const writeTextMock = vi.fn().mockResolvedValue(undefined);
      Object.assign(navigator, {
        clipboard: { writeText: writeTextMock },
      });

      const msg = {
        id: "s1",
        role: "system",
        content: "Error: Connection refused to server on port 8000",
        timestamp: "2026-09-10T12:00:02Z",
      };
      render(<FloatingChatMessage message={msg} />);

      expect(screen.getByText("Error: Connection refused to server on port 8000")).toBeInTheDocument();
      const copyBtn = screen.getByRole("button", { name: "Copy message" });
      expect(copyBtn).toBeInTheDocument();

      await act(async () => {
        fireEvent.click(copyBtn);
      });

      expect(writeTextMock).toHaveBeenCalledWith("Error: Connection refused to server on port 8000");
    });

    it("renders ThinkingCard when thinking text is provided on assistant message", () => {
      const msg = {
        id: "a2",
        role: "assistant",
        thinking: "Let me check the network configuration...",
        content: "Network looks good.",
        timestamp: "2026-09-10T12:00:03Z",
      };
      render(<FloatingChatMessage message={msg} />);

      expect(screen.getByTestId("thinking-card")).toBeInTheDocument();
      expect(screen.getByText("Network looks good.")).toBeInTheDocument();
    });

    it("displays output limit notice when truncated is true", () => {
      const msg = {
        id: "a3",
        role: "assistant",
        content: "Partial response...",
        truncated: true,
        timestamp: "2026-09-10T12:00:04Z",
      };
      render(<FloatingChatMessage message={msg} />);

      expect(screen.getByText("Response reached the output limit.")).toBeInTheDocument();
    });
  });

  describe("Fallback copying mechanism", () => {
    it("falls back to document.execCommand if clipboard API throws", async () => {
      const execCommandMock = vi.fn();
      document.execCommand = execCommandMock;

      // Make navigator.clipboard throw
      Object.assign(navigator, {
        clipboard: {
          writeText: vi.fn().mockRejectedValue(new Error("Permission denied")),
        },
      });

      const msg = {
        id: "u3",
        role: "user",
        content: "Fallback text message",
      };
      render(<FloatingChatMessage message={msg} />);

      const copyBtn = screen.getByRole("button", { name: "Copy message" });
      await act(async () => {
        fireEvent.click(copyBtn);
      });

      expect(execCommandMock).toHaveBeenCalledWith("copy");
      expect(copyBtn.querySelector("[data-testid='CheckIcon']")).not.toBeNull();
    });
  });

  describe("Prop compatibility", () => {
    it("supports `msg` prop as alternative to `message` prop", () => {
      const msg = {
        id: "a4",
        role: "assistant",
        content: "Compatible with msg prop",
      };
      render(<FloatingChatMessage msg={msg} />);
      expect(screen.getByText("Compatible with msg prop")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Copy message" })).toBeInTheDocument();
    });
  });
});
