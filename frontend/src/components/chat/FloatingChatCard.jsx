import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import {
  Box,
  Typography,
  IconButton,
  Paper,
  TextField,
  Chip,
  List,
  Grow,
  LinearProgress,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import MinimizeIcon from "@mui/icons-material/Remove";
import AddIcon from "@mui/icons-material/Add";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import Tooltip from "@mui/material/Tooltip";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { PlanActionsContext } from "./planActions";
import UnifiedChatService, { steerAgent } from "../../api/unifiedChatService";
import { stopChatTurn } from "../../api/chatStopService";
import StreamingMessage from "./StreamingMessage";
import FloatingChatMessage from "./FloatingChatMessage";
import { useUnifiedProgressActions } from "../../contexts/UnifiedProgressContext";
import GlobalMicButton from "../voice/GlobalMicButton";
import { useAppStore } from "../../stores/useAppStore";
import { useVoice } from "../../contexts/VoiceContext";
import { useVoiceSession, useVoiceSessionState } from "../../contexts/VoiceSessionContext";
import extractSpeakableText from "../../utils/extractSpeakableText";
import useSlashCommands from "../../hooks/useSlashCommands";
import { directToolOptions } from "../../utils/slashCommandRegistry";
import SlashCommandPopup from "./SlashCommandPopup";
import ChatFeatureMenu from "./ChatFeatureMenu";
import FileDropOverlay from "./FileDropOverlay";
import useFileDropZone from "../../hooks/useFileDropZone";
import { debugLog } from "../../utils/debugLog";
import { contextChipLabel } from "../../utils/contextChipLabel";
import { StatusPill } from "../settings/ui";
import {
  attachmentExceedsLimit,
  chatErrorMessage,
  downscaleChatAttachment,
  fetchAttachmentMaxBytes,
  formatAttachmentSize,
  refuseAttachmentMessage,
} from "../../utils/chatAttachment";
import {
  CHAT_ATTACH_ACCEPT,
  chatDocumentRefusal,
  splitChatFiles,
  uploadChatDocument,
} from "../../utils/chatDocumentUpload";
import { translate, useUiTranslation, uiLocale } from "../../i18n";

const MIN_WIDTH = 280;
const MIN_HEIGHT = 300;
const DOUBLE_CLICK_MS = 400;
const VIEWPORT_MARGIN = 8; // keep this much gap between the card and the window edge
const COLLAPSED_HEIGHT = 48; // header-only height used for bounds while collapsed

/**
 * Fit the card's persisted geometry inside the current viewport.
 *
 * Position and size live in localStorage, so a spot chosen on a large window
 * is replayed verbatim on a smaller one and can leave most of the card
 * off-screen. Shrink the size to what the window can hold (a tiny window may
 * override the MIN_* floors), then pull the position back so every edge is
 * visible. Returns the same object references when nothing needs to change so
 * callers can skip redundant store writes.
 */
const fitToViewport = (position, size, collapsed) => {
  const maxW = Math.max(1, window.innerWidth - VIEWPORT_MARGIN * 2);
  const maxH = Math.max(1, window.innerHeight - VIEWPORT_MARGIN * 2);
  const w = Math.min(Math.max(MIN_WIDTH, size.w), maxW);
  const h = Math.min(Math.max(MIN_HEIGHT, size.h), maxH);
  const visibleH = collapsed ? Math.min(COLLAPSED_HEIGHT, maxH) : h;

  const x = Math.max(VIEWPORT_MARGIN, Math.min(position.x, window.innerWidth - w - VIEWPORT_MARGIN));
  const y = Math.max(VIEWPORT_MARGIN, Math.min(position.y, window.innerHeight - visibleH - VIEWPORT_MARGIN));

  const nextSize = w === size.w && h === size.h ? size : { w, h };
  const nextPosition = x === position.x && y === position.y ? position : { x, y };
  return { position: nextPosition, size: nextSize };
};

/**
 * The reply kept when a turn ends without chat:complete: what had streamed,
 * pictures included. This card renders only the body, reasoning and pictures,
 * so agent-trail steps go into the text. Returns null when nothing had arrived.
 * The framing is worded in the interface language when the turn ends.
 */
const partialReplyMessage = (partial, status) => {
  const stopped = status === "aborted";
  const steps = partial.agentThinkingSteps || [];
  const stepText = steps
    .map((s) =>
      s.reasoning
        ? translate("chat.floating.trailStepWithReasoning", { iteration: s.iteration, label: s.label, reasoning: s.reasoning })
        : translate("chat.floating.trailStep", { iteration: s.iteration, label: s.label }),
    )
    .join("\n");
  const body = (partial.content || "").trim();
  const images = partial.images || [];
  const hasAnything =
    body || stepText || images.length > 0 || (partial.toolCalls?.length || 0) > 0 || partial.thinking;
  if (!hasAnything) return null;
  const composed = [
    body,
    stepText
      ? `${stopped ? translate("chat.floating.trailStopped") : translate("chat.floating.trailFailed")}\n${stepText}`
      : "",
  ].filter(Boolean).join("\n\n");
  return {
    id: `asst_unified_${Date.now()}_partial`,
    role: "assistant",
    content:
      composed ||
      (images.length > 0
        ? ""
        : stopped
        ? translate("chat.floating.stoppedBeforeOutput")
        : translate("chat.floating.failedBeforeOutput")),
    toolCalls: partial.toolCalls || [],
    thinking: partial.thinking || "",
    generatedImages: images,
    truncated: false,
    timestamp: new Date().toISOString(),
    status,
  };
};

const FloatingChatCard = () => {
  const { t } = useUiTranslation();
  const theme = useTheme();

  // Store state
  const isOpen = useFloatingChatStore((s) => s.isOpen);
  const setIsOpen = useFloatingChatStore((s) => s.setIsOpen);
  const position = useFloatingChatStore((s) => s.position);
  const setPosition = useFloatingChatStore((s) => s.setPosition);
  const size = useFloatingChatStore((s) => s.size);
  const setSize = useFloatingChatStore((s) => s.setSize);
  const collapsed = useFloatingChatStore((s) => s.collapsed);
  const toggleCollapsed = useFloatingChatStore((s) => s.toggleCollapsed);
  const messages = useFloatingChatStore((s) => s.messages);
  const addMessage = useFloatingChatStore((s) => s.addMessage);
  const updateMessage = useFloatingChatStore((s) => s.updateMessage);
  const clearMessages = useFloatingChatStore((s) => s.clearMessages);
  const isSending = useFloatingChatStore((s) => s.isSending);
  const setIsSending = useFloatingChatStore((s) => s.setIsSending);
  const error = useFloatingChatStore((s) => s.error);
  const setError = useFloatingChatStore((s) => s.setError);
  const clearError = useFloatingChatStore((s) => s.clearError);
  const sessionId = useFloatingChatStore((s) => s.sessionId);
  const pageContext = useFloatingChatStore((s) => s.pageContext);
  const entityLabel = useFloatingChatStore((s) => s.entityLabel);

  // Voice: turns from the global mic arrive in voiceTurns (FloatingChatVoiceSink).
  const voiceTurns = useFloatingChatStore((s) => s.voiceTurns);
  const removeVoiceTurn = useFloatingChatStore((s) => s.removeVoiceTurn);
  // Turns other pages queue (System Map's "Ask about this").
  const pendingTurns = useFloatingChatStore((s) => s.pendingTurns);
  const removePendingTurn = useFloatingChatStore((s) => s.removePendingTurn);
  const voiceSession = useVoiceSession();
  const voiceActive = useVoiceSessionState((s) => Boolean(s.session));
  const { speak, ttsEnabled, speakError, clearSpeakError } = useVoice() || {};
  // True while the reply being streamed answers a voice turn: it is spoken.
  const voiceReplyPendingRef = useRef(false);

  // Unified Chat Service (Socket.IO streaming)
  const { socketRef } = useUnifiedProgressActions();
  const [unifiedChatService, setUnifiedChatService] = useState(null);

  // Safety: if socket becomes available while floating is open but we have no service yet
  // (e.g. mount timing), create it. The main effect should cover this, but this is a belt.
  useEffect(() => {
    if (isOpen && !unifiedChatService && socketRef?.current) {
      const svc = new UnifiedChatService(socketRef.current);
      svc.joinSession(sessionId);
      setUnifiedChatService(svc);
    }
  }, [isOpen, unifiedChatService, sessionId, socketRef?.current]);
  const [isStreamingMessage, setIsStreamingMessage] = useState(false);

  // Initialize UnifiedChatService when socket is connected.
  // Depend on socketRef?.current (the actual socket instance) so the effect re-runs
  // as soon as the shared socket from UnifiedProgressContext is ready.
  // Previously only [sessionId] meant: if socketRef.current was null on mount, we
  // bailed forever and floating chat never called joinSession → no streaming.
  useEffect(() => {
    const socket = socketRef?.current;
    if (!socket) {
      return;
    }

    // Eager creation: avoid races and ensure listeners registered before first send.
    const service = new UnifiedChatService(socket);
    service.joinSession(sessionId);
    setUnifiedChatService(service);

    debugLog('[FloatingChat] service created + joined for', sessionId);

    return () => {
      service.cleanup();
      setUnifiedChatService(null);
    };
  }, [sessionId, socketRef?.current]);

  // Hydrate session mode from backend on sessionId change so `/agent` state
  // survives reloads / re-mounts. Without this, the floating card's
  // `inAgentMode` is always false and `agent_screen_active` falls back to
  // viewer-open alone.
  const setSessionMode = useAppStore((s) => s.setSessionMode);
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/chat-sessions/${encodeURIComponent(sessionId)}/mode`
        );
        if (!res.ok) return;
        const data = await res.json();
        if (!cancelled && data?.success && data.mode) {
          setSessionMode(sessionId, data.mode);
        }
      } catch {
        // Leave the cached value (or "chat" default) on network failure.
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId, setSessionMode]);

  // Local state for drag/resize
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const [inputText, setInputText] = useState("");
  const [attachment, setAttachment] = useState(null);
  // Set when more images were offered than the one this chat can send.
  const [attachmentNote, setAttachmentNote] = useState(null);
  const [docUpload, setDocUpload] = useState(null); // { fileName, indexing, progress } while uploading
  // Document notices wait here until the chat is free to send them.
  const [pendingNotices, setPendingNotices] = useState([]);
  // True while this chat's request is a live screen-agent run; the input
  // then takes notes for the running task instead of new messages.
  const [agentWorking, setAgentWorking] = useState(false);
  const noteMode = isSending && agentWorking;
  const [attachmentMaxBytes, setAttachmentMaxBytes] = useState(null);

  const lastClickRef = useRef(0);
  const cardRef = useRef(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileRef = useRef(null);
  const streamingMessageRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetchAttachmentMaxBytes().then((max) => {
      if (!cancelled) setAttachmentMaxBytes(max);
    });
    return () => { cancelled = true; };
  }, []);

  // Terminal-style sent-message history (Up/Down to recall).
  const messageHistoryRef = useRef([]);
  const historyIndexRef = useRef(-1);
  const historyDraftRef = useRef("");
  const HISTORY_MAX = 50;

  const pushHistory = useCallback((text) => {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    const hist = messageHistoryRef.current;
    if (hist[hist.length - 1] !== trimmed) {
      hist.push(trimmed);
      if (hist.length > HISTORY_MAX) hist.shift();
    }
    historyIndexRef.current = -1;
    historyDraftRef.current = "";
  }, []);

  const recallHistory = useCallback((direction) => {
    const hist = messageHistoryRef.current;
    if (hist.length === 0) return false;
    const el = inputRef.current;
    if (!el) return false;
    const value = el.value ?? "";
    const atStart = el.selectionStart === 0 && el.selectionEnd === 0;
    const atEnd =
      el.selectionStart === value.length && el.selectionEnd === value.length;

    const applyValue = (next) => {
      setInputText(next);
      requestAnimationFrame(() => {
        if (inputRef.current) {
          const len = next.length;
          inputRef.current.setSelectionRange(len, len);
        }
      });
    };

    if (direction === "up") {
      if (!atStart) return false;
      if (historyIndexRef.current === -1) {
        historyDraftRef.current = value;
        historyIndexRef.current = hist.length - 1;
      } else if (historyIndexRef.current > 0) {
        historyIndexRef.current -= 1;
      } else {
        return true;
      }
      applyValue(hist[historyIndexRef.current]);
      return true;
    }
    // down
    if (historyIndexRef.current === -1) return false;
    if (!atEnd) return false;
    if (historyIndexRef.current < hist.length - 1) {
      historyIndexRef.current += 1;
      applyValue(hist[historyIndexRef.current]);
    } else {
      historyIndexRef.current = -1;
      applyValue(historyDraftRef.current);
    }
    return true;
  }, []);

  // Initialize default position (bottom-right) on first render
  useEffect(() => {
    if (position.x === -1 && position.y === -1) {
      setPosition({
        x: window.innerWidth - size.w - 24,
        y: window.innerHeight - size.h - 48,
      });
    }
  }, [position, size.w, size.h, setPosition]);

  // Keep the card inside the viewport whenever it opens or the window resizes.
  // Runs against the store directly so the resize listener never goes stale.
  useEffect(() => {
    if (!isOpen) return;
    const fit = () => {
      const s = useFloatingChatStore.getState();
      if (s.position.x === -1 && s.position.y === -1) return; // default not yet applied
      const next = fitToViewport(s.position, s.size, s.collapsed);
      if (next.size !== s.size) s.setSize(next.size);
      if (next.position !== s.position) s.setPosition(next.position);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [isOpen, collapsed, position, size]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleAttachImage = useCallback(async (file) => {
    if (!file || !file.type?.startsWith("image/")) return;
    try {
      const resized = await downscaleChatAttachment(file);
      setAttachment({
        file: resized.file,
        preview: resized.preview,
        byteLength: resized.byteLength,
        mimeType: resized.mimeType,
      });
      clearError();
    } catch (err) {
      setError(err?.message || { kind: "prepareFailed" });
    }
  }, [clearError, setError]);

  // A voice turn's reply is finished (spoken or not): the mic may listen again.
  const finishVoiceReply = useCallback(() => {
    if (!voiceReplyPendingRef.current) return;
    voiceReplyPendingRef.current = false;
    voiceSession.replyFinished();
  }, [voiceSession]);

  // Send message handler — uses UnifiedChatService (Socket.IO streaming).
  // Arguments follow ChatPage's (text, file, options) so slash commands can call either.
  // A document notice (sendOptions.documentNotice) leaves the attached image and
  // the typed-message history alone.
  const handleSendMessage = useCallback(async (overrideText, _file, sendOptions) => {
    const isVoice = Boolean(sendOptions?.isVoiceMessage);
    const isNotice = Boolean(sendOptions?.documentNotice);
    const text = overrideText || inputText;
    const pending = isVoice || isNotice ? null : attachment;
    if ((!text.trim() && !pending) || isSending) return;

    if (pending && attachmentExceedsLimit(pending.byteLength, attachmentMaxBytes)) {
      setError(refuseAttachmentMessage(pending.byteLength, attachmentMaxBytes));
      return;
    }

    if (!isVoice && !isNotice) pushHistory(text);

    const content = text.trim() || (pending ? `Describe this image: ${pending.file.name}` : "");
    const userMessage = {
      id: `user_${Date.now()}`,
      role: "user",
      content,
      imageUrl: pending?.preview,
      imageFileName: pending?.file?.name,
      voice: isVoice || undefined,
      timestamp: new Date().toISOString(),
    };
    addMessage(userMessage);
    if (!overrideText) setInputText("");
    if (pending) {
      setAttachment(null);
      setAttachmentNote(null);
    }
    voiceReplyPendingRef.current = isVoice;

    setIsSending(true);
    clearError();

    const imageBase64 = pending?.preview?.includes(",")
      ? pending.preview.split(",")[1]
      : null;

    if (unifiedChatService) {
      // Primary path: Socket.IO streaming via UnifiedChatService
      setIsStreamingMessage(true);

      try {
        // Page awareness travels as an option so the engine's context providers
        // can render it — keeping the saved message text exactly what was typed.
        // A document notice only acknowledges the upload: no search is possible
        // on that turn, so the engine's "no search ran" nudge and note stay off.
        // A slash command (/imagine, /removebg, ...) names its tool; the
        // engine runs it directly instead of leaving the choice to the model.
        await unifiedChatService.sendMessage(sessionId, content, {
          use_rag: true,
          page_context: pageContext,
          ...directToolOptions(sendOptions),
          ...(isNotice ? { skip_nudges: true } : {}),
        }, imageBase64, isVoice);
      } catch (err) {
        console.error("FloatingChat: Unified send failed:", err);
        setIsStreamingMessage(false);
        setIsSending(false);
        finishVoiceReply();
        addMessage({
          id: `err_${Date.now()}`,
          role: "system",
          content: t("chat.floating.errorNotice", { error: err.message || t("chat.floating.sendFailed") }),
          timestamp: new Date().toISOString(),
        });
        setError(err.message || { kind: "sendFailed" });
      }
    } else {
      // Fallback: no socket connection — show error
      setIsSending(false);
      finishVoiceReply();
      addMessage({
        id: `err_${Date.now()}`,
        role: "system",
        content: t("chat.floating.notConnected"),
        timestamp: new Date().toISOString(),
      });
      setError({ kind: "notConnected" });
    }
  }, [inputText, attachment, attachmentMaxBytes, isSending, sessionId, pageContext, addMessage, setIsSending, clearError, setError, unifiedChatService, pushHistory, finishVoiceReply]);

  useEffect(() => {
    if (pendingNotices.length === 0 || isSending || !unifiedChatService || voiceTurns.length > 0) return;
    const text = pendingNotices.join("\n\n");
    setPendingNotices([]);
    handleSendMessage(text, null, { documentNotice: true });
  }, [pendingNotices, isSending, unifiedChatService, voiceTurns.length, handleSendMessage]);

  // Documents go through the same upload as the Chat page's paperclip; the
  // notices are sent once all of them are in.
  const handleDocumentFiles = useCallback(async (files) => {
    const notices = [];
    for (const file of files) {
      const refusal = chatDocumentRefusal(file);
      if (refusal) {
        notices.push(refusal);
        continue;
      }
      setDocUpload({ fileName: file.name, progress: 0 });
      const result = await uploadChatDocument(file, {
        sessionId,
        onStage: ({ progress, indexing }) =>
          setDocUpload({ fileName: file.name, indexing: Boolean(indexing), progress }),
      });
      if (!result.ok) setError({ kind: "uploadFailed", detail: result.error });
      notices.push(result.message);
    }
    setDocUpload(null);
    if (notices.length) setPendingNotices((prev) => [...prev, ...notices]);
  }, [sessionId, setError]);

  // Everything the paperclip, a paste or a drop hands this chat. It sends one
  // image per message, so only the first offered image is kept.
  const addFiles = useCallback((files) => {
    const { images, documents } = splitChatFiles(files);
    if (images.length) {
      handleAttachImage(images[0]);
      setAttachmentNote(images.length > 1 ? "firstImageOnly" : null);
    }
    if (documents.length) handleDocumentFiles(documents);
  }, [handleAttachImage, handleDocumentFiles]);

  const chatDrop = useFileDropZone({ onFiles: addFiles });

  // Send queued voice turns one at a time, each with the session current at
  // that moment, once the card is connected and not waiting on a reply.
  useEffect(() => {
    if (voiceTurns.length === 0 || isSending || !unifiedChatService) return;
    const turn = voiceTurns[0];
    removeVoiceTurn(turn.id);
    handleSendMessage(turn.text, null, { isVoiceMessage: true });
  }, [voiceTurns, isSending, unifiedChatService, removeVoiceTurn, handleSendMessage]);

  // Send turns queued by other pages, one at a time, after any voice turns.
  useEffect(() => {
    if (pendingTurns.length === 0 || voiceTurns.length > 0 || isSending || !unifiedChatService) return;
    const turn = pendingTurns[0];
    removePendingTurn(turn.id);
    handleSendMessage(turn.text, null, turn.options || {});
  }, [pendingTurns, voiceTurns.length, isSending, unifiedChatService, removePendingTurn, handleSendMessage]);

  // The input is disabled while a reply streams, which makes the browser drop
  // focus. Restore it when sending finishes so the user can keep typing without
  // clicking back into the field. Because this card floats over other pages,
  // only reclaim focus if nothing else on the page has taken it meanwhile.
  useEffect(() => {
    if (isSending || !isOpen) return;
    const active = document.activeElement;
    const focusIsFree =
      !active || active === document.body || cardRef.current?.contains(active);
    if (focusIsFree) inputRef.current?.focus();
  }, [isSending, isOpen]);

  // Slash command hook — popup state, filtering, keyboard nav, command execution
  // Initialized after handleSendMessage so the reference is valid
  const slashCmds = useSlashCommands({
    inputRef,
    addMessage: (msg) => addMessage({ ...msg, id: msg.tempId || `msg_${Date.now()}` }),
    updateMessage: (tempId, updates) => updateMessage(tempId, updates),
    onSendMessage: handleSendMessage,
    setInputText,
    chatState: {
      sessionId,
      projectId: null,
      clearMessages,
      onPlanCreated: () => {}, // no-op — floating chat doesn't support plan view
      voiceContext: { toggleVoice: voiceSession.toggleHandsFree, isVoiceActive: voiceActive },
    },
  });

  // The feature grid: a tile's text goes in the box with the cursor after
  // it; a mode tile runs the slash command that switches the mode.
  const planModeOn = useAppStore((s) => Boolean(s.sessionPlanModes[sessionId]));
  const agentModeOn = useAppStore((s) => s.sessionModes[sessionId] === "agent");
  const { executeCommand } = slashCmds;
  const prefillInput = useCallback((text) => {
    setInputText(text);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(text.length, text.length);
    });
  }, []);
  const switchMode = useCallback((mode) => {
    if (mode === "plan") executeCommand("/plan");
    else if (mode === "agent") executeCommand(agentModeOn ? "/chat" : "/agent");
  }, [executeCommand, agentModeOn]);

  // Handle streaming completion — add final message to store and recreate service
  const handleStreamingComplete = useCallback((result) => {
    setIsStreamingMessage(false);
    setIsSending(false);

    if (voiceReplyPendingRef.current && ttsEnabled && speak && result.content?.trim()) {
      try {
        speak(extractSpeakableText(result.content, result.generatedImages));
      } catch (ttsError) {
        console.warn("FloatingChat: speaking the reply failed:", ttsError);
      }
    }
    finishVoiceReply();

    const generatedImages = result.generatedImages || [];
    if (result.content || result.thinking || generatedImages.length > 0) {
      addMessage({
        id: `asst_unified_${Date.now()}`,
        role: "assistant",
        content: result.content || "",
        toolCalls: result.toolCalls || [],
        thinking: result.thinking || "",
        generatedImages,
        truncated: result.truncated === true,
        synthesized: result.synthesized === true,
        timestamp: new Date().toISOString(),
        // A proposed plan shows as a card; a run's closing report as text.
        ...(result.plan && !result.planResult
          ? { messageType: "plan", plan: result.plan, findings: result.findings || "" }
          : {}),
      });
    }

    // Recreate the service for the next message (clears old listeners)
    if (socketRef?.current) {
      const newService = new UnifiedChatService(socketRef.current);
      newService.joinSession(sessionId);
      setUnifiedChatService(newService);
    }
  }, [addMessage, setIsSending, sessionId, socketRef, ttsEnabled, speak, finishVoiceReply]);

  // What a plan card here can ask for: follow a started run, prefill the
  // input for "keep planning", stop, or take in a changed or new plan.
  const planActions = useMemo(() => ({
    onRunStarted: () => {
      setIsSending(true);
      setIsStreamingMessage(true);
    },
    onStop: () => handleStopRef.current?.(),
    onKeepPlanning: (text) => setInputText(text || ""),
    onPlanChanged: (plan, opts) => {
      if (opts?.added) {
        addMessage({ id: `asst_plan_${plan.id}`, role: "assistant", content: plan.markdown || "",
                     messageType: "plan", plan, findings: "", timestamp: new Date().toISOString() });
        return;
      }
      for (const m of useFloatingChatStore.getState().messages) {
        if (m.plan?.id === plan.id) updateMessage(m.id, { plan });
      }
    },
  }), [addMessage, setIsSending, updateMessage]);
  const handleStopRef = useRef(null);

  // A finished step of a running plan: kept as its own reply while the run
  // goes on.
  const handlePlanSegment = useCallback((result) => {
    addMessage({
      id: `asst_plan_step_${Date.now()}`,
      role: "assistant",
      content: result.content || "",
      toolCalls: result.toolCalls || [],
      generatedImages: result.generatedImages || [],
      timestamp: new Date().toISOString(),
    });
  }, [addMessage]);

  const handleStop = useCallback(() => {
    // Salvage whatever streamed so far, or it vanishes when the bubble unmounts.
    if (streamingMessageRef.current) {
      const kept = partialReplyMessage(streamingMessageRef.current.getPartialState() || {}, "aborted");
      if (kept) addMessage(kept);
    }

    // Abort the chat stream AND hard-kill the agent task — the socket
    // abort alone leaves the see-think-act loop running on the backend.
    if (unifiedChatService) {
      unifiedChatService.abort(sessionId);
      unifiedChatService.cleanup();
    }
    stopChatTurn(sessionId).then((result) => {
      if (result.ok) return;
      addMessage({
        id: `stop_err_${Date.now()}`,
        role: "system",
        content: t("chat.floating.errorNotice", { error: result.error }),
        timestamp: new Date().toISOString(),
      });
    });

    setIsStreamingMessage(false);
    setIsSending(false);
    finishVoiceReply();
  }, [unifiedChatService, sessionId, setIsSending, addMessage, finishVoiceReply]);
  handleStopRef.current = handleStop;

  // The engine sends no chat:complete after a chat:error, so the error closes
  // the turn in flight: what had streamed is kept, the error joins the thread,
  // and the button goes back from Stop to Send.
  const handleTurnError = useCallback((errorText) => {
    if (!useFloatingChatStore.getState().isSending) return;
    const kept = partialReplyMessage(streamingMessageRef.current?.getPartialState() || {}, "error");
    if (kept) addMessage(kept);
    addMessage({
      id: `err_${Date.now()}`,
      role: "system",
      content: t("chat.floating.errorNotice", { error: errorText }),
      timestamp: new Date().toISOString(),
    });
    setIsStreamingMessage(false);
    setIsSending(false);
    finishVoiceReply();
  }, [addMessage, setIsSending, finishVoiceReply]);

  // chat:error on the composer. Raw socket so it does not overwrite
  // StreamingMessage's UnifiedChatService.onError listener.
  useEffect(() => {
    const socket = socketRef?.current;
    if (!socket || !sessionId) return;
    const handleChatError = (data) => {
      if (!data || (data.session_id && data.session_id !== sessionId)) return;
      const errorText = chatErrorMessage(data);
      setError(errorText);
      handleTurnError(errorText);
    };
    socket.on("chat:error", handleChatError);
    return () => {
      socket.off("chat:error", handleChatError);
    };
  }, [sessionId, socketRef, setError, handleTurnError]);

  const handleKeyDown = (e) => {
    // Slash command popup navigation intercepts first
    slashCmds.handleKeyDown(e);
    if (e.defaultPrevented) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleFloatingSend();
      return;
    }
    if (e.key === "ArrowUp" && recallHistory("up")) {
      e.preventDefault();
    } else if (e.key === "ArrowDown" && recallHistory("down")) {
      e.preventDefault();
    }
  };

  // Send with slash command interception
  // Agent steps mark the run as live; it ends when sending does.
  useEffect(() => {
    const socket = socketRef?.current;
    if (!socket || !sessionId) return;
    const handleAgentStep = (data) => {
      if (!data || data.session_id !== sessionId || data.source !== "agent_loop") return;
      setAgentWorking(true);
    };
    socket.on("chat:thinking", handleAgentStep);
    return () => {
      socket.off("chat:thinking", handleAgentStep);
    };
  }, [socketRef?.current, sessionId]);
  useEffect(() => {
    if (!isSending) setAgentWorking(false);
  }, [isSending]);

  // A note for the running agent: shown at once, then handed to the task.
  const handleChimeIn = async () => {
    const text = inputText.trim();
    if (!text) return;
    pushHistory(text);
    setInputText("");
    const id = `note_${Date.now()}`;
    addMessage({ id, role: "user", content: text, timestamp: new Date().toISOString(), agentNote: "sending" });
    try {
      const res = await steerAgent(sessionId, text);
      updateMessage(id, { agentNote: res.queued ? (res.stopping ? "stopping" : "sent") : "late" });
    } catch (err) {
      console.error("FloatingChat: agent note failed:", err);
      updateMessage(id, { agentNote: "failed" });
    }
  };

  const handleFloatingSend = async () => {
    if (noteMode) {
      await handleChimeIn();
      return;
    }
    // Checked on the text too: isCommand only follows typing, and a command
    // recalled from history (Up arrow) must run the same way.
    if (slashCmds.isCommand || inputText.trim().startsWith("/")) {
      const result = await slashCmds.executeCommand(inputText);
      if (result?.handled) {
        setInputText("");
        return;
      }
    }
    handleSendMessage();
  };

  // Drag: double-click to collapse, single-click+drag to move
  const handleHeaderMouseDown = useCallback(
    (e) => {
      if (e.target.closest(".floating-chat-btn")) return;

      const now = Date.now();
      if (now - lastClickRef.current < DOUBLE_CLICK_MS) {
        toggleCollapsed();
        lastClickRef.current = 0;
        return;
      }
      lastClickRef.current = now;

      setIsDragging(true);
      const rect = cardRef.current.getBoundingClientRect();
      setDragOffset({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    },
    [toggleCollapsed]
  );

  // Resize handle
  const handleResizeMouseDown = useCallback(
    (e) => {
      e.stopPropagation();
      setIsResizing(true);
      setResizeStart({ x: e.clientX, y: e.clientY, w: size.w, h: size.h });
    },
    [size]
  );

  // Mouse move/up for drag and resize
  useEffect(() => {
    if (!isDragging && !isResizing) return;

    const handleMouseMove = (e) => {
      if (isDragging) {
        setPosition({
          x: Math.max(0, Math.min(e.clientX - dragOffset.x, window.innerWidth - size.w)),
          y: Math.max(0, Math.min(e.clientY - dragOffset.y, window.innerHeight - 40)),
        });
      }
      if (isResizing) {
        const maxW = window.innerWidth - position.x - VIEWPORT_MARGIN;
        const maxH = window.innerHeight - position.y - VIEWPORT_MARGIN;
        setSize({
          w: Math.min(Math.max(MIN_WIDTH, resizeStart.w + (e.clientX - resizeStart.x)), maxW),
          h: Math.min(Math.max(MIN_HEIGHT, resizeStart.h + (e.clientY - resizeStart.y)), maxH),
        });
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, isResizing, dragOffset, resizeStart, position, size.w, setPosition, setSize]);

  const formatTime = (timestamp) => {
    if (!timestamp) return "";
    return new Date(timestamp).toLocaleTimeString(uiLocale(), {
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  // Our own errors are kept as a kind and worded here; server and helper
  // messages arrive as text.
  const errorText = (err) => {
    if (typeof err === "string") return err;
    if (err.kind === "prepareFailed") return t("chat.input.prepareFailed");
    if (err.kind === "sendFailed") return t("chat.floating.sendFailed");
    if (err.kind === "notConnected") return t("chat.floating.notConnected");
    if (err.kind === "uploadFailed") return t("chat.input.uploadFailed", { error: err.detail });
    return "";
  };

  // The attachment note is kept as an id and worded here.
  const attachmentNoteText = { firstImageOnly: t("chat.floating.firstImageOnly") };

  // Page context chip label
  const contextLabel = contextChipLabel(pageContext, entityLabel);

  return (
    <Grow in={isOpen} unmountOnExit mountOnEnter>
      <Paper
        ref={cardRef}
        elevation={8}
        {...chatDrop.dropProps}
        data-testid="floating-chat-card"
        sx={{
          position: "fixed",
          top: position.y === -1 ? undefined : position.y,
          left: position.x === -1 ? undefined : position.x,
          bottom: position.y === -1 ? 48 : undefined,
          right: position.x === -1 ? 24 : undefined,
          width: size.w,
          height: collapsed ? "auto" : size.h,
          zIndex: 1400,
          userSelect: "none",
          borderRadius: "12px",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          border: `1px solid ${theme.palette.divider}`,
          boxShadow: `0 8px 32px rgba(0, 0, 0, 0.35)`,
        }}
      >
        <FileDropOverlay active={chatDrop.isDragActive} compact label={t("chat.fileDrop.floatingLabel")} />

        {/* Header */}
        <Box
          onMouseDown={handleHeaderMouseDown}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            px: 1.5,
            py: 0.75,
            cursor: isDragging ? "grabbing" : "grab",
            flexShrink: 0,
            bgcolor: theme.palette.mode === "dark" ? "rgba(255,255,255,0.03)" : "rgba(0,0,0,0.02)",
            borderBottom: `1px solid ${theme.palette.divider}`,
          }}
        >
          <ChatBubbleOutlineIcon sx={{ fontSize: 16, color: "primary.main" }} />
          <Typography
            variant="caption"
            noWrap
            sx={{
              fontWeight: 600,
              color: "text.secondary",
              fontSize: "0.8rem",
              mr: 0.5,
            }}
          >
            {t("chat.floating.title")}
          </Typography>
          {contextLabel && (
            <Chip
              label={contextLabel}
              size="small"
              variant="outlined"
              color="primary"
              sx={{ height: 20, fontSize: "0.7rem", maxWidth: 140 }}
            />
          )}

          <Box sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 0 }}>
            <IconButton
              className="floating-chat-btn"
              onClick={clearMessages}
              size="small"
              title={t("chat.sessions.newChat")}
              sx={{ p: 0.25, color: "text.secondary", "&:hover": { color: "primary.main" } }}
            >
              <AddIcon sx={{ fontSize: 16 }} />
            </IconButton>
            <IconButton
              className="floating-chat-btn"
              onClick={toggleCollapsed}
              size="small"
              title={collapsed ? t("chat.floating.expand") : t("common.collapse")}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <MinimizeIcon sx={{ fontSize: 16 }} />
            </IconButton>
            <IconButton
              className="floating-chat-btn"
              onClick={() => setIsOpen(false)}
              size="small"
              title={t("chat.floating.close")}
              sx={{ p: 0.25, color: "text.secondary", "&:hover": { color: "error.main" } }}
            >
              <CloseIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Box>
        </Box>

        {/* Body */}
        {!collapsed && (
          <>
            {/* Messages */}
            <Box
              sx={{
                flexGrow: 1,
                overflowY: "auto",
                px: 1.5,
                py: 1,
                cursor: "default",
              }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              {messages.length === 0 && (
                <Typography
                  variant="body2"
                  sx={{
                    color: "text.secondary",
                    textAlign: "center",
                    py: 4,
                    fontSize: "0.85rem",
                  }}
                >
                  {t("chat.floating.empty")}
                </Typography>
              )}

              <PlanActionsContext.Provider value={planActions}>
                <List dense disablePadding>
                  {messages.slice(-15).map((msg) => (
                    <FloatingChatMessage
                      key={msg.id}
                      message={msg}
                      formatTime={formatTime}
                    />
                  ))}
                </List>
              </PlanActionsContext.Provider>

              {/* Streaming response via Socket.IO */}
              {isStreamingMessage && unifiedChatService && (
                <Box sx={{ py: 0.5 }}>
                  <StreamingMessage
                    ref={streamingMessageRef}
                    chatService={unifiedChatService}
                    sessionId={sessionId}
                    onComplete={handleStreamingComplete}
                    onSegment={handlePlanSegment}
                  />
                </Box>
              )}

              <div ref={messagesEndRef} />
            </Box>

            {voiceTurns.length > 0 && isSending && (
              <Typography
                variant="caption"
                sx={{ color: "text.secondary", px: 1.5, fontSize: "0.72rem" }}
              >
                {t("chat.floating.voiceTurnsWaiting", { count: voiceTurns.length })}
              </Typography>
            )}

            {/* Error */}
            {error && (
              <Typography
                variant="caption"
                sx={{
                  color: "error.main",
                  px: 1.5,
                  py: 0.5,
                  fontSize: "0.75rem",
                }}
              >
                {errorText(error)}
              </Typography>
            )}

            {attachment && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 1.5, pb: 0.5 }}>
                <Box
                  component="img"
                  src={attachment.preview}
                  alt={attachment.file.name}
                  sx={{ width: 40, height: 40, objectFit: "cover", borderRadius: 1 }}
                />
                <StatusPill
                  label={formatAttachmentSize(attachment.byteLength)}
                  tone={attachmentExceedsLimit(attachment.byteLength, attachmentMaxBytes) ? "error" : "neutral"}
                  tooltip={
                    attachmentExceedsLimit(attachment.byteLength, attachmentMaxBytes)
                      ? refuseAttachmentMessage(attachment.byteLength, attachmentMaxBytes)
                      : t("chat.input.sizeTooltip")
                  }
                />
                <IconButton
                  size="small"
                  className="floating-chat-btn"
                  onClick={() => {
                    setAttachment(null);
                    setAttachmentNote(null);
                  }}
                  aria-label={t("chat.floating.removeImage")}
                  sx={{ p: 0.25 }}
                >
                  <CloseIcon sx={{ fontSize: 14 }} />
                </IconButton>
                {attachmentNote && (
                  <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.72rem" }}>
                    {attachmentNoteText[attachmentNote]}
                  </Typography>
                )}
              </Box>
            )}

            {docUpload && (
              <Box sx={{ px: 1.5, pb: 0.5 }} data-testid="floating-upload-progress">
                <Typography variant="caption" sx={{ color: "text.secondary", fontSize: "0.72rem" }} noWrap>
                  {t("chat.input.uploading", {
                    name: docUpload.indexing
                      ? t("chat.input.fileIndexing", { name: docUpload.fileName })
                      : docUpload.fileName,
                  })}
                </Typography>
                <LinearProgress
                  variant="determinate"
                  value={Math.min(100, Math.max(0, docUpload.progress || 0))}
                  sx={{ height: 3, borderRadius: 2 }}
                />
              </Box>
            )}

            {/* The last reply could not be spoken (no voice installed, engine down) */}
            {speakError && (
              <Box
                sx={{ px: 1.5, pb: 0.5, display: "flex", alignItems: "center", gap: 0.5 }}
                data-testid="floating-speak-error"
              >
                <Typography variant="caption" sx={{ color: "warning.main", fontSize: "0.72rem", flex: 1 }}>
                  {speakError}
                </Typography>
                {clearSpeakError && (
                  <IconButton
                    size="small"
                    className="floating-chat-btn"
                    onClick={clearSpeakError}
                    aria-label={t("chat.floating.dismissVoiceMessage")}
                    sx={{ p: 0.25 }}
                  >
                    <CloseIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                )}
              </Box>
            )}

            {/* Input */}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 0.5,
                px: 1,
                py: 0.75,
                borderTop: `1px solid ${theme.palette.divider}`,
                flexShrink: 0,
                cursor: "default",
              }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              {/* The global mic: same state and microphone as the top-bar button */}
              <Box className="floating-chat-btn" sx={{ display: "flex", alignItems: "center" }}>
                <GlobalMicButton variant="compact" label={t("common.voice")} />
              </Box>
              <SlashCommandPopup
                commands={slashCmds.filteredCommands}
                selectedIndex={slashCmds.selectedIndex}
                onSelect={slashCmds.selectCommand}
                anchorEl={inputRef?.current}
                open={slashCmds.popupVisible}
              />
              <input
                type="file"
                hidden
                ref={fileRef}
                accept={CHAT_ATTACH_ACCEPT}
                data-testid="floating-chat-file"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) addFiles([file]);
                }}
              />
              <ChatFeatureMenu
                size="small"
                onPrefill={prefillInput}
                onAttach={() => fileRef.current?.click()}
                onMode={switchMode}
                modes={{ plan: planModeOn, agent: agentModeOn }}
                disabled={isSending}
              />
              <Tooltip title={t("chat.input.attach")}>
                <span>
                  <IconButton
                    className="floating-chat-btn"
                    onClick={() => fileRef.current?.click()}
                    disabled={isSending}
                    size="small"
                    aria-label={t("chat.input.attach")}
                    sx={{ p: 0.25, color: "text.secondary" }}
                  >
                    <AttachFileIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </span>
              </Tooltip>
              <TextField
                size="small"
                placeholder={
                  noteMode
                    ? t("chat.floating.placeholderNote")
                    : t("chat.input.placeholder")
                }
                value={inputText}
                onChange={(e) => {
                  setInputText(e.target.value);
                  slashCmds.handleInputChange(e.target.value);
                }}
                onKeyDown={handleKeyDown}
                onPaste={(e) => {
                  const items = e.clipboardData?.items;
                  if (!items) return;
                  for (let i = 0; i < items.length; i += 1) {
                    if (items[i].type.startsWith("image/")) {
                      const file = items[i].getAsFile();
                      if (file) {
                        addFiles([file]);
                        e.preventDefault();
                      }
                      break;
                    }
                  }
                }}
                disabled={isSending && !noteMode}
                multiline
                maxRows={3}
                inputRef={inputRef}
                sx={{
                  flexGrow: 1,
                  "& .MuiOutlinedInput-root": {
                    fontSize: "0.85rem",
                    borderRadius: "8px",
                  },
                }}
              />
              {noteMode && (
                <Tooltip title={t("chat.input.sendNote")}>
                  <span>
                    <IconButton
                      onClick={handleChimeIn}
                      disabled={!inputText.trim()}
                      size="small"
                      color="warning"
                    >
                      <SendIcon />
                    </IconButton>
                  </span>
                </Tooltip>
              )}
              <IconButton
                onClick={isSending ? handleStop : handleFloatingSend}
                disabled={!inputText.trim() && !attachment && !isSending}
                size="small"
                color="primary"
              >
                {isSending ? <StopIcon /> : <SendIcon />}
              </IconButton>
            </Box>

            {/* Resize handle */}
            <Box
              onMouseDown={handleResizeMouseDown}
              sx={{
                position: "absolute",
                bottom: 0,
                right: 0,
                width: 16,
                height: 16,
                cursor: "se-resize",
                "&::after": {
                  content: '""',
                  position: "absolute",
                  bottom: 3,
                  right: 3,
                  width: 8,
                  height: 8,
                  borderRight: `2px solid ${theme.palette.text.secondary}`,
                  borderBottom: `2px solid ${theme.palette.text.secondary}`,
                  opacity: 0.3,
                },
              }}
            />
          </>
        )}
      </Paper>
    </Grow>
  );
};

export default FloatingChatCard;
