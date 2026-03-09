
import { Box, Paper, Typography, Tooltip, IconButton } from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import HistoryIcon from "@mui/icons-material/History";
import PreviousChatsModal from "../components/chat/PreviousChatsModal";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
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

import OrchestratorPlanView from "../components/orchestrator/OrchestratorPlanView";
import { createPlan } from "../api/orchestratorService";

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

  const resourceManager = getResourceManager();

  const sessionKey = `chat_session_${projectId || 'default'}`;

  const [processId] = useState(() => {
    const storageKey = `chat_process_${projectId || 'default'}`;

    const existingProcesses = Array.from(resourceManager.processes.entries())
      .filter(([id, process]) =>
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
          console.warn(`STRICT MODE PROTECTION: Reusing recent process: ${storedId} (age: ${Math.round((Date.now() - timestamp) / 1000)}s)`);
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
  const { socketRef, connectionState } = useUnifiedProgress();
  const [unifiedChatService, setUnifiedChatService] = useState(null);
  const [isStreamingMessage, setIsStreamingMessage] = useState(false);

  const [, setAgentLoopExecuting] = useState(false);
  const [, setAgentLoopMessageId] = useState(null);

  const [messages, setMessages] = useState([]);
  const [error, setError] = useState('');
  const [orchestratorPlan, setOrchestratorPlan] = useState(null);
  const [orchestratorPlanId, setOrchestratorPlanId] = useState(null);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [previousChatsOpen, setPreviousChatsOpen] = useState(false);
  const [sessionId, _setSessionId] = useState(() => {
    // Per-project session IDs: each project gets its own chat session
    const storageKey = `llamax_chat_session_id_${projectId || 'global'}`;

    // One-time migration: copy old global key to new per-project key if needed
    const oldGlobalKey = "llamax_chat_session_id";
    const oldGlobalSession = localStorage.getItem(oldGlobalKey);
    if (oldGlobalSession && !localStorage.getItem(storageKey)) {
      localStorage.setItem(storageKey, oldGlobalSession);
      localStorage.removeItem(oldGlobalKey);
    }

    let storedSessionId = localStorage.getItem(storageKey);

    if (storedSessionId && !/^session_\d+$/.test(storedSessionId)) {
      console.warn("CLAUDE_FIX: Invalid session ID format detected, creating new session");
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

    registerSession(newSessionId, {
      autoBackup: true,
      preserveContext: true,
      maxHistoryItems: 1000,
      fileGenerationCapable: true
    });

    return newSessionId;
  });
  const [isSending, setIsSending] = useState(false);
  const chatInputRef = useRef(null);
  const historyLoadedRef = useRef(false); // Track if we've already loaded history
  const lastMessageRef = useRef(null);
  const processMessageRef = useRef(null);

  useEffect(() => {
    if (!useUnifiedChat || connectionState !== 'connected' || !socketRef?.current) {
      return;
    }

    const service = new UnifiedChatService(socketRef.current);
    service.joinSession(sessionId);
    setUnifiedChatService(service);

    return () => {
      service.cleanup();
      setUnifiedChatService(null);
    };
  }, [useUnifiedChat, connectionState, sessionId]);

  // When projectId changes (user navigates between projects), load the per-project session
  const prevProjectIdRef = useRef(projectId);
  useEffect(() => {
    if (prevProjectIdRef.current === projectId) return;
    prevProjectIdRef.current = projectId;

    const storageKey = `llamax_chat_session_id_${projectId || 'global'}`;
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

  const handleNewChat = useCallback(() => {
    const newSessionId = `session_${Date.now()}`;

    setMessages([]);

    setError('');

    const storageKey = `llamax_chat_session_id_${projectId || 'global'}`;
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
  }, [_setSessionId, projectId]);

  const [fileGenPopup, setFileGenPopup] = useState({
    open: false,
    fileData: null,
    originalMessage: null,
  });

  const { speak, ttsEnabled, isPlaying: isAISpeaking } = useVoice();

  const [voiceState, setVoiceState] = useState({
    isListening: false,
    isUserSpeaking: false,
    audioLevels: [],
  });

  const handleVoiceStateChange = useCallback((state) => {
    setVoiceState(state);
  }, []);

  const { activeModel, isLoadingModel, modelError } = useStatus();

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

  const detectFileGeneration = useCallback((message) => {
    if (message.includes("Uploaded Successfully") || message.includes("Status:** Uploaded")) {
      return { isCSVRequest: false, isCodeRequest: false };
    }

    const message_lower = message.toLowerCase();

    const explicitCSVPatterns = [
      /(?:generate|create|build|make|produce|export|download|save|output)\s+.*?\.csv/i,
      /(?:generate|create|build|make|produce|export|download|save|output)\s+.*?csv\s+file/i,
      /(?:generate|create|build|make|produce|export|download|save|output)\s+.*?spreadsheet/i,
      /(?:generate|create|build|make|produce|export|download|save|output)\s+.*?excel/i,
      /csv.*?(?:generate|create|build|make|produce|export|download|save|output)/i,
      /spreadsheet.*?(?:generate|create|build|make|produce|export|download|save|output)/i,
      /excel.*?(?:generate|create|build|make|produce|export|download|save|output)/i,
    ];

    const explicitCodePatterns = [
      /(?:generate|create|build|make|produce|write|output)\s+.*?\.(py|jsx?|ts|tsx|css|html|php|json|java|cpp|c|h|rb|go|rs|swift|txt|md)/i,
      /(?:generate|create|build|make|produce|write|output)\s+.*?(?:python|javascript|react|vue|angular)\s+(?:file|code|script|component)/i,
      /(?:generate|create|build|make|produce|write|output)\s+.*?(?:function|class|component|module)/i,
      /(?:write|output|save|export)\s+(?:this|the)\s+(?:code|function|component|script|text|content)\s+(?:to|as|in)\s+.*?\.(py|jsx?|ts|tsx|css|html|php|json|txt|md)/i,
      /(?:create|generate|write|make)\s+.*?(?:python|javascript|text|markdown)\s+(?:script|file|code|document)/i,
      /(?:create|generate|write|make)\s+.*?(?:script|code|component|document).*?(?:for|to)\s+.*?processing/i,
    ];

    const hasExplicitCSVIntent = explicitCSVPatterns.some(pattern => pattern.test(message));
    const hasExplicitCodeIntent = explicitCodePatterns.some(pattern => pattern.test(message));

    const bulkGenerationPatterns = [
      /(?:generate|create|make)\s+\d+.*?(?:csv|files|entries|items)/i,
      /bulk.*?(?:generate|create|export).*?(?:csv|files)/i,
      /batch.*?(?:generate|create|export).*?(?:csv|files)/i,
    ];
    const hasBulkIntent = bulkGenerationPatterns.some(pattern => pattern.test(message));

    const explicitCodeFilePattern = /(?:generate|create|write|save|export).*?\.(py|jsx?|ts|tsx|css|html|php|json|txt|md)(?:\s|$|[^a-z])/i;
    const explicitCSVFilePattern = /(?:generate|create|export|save).*?\.csv(?:\s|$|[^a-z])/i;
    const hasExplicitCodeFileExtension = explicitCodeFilePattern.test(message);
    const hasExplicitCSVFileExtension = explicitCSVFilePattern.test(message);

    if (hasExplicitCodeFileExtension || hasExplicitCodeIntent) {

      const codeFilenameMatch = message.match(
        /(?:generate|create|write|save|export).*?(?:as|to|named?|into)?\s*['""]?([a-zA-Z0-9_\-\.]+\.(py|jsx?|ts|tsx|css|html|php|json|java|cpp|c|h|rb|go|rs|swift|txt|md))['""]?/i
      ) || message.match(/['""]?([a-zA-Z0-9_\-\.]+\.(py|jsx?|ts|tsx|css|html|php|json|java|cpp|c|h|rb|go|rs|swift|txt|md))['""]?/i);

      const filename = codeFilenameMatch ? codeFilenameMatch[1] : `generated_file_${Date.now()}.txt`;
      const description = `Generate code file based on: "${message.substring(0, 100)}${message.length > 100 ? "..." : ""}"`;

      return {
        isCSVRequest: false,
        isCodeRequest: true,
        isBulkRequest: false,
        filename: filename,
        quantity: null,
        description: description,
      };
    }

    if (hasExplicitCSVFileExtension || hasExplicitCSVIntent || hasBulkIntent) {

      const filenameMatch = message.match(
        /(?:save|export|create|generate).*?(?:as|to|named?)?\s*['""]?([a-zA-Z0-9_\-\.]+\.csv)['""]?/i
      );
      const filename = filenameMatch ? filenameMatch[1] : `generated_data_${Date.now()}.csv`;

      const quantityMatch = message.match(/(\d+)\s*(?:csv|files|pages|items|entries)/i);
      const quantity = quantityMatch ? parseInt(quantityMatch[1]) : null;

      const description = hasBulkIntent
        ? `Generate ${quantity || "multiple"} CSV entries based on: "${message.substring(0, 100)}${message.length > 100 ? "..." : ""}"`
        : `Generate CSV file based on: "${message.substring(0, 100)}${message.length > 100 ? "..." : ""}"`

      return {
        isCSVRequest: true,
        isBulkRequest: hasBulkIntent,
        filename: filename,
        quantity: quantity,
        description: description,
      };
    }

    return { isCSVRequest: false, isCodeRequest: false };
  }, []);

  useEffect(() => {
    const initializeSession = async () => {
      const restoredSession = restoreSessionFromBackup(sessionId);
      if (restoredSession) {
      } else {
        registerSession(sessionId, {
          autoBackup: true,
          preserveContext: true,
          maxHistoryItems: 1000,
          fileGenerationCapable: true
        });
      }

      const contextRestoration = restoreConversationContext(sessionId);
    };

    initializeSession();
  }, [sessionId]);

  useEffect(() => {
    const loadHistory = async () => {
      const currentKey = `${sessionId}_${projectId}`;
      if (
        historyLoadedRef.current === currentKey
      ) {
        return;
      }

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
              .map((msg) => ({
                ...msg,
                isLocal: false,
                status: "persisted"
              }));

            const allMessages = [...currentMessages, ...historyMessages];
            allMessages.sort((a, b) => {
              const timeA = new Date(a.timestamp || 0).getTime();
              const timeB = new Date(b.timestamp || 0).getTime();
              return timeA - timeB;
            });

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
      }
    };

    loadHistory();
  }, [sessionId, projectId]);

  useEffect(() => {
    const handleChatHistoryCleared = (event) => {
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
    setIsSending(false);
  }, [resourceManager, processId]);

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

        const result = await generateBulkCSV({
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

  const routeMessageByMode = useCallback(
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
          console.error('CHAT_DEBUG: Message queue returned error:', queueResult.error);
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
        console.error('CHAT_DEBUG: Failed to enqueue message, using direct processing:', error);
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
          if (response.success) {
            setOrchestratorPlan(response.plan);
            setOrchestratorPlanId(response.plan_id);

            const assistantId = `asst_${Date.now()}`;
            setMessages(prev => [...prev, {
              id: assistantId,
              role: 'assistant',
              content: 'I have created an orchestration plan for your request. You can review and execute it above.'
            }]);

            recordMessage(sessionId, 'I have created an orchestration plan for your request. You can review and execute it above.', 'assistant', {
              messageId: assistantId,
              userMessageId: userMessageTempId,
              timestamp: new Date().toISOString()
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
          } catch (e) { }
        }
        return;
      }

      let fileDetection;
      if (useAgentRouting) {
        try {
          const agentDetection = await detectFileGenerationWithAgent(inputText);
          if (agentDetection) {
            fileDetection = agentDetection;
          } else {
            fileDetection = detectFileGeneration(inputText);
          }
        } catch (err) {
          console.warn("AGENT_ROUTER: Agent detection failed, using local:", err);
          fileDetection = detectFileGeneration(inputText);
        }
      } else {
        fileDetection = detectFileGeneration(inputText);
      }

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

          let content = agentResult?.final_answer || result?.error || "Agent execution completed";
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

      if (fileDetection.isCSVRequest || fileDetection.isCodeRequest) {

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
          console.warn("DEBUG: File generation dialog already open, allowing normal chat flow");

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
          console.warn("CHAT_DEBUG: Invalid AI response format, falling back to normal chat");
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
              console.warn("CHAT_DEBUG: TTS playback failed:", ttsError);
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
        console.log("IMAGE ANALYSIS: Routing through unified chat with base64 image");
        // Add user message with image preview immediately
        const userMessage = {
          id: `user_${Date.now()}`,
          role: "user",
          content: inputText || `Describe this image: ${voiceOptions.imageFileName}`,
          imageUrl: voiceOptions.imagePreview, // base64 data URL for immediate display
          imageFileName: voiceOptions.imageFileName,
          messageType: "image_upload",
        };
        setMessages((prev) => [...prev, userMessage]);
        // Don't return — fall through to unified chat flow which sends imageBase64
      }

      // Handle legacy image analysis (pre-generated response from /vision/analyze)
      if (
        voiceOptions &&
        voiceOptions.isImageAnalysis &&
        voiceOptions.analysisResponse
      ) {
        const userMessage = {
          id: `user_${Date.now()}`,
          role: "user",
          content: `[Image uploaded: ${voiceOptions.imageFileName}]${inputText ? ` ${inputText}` : ""
            }`,
          imageUrl: voiceOptions.imageUrl,
          imageFileName:
            voiceOptions.permanentFileName || voiceOptions.imageFileName,
          messageType: "image_upload",
        };
        setMessages((prev) => [...prev, userMessage]);

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
        console.warn(
          "VOICE WARNING: Voice message detected but missing aiResponse - will process through normal chat flow"
        );
        console.warn("VOICE WARNING: Voice options:", {
          isVoiceMessage: voiceOptions.isVoiceMessage,
          hasAiResponse: !!voiceOptions.aiResponse,
          userMessage: inputText.substring(0, 50) + "...",
        });
      }

      if (useUnifiedChat && unifiedChatService) {
        console.log('CHAT_PATH: Using UNIFIED chat (tools enabled)', { sessionId, socketConnected: !!unifiedChatService });
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
          const ackResult = await unifiedChatService.sendMessage(sessionId, modifiedInputText, {
            use_rag: true,
            chat_mode: chatMode,
            project_id: projectId,
          }, imageBase64);
        } catch (unifiedError) {
          console.error("UNIFIED_CHAT: Failed to send:", unifiedError);
          setIsStreamingMessage(false);
          setMessages((prev) => [
            ...prev,
            {
              id: `error_${Date.now()}`,
              role: "system",
              content: `Error: ${unifiedError.message}`,
            },
          ]);
          setIsSending(false);
        } finally {
          try {
            sessionStorage.removeItem(processingStorageKey);
          } catch (e) {
            console.warn("Failed to clear processing state:", e);
          }
        }
        return; // Don't fall through to legacy chat flow
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
                  console.warn("CLAUDE_FIX: Failed to update context tracking:", e);
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
            console.warn(`CLAUDE_FIX: API call attempt ${attempt} failed:`, apiError);

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

  const handleUploadComplete = useCallback((uploadResult) => {
    const successMessage = {
      id: `success_${Date.now()}`,
      role: "system",
      content: `File uploaded successfully! Indexing has been started in the background. Check the DevTools page to monitor progress.`,
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
        height={100}
        opacity={0.3}
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
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Typography variant="h5" component="h1">
            Chat
          </Typography>
        </Box>

        {}
        <Box sx={{ display: "flex", alignItems: "center", gap: 2 }}>
          <Tooltip title="Previous chats">
            <span>
              <IconButton
                onClick={() => setPreviousChatsOpen(true)}
                sx={{
                  width: 40,
                  height: 40,
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
                  backgroundColor: "primary.main",
                  color: "white",
                  width: 40,
                  height: 40,
                  "&:hover": {
                    backgroundColor: "primary.dark",
                  },
                  "&:active": {
                    transform: "scale(0.95)",
                  },
                  transition: "all 0.2s ease-in-out",
                }}
              >
                <AddIcon />
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

      {error && <div style={{ color: 'red', padding: '10px' }}>{error}</div>}

      {}

      {orchestratorPlan && (
        <OrchestratorPlanView
          plan={orchestratorPlan}
          planId={orchestratorPlanId}
          onExecutionComplete={(result) => {
            if (result.plan) {
              setOrchestratorPlan(result.plan);
            }
            if (result.final_answer) {
              const finalMsgId = `asst_${Date.now()}_final`;
              setMessages(prev => [...prev, {
                id: finalMsgId,
                role: 'assistant',
                content: result.final_answer
              }]);
              recordMessage(sessionId, result.final_answer, 'assistant', {
                messageId: finalMsgId,
                timestamp: new Date().toISOString()
              });
            }
          }}
        />
      )}

      <MessageList messages={messages} />

      {}
      {isStreamingMessage && unifiedChatService && (
        <Box sx={{ px: 2, py: 1 }}>
          <StreamingMessage
            chatService={unifiedChatService}
            sessionId={sessionId}
            onComplete={(result) => {
              setIsStreamingMessage(false);
              setIsSending(false);

              if (result.content) {
                const completedMessage = {
                  id: `asst_unified_${Date.now()}`,
                  role: "assistant",
                  content: result.content,
                  toolCalls: result.toolCalls || [],
                  isUnifiedChat: true,
                  timestamp: new Date().toISOString(),
                  generatedImages: result.generatedImages || [],
                };
                setMessages((prev) => [...prev, completedMessage]);
              }

              if (socketRef?.current) {
                const newService = new UnifiedChatService(socketRef.current);
                newService.joinSession(sessionId);
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
      <ChatInput
        onSendMessage={handleSendMessage}
        onStop={handleStop}
        disabled={isSending}
        sessionId={sessionId}
        ref={chatInputRef}
        onVoiceStateChange={handleVoiceStateChange}
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

      <PreviousChatsModal
        open={previousChatsOpen}
        onClose={() => setPreviousChatsOpen(false)}
        projectId={projectId}
        currentSessionId={sessionId}
        onSelectSession={(selectedSessionId) => {
          if (selectedSessionId === null) {
            // Deleted active session — start new chat
            handleNewChat();
            return;
          }
          const storageKey = `llamax_chat_session_id_${projectId || 'global'}`;
          localStorage.setItem(storageKey, selectedSessionId);
          setMessages([]);
          historyLoadedRef.current = false;
          _setSessionId(selectedSessionId);
        }}
      />
    </Paper>
    </PageLayout>
  );
};

export default ChatPage;
