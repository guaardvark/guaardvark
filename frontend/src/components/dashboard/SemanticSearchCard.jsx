
import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  Typography,
  List,
  ListItem,
  ListItemText,
  TextField,
  IconButton,
  CircularProgress,
  Alert,
  Paper,
} from "@mui/material";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import ChatIcon from "@mui/icons-material/Chat";
import DashboardCardWrapper from "./DashboardCardWrapper";
import FileGenPopup from "../FileGenPopup";
import { getChatHistory, sendChatMessage } from "../../api/chatService";
import { getRagDebug } from "../../api/settingsService";
import { generateFileFromChat } from "../../api/filegenService";

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
    const [messages, setMessages] = useState([]);
    const [inputText, setInputText] = useState("");
    const [isSending, setIsSending] = useState(false);
    const [error, setError] = useState(null);
    const [isLoading, setIsLoading] = useState(false);
    const [ragDebugEnabled, setRagDebugEnabled] = useState(false);
    const inputRef = useRef(null);
    const abortControllerRef = useRef(null);

    const [fileGenPopup, setFileGenPopup] = useState({
      open: false,
      fileData: null,
      originalMessage: null,
    });

    const [sessionId] = useState(() => {
      const storedSessionId = localStorage.getItem("llamax_chat_session_id");
      if (storedSessionId) {
        return storedSessionId;
      }
      const newSessionId = `session_${Date.now()}`;
      localStorage.setItem("llamax_chat_session_id", newSessionId);
      return newSessionId;
    });

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
        const filenameMatch = message.match(/(?:save|export|create|generate).*?(?:as|to|named?)?\s*['""]?([a-zA-Z0-9_\-\.]+\.csv)['""]?/i);
        const filename = filenameMatch ? filenameMatch[1] : `generated_data_${Date.now()}.csv`;
        
        return {
          isCSVRequest: true,
          filename: filename,
          description: `Generate CSV file based on: "${message.substring(0, 100)}${message.length > 100 ? '...' : ''}"`
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
        setError("Failed to load chat history");
        setMessages([]);
      } finally {
        setIsLoading(false);
      }
    }, [sessionId]);

    useEffect(() => {
      loadHistory();
    }, [loadHistory]);

    useEffect(() => {
      const fetchRagDebugSetting = async () => {
        try {
          const result = await getRagDebug();
          if (result && typeof result.rag_debug_enabled === "boolean") {
            setRagDebugEnabled(result.rag_debug_enabled);
          }
        } catch (err) {
          console.warn("SemanticSearchCard: Failed to fetch RAG debug setting:", err);
        }
      };
      fetchRagDebugSetting();
    }, []);

    useEffect(() => {
      const handleStorageChange = (e) => {
        if (e.key === "llamax_chat_session_id") {
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

    const filterDebugContent = useCallback((content) => {
      if (!content || ragDebugEnabled) return content;
      
      const debugPatterns = [
        /\*\*Thinking:\*\*.*?(?=\n\n|\n\*\*|$)/gs,
        /\*\*Analysis:\*\*.*?(?=\n\n|\n\*\*|$)/gs,
        /\*\*Context:\*\*.*?(?=\n\n|\n\*\*|$)/gs,
        /\*\*Debug:\*\*.*?(?=\n\n|\n\*\*|$)/gs,
        /\*\*RAG Debug:\*\*.*?(?=\n\n|\n\*\*|$)/gs,
        /\[DEBUG\].*?(?=\n|\[|$)/gs,
        /\[RAG\].*?(?=\n|\[|$)/gs,
        /\[CONTEXT\].*?(?=\n|\[|$)/gs,
        /^---.*?---$/gm,
        /^### Debug Information.*?(?=\n#|$)/gms,
        /^### RAG Debug.*?(?=\n#|$)/gms,
        /^### Context.*?(?=\n#|$)/gms,
      ];
      
      let filteredContent = content;
      debugPatterns.forEach(pattern => {
        filteredContent = filteredContent.replace(pattern, '');
      });
      
      filteredContent = filteredContent.replace(/\n\s*\n\s*\n/g, '\n\n').trim();
      
      return filteredContent;
    }, [ragDebugEnabled]);

    const handleStop = useCallback(() => {
      console.log("DEBUG: Stop button clicked in SemanticSearchCard");
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      setIsSending(false);
    }, []);

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
          content: `File "${fileData.filename}" generated successfully! You can download it from the outputs folder.`,
          timestamp: new Date().toISOString(),
        };
        setMessages((prev) => [...prev, successMessage]);
        
      } catch (error) {
        console.error("File generation error:", error);
        
        
        const errorMessage = {
          id: `error_${Date.now()}`,
          role: "system",
          content: `File generation failed: ${error.message}`,
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

      abortControllerRef.current = new AbortController();

      setIsSending(true);
      setError(null);

      const assistantId = `asst_${Date.now()}`;
      setMessages((prev) => [
        ...prev,
        {
          id: assistantId,
          role: "assistant",
          content: "",
          timestamp: new Date().toISOString(),
        },
      ]);

      try {
        const result = await sendChatMessage(
          sessionId,
          currentInput,
          null,
          (delta) => {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: m.content + delta } : m,
              ),
            );
          },
          (ragDebug) => {
            if (ragDebugEnabled) {
              console.log("SemanticSearchCard RAG Debug:", ragDebug);
            }
          },
          abortControllerRef.current?.signal
        );

        if (result && typeof result === "object") {
          if (result.content && typeof result.content === "string") {
            const filteredContent = filterDebugContent(result.content);
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: filteredContent } : m,
              ),
            );
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
                timestamp: new Date().toISOString(),
              },
            ]);
          }
        }
      } catch (error) {
        console.error("SemanticSearchCard: Failed to send message:", error);
        
        if (error.message === "Request was stopped by user") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, role: "system", content: "Request stopped by user" }
                : m,
            ),
          );
        } else {
          let errorText = "Failed to send message";
          
          if (error.message) {
            if (error.message.includes("Stream ended but received an unexpected final response")) {
              errorText = "Chat service temporarily unavailable. Please try again.";
            } else {
              errorText = typeof error.message === "string"
                ? error.message
                : JSON.stringify(error.message);
            }
          }

          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, role: "system", content: `Error: ${errorText}` }
                : m,
            ),
          );
          
          setError(errorText);
        }
      } finally {
        setIsSending(false);
        abortControllerRef.current = null;
      }
    }, [inputText, isSending, sessionId, ragDebugEnabled, filterDebugContent, detectCSVGeneration]);

    const handleKeyPress = (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        handleSendMessage();
      }
    };

    const formatMessageTime = (timestamp) => {
      if (!timestamp) return "";
      const date = new Date(timestamp);
      return date.toLocaleTimeString([], {
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
        title="Chat"
        {...props}
      >
        {isMinimized ? (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              p: 1,
            }}
          >
            <ChatIcon sx={{ mr: 1 }} />
            <Typography variant="body2">
              {messages.length > 0
                ? `${messages.length} messages`
                : "No messages"}
            </Typography>
          </Box>
        ) : (
          <>
            {}
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
                        {message.role === "user" ? "You" : message.role === "system" ? "System" : "Assistant"}
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
                      {truncateMessage(filterDebugContent(message.content))}
                    </Typography>
                  </ListItem>
                ))}
              </List>
            )}

            {}
            {!isLoading && messages.length === 0 && (
              <Typography
                variant="body2"
                sx={{
                  color: "text.secondary",
                  textAlign: "center",
                  py: 2,
                }}
              >
                No messages yet. Start a conversation!
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
                placeholder="Type a message..."
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
          </>
        )}
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
