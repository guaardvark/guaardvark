import React from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

const deleteChatSession = vi.fn(async () => ({}));
vi.mock("../../../api/chatService", () => ({
  listChatSessions: vi.fn(async () => ({
    sessions: [{ session_id: "s1", preview: "Plan the launch", message_count: 4, created_at: new Date().toISOString() }],
  })),
  deleteChatSession: (...args) => deleteChatSession(...args),
}));

import ChatSessionDrawer from "../ChatSessionDrawer";
import PreviousChatsModal from "../PreviousChatsModal";

describe("chat session right-click", () => {
  it("opens or deletes a session from the drawer", async () => {
    const onSelectSession = vi.fn();
    const onClose = vi.fn();
    render(
      <ChatSessionDrawer open onClose={onClose} currentSessionId="other" onSelectSession={onSelectSession} onNewChat={() => {}} />,
    );
    fireEvent.contextMenu(await screen.findByText("Plan the launch"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Open" }));
    expect(onSelectSession).toHaveBeenCalledWith("s1");
    expect(onClose).toHaveBeenCalled();

    fireEvent.contextMenu(screen.getByText("Plan the launch"), { clientX: 10, clientY: 10 });
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Delete this chat");
    expect(deleteChatSession).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(deleteChatSession).toHaveBeenCalledWith("s1"));
    await waitFor(() => expect(screen.queryByText("Plan the launch")).not.toBeInTheDocument());
  });

  it("asks before the row's delete button deletes, and Cancel keeps the chat", async () => {
    deleteChatSession.mockClear();
    render(
      <ChatSessionDrawer open onClose={() => {}} currentSessionId="other" onSelectSession={() => {}} onNewChat={() => {}} />,
    );
    await screen.findByText("Plan the launch");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("4")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByText("Delete this chat")).not.toBeInTheDocument());
    expect(deleteChatSession).not.toHaveBeenCalled();
    expect(screen.getByText("Plan the launch")).toBeInTheDocument();
  });

  it("offers the same items in the Previous Chats dialog", async () => {
    render(<PreviousChatsModal open onClose={() => {}} currentSessionId="other" onSelectSession={() => {}} />);
    fireEvent.contextMenu(await screen.findByText("Plan the launch"), { clientX: 10, clientY: 10 });
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["Open", "Delete"]);
  });
});
