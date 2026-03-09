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
  getSystemHealth 
} from "../../api/chatService";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

const EnhancedChatInterface = ({
  sessionId,
  projectId = null,
  onMessageReceived = () => {},
  enableWebSearch = true,
  enableRAG = true,
}) => {
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
      setError("Failed to load chat history");
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
                  content: `Error: ${error.message}`,
                  loading: false,
                  error: true,
                }
              : msg
          )
        );

        setError(error.message);
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
      setError("Failed to clear context");
    }
  }, [sessionId, loadContextStats]);

  const handleRefreshContext = useCallback(async () => {
    await loadContextStats();
    await loadSystemHealth();
  }, [loadContextStats, loadSystemHealth]);

  const handleClearSession = useCallback(async () => {
    try {
      await enhancedChatService.clearEnhancedSession(sessionId);
      setMessages([]);
      setContextInfo(null);
      setEnhancedStatus((prev) => ({
        ...prev,
        memoryActive: false,
      }));
      setError(null);
    } catch (error) {
      console.error("Enhanced Chat: Clear session error:", error);
      setError("Failed to clear session");
    }
  }, [sessionId]);

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
            Enhanced Chat
          </Typography>
          <Box sx={{ ml: "auto", display: "flex", gap: 1 }}>
            <Chip
              icon={<MemoryIcon />}
              label={
                enhancedStatus.memoryActive ? "Memory Active" : "No Memory"
              }
              color={enhancedStatus.memoryActive ? "success" : "default"}
              size="small"
            />
            <Chip
              icon={<SearchIcon />}
              label={
                enhancedStatus.webSearchEnabled ? "Web Search" : "No Web Search"
              }
              color={enhancedStatus.webSearchEnabled ? "success" : "default"}
              size="small"
            />
            {enhancedStatus.enhanced && (
              <Chip label="Enhanced Mode" color="primary" size="small" />
            )}
          </Box>
        </Box>

        {/* Enhanced Context Information */}
        {(contextStats || contextInfo) && (
          <Box sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Context: {contextStats?.context_stats?.total_sessions || contextStats?.context_manager_stats?.total_sessions || 0} sessions,{' '}
              {contextStats?.chat_manager_stats?.total_messages || contextInfo?.chat_manager_stats?.total_messages || 0} messages,{' '}
              {contextStats?.context_stats?.total_chunks || contextInfo?.context_stats?.total_chunks || 0} chunks in memory
            </Typography>
          </Box>
        )}

        {/* System Health Indicators */}
        {systemHealth && (
          <Box sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            {systemHealth.context_manager_stats && (
              <Chip
                label={`${systemHealth.context_manager_stats.total_sessions || 0} Context Sessions`}
                size="small"
                variant="outlined"
                color="info"
              />
            )}
            {systemHealth.index_manager_stats && (
              <Chip
                label={`Index Cache: ${systemHealth.index_manager_stats.cache_hit_rate || 0}% hit rate`}
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
            {error}
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
            Clear Context
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={handleClearSession}
            disabled={isLoading}
            color="error"
          >
            Clear All Memory
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={handleRefreshContext}
            disabled={isLoading}
            color="primary"
          >
            Refresh Stats
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={loadChatHistory}
            disabled={isLoading}
          >
            Reload History
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
              Loading enhanced chat...
            </Typography>
          </Box>
        )}

        <MessageList
          messages={messages.map((msg) => ({
            ...msg,
            // Add enhanced message indicators
            content: msg.webSearchUsed
              ? `${msg.content}\n\n*Used web search for real-time information*`
              : msg.content,
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
              ? "Ask anything... I have memory and web access!"
              : "Ask anything... (enhanced features loading)"
          }
        />
      </Box>
    </Paper>
  );
};

export default EnhancedChatInterface;
