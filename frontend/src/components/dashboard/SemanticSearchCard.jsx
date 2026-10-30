
import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  Typography,
  List,
  ListItem,
  TextField,
  IconButton,
  CircularProgress,
  Alert,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import ChatIcon from "@mui/icons-material/Chat";
import DashboardCardWrapper from "./DashboardCardWrapper";
import FileGenPopup from "../FileGenPopup";
import { getChatHistory } from "../../api/chatService";
import { generateFileFromChat } from "../../api/filegenService";
import UnifiedChatService from "../../api/unifiedChatService";
import StreamingMessage from "../chat/StreamingMessage";
import { useUnifiedProgressActions } from "../../contexts/UnifiedProgressContext";
import { uiLocale, useUiTranslation } from "../../i18n";

const SemanticSearchCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const [messages, setMessages] = useState([]);
    const [inputText, setInputText] = useState("");
    const [isSending, setIsSending] = useState(false);
    const [error, setError] = useState(null);
    const [isLoading, setIsLoading] = useState(false);
    const inputRef = useRef(null);

    // Unified Chat Service (Socket.IO streaming)
    const { socketRef } = useUnifiedProgressActions();
    const [unifiedChatService, setUnifiedChatService] = useState(null);
    const [isStreamingMessage, setIsStreamingMessage] = useState(false);

    const [fileGenPopup, setFileGenPopup] = useState({
      open: false,
      fileData: null,
      originalMessage: null,
    });

    const [sessionId] = useState(() => {
      const storedSessionId = localStorage.getItem("guaardvark_chat_session_id");
      if (storedSessionId) {
        return storedSessionId;
      }
      const newSessionId = `session_${Date.now()}`;
      localStorage.setItem("guaardvark_chat_session_id", newSessionId);
      return newSessionId;
    });

    // Initialize UnifiedChatService when socket is connected
    useEffect(() => {
      // Eager creation for the same race/latency reasons as main chat (service must exist
      // to satisfy guards even while socket is still negotiating the connection).
      if (!socketRef?.current) {
        return;
      }

      const service = new UnifiedChatService(socketRef.current);
      service.joinSession(sessionId);
      setUnifiedChatService(service);

      return () => {
        service.cleanup();
        setUnifiedChatService(null);
      };
    }, [sessionId]);

    const detectCSVGeneration = useCallback((message) => {
      const csvKeywords = [
        'csv', 'CSV', 'spreadsheet', 'table', 'data export', 'export to csv',
        'generate csv', 'create csv', 'csv file', 'comma separated',
        'excel', 'Excel', 'sheet', 'worksheet', 'tabular data'
      ];
      
      const generationKeywords = [
        'generate', 'create', 'build', 'make', 'produce', 'export',
        'download', 'save', 'output', 'compile', 'list'
      ];
      
      const message_lower = message.toLowerCase();
      
      const hasCSVKeyword = csvKeywords.some(keyword => 
        message_lower.includes(keyword.toLowerCase())
      );
      
      const hasGenerationKeyword = generationKeywords.some(keyword => 
        message_lower.includes(keyword.toLowerCase())
      );
      
      if (hasCSVKeyword && hasGenerationKeyword) {
        const filenameMatch = message.match(/(?:save|export|create|generate).*?(?:as|to|named?)?\s*['""]?([a-zA-Z0-9_\-.]+\.csv)['""]?/i);
        const filename = filenameMatch ? filenameMatch[1] : `generated_data_${Date.now()}.csv`;
        
        return {
          isCSVRequest: true,
          filename: filename,
          description: t("dashboard.chat.csvDescription", {
            excerpt: `${message.substring(0, 100)}${message.length > 100 ? '...' : ''}`,
          }),
        };
      }
      
      return { isCSVRequest: false };
    }, []);

    const loadHistory = useCallback(async () => {
      setIsLoading(true);
      setError(null);
      try {
        const history = await getChatHistory(sessionId, null, 50);
        if (history && Array.isArray(history.messages)) {
          setMessages(history.messages.slice(-10));
        } else {
          setMessages([]);
        }
      } catch (err) {
        console.error("SemanticSearchCard: Failed to load chat history:", err);
        setError(t("dashboard.chat.historyFailed"));
        setMessages([]);
      } finally {
        setIsLoading(false);
      }
    }, [sessionId]);

    useEffect(() => {
      loadHistory();
    }, [loadHistory]);

    useEffect(() => {
      const handleStorageChange = (e) => {
        if (e.key === "guaardvark_chat_session_id") {
          loadHistory();
        }
      };
      window.addEventListener("storage", handleStorageChange);
      return () => window.removeEventListener("storage", handleStorageChange);
    }, [loadHistory]);

    useEffect(() => {
      const handleChatHistoryCleared = (event) => {
        console.log("SemanticSearchCard: Chat history cleared event received", event.detail);
        setMessages([]);
        setError(null);
      };

      window.addEventListener('chatHistoryCleared', handleChatHistoryCleared);
      
      return () => {
        window.removeEventListener('chatHistoryCleared', handleChatHistoryCleared);
      };
    }, []);

    const handleStop = useCallback(() => {
      console.log("DEBUG: Stop button clicked in SemanticSearchCard");
      if (unifiedChatService) {
        unifiedChatService.abort(sessionId);
      }
      setIsStreamingMessage(false);
      setIsSending(false);
    }, [unifiedChatService, sessionId]);

    const handleFileGenConfirm = useCallback(async () => {
      if (!fileGenPopup.fileData || !fileGenPopup.originalMessage) return;
      
      const { fileData, originalMessage } = fileGenPopup;
      
      setFileGenPopup({ open: false, fileData: null, originalMessage: null });
      
      try {
        
        const result = await generateFileFromChat({
          filename: fileData.filename,
          user_instructions: originalMessage,
          project_id: null,
        });
        
        if (result.error) {
          throw new Error(result.error);
        }
        
        
        const successMessage = {
          id: `success_${Date.now()}`,
          role: "system",
          content: t("dashboard.chat.fileGenerated", { filename: fileData.filename }),
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, successMessage]);
        
      } catch (error) {
        console.error("File generation error:", error);
        
        
        const errorMessage = {
          id: `error_${Date.now()}`,
          role: "system",
          content: t("dashboard.chat.fileGenFailed", { message: error.message }),
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, errorMessage]);
      }
    }, [fileGenPopup]);

    const handleFileGenDismiss = useCallback(() => {
      setFileGenPopup({ open: false, fileData: null, originalMessage: null });
    }, []);

    const handleSendMessage = useCallback(async () => {
      if (!inputText.trim() || isSending) return;

      const userMessage = {
        id: `user_${Date.now()}`,
        role: "user",
        content: inputText,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMessage]);
      const currentInput = inputText;
      setInputText("");

      const csvDetection = detectCSVGeneration(inputText);
      if (csvDetection.isCSVRequest) {
        console.log("DEBUG: CSV generation detected in SemanticSearchCard:", csvDetection);

        setFileGenPopup({
          open: true,
          fileData: {
            filename: csvDetection.filename,
            description: csvDetection.description,
          },
          originalMessage: inputText,
        });

        return;
      }

      if (!unifiedChatService) {
        console.error("SemanticSearchCard: UnifiedChatService not available (socket not connected)");
        setError(t("dashboard.chat.notConnected"));
        return;
      }

      setIsSending(true);
      setIsStreamingMessage(true);
      setError(null);

      try {
        console.log("SemanticSearchCard: Sending via UnifiedChatService", { sessionId });
        await unifiedChatService.sendMessage(sessionId, currentInput, {
          use_rag: true,
        });
      } catch (error) {
        console.error("SemanticSearchCard: Failed to send message:", error);
        setIsStreamingMessage(false);
        setIsSending(false);

        const errorText = error.message || t("dashboard.chat.sendFailed");
        setMessages((prev) => [
          ...prev,
          {
            id: `error_${Date.now()}`,
            role: "system",
            content: t("dashboard.chat.errorMessage", { message: errorText }),
            timestamp: new Date().toISOString(),
          },
        ]);
        setError(errorText);
      }
    }, [inputText, isSending, sessionId, unifiedChatService, detectCSVGeneration]);

    const handleKeyPress = (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        handleSendMessage();
      }
    };

    const formatMessageTime = (timestamp) => {
      if (!timestamp) return "";
      const date = new Date(timestamp);
      return date.toLocaleTimeString(uiLocale(), {
        hour: "2-digit",
        minute: "2-digit",
      });
    };

    const truncateMessage = (content, maxLength = 200) => {
      if (!content) return "";
      return content.length > maxLength
        ? content.substring(0, maxLength) + "..."
        : content;
    };

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.chat.title")}
        minimizedContent={
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", p: 0.5 }}>
            <ChatIcon sx={{ mr: 1, fontSize: "1rem" }} />
            <Typography variant="body2">
              {messages.length > 0 ? t("dashboard.chat.messageCount", { count: messages.length }) : t("dashboard.chat.noMessages")}
            </Typography>
          </Box>
        }
        {...props}
        contextMenuActions={[
          { label: t("dashboard.chat.reloadHistory"), onClick: loadHistory },
          isSending && { label: t("dashboard.chat.stopGenerating"), onClick: handleStop },
        ]}
      >
            {error && (
              <Alert severity="error" sx={{ mb: 1 }}>
                {error}
              </Alert>
            )}

            {}
            {isLoading && (
              <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
                <CircularProgress size={24} />
              </Box>
            )}

            {}
            {!isLoading && messages.length > 0 && (
              <List
                dense
                sx={{
                  flexGrow: 1,
                  overflowY: "auto",
                  maxHeight: "300px",
                  mb: 1,
                }}
              >
                {messages.slice(-8).map((message) => (
                  <ListItem
                    key={message.id}
                    disableGutters
                    sx={{
                      flexDirection: "column",
                      alignItems: "flex-start",
                      py: 0.5,
                      px: 0.5,
                    }}
                    className="non-draggable"
                  >
                    <Box
                      sx={{
                        display: "flex",
                        justifyContent: "space-between",
                        width: "100%",
                        mb: 0.5,
                      }}
                    >
                      <Typography
                        variant="caption"
                        sx={{
                          fontWeight: "bold",
                          color:
                            message.role === "user"
                              ? "primary.main"
                              : message.role === "system"
                              ? "error.main"
                              : "secondary.main",
                        }}
                      >
                        {message.role === "user" ? t("dashboard.chat.roles.user") : message.role === "system" ? t("dashboard.chat.roles.system") : t("dashboard.chat.roles.assistant")}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {formatMessageTime(message.timestamp)}
                      </Typography>
                    </Box>
                    <Typography
                      variant="body2"
                      sx={{
                        width: "100%",
                        wordBreak: "break-word",
                        color:
                          message.role === "system" ? "error.main" : "inherit",
                        fontSize: "0.875rem",
                      }}
                    >
                      {truncateMessage(message.content)}
                    </Typography>
                  </ListItem>
                ))}
              </List>
            )}

            {/* Streaming message from UnifiedChatService */}
            {isStreamingMessage && unifiedChatService && (
              <Box sx={{ px: 0.5, py: 0.5 }} className="non-draggable">
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
                        timestamp: new Date().toISOString(),
                      };
                      setMessages((prev) => [...prev, completedMessage]);
                    }

                    // Create a fresh service instance for the next message
                    if (socketRef?.current) {
                      const newService = new UnifiedChatService(socketRef.current);
                      newService.joinSession(sessionId);
                      setUnifiedChatService(newService);
                    }
                  }}
                />
              </Box>
            )}

            {}
            {!isLoading && messages.length === 0 && !isStreamingMessage && (
              <Typography
                variant="body2"
                sx={{
                  color: "text.secondary",
                  textAlign: "center",
                  py: 2,
                }}
              >
                {t("dashboard.chat.empty")}
              </Typography>
            )}

            {}
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                gap: 1,
                mt: "auto",
                pt: 1,
                borderTop: 1,
                borderColor: "divider",
              }}
            >
              <TextField
                ref={inputRef}
                size="small"
                placeholder={t("dashboard.chat.placeholder")}
                value={inputText}
                onChange={(e) => setInputText(e.target.value)}
                onKeyPress={handleKeyPress}
                disabled={isSending}
                multiline
                maxRows={3}
                sx={{ flexGrow: 1 }}
                className="non-draggable"
              />
              <IconButton
                onClick={isSending ? handleStop : handleSendMessage}
                disabled={!inputText.trim() && !isSending}
                size="small"
                className="non-draggable"
              >
                {isSending ? <StopIcon /> : <SendIcon />}
              </IconButton>
            </Box>
        <FileGenPopup
          open={fileGenPopup.open}
          onConfirm={handleFileGenConfirm}
          onDismiss={handleFileGenDismiss}
          fileData={fileGenPopup.fileData}
        />
      </DashboardCardWrapper>
    );
  },
);

SemanticSearchCard.displayName = "SemanticSearchCard";
export default SemanticSearchCard;
