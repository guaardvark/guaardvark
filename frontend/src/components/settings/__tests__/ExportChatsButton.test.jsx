import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

const exportChatSessions = vi.fn();
vi.mock("../../../api/chatService", () => ({
  exportChatSessions: (...args) => exportChatSessions(...args),
}));

import ExportChatsButton from "../ExportChatsButton";

describe("ExportChatsButton", () => {
  beforeEach(() => {
    exportChatSessions.mockReset();
  });

  it("exports and reports the folder and counts", async () => {
    exportChatSessions.mockResolvedValue({
      success: true,
      sessions: 3,
      messages: 12,
      relative_directory: "chat-exports/chats-20260903-140000",
    });
    const showMessage = vi.fn();

    render(<ExportChatsButton showMessage={showMessage} />);
    fireEvent.click(screen.getByRole("button", { name: /export chats/i }));

    await waitFor(() => expect(showMessage).toHaveBeenCalledTimes(1));
    expect(exportChatSessions).toHaveBeenCalledTimes(1);
    expect(showMessage).toHaveBeenCalledWith(
      "Exported 3 chats (12 messages) to outputs/chat-exports/chats-20260903-140000",
      "success",
    );
    expect(screen.getByRole("button", { name: /export chats/i })).not.toBeDisabled();
  });

  it("reports a failed export as an error", async () => {
    exportChatSessions.mockRejectedValue(new Error("disk full"));
    const showMessage = vi.fn();

    render(<ExportChatsButton showMessage={showMessage} />);
    fireEvent.click(screen.getByRole("button", { name: /export chats/i }));

    await waitFor(() => expect(showMessage).toHaveBeenCalledWith("Chat export failed: disk full", "error"));
  });

  it("stays disabled when the page says so", () => {
    render(<ExportChatsButton showMessage={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: /export chats/i })).toBeDisabled();
  });
});
