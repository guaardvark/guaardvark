import React, { useRef } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

const { uploadFile } = vi.hoisted(() => ({ uploadFile: vi.fn() }));

vi.mock("../../../api", () => ({ uploadFile }));
vi.mock("../../voice/GlobalMicButton", () => ({ default: () => null }));
vi.mock("../SlashCommandPopup", () => ({ default: () => null }));
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
vi.mock("../../../contexts/VoiceSessionContext", () => ({
  useVoiceSession: () => ({ toggleHandsFree: () => {} }),
  useVoiceSessionState: (select) => select({ session: null, push: null, phase: null }),
}));
vi.mock("../../../stores/useAppStore", () => ({
  useAppStore: (select) => select({
    sessionModes: {},
    setSessionMode: () => {},
    sessionPlanModes: {},
    setSessionPlanMode: () => {},
  }),
}));
vi.mock("../../../utils/chatAttachment", async (importOriginal) => ({
  ...(await importOriginal()),
  fetchAttachmentMaxBytes: async () => 8 * 1024 * 1024,
  downscaleChatAttachment: async (file) => {
    // A test may make a picture slow to resize.
    if (file.__delay) await new Promise((r) => setTimeout(r, file.__delay));
    return {
      file,
      preview: "data:image/png;base64,AAAA",
      // A test may pin the size a picture has after resize.
      byteLength: file.__bytes ?? 4,
      mimeType: "image/png",
    };
  },
}));

import ChatInput from "../ChatInput";
import useFileDropZone from "../../../hooks/useFileDropZone";

// The Chat page's wiring: the whole chat is the drop zone, the composer takes the files.
function ChatSurface({ onSendMessage, disabled = false, codeGenMode = false }) {
  const inputRef = useRef(null);
  const drop = useFileDropZone({ onFiles: (files) => inputRef.current?.addFiles(files) });
  return (
    <div data-testid="chat" {...drop.dropProps}>
      <div data-testid="message-list">earlier messages</div>
      <ChatInput
        ref={inputRef}
        onSendMessage={onSendMessage}
        sessionId="s1"
        disabled={disabled}
        codeGenMode={codeGenMode}
      />
    </div>
  );
}

const pdf = () => new File(["%PDF-1.4"], "report.pdf", { type: "application/pdf" });
const png = (name = "photo.png") => new File(["png"], name, { type: "image/png" });
const drop = (el, files) => fireEvent.drop(el, { dataTransfer: { files, types: ["Files"] } });

beforeEach(() => {
  uploadFile.mockReset();
  uploadFile.mockResolvedValue({ document_id: 42 });
  global.fetch = vi.fn(async (url) => {
    if (String(url).startsWith("/api/docs/42")) {
      return { ok: true, json: async () => ({ index_status: "INDEXED" }) };
    }
    return { ok: false, json: async () => ({}) };
  });
});

describe("Chat page drop", () => {
  it("uploads a PDF dropped on the message list the same way as the paperclip", async () => {
    const viaDrop = vi.fn();
    const { unmount } = render(<ChatSurface onSendMessage={viaDrop} />);
    const dropped = pdf();
    expect(drop(screen.getByTestId("message-list"), [dropped])).toBe(false);
    await waitFor(() => expect(viaDrop).toHaveBeenCalledTimes(1));
    const dropUpload = uploadFile.mock.calls[0];
    const dropNotice = viaDrop.mock.calls[0];
    unmount();

    uploadFile.mockClear();
    const viaClip = vi.fn();
    const { container } = render(<ChatSurface onSendMessage={viaClip} />);
    const picked = pdf();
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [picked] } });
    await waitFor(() => expect(viaClip).toHaveBeenCalledTimes(1));
    const clipUpload = uploadFile.mock.calls[0];

    expect(dropUpload[0]).toBe(dropped);
    expect(dropUpload.slice(1, 5)).toEqual([null, "chat-upload,file-upload,s1", {}, null]);
    expect(clipUpload.slice(1, 5)).toEqual(dropUpload.slice(1, 5));
    expect(dropNotice[0]).toContain("**Document Uploaded Successfully**");
    expect(dropNotice[0]).toContain("report.pdf");
    expect(dropNotice[0]).toContain("**Status:** Uploaded and indexed");
    // The page forwards this flag as the engine's skip_nudges: an upload
    // acknowledgement must not end with "Note: no search ran for this reply."
    expect(dropNotice[2]).toEqual({ documentNotice: true });
    expect(viaClip.mock.calls[0]).toEqual(dropNotice);
  });

  it("sends the codegen request for a dropped code file as a normal turn, not a notice", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} codeGenMode />);
    const script = new File(["print(1)"], "script.py", { type: "text/x-python" });
    drop(screen.getByTestId("message-list"), [script]);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [text, , options] = send.mock.calls[0];
    expect(text.startsWith("/codegen")).toBe(true);
    expect(options).toBeUndefined();
  });

  it("marks a refused file's notice the same way", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    const exe = new File(["MZ"], "tool.exe", { type: "application/octet-stream" });
    drop(screen.getByTestId("message-list"), [exe]);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(uploadFile).not.toHaveBeenCalled();
    expect(send.mock.calls[0][0]).toContain("tool.exe");
    expect(send.mock.calls[0][2]).toEqual({ documentNotice: true });
  });

  it("uploads a text file dropped on the composer", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    const notes = new File(["hello"], "notes.txt", { type: "text/plain" });
    expect(drop(screen.getByPlaceholderText(/Type your message/), [notes])).toBe(false);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(uploadFile.mock.calls[0][0]).toBe(notes);
    expect(send.mock.calls[0][0]).toContain("notes.txt");
  });

  it("holds the notice until the reply in progress has finished", async () => {
    const send = vi.fn();
    const { rerender } = render(<ChatSurface onSendMessage={send} disabled />);
    drop(screen.getByTestId("message-list"), [pdf()]);
    await waitFor(() => expect(uploadFile).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(send).not.toHaveBeenCalled();
    rerender(<ChatSurface onSendMessage={send} />);
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  });

  it("attaches a dropped image like a paste and sends no upload", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    drop(screen.getByTestId("message-list"), [png()]);
    expect(await screen.findByAltText("photo.png")).toBeInTheDocument();
    expect(uploadFile).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    expect(screen.queryByText(/only the first image is sent/i)).not.toBeInTheDocument();
  });

  it("sends every attached image, the first as the primary one", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    drop(screen.getByTestId("message-list"), [png("a.png"), png("b.png")]);
    expect(await screen.findByAltText("b.png")).toBeInTheDocument();
    expect(screen.queryByText(/only the first image is sent/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Analyze image" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [text, , options] = send.mock.calls[0];
    expect(text).toBe("Describe these 2 images: a.png, b.png");
    expect(options.isImageAnalysis).toBe(true);
    expect(options.imageFileName).toBe("a.png");
    expect(options.imageFileNames).toEqual(["a.png", "b.png"]);
    expect(options.imagesBase64).toEqual(["AAAA", "AAAA"]);
    expect(options.imageBase64).toBe("AAAA");
    expect(options.imagePreviews).toHaveLength(2);
  });

  it("refuses several images whose total is over the limit, naming the total", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    // Each picture is 5 MB after resize: one fits the 8 MB limit, two do not.
    const big = (name) => Object.assign(png(name), { __bytes: 5 * 1024 * 1024 });
    drop(screen.getByTestId("message-list"), [big("a.png"), big("b.png")]);
    expect(await screen.findByAltText("b.png")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Analyze image" }));
    expect(await screen.findByText(/These 2 images total 10\.0 MB after resize/)).toBeInTheDocument();
    expect(send).not.toHaveBeenCalled();
  });

  it("keeps the pictures of one drop in attach order whatever order their resizes finish", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    // a.png is attached first but resizes last.
    const slow = Object.assign(png("a.png"), { __delay: 60 });
    drop(screen.getByTestId("message-list"), [slow, png("b.png")]);
    expect(await screen.findByAltText("b.png")).toBeInTheDocument();
    expect(await screen.findByAltText("a.png")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Analyze image" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    const [text, , options] = send.mock.calls[0];
    expect(text).toBe("Describe these 2 images: a.png, b.png");
    expect(options.imageFileName).toBe("a.png");
    expect(options.imageFileNames).toEqual(["a.png", "b.png"]);
  });

  it("holds Send while a dropped picture is still being resized, then sends it too", async () => {
    const send = vi.fn();
    render(<ChatSurface onSendMessage={send} />);
    const slow = Object.assign(png("slow.png"), { __delay: 80 });
    drop(screen.getByTestId("message-list"), [png("fast.png"), slow]);
    expect(await screen.findByAltText("fast.png")).toBeInTheDocument();
    expect(screen.getByLabelText("Preparing slow.png")).toBeInTheDocument();

    // The tooltip's label sits on the wrapper of a disabled button.
    const sendButton = within(screen.getByLabelText("Preparing image...")).getByRole("button");
    expect(sendButton).toBeDisabled();
    fireEvent.click(sendButton);
    // The composer's placeholder changes once pictures are attached, so the
    // field is found by its role, not its text.
    fireEvent.keyPress(screen.getByRole("textbox"), { key: "Enter", code: "Enter", charCode: 13 });
    expect(send).not.toHaveBeenCalled();

    expect(await screen.findByAltText("slow.png")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Analyze image" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0][2].imageFileNames).toEqual(["fast.png", "slow.png"]);
  });
});
