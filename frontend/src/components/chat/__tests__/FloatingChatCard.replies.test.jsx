import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useFloatingChatStore } from "../../../stores/useFloatingChatStore";
import { useAppStore } from "../../../stores/useAppStore";

const { sendMessage, socketHandlers, stream } = vi.hoisted(() => ({
  sendMessage: vi.fn(async () => ({ success: true })),
  socketHandlers: {},
  // props: the StreamingMessage stub's last props; partial: what getPartialState returns.
  stream: { props: null, partial: {} },
}));

const fakeSocket = {
  connected: true,
  emit: () => {},
  on: (event, cb) => {
    (socketHandlers[event] ||= new Set()).add(cb);
  },
  off: (event, cb) => {
    socketHandlers[event]?.delete(cb);
  },
};
const emitFromServer = (event, data) => {
  for (const cb of [...(socketHandlers[event] || [])]) cb(data);
};

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
  default: React.forwardRef(function StreamingMessageStub(props, ref) {
    stream.props = props;
    React.useImperativeHandle(ref, () => ({ getPartialState: () => stream.partial }));
    return <div data-testid="streaming" />;
  }),
}));
vi.mock("../../../contexts/VoiceContext", () => ({ useVoice: () => ({}) }));
vi.mock("../../../contexts/VoiceSessionContext", () => ({
  useVoiceSession: () => ({ toggleHandsFree: () => {}, replyFinished: () => {} }),
  useVoiceSessionState: (select) => select({ session: null, push: null, phase: null }),
}));
vi.mock("../../voice/GlobalMicButton", () => ({ default: () => null }));
vi.mock("../SlashCommandPopup", () => ({ default: () => null }));

import FloatingChatCard from "../FloatingChatCard";

const jsonResponse = (body) => ({
  ok: true,
  status: 200,
  url: "",
  headers: { get: () => "application/json" },
  json: async () => body,
  text: async () => JSON.stringify(body),
});

const FOX = { url: "/api/outputs/fox.png", alt: "A fox", caption: "" };

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  sendMessage.mockClear();
  for (const key of Object.keys(socketHandlers)) delete socketHandlers[key];
  stream.props = null;
  stream.partial = {};
  fetch.mockReset();
  fetch.mockImplementation(async (url) =>
    String(url).includes("/settings/chat_image_model")
      ? jsonResponse({ success: true, data: { model: "z-image-turbo" } })
      : jsonResponse({})
  );
  useAppStore.setState({ chatImageModel: "auto" });
  useFloatingChatStore.setState({
    isOpen: true,
    collapsed: false,
    messages: [],
    voiceTurns: [],
    isSending: false,
    error: null,
    sessionId: "floating_test",
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

const typeAndSend = async (text) => {
  const box = screen.getByPlaceholderText(/Type your message/);
  await act(async () => {
    fireEvent.change(box, { target: { value: text } });
  });
  await act(async () => {
    fireEvent.keyDown(box, { key: "Enter" });
  });
};

describe("FloatingChatCard replies", () => {
  it("keeps the generated picture on the finished reply", async () => {
    renderCard();
    await typeAndSend("draw a fox");
    expect(stream.props).not.toBe(null);

    await act(async () => {
      stream.props.onComplete({ content: "Here's the generated image.", generatedImages: [FOX] });
    });

    expect(screen.getByText("Here's the generated image.")).toBeInTheDocument();
    expect(screen.getByAltText("A fox")).toHaveAttribute("src", FOX.url);
    const saved = useFloatingChatStore.getState().messages.at(-1);
    expect(saved.generatedImages).toEqual([FOX]);
  });

  it("shows the pictures of a thread restored after a reload", () => {
    useFloatingChatStore.setState({
      messages: [
        { id: "u1", role: "user", content: "draw a fox" },
        { id: "a1", role: "assistant", content: "Here's the generated image.", generatedImages: [FOX] },
      ],
    });
    renderCard();
    expect(screen.getByAltText("A fox")).toHaveAttribute("src", FOX.url);
  });

  it("keeps pictures that arrived before Stop", async () => {
    renderCard();
    await typeAndSend("draw a fox");
    stream.partial = { content: "", images: [FOX] };

    await act(async () => {
      fireEvent.click(screen.getByTestId("StopIcon").closest("button"));
    });

    expect(screen.getByAltText("A fox")).toHaveAttribute("src", FOX.url);
    expect(screen.queryByText("(stopped before any output)")).toBeNull();
    expect(useFloatingChatStore.getState().messages.at(-1).status).toBe("aborted");
  });

  it("sends /imagine with its direct-tool options", async () => {
    renderCard();
    await typeAndSend("/imagine a red fox");

    await waitFor(() => expect(sendMessage).toHaveBeenCalledTimes(1));
    const [sessionId, text, options] = sendMessage.mock.calls[0];
    expect(sessionId).toBe("floating_test");
    expect(text).toBe("/imagine a red fox");
    expect(options).toMatchObject({
      use_rag: true,
      direct_tool: "generate_image",
      direct_tool_params: { prompt: "a red fox", model: "z-image-turbo" },
      slash_command: "imagine",
      slash_args: "a red fox",
      image_model: "z-image-turbo",
    });
  });

  it("sends an ordinary message without direct-tool options", async () => {
    renderCard();
    await typeAndSend("hello");
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(sendMessage.mock.calls[0][2]).not.toHaveProperty("direct_tool");
  });

  it("ends the turn on chat:error, keeping what had streamed", async () => {
    renderCard();
    await typeAndSend("draw a fox");
    expect(useFloatingChatStore.getState().isSending).toBe(true);
    expect(screen.getByTestId("StopIcon")).toBeInTheDocument();
    stream.partial = { content: "Working on it", images: [FOX] };

    await act(async () => {
      emitFromServer("chat:error", { error: "LLM error: boom", session_id: "floating_test" });
    });

    expect(useFloatingChatStore.getState().isSending).toBe(false);
    expect(screen.queryByTestId("StopIcon")).toBeNull();
    expect(screen.getByTestId("SendIcon")).toBeInTheDocument();
    expect(screen.queryByTestId("streaming")).toBeNull();
    expect(screen.getByText("Working on it")).toBeInTheDocument();
    expect(screen.getByAltText("A fox")).toBeInTheDocument();
    expect(screen.getByText("Error: LLM error: boom")).toBeInTheDocument();
    expect(screen.getByText("LLM error: boom")).toBeInTheDocument();
  });

  it("leaves the thread alone for a chat:error from another session", async () => {
    renderCard();
    await typeAndSend("hello");

    await act(async () => {
      emitFromServer("chat:error", { error: "elsewhere", session_id: "floating_other" });
    });

    expect(useFloatingChatStore.getState().isSending).toBe(true);
    expect(screen.queryByText("Error: elsewhere")).toBeNull();
  });

  it("says so in the thread when Stop does not reach the backend", async () => {
    renderCard();
    await typeAndSend("draw a fox");
    fetch.mockImplementation(async (url) =>
      String(url).includes("/abort") || String(url).includes("/agent-control/kill")
        ? {
            ok: false,
            status: 502,
            statusText: "Bad Gateway",
            url: String(url),
            headers: { get: () => "application/json" },
            json: async () => ({ error: "backend_offline" }),
            text: async () => JSON.stringify({ error: "backend_offline" }),
          }
        : jsonResponse({})
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("StopIcon").closest("button"));
    });

    const expected =
      "Error: Stop did not reach the chat turn and the agent task: the backend is not reachable. It may still be running.";
    await waitFor(() => expect(screen.getByText(expected)).toBeInTheDocument());
    expect(useFloatingChatStore.getState().messages.at(-1).content).toBe(expected);
  });
});
