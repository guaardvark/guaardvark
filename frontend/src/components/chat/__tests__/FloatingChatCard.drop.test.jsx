import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";

const { uploadFile, sendMessage } = vi.hoisted(() => ({
  uploadFile: vi.fn(),
  sendMessage: vi.fn(),
}));

vi.mock("../../../api", () => ({ uploadFile }));
vi.mock("../../../api/unifiedChatService", () => ({
  default: class {
    joinSession() {}
    cleanup() {}
    sendMessage(...args) {
      return sendMessage(...args);
    }
  },
  steerAgent: vi.fn(),
}));
const socket = { on: () => {}, off: () => {} };
vi.mock("../../../contexts/UnifiedProgressContext", () => ({
  useUnifiedProgress: () => ({ socketRef: { current: socket } }),
  useUnifiedProgressActions: () => ({ socketRef: { current: socket } }),
}));
vi.mock("../../../contexts/VoiceContext", () => ({ useVoice: () => ({}) }));
vi.mock("../../../contexts/VoiceSessionContext", () => ({
  useVoiceSession: () => ({ toggleHandsFree: () => {}, replyFinished: () => {} }),
  useVoiceSessionState: (select) => select({ session: null, push: null, phase: null }),
}));
vi.mock("../../voice/GlobalMicButton", () => ({ default: () => null }));
vi.mock("../SlashCommandPopup", () => ({ default: () => null }));
vi.mock("../StreamingMessage", () => ({ default: () => null }));
vi.mock("../FloatingChatMessage", () => ({
  default: ({ message }) => <li>{message.content}</li>,
}));
vi.mock("../../../hooks/useSlashCommands", () => ({
  default: () => ({
    filteredCommands: [],
    selectedIndex: 0,
    selectCommand: () => {},
    popupVisible: false,
    handleInputChange: () => {},
    handleKeyDown: () => {},
    isCommand: false,
    executeCommand: async () => ({ handled: false }),
  }),
}));
vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (select) => select({ sessionModes: {}, setSessionMode: () => {} }),
}));
vi.mock("../../../utils/chatAttachment", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchAttachmentMaxBytes: async () => 8 * 1024 * 1024,
  downscaleChatAttachment: async (file) => ({
    file,
    preview: "data:image/png;base64,AAAA",
    byteLength: 4,
    mimeType: "image/png",
  }),
}));

import FloatingChatCard from "../FloatingChatCard";
import { useFloatingChatStore } from "../../../stores/useFloatingChatStore";

const pdf = () => new File(["%PDF-1.4"], "report.pdf", { type: "application/pdf" });
const png = (name) => new File(["png"], name, { type: "image/png" });
const drop = (el, files) => fireEvent.drop(el, { dataTransfer: { files, types: ["Files"] } });

beforeEach(() => {
  Element.prototype.scrollIntoView = () => {};
  uploadFile.mockReset();
  uploadFile.mockResolvedValue({ document_id: 42 });
  sendMessage.mockReset();
  sendMessage.mockResolvedValue(undefined);
  global.fetch = vi.fn(async (url) => {
    if (String(url).startsWith("/api/docs/42")) {
      return { ok: true, json: async () => ({ index_status: "INDEXED" }) };
    }
    return { ok: false, json: async () => ({}) };
  });
  useFloatingChatStore.setState({
    isOpen: true,
    collapsed: false,
    messages: [{ id: "m1", role: "assistant", content: "earlier reply" }],
    sessionId: "float-1",
    isSending: false,
    error: null,
    voiceTurns: [],
  });
});

const renderCard = async () => {
  render(<FloatingChatCard />);
  await act(async () => {});
};

describe("FloatingChatCard drop", () => {
  it("uploads a PDF dropped on the message list through the paperclip's path", async () => {
    await renderCard();
    const file = pdf();
    expect(drop(screen.getByText("earlier reply"), [file])).toBe(false);

    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(uploadFile.mock.calls[0][0]).toBe(file);
    expect(uploadFile.mock.calls[0].slice(1, 5)).toEqual([null, "chat-upload,file-upload,float-1", {}, null]);
    const [sessionId, content, options, image] = sendMessage.mock.calls[0];
    expect(sessionId).toBe("float-1");
    expect(content).toContain("**Document Uploaded Successfully**");
    expect(content).toContain("report.pdf");
    // An upload acknowledgement must not end with "Note: no search ran for this reply."
    expect(options.skip_nudges).toBe(true);
    expect(image).toBeNull();
  });

  it("sends a typed message without the notice flag", async () => {
    await renderCard();
    const box = screen.getByPlaceholderText(/Type your message/);
    await act(async () => {
      fireEvent.change(box, { target: { value: "find the Q3 report" } });
    });
    await act(async () => {
      fireEvent.keyDown(box, { key: "Enter" });
    });
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    const [, content, options] = sendMessage.mock.calls[0];
    expect(content).toBe("find the Q3 report");
    expect(options.skip_nudges).toBeUndefined();
  });

  it("takes documents from its paperclip too", async () => {
    await renderCard();
    fireEvent.change(screen.getByTestId("floating-chat-file"), { target: { files: [pdf()] } });
    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    expect(uploadFile.mock.calls[0][2]).toBe("chat-upload,file-upload,float-1");
  });

  it("attaches a dropped image without sending it", async () => {
    await renderCard();
    drop(screen.getByPlaceholderText(/Type your message/), [png("cat.png")]);
    expect(await screen.findByAltText("cat.png")).toBeInTheDocument();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("keeps the first of several images and says only it is sent", async () => {
    await renderCard();
    drop(screen.getByText("earlier reply"), [png("a.png"), png("b.png")]);
    expect(await screen.findByAltText("a.png")).toBeInTheDocument();
    expect(screen.queryByAltText("b.png")).not.toBeInTheDocument();
    expect(screen.getByText("Only the first image is sent to the model")).toBeInTheDocument();
  });
});
