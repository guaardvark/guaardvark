import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useFloatingChatStore } from "../../../stores/useFloatingChatStore";

const sendMessage = vi.fn(async () => ({ success: true }));
const speak = vi.fn();
let streamingProps = null;

const fakeSocket = { connected: true, on: vi.fn(), off: vi.fn(), emit: vi.fn() };

vi.mock("../../../contexts/UnifiedProgressContext", () => ({
  useUnifiedProgress: () => ({ socketRef: { current: fakeSocket } }),
  useUnifiedProgressActions: () => ({ socketRef: { current: fakeSocket } }),
}));
vi.mock("../../../api/unifiedChatService", () => ({
  default: class {
    joinSession() {}
    cleanup() {}
    abort() {}
    sendMessage(...args) {
      return sendMessage(...args);
    }
  },
  steerAgent: vi.fn(),
}));
vi.mock("../StreamingMessage", () => ({
  default: React.forwardRef(function StreamingMessageStub(props, _ref) {
    streamingProps = props;
    return <div data-testid="streaming" />;
  }),
}));
vi.mock("../../../contexts/VoiceContext", () => ({
  useVoice: () => ({ speak, ttsEnabled: true, availableVoices: [] }),
}));

import FloatingChatCard from "../FloatingChatCard";

const okJson = (body) => ({ ok: true, status: 200, json: async () => body });

beforeEach(() => {
  // jsdom has no layout, so no scrollIntoView.
  Element.prototype.scrollIntoView = vi.fn();
  sendMessage.mockClear();
  speak.mockClear();
  streamingProps = null;
  fetch.mockReset();
  fetch.mockImplementation(async () => okJson({}));
  useFloatingChatStore.setState({
    isOpen: true,
    collapsed: false,
    messages: [],
    voiceTurns: [],
    isSending: false,
    error: null,
    sessionId: "floating_old",
    position: { x: 10, y: 10 },
    size: { w: 380, h: 520 },
  });
});

const renderCard = () =>
  render(
    <MemoryRouter>
      <FloatingChatCard />
    </MemoryRouter>
  );

describe("FloatingChatCard voice turns", () => {
  it("queues a voice turn that arrives while a reply streams, then sends it as a voice message", async () => {
    useFloatingChatStore.setState({ isSending: true });
    renderCard();

    await act(async () => {
      useFloatingChatStore.getState().enqueueVoiceTurn({ id: "v1", text: "turn on the lights", at: 1 });
    });
    expect(sendMessage).not.toHaveBeenCalled();
    expect(screen.getByText("1 voice message waits for this reply")).toBeInTheDocument();

    // "+ New chat" while the turn waits: it must go to the new session.
    await act(async () => {
      useFloatingChatStore.getState().clearMessages();
    });
    const currentSession = useFloatingChatStore.getState().sessionId;
    expect(currentSession).not.toBe("floating_old");

    await act(async () => {
      useFloatingChatStore.getState().setIsSending(false);
    });

    expect(sendMessage).toHaveBeenCalledTimes(1);
    const [sessionId, text, options, image, isVoice] = sendMessage.mock.calls[0];
    expect(sessionId).toBe(currentSession);
    expect(text).toBe("turn on the lights");
    expect(options).toMatchObject({ use_rag: true });
    expect(image).toBe(null);
    expect(isVoice).toBe(true);
    expect(useFloatingChatStore.getState().voiceTurns).toEqual([]);
    expect(screen.getByTestId("voice-message-badge")).toBeInTheDocument();
  });

  it("speaks the reply to a voice turn, not to a typed one", async () => {
    renderCard();
    await act(async () => {
      useFloatingChatStore.getState().enqueueVoiceTurn({ id: "v1", text: "what time is it", at: 1 });
    });
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(streamingProps).not.toBe(null);

    await act(async () => {
      streamingProps.onComplete({ content: "It is ten past four." });
    });
    expect(speak).toHaveBeenCalledWith("It is ten past four.");

    const box = screen.getByPlaceholderText(/Type your message/);
    await act(async () => {
      fireEvent.change(box, { target: { value: "and tomorrow?" } });
    });
    await act(async () => {
      fireEvent.keyDown(box, { key: "Enter" });
    });
    expect(sendMessage).toHaveBeenCalledTimes(2);
    expect(sendMessage.mock.calls[1][4]).toBe(false);
    await act(async () => {
      streamingProps.onComplete({ content: "Rain." });
    });
    expect(speak).toHaveBeenCalledTimes(1);
  });

  it("sends two queued turns one after the other", async () => {
    useFloatingChatStore.setState({ isSending: true });
    renderCard();
    await act(async () => {
      const { enqueueVoiceTurn } = useFloatingChatStore.getState();
      enqueueVoiceTurn({ id: "a", text: "first", at: 1 });
      enqueueVoiceTurn({ id: "b", text: "second", at: 2 });
    });
    await act(async () => {
      useFloatingChatStore.getState().setIsSending(false);
    });
    expect(sendMessage.mock.calls.map((c) => c[1])).toEqual(["first"]);
    await act(async () => {
      streamingProps.onComplete({ content: "ok" });
    });
    expect(sendMessage.mock.calls.map((c) => c[1])).toEqual(["first", "second"]);
  });
});
