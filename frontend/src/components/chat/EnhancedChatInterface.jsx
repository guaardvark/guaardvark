// frontend/src/components/chat/EnhancedChatInterface.jsx
// Enhanced Chat Interface with memory and web search capabilities
import {
  AutoAwesome as EnhancedIcon,
  Memory as MemoryIcon,
  Search as SearchIcon,
  Warning as WarningIcon,
} from "@mui/icons-material";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Typography,
} from "@mui/material";
import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  getEnhancedChatHistory,
  sendChatMessage,
  getContextStats,
  clearSessionContext,
  clearEnhancedSession,
  getSystemHealth
} from "../../api/chatService";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";
import { useUiTranslation } from "../../i18n";

const EnhancedChatInterface = ({
  sessionId,
  projectId = null,
  onMessageReceived = () => {},
  enableWebSearch = true,
  enableRAG = true,
}) => {
  const { t } = useUiTranslation();
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [enhancedStatus, setEnhancedStatus] = useState({
    enhanced: false,
    webSearchEnabled: false,
    memoryActive: false,
  });
  const [contextInfo, setContextInfo] = useState(null);
  const [contextStats, setContextStats] = useState(null);
  const [systemHealth, setSystemHealth] = useState(null);
  const [error, setError] = useState(null);

  const chatInputRef = useRef(null);

  // Load chat history and enhanced status on mount
  useEffect(() => {
    loadChatHistory();
    checkEnhancedStatus();
    loadContextStats();
  }, [sessionId]);

  // Load context stats when enhanced status changes
  useEffect(() => {
    if (enhancedStatus.enhanced) {
      loadContextStats();
    }
  }, [sessionId, enhancedStatus.enhanced]);

  // Listen for chat history cleared events
  useEffect(() => {
    const handleChatHistoryCleared = (event) => {
      console.log("EnhancedChatInterface: Chat history cleared event received", event.detail);
      // Clear messages and reset context
      setMessages([]);
      setContextInfo(null);
      setError(null);
      setEnhancedStatus({
        enhanced: false,
        webSearchEnabled: false,
        memoryActive: false,
      });
    };

    window.addEventListener('chatHistoryCleared', handleChatHistoryCleared);
    
    return () => {
      window.removeEventListener('chatHistoryCleared', handleChatHistoryCleared);
    };
  }, []);

  const loadChatHistory = useCallback(async () => {
    try {
      setIsLoading(true);
      const history = await getEnhancedChatHistory(sessionId, 50);

      setMessages(history.messages || []);
      setContextInfo(history.context_info);
      setEnhancedStatus((prev) => ({
        ...prev,
        enhanced: history.enhanced,
        memoryActive: !!(history.context_info || history.local_context),
      }));
    } catch (error) {
      console.error("Enhanced Chat: Failed to load history:", error);
      setError({ kind: "loadHistory" });
    } finally {
      setIsLoading(false);
    }
  }, [sessionId]);

  const loadContextStats = useCallback(async () => {
    try {
      const stats = await getContextStats(sessionId);
      if (stats) {
        setContextStats(stats);
        setEnhancedStatus((prev) => ({
          ...prev,
          memoryActive: !!(stats.context_stats?.total_sessions > 0 || stats.chat_manager_stats?.total_messages > 0),
        }));
      }
    } catch (error) {
      console.warn("Enhanced Chat: Failed to load context stats:", error);
    }
  }, [sessionId]);

  const loadSystemHealth = useCallback(async () => {
    try {
      const health = await getSystemHealth();
      setSystemHealth(health);
    } catch (error) {
      console.warn("Enhanced Chat: Failed to load system health:", error);
    }
  }, []);

  const checkEnhancedStatus = useCallback(async () => {
    try {
      // Load system health to check enhanced status
      const health = await getSystemHealth();
      if (health) {
        setSystemHealth(health);
        setEnhancedStatus((prev) => ({
          ...prev,
          enhanced: true, // Enhanced features are active
          webSearchEnabled: enableWebSearch,
        }));
      }
    } catch (error) {
      console.warn("Enhanced Chat: Failed to check status:", error);
    }
  }, [enableWebSearch]);

  const handleSendMessage = useCallback(
    async (inputText, file) => {
      if (!inputText.trim() && !file) return;

      // Slash command handling for Uncle Claude integration
      if (inputText.startsWith("/claude ")) {
        const claudeMessage = inputText.slice(8);
        setIsLoading(true);
        setMessages((prev) => [...prev, { id: `user_${Date.now()}`, role: "user", content: inputText, timestamp: new Date().toISOString() }]);
        try {
          const { claudeAdvisorService } = await import("../../api/claudeAdvisorService");
          const res = await claudeAdvisorService.escalate(claudeMessage, messages.filter(m => m.role).slice(-10));
          setMessages((prev) => [...prev, { id: `claude_${Date.now()}`, role: "assistant", content: res?.data?.response || t("chat.enhanced.uncleClaudeUnavailable"), timestamp: new Date().toISOString(), source: "uncle_claude" }]);
        } catch (err) {
          setMessages((prev) => [...prev, { id: `err_${Date.now()}`, role: "assistant", content: t("chat.enhanced.uncleClaudeError", { error: err.message }), timestamp: new Date().toISOString() }]);
        } finally { setIsLoading(false); }
        return;
      }
      if (inputText.startsWith("/ask-family ")) {
        const familyMessage = inputText.slice(12);
        setIsLoading(true);
        setMessages((prev) => [...prev, { id: `user_${Date.now()}`, role: "user", content: inputText, timestamp: new Date().toISOString() }]);
        try {
          const { BASE_URL, handleResponse } = await import("../../api/apiClient");
          const res = await fetch(`${BASE_URL}/interconnector/ask-family`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: familyMessage }) });
          const data = await handleResponse(res);
          const reply = data?.data?.handled_by ? `[${data.data.handled_by}]: ${JSON.stringify(data.data.response)}` : (data?.data?.message || t("chat.enhanced.noFamilyMember"));
          setMessages((prev) => [...prev, { id: `family_${Date.now()}`, role: "assistant", content: reply, timestamp: new Date().toISOString(), source: "family" }]);
        } catch (err) {
          setMessages((prev) => [...prev, { id: `err_${Date.now()}`, role: "assistant", content: t("chat.enhanced.familyError", { error: err.message }), timestamp: new Date().toISOString() }]);
        } finally { setIsLoading(false); }
        return;
      }
      if (inputText.startsWith("/improve ")) {
        const improvementDesc = inputText.slice(9);
        setIsLoading(true);
        setMessages((prev) => [...prev, { id: `user_${Date.now()}`, role: "user", content: inputText, timestamp: new Date().toISOString() }]);
        try {
          const { selfImprovementService } = await import("../../api/selfImprovementService");
          const res = await selfImprovementService.submitTask(improvementDesc);
          setMessages((prev) => [...prev, { id: `imp_${Date.now()}`, role: "assistant", content: t("chat.enhanced.improvementSubmitted", { result: JSON.stringify(res?.data) }), timestamp: new Date().toISOString(), source: "self_improvement" }]);
        } catch (err) {
          setMessages((prev) => [...prev, { id: `err_${Date.now()}`, role: "assistant", content: t("chat.enhanced.improvementError", { error: err.message }), timestamp: new Date().toISOString() }]);
        } finally { setIsLoading(false); }
        return;
      }

      setIsLoading(true);
      setError(null);

      // Add user message immediately
      const userMessage = {
        id: `user_${Date.now()}`,
        role: "user",
        content: inputText,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMessage]);

      // Add placeholder for assistant response
      const assistantId = `asst_${Date.now()}`;
      const assistantPlaceholder = {
        id: assistantId,
        role: "assistant",
        content: "",
        timestamp: new Date().toISOString(),
        loading: true,
      };
      setMessages((prev) => [...prev, assistantPlaceholder]);

      try {
        const result = await sendChatMessage(
          sessionId,
          inputText,
          projectId,
          null, // onDeltaReceived
          null, // onRagDebugReceived
          null, // signal
          null  // chatMode
        );

        // Update the assistant message with the response
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? {
                  ...msg,
                  content: result.content,
                  loading: false,
                  enhanced: result.enhanced,
                  webSearchUsed: result.webSearchUsed,
                  webSearchContext: result.webSearchContext,
                }
              : msg
          )
        );

        // Update enhanced status
        setEnhancedStatus((prev) => ({
          ...prev,
          enhanced: result.enhanced,
          memoryActive: true,
        }));

        // Update context info if available
        if (result.context_info) {
          setContextInfo(result.context_info);
        }

        // Notify parent component
        onMessageReceived({
          userMessage: inputText,
          assistantResponse: result.content,
          enhanced: result.enhanced,
          webSearchUsed: result.webSearchUsed,
        });
      } catch (error) {
        console.error("Enhanced Chat: Send message error:", error);

        // Update the assistant message with error
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? {
                  ...msg,
                  content: t("chat.enhanced.sendError", { error: error.message }),
                  loading: false,
                  error: true,
                }
              : msg
          )
        );

        setError({ detail: error.message });
      } finally {
        setIsLoading(false);
        if (chatInputRef.current) {
          chatInputRef.current.focus();
        }
      }
    },
    [sessionId, projectId, enableRAG, enableWebSearch, onMessageReceived]
  );

  // Memory control functions
  const handleClearContext = useCallback(async () => {
    try {
      await clearSessionContext(sessionId);
      setContextInfo(null);
      setContextStats(null);
      setEnhancedStatus((prev) => ({
        ...prev,
        memoryActive: false,
      }));
      // Reload context stats to reflect changes
      await loadContextStats();
    } catch (error) {
      console.error("Enhanced Chat: Failed to clear context:", error);
      setError({ kind: "clearContext" });
    }
  }, [sessionId, loadContextStats]);

  const handleRefreshContext = useCallback(async () => {
    await loadContextStats();
    await loadSystemHealth();
  }, [loadContextStats, loadSystemHealth]);

  const handleClearSession = useCallback(async () => {
    try {
      await clearEnhancedSession(sessionId);
      setMessages([]);
      setContextInfo(null);
      setEnhancedStatus((prev) => ({
        ...prev,
        memoryActive: false,
      }));
      setError(null);
    } catch (error) {
      console.error("Enhanced Chat: Clear session error:", error);
      setError({ kind: "clearSession" });
    }
  }, [sessionId]);

  // Our own failures are kept as a kind and worded here; a send failure
  // carries the server's message.
  const errorText = (err) => {
    if (err.kind === "loadHistory") return t("chat.enhanced.loadHistoryFailed");
    if (err.kind === "clearContext") return t("chat.enhanced.clearContextFailed");
    if (err.kind === "clearSession") return t("chat.enhanced.clearSessionFailed");
    return err.detail;
  };

  return (
    <Paper
      elevation={2}
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Enhanced Status Bar */}
      <Box
        sx={{
          p: 2,
          borderBottom: 1,
          borderColor: "divider",
          backgroundColor: "background.default",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
          <EnhancedIcon color="primary" fontSize="small" />
          <Typography variant="h6" component="h2">
            {t("chat.enhanced.title")}
          </Typography>
          <Box sx={{ ml: "auto", display: "flex", gap: 1 }}>
            <Chip
              icon={<MemoryIcon />}
              label={
                enhancedStatus.memoryActive ? t("chat.enhanced.memoryActive") : t("chat.enhanced.noMemory")
              }
              color={enhancedStatus.memoryActive ? "success" : "default"}
              size="small"
            />
            <Chip
              icon={<SearchIcon />}
              label={
                enhancedStatus.webSearchEnabled ? t("chat.enhanced.webSearch") : t("chat.enhanced.noWebSearch")
              }
              color={enhancedStatus.webSearchEnabled ? "success" : "default"}
              size="small"
            />
            {enhancedStatus.enhanced && (
              <Chip label={t("chat.enhanced.enhancedMode")} color="primary" size="small" />
            )}
          </Box>
        </Box>

        {/* Enhanced Context Information */}
        {(contextStats || contextInfo) && (
          <Box sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              {t("chat.enhanced.contextSummary", {
                sessions: contextStats?.context_stats?.total_sessions || contextStats?.context_manager_stats?.total_sessions || 0,
                messages: contextStats?.chat_manager_stats?.total_messages || contextInfo?.chat_manager_stats?.total_messages || 0,
                chunks: contextStats?.context_stats?.total_chunks || contextInfo?.context_stats?.total_chunks || 0,
              })}
            </Typography>
          </Box>
        )}

        {/* System Health Indicators */}
        {systemHealth && (
          <Box sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {systemHealth.context_manager_stats && (
              <Chip
                label={t("chat.enhanced.contextSessions", { count: systemHealth.context_manager_stats.total_sessions || 0 })}
                size="small"
                variant="outlined"
                color="info"
              />
            )}
            {systemHealth.index_manager_stats && (
              <Chip
                label={t("chat.enhanced.indexCache", { rate: systemHealth.index_manager_stats.cache_hit_rate || 0 })}
                size="small"
                variant="outlined"
                color="success"
              />
            )}
          </Box>
        )}

        {/* Error Display */}
        {error && (
          <Alert
            severity="warning"
            sx={{ mt: 1 }}
            onClose={() => setError(null)}
            icon={<WarningIcon />}
          >
            {errorText(error)}
          </Alert>
        )}

        {/* Enhanced Memory Controls */}
        <Box sx={{ mt: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            size="small"
            variant="outlined"
            onClick={handleClearContext}
            disabled={isLoading}
            color="warning"
          >
            {t("chat.enhanced.clearContext")}
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={handleClearSession}
            disabled={isLoading}
            color="error"
          >
            {t("chat.enhanced.clearAllMemory")}
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={handleRefreshContext}
            disabled={isLoading}
            color="primary"
          >
            {t("chat.enhanced.refreshStats")}
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={loadChatHistory}
            disabled={isLoading}
          >
            {t("chat.enhanced.reloadHistory")}
          </Button>
        </Box>
      </Box>

      {/* Messages Area */}
      <Box sx={{ flex: 1, overflow: "hidden", position: "relative" }}>
        {isLoading && messages.length === 0 && (
          <Box
            sx={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              display: "flex",
              alignItems: "center",
              gap: 2,
            }}
          >
            <CircularProgress size={24} />
            <Typography variant="body2" color="text.secondary">
              {t("chat.enhanced.loading")}
            </Typography>
          </Box>
        )}

        <MessageList
          messages={messages.map((msg) => ({
            ...msg,
            // Add enhanced message indicators
            content: msg.webSearchUsed
              ? t("chat.enhanced.contentWithWebSearch", { content: msg.content })
              : msg.content,
            // Claude badge for Uncle Claude responses
            badge: msg.source === "uncle_claude" ? "Uncle Claude" : msg.source === "family" ? t("chat.enhanced.familyNode") : msg.source === "self_improvement" ? t("chat.enhanced.selfImprovement") : undefined,
            source: msg.source,
          }))}
        />
      </Box>

      {/* Chat Input */}
      <Box sx={{ p: 2, borderTop: 1, borderColor: "divider" }}>
        <ChatInput
          ref={chatInputRef}
          onSendMessage={handleSendMessage}
          disabled={isLoading}
          sessionId={sessionId}
          placeholder={
            enhancedStatus.enhanced
              ? t("chat.enhanced.placeholderReady")
              : t("chat.enhanced.placeholderLoading")
          }
        />
      </Box>
    </Paper>
  );
};

export default EnhancedChatInterface;
