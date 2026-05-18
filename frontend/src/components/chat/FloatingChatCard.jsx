import React, { useEffect, useState, useRef, useCallback } from "react";
import {
  Box,
  Typography,
  IconButton,
  Paper,
  TextField,
  Chip,
  List,
  ListItem,
  Grow,
  useTheme,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import MinimizeIcon from "@mui/icons-material/Remove";
import AddIcon from "@mui/icons-material/Add";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import HearingIcon from "@mui/icons-material/Hearing";
import Tooltip from "@mui/material/Tooltip";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import UnifiedChatService from "../../api/unifiedChatService";
import StreamingMessage from "./StreamingMessage";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import VoiceChatButton from "../voice/VoiceChatButton";
import ContinuousVoiceChat from "../voice/ContinuousVoiceChat";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceSettings } from "../../hooks/useVoiceSettings";
import useSlashCommands from "../../hooks/useSlashCommands";
import SlashCommandPopup from "./SlashCommandPopup";

const MIN_WIDTH = 280;
const MIN_HEIGHT = 300;
const DOUBLE_CLICK_MS = 400;

const FloatingChatCard = () => {
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

  // Listener mode state
  const listenerModeEnabled = useAppStore((s) => s.listenerModeEnabled);
  const toggleListenerMode = useAppStore((s) => s.toggleListenerMode);
  const systemName = useAppStore((s) => s.systemName);
  const voiceSettings = useVoiceSettings();
  const wakeWordEnabled = voiceSettings.wakeWordEnabled !== false;  // Default ON

  // Unified Chat Service (Socket.IO streaming)
  const { socketRef, connectionState } = useUnifiedProgress();
  const [unifiedChatService, setUnifiedChatService] = useState(null);
  const [isStreamingMessage, setIsStreamingMessage] = useState(false);

  // Initialize UnifiedChatService when socket is connected
  useEffect(() => {
    if (connectionState !== 'connected' || !socketRef?.current) {
      return;
    }

    const service = new UnifiedChatService(socketRef.current);
    service.joinSession(sessionId);
    setUnifiedChatService(service);

    return () => {
      service.cleanup();
      setUnifiedChatService(null);
    };
  }, [connectionState, sessionId]);

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

  const lastClickRef = useRef(0);
  const cardRef = useRef(null);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const streamingMessageRef = useRef(null);

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

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Build context prefix for messages
  const buildContextPrefix = useCallback(() => {
    if (!pageContext || pageContext.page === "Chat" || pageContext.page === "Unknown") {
      return "";
    }
    let prefix = `[Context: User is viewing the ${pageContext.page} page`;
    if (pageContext.entityType && pageContext.entityId) {
      prefix += `, ${pageContext.entityType} ID: ${pageContext.entityId}`;
    }
    prefix += "]\n\n";
    return prefix;
  }, [pageContext]);

  // Send message handler — uses UnifiedChatService (Socket.IO streaming)
  const handleSendMessage = useCallback(async (overrideText) => {
    const text = overrideText || inputText;
    if (!text.trim() || isSending) return;

    pushHistory(text);

    const userMessage = {
      id: `user_${Date.now()}`,
      role: "user",
      content: text,
      timestamp: new Date().toISOString(),
    };
    addMessage(userMessage);
    if (!overrideText) setInputText("");

    setIsSending(true);
    clearError();

    const contextPrefix = buildContextPrefix();
    const messageToSend = contextPrefix + text;

    if (unifiedChatService) {
      // Primary path: Socket.IO streaming via UnifiedChatService
      setIsStreamingMessage(true);

      try {
        await unifiedChatService.sendMessage(sessionId, messageToSend, {
          use_rag: true,
        });
      } catch (err) {
        console.error("FloatingChat: Unified send failed:", err);
        setIsStreamingMessage(false);
        setIsSending(false);
        const errorText = err.message || "Failed to send message";
        addMessage({
          id: `err_${Date.now()}`,
          role: "system",
          content: `Error: ${errorText}`,
          timestamp: new Date().toISOString(),
        });
        setError(errorText);
      }
    } else {
      // Fallback: no socket connection — show error
      setIsSending(false);
      const errorText = "Chat service not connected. Please wait for connection.";
      addMessage({
        id: `err_${Date.now()}`,
        role: "system",
        content: errorText,
        timestamp: new Date().toISOString(),
      });
      setError(errorText);
    }
  }, [inputText, isSending, sessionId, buildContextPrefix, addMessage, setIsSending, clearError, setError, unifiedChatService, pushHistory]);

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
    },
  });

  // Handle streaming completion — add final message to store and recreate service
  const handleStreamingComplete = useCallback((result) => {
    setIsStreamingMessage(false);
    setIsSending(false);

    if (result.content) {
      addMessage({
        id: `asst_unified_${Date.now()}`,
        role: "assistant",
        content: result.content,
        toolCalls: result.toolCalls || [],
        timestamp: new Date().toISOString(),
      });
    }

    // Recreate the service for the next message (clears old listeners)
    if (socketRef?.current) {
      const newService = new UnifiedChatService(socketRef.current);
      newService.joinSession(sessionId);
      setUnifiedChatService(newService);
    }
  }, [addMessage, setIsSending, sessionId, socketRef]);

  const handleStop = useCallback(() => {
    // Salvage whatever streamed so far. Floating card renders only
    // msg.content, so we flatten any thinking-trail steps into the body
    // text — otherwise the trail vanishes when the bubble unmounts.
    if (streamingMessageRef.current) {
      const partial = streamingMessageRef.current.getPartialState() || {};
      const steps = partial.agentThinkingSteps || [];
      const stepText = steps.length
        ? steps
            .map((s) => `step ${s.iteration}: ${s.label}${s.reasoning ? ` — ${s.reasoning}` : ""}`)
            .join("\n")
        : "";
      const body = (partial.content || "").trim();
      const hasAnything = body || stepText || (partial.toolCalls?.length || 0) > 0;
      if (hasAnything) {
        const composed = [
          body,
          stepText ? `--- agent trail (stopped) ---\n${stepText}` : "",
        ].filter(Boolean).join("\n\n");
        addMessage({
          id: `asst_unified_${Date.now()}_partial`,
          role: "assistant",
          content: composed || "(stopped before any output)",
          toolCalls: partial.toolCalls || [],
          timestamp: new Date().toISOString(),
          status: "aborted",
        });
      }
    }

    // Abort the chat stream AND hard-kill the agent task — the socket
    // abort alone leaves the see-think-act loop running on the backend.
    if (unifiedChatService) {
      unifiedChatService.abort(sessionId);
      unifiedChatService.cleanup();
    }
    fetch(`/api/chat/unified/${sessionId}/abort`, { method: "POST" }).catch(() => {});
    fetch("/api/agent-control/kill", { method: "POST" }).catch(() => {});

    setIsStreamingMessage(false);
    setIsSending(false);
  }, [unifiedChatService, sessionId, setIsSending, addMessage]);

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
  const handleFloatingSend = async () => {
    if (slashCmds.isCommand) {
      const result = await slashCmds.executeCommand(inputText);
      if (result?.handled) {
        setInputText("");
        return;
      }
    }
    handleSendMessage();
  };

  // Voice transcription handler
  const handleTranscriptionReceived = useCallback(({ userMessage, aiResponse }) => {
    if (!userMessage) return;

    if (aiResponse) {
      // Voice stream returned both transcription and response — add directly
      addMessage({
        id: `user_${Date.now()}`,
        role: "user",
        content: userMessage,
        timestamp: new Date().toISOString(),
      });
      addMessage({
        id: `asst_${Date.now() + 1}`,
        role: "assistant",
        content: aiResponse,
        timestamp: new Date().toISOString(),
      });
    } else {
      // No AI response — send through normal chat pipeline for streaming
      handleSendMessage(userMessage);
    }
  }, [addMessage, handleSendMessage]);

  // Bridge ContinuousVoiceChat's onMessageReceived to floating chat
  const handleContinuousVoiceMessage = useCallback(({ transcription, response }) => {
    if (!transcription || !transcription.trim()) return;

    if (response) {
      addMessage({
        id: `user_${Date.now()}`,
        role: "user",
        content: transcription.trim(),
        timestamp: new Date().toISOString(),
      });
      addMessage({
        id: `asst_${Date.now() + 1}`,
        role: "assistant",
        content: response,
        timestamp: new Date().toISOString(),
      });
    } else {
      handleSendMessage(transcription.trim());
    }
  }, [addMessage, handleSendMessage]);

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
        setSize({
          w: Math.max(MIN_WIDTH, resizeStart.w + (e.clientX - resizeStart.x)),
          h: Math.max(MIN_HEIGHT, resizeStart.h + (e.clientY - resizeStart.y)),
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
  }, [isDragging, isResizing, dragOffset, resizeStart, setPosition, setSize]);

  const formatTime = (timestamp) => {
    if (!timestamp) return "";
    return new Date(timestamp).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  // Page context chip label
  const contextLabel =
    pageContext && pageContext.page !== "Unknown" && pageContext.page !== "Chat"
      ? pageContext.entityId
        ? `${pageContext.page} #${pageContext.entityId}`
        : pageContext.page
      : null;

  return (
    <Grow in={isOpen} unmountOnExit mountOnEnter>
      <Paper
        ref={cardRef}
        elevation={8}
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
            Chat
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
              title="New chat"
              sx={{ p: 0.25, color: "text.secondary", "&:hover": { color: "primary.main" } }}
            >
              <AddIcon sx={{ fontSize: 16 }} />
            </IconButton>
            <IconButton
              className="floating-chat-btn"
              onClick={toggleCollapsed}
              size="small"
              title={collapsed ? "Expand" : "Collapse"}
              sx={{ p: 0.25, color: "text.secondary" }}
            >
              <MinimizeIcon sx={{ fontSize: 16 }} />
            </IconButton>
            <IconButton
              className="floating-chat-btn"
              onClick={() => setIsOpen(false)}
              size="small"
              title="Close"
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
                  Ask anything about what you're working on.
                </Typography>
              )}

              <List dense disablePadding>
                {messages.slice(-15).map((msg) => (
                  <ListItem
                    key={msg.id}
                    disableGutters
                    disablePadding
                    sx={{
                      flexDirection: "column",
                      alignItems: msg.role === "user" ? "flex-end" : "flex-start",
                      py: 0.5,
                    }}
                  >
                    <Box
                      sx={{
                        maxWidth: "85%",
                        bgcolor:
                          msg.role === "user"
                            ? "primary.main"
                            : msg.role === "system"
                            ? "error.dark"
                            : theme.palette.mode === "dark"
                            ? "rgba(255,255,255,0.06)"
                            : "rgba(0,0,0,0.04)",
                        color:
                          msg.role === "user" || msg.role === "system"
                            ? "#fff"
                            : "text.primary",
                        borderRadius: msg.role === "user" ? "12px 12px 2px 12px" : "12px 12px 12px 2px",
                        px: 1.5,
                        py: 0.75,
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          fontSize: "0.82rem",
                          wordBreak: "break-word",
                          whiteSpace: "pre-wrap",
                          lineHeight: 1.5,
                        }}
                      >
                        {msg.content || ""}
                      </Typography>
                    </Box>
                    <Typography
                      variant="caption"
                      sx={{
                        fontSize: "0.65rem",
                        color: "text.disabled",
                        mt: 0.25,
                        px: 0.5,
                      }}
                    >
                      {formatTime(msg.timestamp)}
                    </Typography>
                  </ListItem>
                ))}
              </List>

              {/* Streaming response via Socket.IO */}
              {isStreamingMessage && unifiedChatService && (
                <Box sx={{ py: 0.5 }}>
                  <StreamingMessage
                    ref={streamingMessageRef}
                    chatService={unifiedChatService}
                    sessionId={sessionId}
                    onComplete={handleStreamingComplete}
                  />
                </Box>
              )}

              <div ref={messagesEndRef} />
            </Box>

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
                {error}
              </Typography>
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
              {/* Listener mode toggle */}
              <Tooltip title={listenerModeEnabled ? "Push-to-talk" : "Listener mode"}>
                <IconButton
                  className="floating-chat-btn"
                  onClick={toggleListenerMode}
                  size="small"
                  sx={{
                    p: 0.25,
                    width: 24,
                    height: 24,
                    border: 1,
                    borderColor: listenerModeEnabled ? 'success.main' : 'transparent',
                    color: listenerModeEnabled ? 'success.main' : 'text.secondary',
                  }}
                >
                  <HearingIcon sx={{ fontSize: 14 }} />
                </IconButton>
              </Tooltip>

              {/* Voice input: push-to-talk or continuous listener */}
              {listenerModeEnabled ? (
                <Box sx={{ maxWidth: 120, overflow: 'hidden', display: 'flex', alignItems: 'center' }}>
                  <ContinuousVoiceChat
                    sessionId={sessionId}
                    onMessageReceived={handleContinuousVoiceMessage}
                    onError={(err) => setError(err?.message || "Voice error")}
                    compact={true}
                    wakeWordEnabled={wakeWordEnabled}
                    systemName={systemName || 'Guaardvark'}
                    onWakeWordDetected={() => {}}
                  />
                </Box>
              ) : (
                <VoiceChatButton
                  onTranscriptionReceived={handleTranscriptionReceived}
                  onError={(err) => setError(err?.message || "Voice error")}
                  disabled={isSending}
                  sessionId={sessionId}
                  size="small"
                />
              )}
              <SlashCommandPopup
                commands={slashCmds.filteredCommands}
                selectedIndex={slashCmds.selectedIndex}
                onSelect={slashCmds.selectCommand}
                anchorEl={inputRef?.current}
                open={slashCmds.popupVisible}
              />
              <TextField
                size="small"
                placeholder="Type your message, paste an image, or use voice..."
                value={inputText}
                onChange={(e) => {
                  setInputText(e.target.value);
                  slashCmds.handleInputChange(e.target.value);
                }}
                onKeyDown={handleKeyDown}
                disabled={isSending}
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
              <IconButton
                onClick={isSending ? handleStop : handleFloatingSend}
                disabled={!inputText.trim() && !isSending}
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
