
import { Alert, Box, Chip, Paper, Typography, Tooltip, IconButton } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import HistoryIcon from "@mui/icons-material/History";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import SchoolIcon from "@mui/icons-material/School";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import LessonPearlsFloater from "../components/agent/LessonPearlsFloater";
import LessonSummaryModal from "../components/modals/LessonSummaryModal";
import { useAppStore } from "../stores/useAppStore";
import { BASE_URL } from "../api/apiClient";
import ChatSessionDrawer from "../components/chat/ChatSessionDrawer";
// AgentScreenViewer is now global in Sidebar — available on all pages
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getChatHistory, sendChatMessage } from "../api";
import { generateFileFromChat } from "../api/filegenService";
import FileGenPopup from "../components/FileGenPopup";
import ChatInput from "../components/chat/ChatInput";
import MessageList from "../components/chat/MessageList";
import UnifiedUploadModal from "../components/modals/UnifiedUploadModal";
import PageLayout from "../components/layout/PageLayout";
import BackgroundWaveform from "../components/voice/BackgroundWaveform";
import { useVoice } from "../contexts/VoiceContext";
import { useStatus } from "../contexts/StatusContext";
import { generateBulkCSV } from "../api/bulkGenerationService";
import {
  ProcessType,
  createDialogState,
  createManagedProcess,
  enqueueMessage,
  getResourceManager,
  managedApiCall,
} from "../utils/resource_manager";
import {
  registerSession,
  recordMessage,
  preserveContextDuringFileGeneration,
  restoreConversationContext,
  restoreSessionFromBackup,
} from "../api/sessionStateService";
import { useAgentRouter } from "../hooks/useAgentRouter";
import { routeAndExecute } from "../api/toolsService";
import UnifiedChatService from "../api/unifiedChatService";
import StreamingMessage from "../components/chat/StreamingMessage";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import extractSpeakableText from "../utils/extractSpeakableText";

import { createPlan } from "../api/orchestratorService";
import { mergeActivePlanIntoMessages, hydrateOrchestratorFields } from "../utils/orchestratorChat";
import { debugLog } from "../utils/debugLog";

const USE_AGENT_ROUTING = () => {
  try {
    const val = localStorage.getItem("use_agent_routing");
    return val === null || val === "true"; // ON by default, opt-out with "false"
  } catch {
    return true;
  }
};

const USE_UNIFIED_CHAT = () => {
  try {
    const val = localStorage.getItem("use_unified_chat");
    return val === null || val === "true";
  } catch {
    return true;
  }
};

const ChatPage = () => {
  const { projectId } = useParams();
  const navHistory = useNavigate();

  const resourceManager = getResourceManager();

  const sessionKey = `chat_session_${projectId || 'default'}`;

  const [processId] = useState(() => {
    const storageKey = `chat_process_${projectId || 'default'}`;

    const existingProcesses = Array.from(resourceManager.processes.entries())
      .filter(([_id, process]) =>
        process.type === ProcessType.CHAT_MESSAGE &&
        process.metadata?.projectId === projectId
      );

    if (existingProcesses.length > 0) {
      console.warn(`DUPLICATE PREVENTION: Reusing existing process: ${existingProcesses[0][0]}`);
      return existingProcesses[0][0];
    }

    try {
      const storedProcess = sessionStorage.getItem(storageKey);
      if (storedProcess) {
        const { processId: storedId, timestamp } = JSON.parse(storedProcess);
        if (Date.now() - timestamp < 30000) {
          // Expected in React.StrictMode dev (double-mount). Keep it quiet unless debugging.
          if (import.meta.env.DEV) {
            console.debug(`STRICT MODE PROTECTION: Reusing recent process: ${storedId} (age: ${Math.round((Date.now() - timestamp) / 1000)}s)`);
          }
          return storedId;
        }
      }
    } catch (e) {
      console.warn('Failed to read process storage:', e);
    }

    const newProcessId = createManagedProcess(ProcessType.CHAT_MESSAGE, { projectId, sessionKey });

    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        processId: newProcessId,
        timestamp: Date.now()
      }));
    } catch (e) {
      console.warn('Failed to store process info:', e);
    }

    return newProcessId;
  });

  const [messageQueueId] = useState(() =>
    resourceManager.createMessageQueue(processId)
  );
  const [dialogStateId] = useState(() =>
    createDialogState(processId, "file_generation")
  );

  const agentRouter = useAgentRouter();
  const [useAgentRouting] = useState(USE_AGENT_ROUTING);

  const [useUnifiedChat] = useState(USE_UNIFIED_CHAT);
  const { socketRef, connectionState, forceReconnect } = useUnifiedProgress();
  const [budgetTelemetry, setBudgetTelemetry] = useState(null);  // Phase 2.1: surface from TierTelemetry when agent mode
  const [unifiedChatService, setUnifiedChatService] = useState(null);
  const [isStreamingMessage, setIsStreamingMessage] = useState(false);

  const [, setAgentLoopExecuting] = useState(false);
  const [, setAgentLoopMessageId] = useState(null);

  const [messages, setMessages] = useState([]);
  const [error, setError] = useState('');
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [previousChatsOpen, setPreviousChatsOpen] = useState(false);
  const [sessionId, _setSessionId] = useState(() => {
    // Per-project session IDs: each project gets its own chat session
    const storageKey = `guaardvark_chat_session_id_${projectId || 'global'}`;

    // One-time migration: copy old global key to new per-project key if needed
    const oldGlobalKey = "guaardvark_chat_session_id";
    const oldGlobalSession = localStorage.getItem(oldGlobalKey);
    if (oldGlobalSession && !localStorage.getItem(storageKey)) {
      localStorage.setItem(storageKey, oldGlobalSession);
      localStorage.removeItem(oldGlobalKey);
    }

    let storedSessionId = localStorage.getItem(storageKey);

    if (storedSessionId && !/^session_\d+$/.test(storedSessionId)) {
      console.warn("Invalid session ID format detected, creating new session");
      storedSessionId = null;
    }

    if (storedSessionId) {
      if (!sessionStorage.getItem("session_logged_" + storedSessionId)) {
        sessionStorage.setItem("session_logged_" + storedSessionId, "true");
      }

      sessionStorage.setItem("session_continuity_" + storedSessionId, Date.now().toString());

      return storedSessionId;
    }

    const newSessionId = `session_${Date.now()}`;
    localStorage.setItem(storageKey, newSessionId);
    sessionStorage.setItem("session_logged_" + newSessionId, "true");
    sessionStorage.setItem("session_continuity_" + newSessionId, Date.now().toString());

    sessionStorage.setItem("context_preservation_" + newSessionId, JSON.stringify({
      initialized: Date.now(),
      messageCount: 0,
      lastActivity: Date.now()
    }));

    // Registration happens in useEffect below — don't duplicate here
    return newSessionId;
  });

  // surface budget in UI for agent mode awareness (Phase 2.1)
  // Declared here (after its useState) so the `sessionId` identifier is initialized.
  // This prevents TDZ "can't access lexical declaration before initialization".
  // The derived is still before any JSX usage and before effects that close over it.
  const showBudget = useAppStore.getState().getSessionMode(sessionId) === "agent" && budgetTelemetry;

  const [isSending, setIsSending] = useState(false);
  const chatInputRef = useRef(null);
  const historyLoadedRef = useRef(false); // Track if we've already loaded history
  const historyLoadingRef = useRef(false); // Prevent concurrent history fetches
  const lastMessageRef = useRef(null);
  const processMessageRef = useRef(null);
  const streamingMessageRef = useRef(null);
  // Holds the service instance that should be passed to StreamingMessage for
  // the current in-flight turn. Used to paper over async setState timing so
  // that the conditional render of StreamingMessage always gets a live service
  // with listeners (prevents missed chat:thinking / chat:complete for agent steps).
  const streamingServiceRef = useRef(null);

  useEffect(() => {
    // Create the service wrapper as soon as the socket object exists (eagerly).
    // We no longer require connectionState==='connected' here — the underlying
    // socket.io client buffers emits (incl. chat:join) until the connection is ready.
    // This eliminates a race where /agent (or mode=agent) + immediate send would
    // see no service yet and hard-error with the "unified chat socket not connected" message.
    //
    // Re-create / re-join when the socket ref or connectionState changes so that
    // transient disconnects and StrictMode double-mounts don't leave hasService=false.
    if (!useUnifiedChat || !socketRef?.current) {
      if (unifiedChatService) {
        unifiedChatService.cleanup();
        setUnifiedChatService(null);
      }
      return;
    }

    const currentSocket = socketRef.current;
    // Avoid recreating if we already have a service bound to this exact socket instance.
    if (unifiedChatService && unifiedChatService.socket === currentSocket) {
      debugLog('[ChatPage] SERVICE_EFFECT: reusing existing service for same socket; calling joinSession (idempotent + connect rebind)');
      unifiedChatService.joinSession(sessionId);
      return;
    }

    const service = new UnifiedChatService(currentSocket);
    debugLog('[ChatPage] SERVICE_EFFECT: creating UnifiedChatService + join for session=', sessionId, 'connState=', connectionState, 'socketConnected=', !!currentSocket?.connected);
    service.joinSession(sessionId);
    debugLog('[ChatPage] SERVICE_EFFECT: AFTER joinSession; attaching page-level guards (onImage, onComplete). chat:thinking for agent_loop ONLY attached later in StreamingMessage useEffect when isStreamingMessage renders it.');
    setUnifiedChatService(service);

    // Listen for image events at the page level — catches images that arrive
    // after StreamingMessage unmounts (e.g., slow image generation)
    service.onImage((data) => {
      if (data.session_id !== sessionId) return;
      const newImg = {
        url: data.image_url,
        alt: data.alt || "Generated image",
        caption: data.caption || "",
      };
      // Update the most recent assistant message with the image
      setMessages((prev) => {
        const updated = [...prev];
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].role === "assistant") {
            const existing = updated[i].generatedImages || [];
            if (!existing.some(img => img.url === newImg.url)) {
              updated[i] = {
                ...updated[i],
                generatedImages: [...existing, newImg],
              };
            }
            break;
          }
        }
        return updated;
      });
    });

    // NOTE: the page-level chat:complete safety guard is intentionally NOT
    // registered here. Registering it on `service` collided with
    // StreamingMessage's own chat:complete listener (UnifiedChatService._on
    // removes any prior listener for the same event on the same instance), so
    // the two overwrote each other and the "spinner + stop forever" guard never
    // actually coexisted with the streaming listener. It now lives in its own
    // effect below, attached directly to the raw socket (see the
    // "chat:complete safety net" effect), so it survives service recreation and
    // does not contend for the service's single per-event slot.

    return () => {
      service.cleanup();
      setUnifiedChatService((prev) => (prev === service ? null : prev));
    };
    // IMPORTANT: connectionState is deliberately NOT a dependency. The shared
    // socket instance is stable (created once in UnifiedProgressContext), and
    // including connectionState caused this effect's cleanup -> service.cleanup()
    // to strip ALL live chat listeners (chat:thinking / chat:complete) off the
    // socket on every transient connect/disconnect/error/forceReconnect during
    // a run — killing live agent steps and the completion signal until refresh.
    // Reconnect re-join is already handled inside joinSession's internal
    // "connect" listener, so we don't need to recreate on connectionState.
  }, [useUnifiedChat, sessionId, socketRef?.current]);

  // chat:complete safety net — attached directly to the raw socket, independent
  // of the recreatable UnifiedChatService. Guarantees the stop/send button
  // (driven solely by isSending) always reverts when a run finishes, even if the
  // per-turn StreamingMessage listener was torn down or never attached. Uses
  // socket.on/off directly (NOT UnifiedChatService._on) so it does not contend
  // for the service's single-listener-per-event slot used by StreamingMessage.
  useEffect(() => {
    const socket = socketRef?.current;
    if (!socket || !sessionId) return;
    const handleComplete = (data) => {
      if (!data || data.session_id !== sessionId) return;
      debugLog('[ChatPage] RAW-SOCKET chat:complete safety net: clearing isSending/isStreamingMessage for session=', data.session_id);
      setIsStreamingMessage(false);
      setIsSending(false);
      streamingServiceRef.current = null;
    };
    socket.on("chat:complete", handleComplete);
    return () => {
      socket.off("chat:complete", handleComplete);
    };
  }, [socketRef?.current, sessionId]);

  // When projectId changes (user navigates between projects), load the per-project session
  const prevProjectIdRef = useRef(projectId);
  useEffect(() => {
    if (prevProjectIdRef.current === projectId) return;
    prevProjectIdRef.current = projectId;

    const storageKey = `guaardvark_chat_session_id_${projectId || 'global'}`;
    let storedSessionId = localStorage.getItem(storageKey);

    if (storedSessionId && !/^session_\d+$/.test(storedSessionId)) {
      storedSessionId = null;
    }

    // Session persists until explicitly cleared or new chat created

    if (!storedSessionId) {
      storedSessionId = `session_${Date.now()}`;
      localStorage.setItem(storageKey, storedSessionId);
    }

    setMessages([]);
    historyLoadedRef.current = null;
    _setSessionId(storedSessionId);
  }, [projectId]);

  const updateMessageStatus = useCallback((tempId, updates) => {
    setMessages((prev) =>
      prev.map((msg) => {
        if (msg.tempId === tempId || msg.id === tempId) {
          return { ...msg, ...updates };
        }
        return msg;
      })
    );
  }, []);

  const updateOrchestratorMessage = useCallback((messageId, patch) => {
    setMessages((prev) =>
      prev.map((msg) => (msg.id === messageId ? { ...msg, ...patch } : msg))
    );
  }, []);

  const handleOrchestratorUpdate = useCallback((messageId, payload) => {
    if (payload?.plan) {
      updateOrchestratorMessage(messageId, { orchestratorPlan: payload.plan });
    }
    if (payload?.final_answer) {
      const finalMsgId = `asst_${Date.now()}_final`;
      setMessages((prev) => [...prev, {
        id: finalMsgId,
        role: 'assistant',
        content: payload.final_answer,
        timestamp: new Date().toISOString(),
      }]);
      recordMessage(sessionId, payload.final_answer, 'assistant', {
        messageId: finalMsgId,
        timestamp: new Date().toISOString(),
      });
    }
  }, [sessionId, updateOrchestratorMessage]);

  // --- Lesson Pearls: Begin/End lesson lifecycle ---
  const activeLessonId = useAppStore((s) => s.activeLessonId);
  const setActiveLessonId = useAppStore((s) => s.setActiveLessonId);
  const clearLessonPearls = useAppStore((s) => s.clearLessonPearls);
  const [lessonSummary, setLessonSummary] = useState(null); // { memoryId, title, steps }
  const [lessonBusy, setLessonBusy] = useState(false);

  const handleBeginLesson = useCallback(async () => {
    if (activeLessonId || lessonBusy || !sessionId) return;
    setLessonBusy(true);
    try {
      const res = await fetch(`${BASE_URL}/lessons/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        console.error("Begin lesson failed:", data?.error || res.status);
        return;
      }
      clearLessonPearls();
      setActiveLessonId(data.lesson_id);
    } catch (err) {
      console.error("Begin lesson error:", err);
    } finally {
      setLessonBusy(false);
    }
  }, [activeLessonId, lessonBusy, sessionId, clearLessonPearls, setActiveLessonId]);

  const handleEndLesson = useCallback(async () => {
    if (!activeLessonId || lessonBusy) return;
    setLessonBusy(true);
    const lessonId = activeLessonId;
    try {
      const res = await fetch(`${BASE_URL}/lessons/${lessonId}/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) {
        // Graceful degradation — surface but don't crash. Most common reason:
        // backend restart orphaned the in-memory ACTIVE_LESSONS entry.
        console.warn("End lesson:", data?.error || `HTTP ${res.status}`);
        setActiveLessonId(null);
        clearLessonPearls();
        return;
      }
      setLessonSummary({
        memoryId: data.memory_id,
        title: data.summary?.title || "Lesson",
        steps: data.summary?.steps || [],
        parameters: data.summary?.parameters || [],
      });
      setActiveLessonId(null);
      clearLessonPearls();
    } catch (err) {
      console.error("End lesson error:", err);
      setActiveLessonId(null);
      clearLessonPearls();
    } finally {
      setLessonBusy(false);
    }
  }, [activeLessonId, lessonBusy, setActiveLessonId, clearLessonPearls]);

  const handleNewChat = useCallback(() => {
    const newSessionId = `session_${Date.now()}`;

    // Carry the current session's mode forward. Without this, a "new chat"
    // silently resets to chat-mode even when the user was actively in agent
    // mode, and the next agent-y prompt fails with "I cannot do that".
    const priorMode = useAppStore.getState().getSessionMode(sessionId);
    if (priorMode === "agent") {
      useAppStore.getState().setSessionMode(newSessionId, "agent");
      fetch(`/api/chat-sessions/${encodeURIComponent(newSessionId)}/mode`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "agent" }),
      }).catch(() => {});
    }

    setMessages([]);

    setError('');
    streamingServiceRef.current = null;

    const storageKey = `guaardvark_chat_session_id_${projectId || 'global'}`;
    localStorage.setItem(storageKey, newSessionId);
    sessionStorage.setItem("session_logged_" + newSessionId, "true");
    sessionStorage.setItem("session_continuity_" + newSessionId, Date.now().toString());

    _setSessionId(newSessionId);

    sessionStorage.setItem("context_preservation_" + newSessionId, JSON.stringify({
      initialized: Date.now(),
      messageCount: 0,
      lastActivity: Date.now()
    }));

    registerSession(newSessionId, {
      autoBackup: true,
      preserveContext: true,
      maxHistoryItems: 1000,
      fileGenerationCapable: true
    });

    historyLoadedRef.current = false;

    lastMessageRef.current = null;

    setFileGenPopup({ open: false });

    setUploadModalOpen(false);


    setTimeout(() => {
      if (chatInputRef.current) {
        chatInputRef.current.focus();
      }
    }, 100);
  }, [_setSessionId, projectId, sessionId]);

  const [fileGenPopup, setFileGenPopup] = useState({
    open: false,
    fileData: null,
    originalMessage: null,
  });

  const { speak, ttsEnabled, isPlaying: isAISpeaking } = useVoice();

  // Track whether current in-flight message was voice-initiated (for TTS on streaming complete)
  const pendingVoiceMessageRef = useRef(false);

  const [voiceState, setVoiceState] = useState({
    isListening: false,
    isUserSpeaking: false,
    audioLevels: [],
  });

  const handleVoiceStateChange = useCallback((state) => {
    setVoiceState(state);
  }, []);

  const { activeModel, isLoadingModel, modelError } = useStatus();

  // Cold start: poll model readiness so first message isn't slow
  const [llmReady, setLlmReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const r = await fetch("/api/meta/llm-ready");
        if (r.ok) {
          const d = await r.json();
          if (d.ready) { setLlmReady(true); return; }
        }
      } catch { /* backend not up yet */ }
      if (!cancelled) setTimeout(check, 2000);
    };
    check();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    resourceManager.activate();

    // Re-register process if it was cleaned up (e.g., by React StrictMode unmount cycle)
    if (!resourceManager.processes.has(processId)) {
      resourceManager.processes.set(processId, {
        id: processId,
        type: ProcessType.CHAT_MESSAGE,
        metadata: { projectId, sessionKey },
        resources: new Set(),
        state: {},
        created: Date.now(),
        lastActivity: Date.now()
      });
    }

    return () => {
      resourceManager.cleanupProcess(processId);
    };
  }, [resourceManager, processId, projectId, sessionKey]);

  const detectFileGenerationWithAgent = useCallback(async (message) => {
    if (!useAgentRouting) {
      return null;
    }

    try {
      const routeDecision = await agentRouter.route(message, {
        project_id: projectId,
        session_id: sessionId,
      });

      if (routeDecision && agentRouter.isHighConfidence(routeDecision, 0.6)) {

        if (agentRouter.isAgentLoopRoute(routeDecision)) {
          // Screen/desktop automation and agent-screen sessions must use unified
          // chat (Gemma4 direct → ACS), not legacy route-and-execute.
          if (!agentRouter.shouldUseLegacyAgentLoop(routeDecision, sessionId)) {
            debugLog(
              "[ChatPage] Agent loop route deferred to unified chat:",
              routeDecision.reasoning,
            );
            return null;
          }
          return {
            isAgentLoopRequest: true,
            isCSVRequest: false,
            isCodeRequest: false,
            routeDecision,
            confidence: routeDecision.confidence,
            reasoning: routeDecision.reasoning,
          };
        }

        return agentRouter.toDetectionFormat(routeDecision);
      }

      return null;
    } catch (err) {
      console.warn("AGENT_ROUTER: Backend routing failed, falling back to local:", err);
      return null;
    }
  }, [useAgentRouting, agentRouter, projectId, sessionId]);

  // De-hardcoded: delegate fully to useAgentRouter (which calls backend /tools/route, itself bridging to AgentBrain per unification).
  // This leans on the architecture (AgentBrain tiers + memory/lessons/STA + session mode flags) instead of local regex arrays.
  // Legacy patterns removed; router + toDetectionFormat provides the shape. If router low-conf or disabled, minimal fallback.
  // See approved plan + useAgentRouter.js + slashCommandHandlers (mode drives agent_screen_active).
  // detectFileGeneration (legacy neutral stub) removed — file-gen detection is fully
  // delegated to detectFileGenerationWithAgent / the agentRouter. See send logic below.

  useEffect(() => {
    const initializeSession = async () => {
      const restoredSession = restoreSessionFromBackup(sessionId);
      if (restoredSession) {
        // Restored session is already wired; nothing to do
      } else {
        registerSession(sessionId, {
          autoBackup: true,
          preserveContext: true,
          maxHistoryItems: 1000,
          fileGenerationCapable: true
        });
      }

      const _contextRestoration = restoreConversationContext(sessionId);
    };

    initializeSession();
  }, [sessionId]);

  useEffect(() => {
    const loadHistory = async () => {
      const currentKey = `${sessionId}_${projectId}`;
      if (
        historyLoadedRef.current === currentKey || historyLoadingRef.current
      ) {
        return;
      }

      historyLoadingRef.current = true;
      try {
        const history = await getChatHistory(sessionId, null, 100);
        if (history && Array.isArray(history.messages)) {
          setMessages((currentMessages) => {
            const existingMessageKeys = new Set();
            const recentMessageWindow = 10000;
            const now = Date.now();

            currentMessages.forEach((msg) => {
              const primaryKey = `${msg.role}:${msg.content.trim()}`;
              existingMessageKeys.add(primaryKey);

              const contentHash = msg.content.trim().toLowerCase().substring(0, 100);
              existingMessageKeys.add(`hash:${msg.role}:${contentHash}`);
            });

            const recentMessages = new Set();
            try {
              const recentData = sessionStorage.getItem(`recent_messages_${sessionId}`);
              if (recentData) {
                const parsed = JSON.parse(recentData);
                Object.entries(parsed).forEach(([key, timestamp]) => {
                  if (now - timestamp < recentMessageWindow) {
                    recentMessages.add(key);
                  }
                });
              }
            } catch (e) {
              console.warn("Failed to read recent messages:", e);
            }

            const historyMessages = history.messages
              .filter((historyMsg) => {
                const primaryKey = `${historyMsg.role}:${historyMsg.content.trim()}`;
                const contentHash = historyMsg.content.trim().toLowerCase().substring(0, 100);
                const hashKey = `hash:${historyMsg.role}:${contentHash}`;

                if (existingMessageKeys.has(primaryKey)) {
                  return false;
                }

                if (existingMessageKeys.has(hashKey)) {
                  return false;
                }

                if (recentMessages.has(primaryKey)) {
                  return false;
                }

                return true;
              })
              .map((msg) => hydrateOrchestratorFields({
                ...msg,
                isLocal: false,
                status: "persisted",
                // Hydrate fields that MessageItem reads as top-level props from
                // their persisted form inside extra_data. The backend saves
                // agentThinkingSteps and tool-call steps under extra_data on
                // the LLMMessage row; without this hydration both vanish on
                // hard refresh because MessageItem looks at message.toolCalls /
                // message.agentThinkingSteps directly.
                toolCalls: msg.toolCalls ?? msg.extra_data?.steps,
                agentThinkingSteps: msg.agentThinkingSteps ?? msg.extra_data?.agentThinkingSteps,
                generatedImages: msg.generatedImages ?? msg.extra_data?.generatedImages,
                thinking: msg.thinking ?? msg.extra_data?.thinking,
                truncated: msg.truncated ?? msg.extra_data?.truncated,
                // Note: these hydrated agentThinkingSteps come from persisted DB extra_data (backend drain on agent complete).
                // They render via MessageItem + AgentThinkingTrail but are *not* live-streamed steps.
              }));

            let allMessages = [...currentMessages, ...historyMessages];
            allMessages.sort((a, b) => {
              const timeA = new Date(a.timestamp || 0).getTime();
              const timeB = new Date(b.timestamp || 0).getTime();
              return timeA - timeB;
            });

            if (history.active_plan && history.active_plan_id) {
              allMessages = mergeActivePlanIntoMessages(
                allMessages,
                history.active_plan,
                history.active_plan_id
              );
            }

            return allMessages;
          });
        } else {
          console.warn("ChatPage: invalid history data", history);
          // Don't clear existing messages on invalid response
        }
        historyLoadedRef.current = currentKey;
      } catch (error) {
        console.error("ChatPage: Failed to load chat history:", error);
        // Keep existing messages rather than clearing on error
      } finally {
        historyLoadingRef.current = false;
      }
    };

    loadHistory();
  }, [sessionId, projectId]);

  useEffect(() => {
    const handleChatHistoryCleared = (_event) => {
      setMessages([]);
      historyLoadedRef.current = null;
      setError('');
    };

    window.addEventListener('chatHistoryCleared', handleChatHistoryCleared);

    return () => {
      window.removeEventListener('chatHistoryCleared', handleChatHistoryCleared);
    };
  }, []);

  const handleStop = useCallback(() => {
    resourceManager.cleanupProcess(processId);

    // Salvage whatever the streamer has so far — body text, tool cards,
    // images, and (most importantly) the agent thinking trail. Without this,
    // hitting Stop on step 4-of-10 wipes the entire reasoning trail.
    if (isStreamingMessage && streamingMessageRef.current) {
      const partial = streamingMessageRef.current.getPartialState() || {};
      const hasContent = !!partial.content;
      const hasTools = (partial.toolCalls?.length || 0) > 0;
      const hasSteps = (partial.agentThinkingSteps?.length || 0) > 0;
      const hasImages = (partial.images?.length || 0) > 0;
      const hasThinking = !!partial.thinking;
      debugLog('[ChatPage] handleStop salvage: hasSteps=', hasSteps, 'count=', partial.agentThinkingSteps?.length);
      if (hasContent || hasTools || hasSteps || hasImages || hasThinking) {
        const completedMessage = {
          id: `asst_unified_${Date.now()}_partial`,
          role: "assistant",
          content: partial.content || "",
          toolCalls: partial.toolCalls || [],
          agentThinkingSteps: partial.agentThinkingSteps || [],
          thinkingText: partial.thinkingText || "",
          thinking: partial.thinking || "",
          truncated: partial.truncated === true,
          isUnifiedChat: true,
          timestamp: new Date().toISOString(),
          generatedImages: partial.images || [],
          status: "aborted"
        };
        // Clean up socket listener so a late event doesn't double-post.
        if (unifiedChatService) {
          unifiedChatService.cleanup();
        }
        setMessages((prev) => [...prev, completedMessage]);
      }
    }

    setIsSending(false);
    setIsStreamingMessage(false);
    streamingServiceRef.current = null;
    // Abort the backend chat + kill any running agent task
    fetch(`/api/chat/unified/${sessionId}/abort`, { method: 'POST' }).catch(() => {});
    fetch('/api/agent-control/kill', { method: 'POST' }).catch(() => {});
  }, [resourceManager, processId, sessionId, isStreamingMessage, unifiedChatService]);

  const handleFileGenConfirm = useCallback(async () => {
    if (!fileGenPopup.fileData || !fileGenPopup.originalMessage) return;

    const { fileData, originalMessage } = fileGenPopup;

    try {
      resourceManager.updateDialogState(dialogStateId, {
        open: false,
        processing: true,
        type: fileData.isBulkRequest
          ? "bulk_csv_generation_processing"
          : "file_generation_processing",
      });
    } catch (error) {
      console.error("Error updating dialog state:", error);
    }

    setFileGenPopup({ open: false, fileData: null, originalMessage: null });

    try {
      if (fileData.isBulkRequest) {

        const progressTracker =
          resourceManager.createProgressTracker(processId);

        resourceManager.emitProgress(progressTracker, {
          status: "start",
          message: `Starting bulk CSV generation: ${fileData.quantity || "multiple"
            } entries...`,
          progress: 0,
          processType: "bulk_csv_generation",
        });

        const _result = await generateBulkCSV({
          prompt: originalMessage,
          filename: fileData.filename,
          quantity: fileData.quantity,
        });

        resourceManager.emitProgress(progressTracker, {
          status: "complete",
          message: `Bulk CSV generation initiated successfully`,
          progress: 100,
          processType: "bulk_csv_generation",
        });

        const successMessage = {
          id: `success_${Date.now()}`,
          role: "system",
          content: `Bulk CSV generation started! Generating ${fileData.quantity || "multiple"
            } entries for "${fileData.filename
            }". This may take several minutes to complete.`,
        };
        setMessages((prev) => [...prev, successMessage]);
      } else {
        const result = await managedApiCall(
          processId,
          async (signal) => {
            const progressTracker =
              resourceManager.createProgressTracker(processId);

            resourceManager.emitProgress(progressTracker, {
              status: "start",
              message: `Generating ${fileData.filename}...`,
              progress: 0,
              processType: "file_generation",
            });

            const result = await generateFileFromChat({
              filename: fileData.filename,
              user_instructions: `Using uploaded code files as reference, ${originalMessage}`,
              project_id: projectId || 1,
              // project_root forwarded if present for GUAARDVARK.md etc. (analysis flows)
              signal,
            });

            resourceManager.emitProgress(progressTracker, {
              status: "complete",
              message: `File generated successfully`,
              progress: 100,
              processType: "file_generation",
            });

            return result;
          },
          { timeout: 120000, retries: 1 }
        );

        if (result.error) {
          throw new Error(result.error);
        }

        const successMessage = {
          id: `success_${Date.now()}`,
          role: "system",
          content: `File "${fileData.filename}" generated successfully! You can download it from the outputs folder.`,
        };
        setMessages((prev) => [...prev, successMessage]);
      }
    } catch (error) {
      console.error("File generation error:", error);

      const errorMessage = {
        id: `error_${Date.now()}`,
        role: "system",
        content: `${fileData.isBulkRequest ? "Bulk CSV generation" : "File generation"
          } failed: ${error.message}`,
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      try {
        resourceManager.updateDialogState(dialogStateId, {
          open: false,
          processing: false,
          type: null,
          fileData: null,
          originalMessage: null,
        });
      } catch (error) {
        console.error("Error clearing dialog state:", error);
      }
    }
  }, [
    fileGenPopup,
    projectId,
    resourceManager,
    dialogStateId,
    processId,
    managedApiCall,
  ]);

  const handleFileGenDismiss = useCallback(() => {
    try {
      resourceManager.updateDialogState(dialogStateId, {
        open: false,
        processing: false,
        type: null,
        fileData: null,
        originalMessage: null,
      });
    } catch (error) {
      console.error("Error updating dialog state on dismiss:", error);
    }

    setFileGenPopup({ open: false, fileData: null, originalMessage: null });
  }, [resourceManager, dialogStateId]);

  const _routeMessageByMode = useCallback(
    async (mode, sessionId, inputText, projectId, onDelta, signal) => {
      const { sendChatMessage } = await import("../api");

      switch (mode) {
        case "analyze":
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            mode
          );

        case "quick":
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            mode
          );

        case "filegen":
          setMessages((prev) => [
            ...prev,
            {
              id: `info_${Date.now()}`,
              role: "system",
              content:
                "**File Generation Mode**: Analyzing your request for code/file generation...",
            },
          ]);
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            mode
          );

        case "coding":
          setMessages((prev) => [
            ...prev,
            {
              id: `info_${Date.now()}`,
              role: "system",
              content: "**Coding Mode**: Providing programming assistance...",
            },
          ]);
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            mode
          );

        case "web":
          setMessages((prev) => [
            ...prev,
            {
              id: `info_${Date.now()}`,
              role: "system",
              content:
                "**Web Research Mode**: Searching for current information...",
            },
          ]);
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            mode
          );

        case "data":
          setMessages((prev) => [
            ...prev,
            {
              id: `info_${Date.now()}`,
              role: "system",
              content: "**Data Tools Mode**: Processing data request...",
            },
          ]);
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            mode
          );

        default:
          return await sendChatMessage(
            sessionId,
            inputText,
            projectId,
            onDelta,
            null,
            signal,
            "analyze"
          );
      }
    },
    []
  );

  const handleSendMessage = useCallback(
    async (inputText, file, voiceOptions) => {
      // Allow image analysis messages through even when inputText is empty
      if (!inputText.trim() && !file && !voiceOptions?.isImageAnalysis) return;
      if (isSending) return;


      const messageKey = `${inputText.trim()}_${voiceOptions?.isImageAnalysis ? `image_${Date.now()}` : voiceOptions?.isVoiceMessage ? "voice" : "text"
        }`;
      const now = Date.now();
      const DUPLICATE_WINDOW = 3000;

      let isDuplicate = false;
      if (
        lastMessageRef.current &&
        lastMessageRef.current.key === messageKey &&
        now - lastMessageRef.current.timestamp < DUPLICATE_WINDOW
      ) {
        isDuplicate = true;
      }

      const storageKey = `last_message_${sessionId}`;
      try {
        const storedData = sessionStorage.getItem(storageKey);
        if (storedData) {
          const parsed = JSON.parse(storedData);
          if (
            parsed.key === messageKey &&
            now - parsed.timestamp < DUPLICATE_WINDOW
          ) {
            isDuplicate = true;
          }
        }
      } catch (e) {
        console.warn("Failed to read duplicate prevention data from storage:", e);
      }

      if (isDuplicate) {
        console.warn(
          "DUPLICATE PREVENTION: Ignoring duplicate message within 3s window:",
          messageKey
        );
        return;
      }

      const dedupeData = {
        key: messageKey,
        timestamp: now,
      };
      lastMessageRef.current = dedupeData;

      try {
        sessionStorage.setItem(storageKey, JSON.stringify(dedupeData));

        const recentKey = `recent_messages_${sessionId}`;
        const recentData = sessionStorage.getItem(recentKey);
        const recentMessages = recentData ? JSON.parse(recentData) : {};

        const primaryKey = `user:${inputText.trim()}`;
        recentMessages[primaryKey] = now;

        Object.keys(recentMessages).forEach(key => {
          if (now - recentMessages[key] > 10000) {
            delete recentMessages[key];
          }
        });

        sessionStorage.setItem(recentKey, JSON.stringify(recentMessages));
      } catch (e) {
        console.warn("Failed to store duplicate prevention data:", e);
      }

      const chatMode = voiceOptions?.chatMode || "analyze";

      const messageData = {
        inputText,
        file,
        voiceOptions,
        chatMode,
        sessionId,
        projectId,
      };

      const callProcessMessage = (...args) => processMessageRef.current(...args);

      try {
        const queueResult = await enqueueMessage(messageQueueId, messageData, {
          processor: async (data) => {
            await callProcessMessage(
              data.inputText,
              data.file,
              data.voiceOptions,
              data.chatMode,
              data.sessionId,
              data.projectId
            );
          },
        });

        if (queueResult && queueResult.error) {
          console.error('Message queue returned error:', queueResult.error);
          await callProcessMessage(
            inputText,
            file,
            voiceOptions,
            chatMode,
            sessionId,
            projectId
          );
        }
      } catch (error) {
        console.error('Failed to enqueue message, using direct processing:', error);
        await callProcessMessage(
          inputText,
          file,
          voiceOptions,
          chatMode,
          sessionId,
          projectId
        );
      }
    },
    [sessionId, projectId, messageQueueId, isSending]
  );

  const processMessage = useCallback(
    async (inputText, file, voiceOptions, chatMode, sessionId, projectId) => {
      let userMessageTempId = null;

      const processKey = `process_${inputText.trim()}_${sessionId}_${Date.now()}`;
      const processingStorageKey = `processing_${sessionId}`;

      try {
        const existingProcess = sessionStorage.getItem(processingStorageKey);
        if (existingProcess) {
          const { key: existingKey, timestamp } = JSON.parse(existingProcess);
          const timeDiff = Date.now() - timestamp;

          if (existingKey === `process_${inputText.trim()}_${sessionId}` && timeDiff < 2000) {
            console.warn('PROCESS_DUPLICATE: Blocking duplicate processMessage call within 2s:', {
              key: processKey,
              existingKey,
              timeDiff
            });
            return;
          }
        }

        sessionStorage.setItem(processingStorageKey, JSON.stringify({
          key: `process_${inputText.trim()}_${sessionId}`,
          timestamp: Date.now()
        }));

      } catch (e) {
        console.warn('Failed to check processing state:', e);
      }

      if (inputText && inputText.length > 100000) {
        const errorMessage = {
          id: `error_${Date.now()}`,
          role: "system",
          content: `Message too long: ${inputText.length} characters. Maximum allowed: 100,000 characters.`,
        };
        setMessages((prev) => [...prev, errorMessage]);
        return;
      }

      if (file) {
        setUploadModalOpen(true);
        return;
      }

      if (inputText.trim()) {
        userMessageTempId = `temp_user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const userMessage = {
          tempId: userMessageTempId,
          id: null,
          role: "user",
          content: inputText,
          status: "pending",
          timestamp: new Date().toISOString(),
          isLocal: true,
          sessionId: sessionId,
          claudeFix: {
            messagePreserved: true,
            routingMethod: 'normal_chat',
            contextPreservation: true
          }
        };
        setMessages((prev) => [...prev, userMessage]);

        recordMessage(sessionId, inputText, 'user', {
          messageId: userMessage.tempId,
          status: 'pending',
          systemRouting: 'normal_chat',
          contextPreservation: true,
          timestamp: userMessage.timestamp
        });
      }

      // Allow image analysis through even when inputText is empty
      if (!inputText.trim() && !voiceOptions?.isImageAnalysis) {
        return;
      }

      if (inputText.trim().startsWith('/plan ')) {
        const planRequest = inputText.trim().substring(6);

        setIsSending(true);
        updateMessageStatus(userMessageTempId, { status: "sent" });

        try {
          const response = await createPlan(planRequest, { projectId, sessionId });
          if (response.success && response.plan?.steps?.length > 0) {
            const assistantId = `asst_plan_${response.plan_id}`;
            const planTimestamp = new Date().toISOString();
            setMessages(prev => [...prev, {
              id: assistantId,
              role: 'assistant',
              content: '',
              timestamp: planTimestamp,
              orchestratorPlan: response.plan,
              orchestratorPlanId: response.plan_id,
              messageType: 'orchestrator_plan',
            }]);

            recordMessage(sessionId, 'Orchestration plan', 'assistant', {
              messageId: assistantId,
              userMessageId: userMessageTempId,
              timestamp: planTimestamp,
            });

            if (userMessageTempId) {
              updateMessageStatus(userMessageTempId, { status: "persisted" });
            }

          } else {
            throw new Error(response.error || "Failed to create plan");
          }
        } catch (e) {
          console.error("Orchestrator error:", e);
          setMessages(prev => [...prev, {
            id: `err_${Date.now()}`,
            role: 'system',
            content: `Failed to create plan: ${e.message}`
          }]);
        } finally {
          setIsSending(false);
          try {
            sessionStorage.removeItem(processingStorageKey);
          } catch {
            // sessionStorage may be unavailable; cleanup is best-effort
          }
        }
        return;
      }

      // Lean on useAgentRouter (backend → AgentBrain arch for routing decision using memory/STA/flags).
      // No more local hardcoded patterns (see detectFileGeneration simplification + plan).
      let fileDetection = null;

      // For direct tool calls (e.g. /imagine from slashCommandHandlers using direct_tool),
      // bypass file generation detection entirely. These are handled specially later
      // via direct_tool options in the unified send. Otherwise, /imagine inputText
      // can lead to null fileDetection and crash on .isCSVRequest.
      if (voiceOptions?.direct_tool) {
        fileDetection = { isCSVRequest: false, isCodeRequest: false };
      } else if (useAgentRouting) {
        try {
          fileDetection = await detectFileGenerationWithAgent(inputText);
        } catch (err) {
          console.warn("AGENT_ROUTER: detection failed (falling to unified brain path):", err);
        }
      }
      if (!fileDetection) {
        fileDetection = { isCSVRequest: false, isCodeRequest: false };
      }
      // If no high-conf agent/file from router, fall through to normal unified chat (which hits AgentBrain).
      // detectFileGeneration now neutral (no regex).

      let shouldContinueWithNormalChat = true;

      if (fileDetection?.isAgentLoopRequest) {
        shouldContinueWithNormalChat = false;

        const userMsgId = `user_${Date.now()}`;
        const userMessage = {
          id: userMsgId,
          role: "user",
          content: inputText,
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, userMessage]);

        const agentMsgId = `agent_${Date.now()}`;
        setAgentLoopMessageId(agentMsgId);
        const thinkingMessage = {
          id: agentMsgId,
          role: "assistant",
          content: "Agent is actively reasoning and routing your request...",
          isAgentLoop: true,
          agentLoopStatus: "thinking",
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, thinkingMessage]);
        setAgentLoopExecuting(true);

        try {
          const result = await routeAndExecute(inputText, {
            project_id: projectId,
            session_id: sessionId,
          });


          const agentResult = result?.result?.type === "agent_result"
            ? result.result
            : result?.result || result;

          let content =
            agentResult?.final_answer ||
            agentResult?.error ||
            result?.error;
          if (!content) {
            if (agentResult?.success === false) {
              content = "Agent execution failed (no response from the model).";
            } else {
              content = "Agent execution completed with no response.";
            }
          }
          const screenshotUrls = agentResult?.screenshot_urls || [];
          for (const url of screenshotUrls) {
            content += `\n\n![Screenshot](${url})`;
          }

          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id === agentMsgId) {
                return {
                  id: agentMsgId,
                  role: "assistant",
                  content,
                  timestamp: new Date().toISOString(),
                };
              }
              return msg;
            })
          );
        } catch (agentError) {
          console.error("AGENT_LOOP: Execution failed:", agentError);
          setMessages((prev) =>
            prev.map((msg) => {
              if (msg.id === agentMsgId) {
                return {
                  id: agentMsgId,
                  role: "assistant",
                  content: `Agent execution failed: ${agentError.message}`,
                  timestamp: new Date().toISOString(),
                };
              }
              return msg;
            })
          );
        } finally {
          setAgentLoopExecuting(false);
          setAgentLoopMessageId(null);
        }

        return; // Don't continue with normal chat for agent loop requests
      }

      if (fileDetection && (fileDetection.isCSVRequest || fileDetection.isCodeRequest)) {

        const continuityMarker = preserveContextDuringFileGeneration(sessionId, inputText, fileDetection);

        const contextPreservationMessage = {
          id: `context_${Date.now()}`,
          role: "user",
          content: inputText,
          timestamp: new Date().toISOString(),
          contextPreserved: true,
          fileGenerationAttempted: true,
          continuityMarker: continuityMarker.id
        };
        setMessages((prev) => [...prev, contextPreservationMessage]);

        recordMessage(sessionId, inputText, 'user', {
          fileGenerationTriggered: true,
          fileGenerationData: fileDetection,
          systemRouting: 'file_generation_parallel',
          continuityMarkerId: continuityMarker.id
        });

        const currentState = resourceManager.getDialogState(dialogStateId);
        if (!currentState || !currentState.open) {
          try {
            resourceManager.updateDialogState(dialogStateId, {
              open: true,
              type: fileDetection.isCSVRequest ? "csv_generation" : "code_generation",
              fileData: {
                filename: fileDetection.filename || "generated_file.jsx",
                description: fileDetection.description || "Generated file",
              },
              originalMessage: inputText,
            });

            setFileGenPopup({
              open: true,
              fileData: {
                filename: fileDetection.filename || "generated_file.jsx",
                description: fileDetection.description || "Generated file",
                isBulkRequest: fileDetection.isBulkRequest,
                quantity: fileDetection.quantity,
              },
              originalMessage: inputText,
            });


            shouldContinueWithNormalChat = true;

          } catch (error) {
            console.error("Error opening file generation dialog:", error);
            shouldContinueWithNormalChat = true;
          }
        } else {
          debugLog("File generation dialog already open, allowing normal chat flow");

          const infoMessage = {
            id: `info_${Date.now()}`,
            role: "system",
            content: "File generation is already in progress. I'll continue our conversation while that processes.",
          };
          setMessages((prev) => [...prev, infoMessage]);
          shouldContinueWithNormalChat = true;
        }
      } else {
        shouldContinueWithNormalChat = true;
      }

      if (!shouldContinueWithNormalChat) {
        shouldContinueWithNormalChat = true;
      }

      setIsSending(true);

      if (userMessageTempId) {
        updateMessageStatus(userMessageTempId, { status: "sent" });
      }

      if (
        voiceOptions &&
        voiceOptions.isVoiceMessage &&
        voiceOptions.aiResponse
      ) {
        if (typeof voiceOptions.aiResponse !== 'string' || !voiceOptions.aiResponse.trim()) {
          console.warn("Invalid AI response format, falling back to normal chat");
        } else {
          const assistantMessage = {
            id: `asst_${Date.now()}`,
            role: "assistant",
            content: voiceOptions.aiResponse,
          };
          setMessages((prev) => [...prev, assistantMessage]);

          if (
            ttsEnabled &&
            voiceOptions.aiResponse &&
            voiceOptions.aiResponse.trim() &&
            !voiceOptions.skipTTS
          ) {
            try {
              speak(voiceOptions.aiResponse);
            } catch (ttsError) {
              console.warn("TTS playback failed:", ttsError);
            }
          }

          setIsSending(false);
          return; // Don't send to regular chat API since we already have the response
        }
      }

      // Handle unified chat image analysis (base64 path - response streamed via Socket.IO)
      if (
        voiceOptions &&
        voiceOptions.isImageAnalysis &&
        voiceOptions.imageBase64 &&
        !voiceOptions.analysisResponse
      ) {
        debugLog("Image analysis: routing through unified chat with base64 image");
        // Upgrade existing user message with image data instead of adding a duplicate
        const imageContent = inputText || `Describe this image: ${voiceOptions.imageFileName}`;
        if (userMessageTempId) {
          setMessages((prev) => prev.map((m) =>
            m.tempId === userMessageTempId
              ? { ...m, content: imageContent, imageUrl: voiceOptions.imagePreview, imageFileName: voiceOptions.imageFileName, messageType: "image_upload" }
              : m
          ));
        } else {
          setMessages((prev) => [...prev, {
            id: `user_${Date.now()}`,
            role: "user",
            content: imageContent,
            imageUrl: voiceOptions.imagePreview,
            imageFileName: voiceOptions.imageFileName,
            messageType: "image_upload",
          }]);
        }
        // Don't return — fall through to unified chat flow which sends imageBase64
      }

      // Handle legacy image analysis (pre-generated response from /vision/analyze)
      if (
        voiceOptions &&
        voiceOptions.isImageAnalysis &&
        voiceOptions.analysisResponse
      ) {
        // Upgrade existing user message with image data instead of adding a duplicate
        const legacyContent = `[Image uploaded: ${voiceOptions.imageFileName}]${inputText ? ` ${inputText}` : ""}`;
        const legacyFileName = voiceOptions.permanentFileName || voiceOptions.imageFileName;
        if (userMessageTempId) {
          setMessages((prev) => prev.map((m) =>
            m.tempId === userMessageTempId
              ? { ...m, content: legacyContent, imageUrl: voiceOptions.imageUrl, imageFileName: legacyFileName, messageType: "image_upload" }
              : m
          ));
        } else {
          setMessages((prev) => [...prev, {
            id: `user_${Date.now()}`,
            role: "user",
            content: legacyContent,
            imageUrl: voiceOptions.imageUrl,
            imageFileName: legacyFileName,
            messageType: "image_upload",
          }]);
        }

        const assistantMessage = {
          id: `asst_${Date.now()}`,
          role: "assistant",
          content: voiceOptions.analysisResponse,
          imageAnalysis: true,
          analysisDetails: voiceOptions.analysisDetails,
          relatedImageUrl: voiceOptions.imageUrl,
        };
        setMessages((prev) => [...prev, assistantMessage]);

        if (
          ttsEnabled &&
          voiceOptions.analysisResponse &&
          voiceOptions.analysisResponse.trim()
        ) {
          speak(voiceOptions.analysisResponse);
        }

        setIsSending(false);
        return; // Don't send to regular chat API since we already have the response
      }

      if (voiceOptions && voiceOptions.isVoiceMessage && !voiceOptions.aiResponse) {
        // Voice transcription without pre-generated response — send through normal chat pipeline.
        // Mark as voice-initiated so TTS fires when the streaming response completes.
        pendingVoiceMessageRef.current = true;
      }

      const socketIsLive = !!(socketRef?.current && socketRef.current.connected);
      const canUseUnified = useUnifiedChat && (unifiedChatService || socketIsLive);
      if (canUseUnified) {
        debugLog('Chat path: using unified chat', {
          sessionId,
          socketConnected: Boolean(unifiedChatService || socketIsLive),
        });
        debugLog('[ChatPage] SET isStreamingMessage(true) + will ensure service before send (normal path)');
        setIsSending(true);
        setIsStreamingMessage(true);

        if (userMessageTempId) {
          updateMessageStatus(userMessageTempId, { status: "sent" });
        }

        try {
          let modifiedInputText = inputText;
          if (fileDetection && (fileDetection.isCSVRequest || fileDetection.isCodeRequest)) {
            modifiedInputText += "\n\n[SYSTEM NOTE: The frontend has successfully intercepted this file generation request and opened the dedicated File Generation popup for the user. Acknowledge this briefly, do not say you cannot generate files, and do not attempt to generate the file yourself.]";
          }
          // Pass image data through unified chat if present
          const imageBase64 = voiceOptions?.imageBase64 || null;
          const isVoice = !!(voiceOptions?.isVoiceMessage);

          // Lazily ensure a service wrapper exists so listeners (thinking/tool/stream) get attached
          // even if the creation effect hasn't committed the state yet.
          let serviceToUse = unifiedChatService;
          if (!serviceToUse && socketRef?.current) {
            debugLog('[ChatPage] LAZY ensure service (normal path): creating fresh + joinSession for', sessionId);
            serviceToUse = new UnifiedChatService(socketRef.current);
            serviceToUse.joinSession(sessionId);
            setUnifiedChatService(serviceToUse);
          }
          // Stash in the streaming ref so the {isStreamingMessage && <StreamingMessage chatService=... />}
          // render below always receives a valid service instance for this turn (even on the same
          // render frame where the setState above hasn't flushed yet). This is required for live
          // agent thinking steps to be received and for onComplete to fire and clear the spinner/stop.
          streamingServiceRef.current = serviceToUse || unifiedChatService || null;
          debugLog('[ChatPage] normal unified path PRE-SEND: setIsStreamingMessage(true) already done; serviceToRender=', !!(unifiedChatService || streamingServiceRef.current), 'about to await sendMessage (backend may emit thinking before mount completes)');
          console.debug(`[SOCKET-CHAT] PRE-SEND (normal) session=${sessionId} -- HTTP returns fast; agent thread can emit chat:* before this client's room join is processed on server`);

          // Mitigate the core race: give the 'chat:join' (emitted in joinSession above) a brief window to be
          // processed into join_room on the server before we fire the HTTP that starts the background thread
          // (which can immediately begin emitting chat:thinking / agent_loop steps to the room).
          // We still have the page-level guard + streamer onThinking, but this reduces lost early steps.
          await new Promise((resolve) => {
            const t = setTimeout(resolve, 200);
            try {
              (serviceToUse || unifiedChatService).onJoined((d) => {
                if (d && d.session_id === sessionId) {
                  clearTimeout(t);
                  debugLog('[SOCKET-CHAT] chat:joined ack before send (race reduced for this turn)');
                  resolve();
                }
              });
            } catch { resolve(); }
          });

          const directToolOptions = {};
          if (voiceOptions?.direct_tool) {
            directToolOptions.direct_tool = voiceOptions.direct_tool;
            directToolOptions.direct_tool_params = voiceOptions.direct_tool_params || {};
            if (voiceOptions.slash_command) {
              directToolOptions.slash_command = voiceOptions.slash_command;
            }
            if (voiceOptions.slash_args) {
              directToolOptions.slash_args = voiceOptions.slash_args;
            }
          }

          // Support for project folder analysis (e.g. "analyze /path/to/site and suggest CSS improvements")
          // Send project_root so backend auto-loads GUAARDVARK.md and injects rich context.
          // This makes the feature (project analysis, build.py review, CSS suggestions, initiative)
          // work in GUI chat as well as CLI.
          let sendOptions = {
            use_rag: true,
            chat_mode: chatMode,
            project_id: projectId,
            ...directToolOptions,
          };
          // Heuristic: if message mentions an absolute path, treat as project_root for context loading
          const pathMatch = modifiedInputText.match(/(\/[^\s'"]+\/[^\s'"]*)/);
          if (pathMatch && (modifiedInputText.toLowerCase().includes('analyze') || modifiedInputText.toLowerCase().includes('suggest') || modifiedInputText.toLowerCase().includes('review'))) {
            sendOptions.project_root = pathMatch[1];
          }
          const _ackResult = await (serviceToUse || unifiedChatService).sendMessage(sessionId, modifiedInputText, sendOptions, imageBase64, isVoice);
          console.debug(`[SOCKET-CHAT] POST-SEND ack (normal) session=${sessionId}`);
        } catch (unifiedError) {
          console.error("UNIFIED_CHAT: Failed to send:", unifiedError);
          setIsStreamingMessage(false);
          setIsSending(false);
          streamingServiceRef.current = null;
          setMessages((prev) => [
            ...prev,
            {
              id: `error_${Date.now()}`,
              role: "system",
              content: `Error: ${unifiedError.message}`,
            },
          ]);
        } finally {
          try {
            sessionStorage.removeItem(processingStorageKey);
          } catch (e) {
            console.warn("Failed to clear processing state:", e);
          }
        }
        return; // Don't fall through to legacy chat flow
      }

      if (useAppStore.getState().getSessionMode(sessionId) === "agent") {
        // Agent mode is strict: it needs the unified/tools path (thinking, tool calls,
        // agent_screen_active, etc.). Do not silently fall back to plain enhanced-chat.
        //
        // Be resilient to transient disconnects / StrictMode / late socket init:
        // 1. Force a reconnect attempt.
        // 2. Lazily create the service if the socket object is present.
        // 3. Short retry (a couple of times) before giving the user the error message.
        console.warn("CHAT_PATH: Agent mode detected; ensuring unified chat path", {
          useUnifiedChat,
          hasService: !!unifiedChatService,
          connectionState,
          sessionId,
        });

        if (typeof forceReconnect === "function") {
          try { forceReconnect(); } catch (_) { /* best-effort reconnect */ }
        }

        // Give the reconnection a tiny moment to start (socket.io will handle the rest).
        await new Promise((r) => setTimeout(r, 150));

        // Re-evaluate liveness and ensure a service wrapper.
        const refreshedSocketIsLive = !!(socketRef?.current && socketRef.current.connected);
        let serviceToUse = unifiedChatService;
        if (!serviceToUse && socketRef?.current) {
          debugLog('[ChatPage] AGENT RECOVERY ensure service: creating fresh UnifiedChatService + join for', sessionId);
          serviceToUse = new UnifiedChatService(socketRef.current);
          serviceToUse.joinSession(sessionId);
          setUnifiedChatService(serviceToUse);
        }
        streamingServiceRef.current = serviceToUse || unifiedChatService || null;

        const canRetryUnified = useUnifiedChat && (serviceToUse || refreshedSocketIsLive);

        if (canRetryUnified && serviceToUse) {
          // One more attempt through the unified path.
          try {
            let modifiedInputText = inputText;
            if (fileDetection && (fileDetection.isCSVRequest || fileDetection.isCodeRequest)) {
              modifiedInputText += "\n\n[SYSTEM NOTE: The frontend has successfully intercepted this file generation request and opened the dedicated File Generation popup for the user. Acknowledge this briefly, do not say you cannot generate files, and do not attempt to generate the file yourself.]";
            }
            const imageBase64 = voiceOptions?.imageBase64 || null;
            const isVoice = !!(voiceOptions?.isVoiceMessage);

            debugLog('[ChatPage] AGENT RECOVERY PRE-SEND (CRITICAL): await sendMessage *before* setIsStreamingMessage(true). Agent backend may start emitting chat:thinking (source=agent_loop) immediately after HTTP ack.');
            console.debug(`[SOCKET-CHAT] PRE-SEND (agent retry after forceReconnect) session=${sessionId}`);

            // Same small joined window for the recovery path (very common after transient disconnects/StrictMode).
            await new Promise((resolve) => {
              const t = setTimeout(resolve, 200);
              try {
                serviceToUse.onJoined((d) => {
                  if (d && d.session_id === sessionId) {
                    clearTimeout(t);
                    debugLog('[SOCKET-CHAT] chat:joined ack (agent retry path)');
                    resolve();
                  }
                });
              } catch { resolve(); }
            });

            // Set streaming flags BEFORE the send (symmetric to the normal unified
            // path). This ensures isStreamingMessage becomes true (and the
            // <StreamingMessage chatService=...> render + its useEffect that registers
            // chatService.onThinking for agent_loop steps) happens as early as possible,
            // shrinking the window where the backend can emit the first source=agent_loop
            // chat:thinking before the live listener is attached. The joined-wait above +
            // streamingServiceRef + the raw-socket chat:complete guard provide additional
            // safety. NOTE: there is exactly ONE sendMessage call here — a previous edit
            // left a duplicate send above this block, which caused the agent-recovery path
            // to submit the same message twice (two backend runs). Removed.
            debugLog('[ChatPage] AGENT RECOVERY: setting isStreamingMessage(true) + ref BEFORE the single send (earlier attach of onThinking for agent steps)');
            setIsStreamingMessage(true);
            streamingServiceRef.current = serviceToUse;
            setIsSending(true);

            debugLog('[ChatPage] AGENT RECOVERY PRE-SEND (after flags): awaiting sendMessage. Early events should now have a listener if the service is live.');
            await serviceToUse.sendMessage(sessionId, modifiedInputText, {
              use_rag: true,
              chat_mode: chatMode,
              project_id: projectId,
            }, imageBase64, isVoice);
            console.debug(`[SOCKET-CHAT] POST-SEND ack (agent retry) session=${sessionId}`);

            try { sessionStorage.removeItem(processingStorageKey); } catch (_) { /* storage may be unavailable */ }
            return;
          } catch (retryErr) {
            console.warn("UNIFIED_CHAT (agent retry): still failing after reconnect attempt:", retryErr?.message);
            // fall through to the error UI below
          }
        }

        // Only now show the guidance message. Agent mode really shouldn't use the no-tools path.
        const reason = useUnifiedChat
          ? "the unified chat socket is not connected (recovery attempted)"
          : "unified chat is disabled";
        const content =
          `Agent mode requires unified chat tools, but ${reason}. ` +
          "Reconnect (or reload) or type `/chat` to return to normal chat mode.";

        console.warn("CHAT_PATH: Blocked enhanced-chat fallback in agent mode (after recovery attempt)", {
          useUnifiedChat,
          hasService: !!unifiedChatService,
          connectionState,
          sessionId,
        });

        if (userMessageTempId) {
          updateMessageStatus(userMessageTempId, { status: "error" });
        }
        setMessages((prev) => [
          ...prev,
          {
            id: `agent_mode_error_${Date.now()}`,
            role: "system",
            content,
          },
        ]);
        try {
          sessionStorage.removeItem(processingStorageKey);
        } catch (e) {
          console.warn("Failed to clear processing state:", e);
        }
        setIsSending(false);
        return;
      }

      console.warn('CHAT_PATH: Falling back to ENHANCED chat (NO tools)', {
        useUnifiedChat,
        hasService: !!unifiedChatService,
        connectionState,
        sessionId,
      });

      const assistantId = `asst_${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        { id: assistantId, role: "assistant", content: "" },
      ]);

      try {
        let result = null;
        let apiCallSucceeded = false;
        const maxRetries = 3;

        for (let attempt = 1; attempt <= maxRetries && !apiCallSucceeded; attempt++) {
          try {

            result = await managedApiCall(
              processId,
              async (signal) => {

                try {
                  const contextData = JSON.parse(sessionStorage.getItem("context_preservation_" + sessionId) || "{}");
                  contextData.messageCount = (contextData.messageCount || 0) + 1;
                  contextData.lastActivity = Date.now();
                  sessionStorage.setItem("context_preservation_" + sessionId, JSON.stringify(contextData));
                } catch (e) {
                  console.warn("Failed to update context tracking:", e);
                }

                let modifiedInputText = inputText;
                if (fileDetection && (fileDetection.isCSVRequest || fileDetection.isCodeRequest)) {
                  modifiedInputText += "\n\n[SYSTEM NOTE: The frontend has successfully intercepted this file generation request and opened the dedicated File Generation popup for the user. Acknowledge this briefly, do not say you cannot generate files, and do not attempt to generate the file yourself.]";
                }

                return await sendChatMessage(
                  sessionId,
                  modifiedInputText,
                  projectId,
                  (delta) => {
                    setMessages((prev) =>
                      prev.map((m) =>
                        m.id === assistantId
                          ? { ...m, content: m.content + delta }
                          : m
                      )
                    );
                  },
                  signal
                );
              },
              { timeout: 120000, retries: 1 }
            );

            apiCallSucceeded = true;

          } catch (apiError) {
            console.warn(`API call attempt ${attempt} failed:`, apiError);

            if (attempt === maxRetries) {
              result = {
                success: true,
                content: `I apologize, but I'm experiencing technical difficulties processing your message: "${inputText.substring(0, 100)}${inputText.length > 100 ? '...' : ''}"\n\nThe system encountered connectivity issues, but your message has been preserved in our conversation history. Please try asking your question again, or rephrase it if you'd like.`,
                enhanced: false,
                fallback: true,
                technicalError: true
              };
              apiCallSucceeded = true;
            } else {
              await new Promise(resolve => setTimeout(resolve, 1000 * attempt));
            }
          }
        }


        if (result?.content && result.success) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                  ...m,
                  content: result.content,
                  claudeFix: {
                    responseReceived: true,
                    enhanced: result.enhanced,
                    fallback: result.fallback,
                    technicalError: result.technicalError,
                    sessionId: sessionId,
                    contextPreserved: true
                  }
                }
                : m
            )
          );

          recordMessage(sessionId, result.content, 'assistant', {
            messageId: assistantId,
            enhanced: result.enhanced,
            fallback: result.fallback,
            userMessageId: result.userMessageId,
            contextPreservation: true,
            timestamp: new Date().toISOString()
          });

          if (userMessageTempId) {
            updateMessageStatus(userMessageTempId, {
              status: "persisted",
              id: result.userMessageId || null
            });

            recordMessage(sessionId, inputText, 'user', {
              messageId: userMessageTempId,
              status: 'persisted',
              systemRouting: 'normal_chat',
              contextPreservation: true,
              persistedId: result.userMessageId
            });
          }
        }
        if (result.warning) {
          const warningText =
            typeof result.warning === "string"
              ? result.warning
              : JSON.stringify(result.warning);
          setMessages((prev) => [
            ...prev,
            {
              id: `warn_${Date.now()}`,
              role: "system",
              content: `Warning: ${warningText}`,
            },
          ]);
        }

        if (ttsEnabled && result.content && result.content.trim() && !inputText.trim().startsWith('/')) {
          speak(result.content);
        }
      } catch (error) {
        console.error("Failed to send message:", error);

        if (
          error.message === "Request was aborted" ||
          error.message === "Request was stopped by user"
        ) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, role: "system", content: "Request stopped by user" }
                : m
            )
          );
        } else {
          const errorText =
            typeof error.message === "string"
              ? error.message
              : JSON.stringify(error.message);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, role: "system", content: `Error: ${errorText}` }
                : m
            )
          );
        }
      } finally {
        setIsSending(false);

        try {
          sessionStorage.removeItem(processingStorageKey);
        } catch (e) {
          console.warn('Failed to clear processing state:', e);
        }
      }
    },
    [
      sessionId,
      projectId,
      ttsEnabled,
      speak,
      useUnifiedChat,
      unifiedChatService,
    ]
  );

  processMessageRef.current = processMessage;

  useEffect(() => {
    if (!isSending) {
      chatInputRef.current?.focus();
    }
  }, [isSending]);

  const handleUploadComplete = useCallback((_uploadResult) => {
    const successMessage = {
      id: `success_${Date.now()}`,
      role: "system",
      content: `File uploaded successfully! Indexing has been started in the background. Check the System Dashboard to monitor progress.`,
    };
    setMessages((prev) => [...prev, successMessage]);

  }, []);

  return (
    <PageLayout variant="fullscreen" noPadding>
    <Paper
      sx={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        overflow: "hidden",
        position: "relative",
      }}
    >
      {}
      <BackgroundWaveform
        isVoiceChatActive={voiceState.isListening}
        isUserSpeaking={voiceState.isUserSpeaking}
        isAISpeaking={isAISpeaking}
        micAudioLevels={voiceState.audioLevels}
        fullWindow
      />
      <Box
        sx={{
          p: 2,
          borderBottom: 1,
          borderColor: "divider",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
          <IconButton size="small" onClick={() => navHistory(-1)} sx={{ opacity: 0.5, "&:hover": { opacity: 1 } }}>
            <ChevronLeftIcon fontSize="small" />
          </IconButton>
          <IconButton size="small" onClick={() => navHistory(1)} sx={{ opacity: 0.5, "&:hover": { opacity: 1 }, mr: 1.5 }}>
            <ChevronRightIcon fontSize="small" />
          </IconButton>
          <Typography variant="h5" component="h1" sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            Chat
            {messages.length > 0 && (
              <Chip
                label={`${messages.length} msg${messages.length !== 1 ? 's' : ''}`}
                size="small"
                variant="outlined"
                sx={{ height: 20, fontSize: '0.7rem' }}
              />
            )}
          </Typography>
        </Box>

        {}
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          {!activeLessonId ? (
            <Tooltip title="Begin Lesson — thumbs-ups will string into a lesson summary">
              <span>
                <IconButton
                  onClick={handleBeginLesson}
                  disabled={lessonBusy || !sessionId}
                  sx={{
                    width: 40,
                    height: 40,
                    color: "text.secondary",
                    transition: "all 0.2s ease-in-out",
                  }}
                >
                  <SchoolIcon />
                </IconButton>
              </span>
            </Tooltip>
          ) : (
            <Tooltip title="End Lesson — distill pearls into an editable summary">
              <span>
                <IconButton
                  onClick={handleEndLesson}
                  disabled={lessonBusy}
                  sx={{
                    width: 40,
                    height: 40,
                    color: "error.main",
                    transition: "all 0.2s ease-in-out",
                  }}
                >
                  <StopCircleIcon />
                </IconButton>
              </span>
            </Tooltip>
          )}
          <Tooltip title="All chats">
            <span>
              <IconButton
                onClick={() => setPreviousChatsOpen(true)}
                sx={{
                  width: 40,
                  height: 40,
                  color: "text.secondary",
                  transition: "all 0.2s ease-in-out",
                }}
              >
                <HistoryIcon />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Start a new chat session">
            <span>
              <IconButton
                onClick={handleNewChat}
                sx={{
                  width: 20,
                  height: 20,
                  color: "text.secondary",
                  "&:active": {
                    transform: "scale(0.95)",
                  },
                  transition: "all 0.2s ease-in-out",
                }}
              >
                <AddIcon sx={{ fontSize: 18 }} />
              </IconButton>
            </span>
          </Tooltip>

          <Tooltip
            title={`Active Model: ${isLoadingModel ? "Loading..." : modelError ? "Error fetching model" : activeModel || "N/A"}`}
          >
            <span>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                Model:{" "}
                {isLoadingModel
                  ? "Loading..."
                  : modelError
                    ? "Error"
                    : activeModel || "Default"}
              </Typography>
            </span>
          </Tooltip>
        </Box>
      </Box>

      {!llmReady && (
        <Alert severity="info" sx={{ mx: 2, mb: 1 }}>
          Model is loading into GPU memory... Chat will be ready in a moment.
        </Alert>
      )}

      {/* Agent screen floating panel renders via portal from the chip in the header */}
      {error && <Alert severity="error" sx={{ mx: 2, mb: 1 }}>{error}</Alert>}

      {}

      <MessageList
        messages={messages}
        sessionId={sessionId}
        onOrchestratorUpdate={handleOrchestratorUpdate}
      />

      {}
      {(() => {
        const renderGuard = isStreamingMessage && (unifiedChatService || streamingServiceRef.current || (socketRef?.current && socketRef.current.connected));
        const chatSvcForChild = unifiedChatService || streamingServiceRef.current;
        if (isStreamingMessage) {
          debugLog('[ChatPage] RENDER DECISION: isStreamingMessage=', isStreamingMessage, 'guard=', renderGuard, 'chatSvcForChild=', !!chatSvcForChild, 'unifiedState=', !!unifiedChatService, 'ref=', !!streamingServiceRef.current, 'socketLive=', !!(socketRef?.current && socketRef.current.connected));
          if (!chatSvcForChild && renderGuard) {
            debugLog('[ChatPage] WARNING: render guard true from socket-only, but passing falsy chatService to StreamingMessage -> its useEffect will skip listener attach entirely (no onThinking for agent_loop steps)!');
          }
        }
        return renderGuard;
      })() && (
        <Box sx={{ px: 2, py: 1 }}>
          <StreamingMessage
            ref={streamingMessageRef}
            chatService={unifiedChatService || streamingServiceRef.current}
            sessionId={sessionId}
            onComplete={(result) => {
              debugLog('[ChatPage] Streaming onComplete handler (from child): agentSteps in result=', (result.agentThinkingSteps||[]).length, 'will append to messages and clear isStreaming');
              setIsStreamingMessage(false);
              setIsSending(false);
              streamingServiceRef.current = null;

              const hasAgentTrail = (result.agentThinkingSteps?.length || 0) > 0;
              if (result.content || result.generatedImages?.length > 0 || result.toolCalls?.length > 0 || hasAgentTrail || result.thinking) {
                debugLog('[ChatPage] APPENDING completed unified message with live agentThinkingSteps.length=', (result.agentThinkingSteps||[]).length, ' (these came from StreamingMessage onThinking appends + ref read on complete)');
                const completedMessage = {
                  id: `asst_unified_${Date.now()}`,
                  role: "assistant",
                  content: result.content || "",
                  toolCalls: result.toolCalls || [],
                  isUnifiedChat: true,
                  timestamp: new Date().toISOString(),
                  generatedImages: result.generatedImages || [],
                  thinkingText: result.thinkingText || "",
                  agentThinkingSteps: result.agentThinkingSteps || [],
                  thinking: result.thinking || "",
                  truncated: result.truncated === true,
                  iterations: result.iterations || 0,
                  budget: result.budget || budgetTelemetry,  // Phase 2.1 surface budget telemetry
                };
                if (result.budget) setBudgetTelemetry(result.budget);
                setMessages((prev) => [...prev, completedMessage]);

                // TTS for voice-initiated messages — extract only the conversational part
                if (pendingVoiceMessageRef.current && ttsEnabled && result.content.trim()) {
                  pendingVoiceMessageRef.current = false;
                  try {
                    const ttsText = extractSpeakableText(result.content, result.generatedImages);
                    speak(ttsText);
                  } catch (ttsError) {
                    console.warn("Voice TTS playback failed:", ttsError);
                  }
                }
              }
              pendingVoiceMessageRef.current = false;

              // Clean up old service listeners BEFORE creating new one.
              // Without this, old listeners stay registered on the shared socket,
              // causing duplicate responses on the next message.
              if (unifiedChatService) {
                debugLog('[ChatPage] POST-STREAM-COMPLETE: cleanup old unified, creating new service + re-attach onComplete guard for next turn');
                unifiedChatService.cleanup();
              }
              if (socketRef?.current) {
                const newService = new UnifiedChatService(socketRef.current);
                newService.joinSession(sessionId);
                // Re-attach the page-level complete guard (and keep behavior for late images
                // if we also re-wired onImage here). The creation useEffect only triggers on
                // socketRef/connectionState changes, not our post-complete manual recreate.
                newService.onComplete((data) => {
                  debugLog('[ChatPage] MANUAL post-complete newService onComplete guard attached');
                  if (data.session_id !== sessionId) return;
                  setIsStreamingMessage(false);
                  setIsSending(false);
                  streamingServiceRef.current = null;
                  if (data.response) {
                    setMessages((prev) => {
                      const last = prev[prev.length - 1];
                      if (last && last.role === "assistant" && (last.content || "").trim() === (data.response || "").trim()) {
                        return prev;
                      }
                      return [
                        ...prev,
                        {
                          id: `asst_guard_${Date.now()}`,
                          role: "assistant",
                          content: data.response || "",
                          isUnifiedChat: true,
                          timestamp: new Date().toISOString(),
                        },
                      ];
                    });
                  }
                });
                setUnifiedChatService(newService);
              }
            }}
          />
        </Box>
      )}

      {isSending && !isStreamingMessage && (
        <Typography sx={{ p: 2, fontStyle: "italic" }} align="center">
          Assistant is typing...
        </Typography>
      )}
      {showBudget && (
        <Box sx={{ p: 1, bgcolor: 'warning.light', fontSize: '0.8em', border: '1px solid #ff9800' }}>
          [Phase 2.1] Agent Budget Surface: {budgetTelemetry.remaining || '?'} / {budgetTelemetry.total || 20} remaining (from TierTelemetry) — using ENTITY patterns from queryClassifier for richer context to tighten budget awareness.
        </Box>
      )}
      <ChatInput
        onSendMessage={handleSendMessage}
        onStop={handleStop}
        disabled={isSending}
        sessionId={sessionId}
        projectId={projectId}
        ref={chatInputRef}
        onVoiceStateChange={handleVoiceStateChange}
        onAddMessage={(msg) => setMessages((prev) => [...prev, { ...msg, id: msg.tempId || `msg_${Date.now()}` }])}
        onUpdateMessage={(tempId, updates) => setMessages((prev) =>
          prev.map((m) => (m.tempId === tempId || m.id === tempId) ? { ...m, ...updates } : m)
        )}
        onClearMessages={() => setMessages([])}
        onPlanCreated={(plan, planId) => {
          const assistantId = `asst_plan_${planId}`;
          setMessages((prev) => [...prev, {
            id: assistantId,
            role: 'assistant',
            content: '',
            timestamp: new Date().toISOString(),
            orchestratorPlan: plan,
            orchestratorPlanId: planId,
            messageType: 'orchestrator_plan',
          }]);
        }}
      />
      <FileGenPopup
        open={fileGenPopup.open}
        onConfirm={handleFileGenConfirm}
        onDismiss={handleFileGenDismiss}
        fileData={fileGenPopup.fileData}
        useRAG={true}
      />

      <UnifiedUploadModal
        open={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        onUploadComplete={handleUploadComplete}
        sessionId={sessionId}
        projectId={projectId}
        mode="chat"
      />

      <ChatSessionDrawer
        open={previousChatsOpen}
        onClose={() => setPreviousChatsOpen(false)}
        projectId={projectId}
        currentSessionId={sessionId}
        onNewChat={handleNewChat}
        onSelectSession={(selectedSessionId) => {
          if (selectedSessionId === null) {
            handleNewChat();
            return;
          }
          const storageKey = `guaardvark_chat_session_id_${projectId || 'global'}`;
          localStorage.setItem(storageKey, selectedSessionId);
          setMessages([]);
          historyLoadedRef.current = false;
          historyLoadingRef.current = false;
          sessionStorage.removeItem(`recent_messages_${selectedSessionId}`);
          _setSessionId(selectedSessionId);
        }}
      />
    </Paper>

    {/* Lesson pearls — floats over the chat while a lesson is active */}
    <LessonPearlsFloater />

    {/* Post-End Lesson summary with editable steps */}
    <LessonSummaryModal
      open={!!lessonSummary}
      onClose={() => setLessonSummary(null)}
      memoryId={lessonSummary?.memoryId}
      initialTitle={lessonSummary?.title}
      initialSteps={lessonSummary?.steps}
      initialParameters={lessonSummary?.parameters}
      onSaved={() => setLessonSummary(null)}
    />
    </PageLayout>
  );
};

export default ChatPage;
