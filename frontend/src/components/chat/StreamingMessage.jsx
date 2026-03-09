/**
 * StreamingMessage - Builds up a message from Socket.IO streaming events.
 * Shows thinking indicator, tool call cards, and final text as they arrive.
 */
import React, { useEffect, useState, useRef, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Paper,
  Avatar,
  Typography,
  CircularProgress,
  Chip,
  CardMedia,
} from "@mui/material";
import { GuaardvarkLogo } from "../branding";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useAppStore } from "../../stores/useAppStore";
import { BASE_URL } from "../../api/apiClient";
import ToolCallCard from "./ToolCallCard";

const UPLOAD_BASE_URL = BASE_URL + "/uploads";

const StreamingMessage = ({ chatService, sessionId, onComplete }) => {
  const [status, setStatus] = useState("idle"); // idle | thinking | streaming | complete | error
  const [thinkingText, setThinkingText] = useState("");
  const [toolCalls, setToolCalls] = useState([]); // [{tool, params, result, durationMs, isPending}]
  const [content, setContent] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [tokenUsage, setTokenUsage] = useState(null); // {input_tokens, output_tokens} or null
  const [images, setImages] = useState([]); // [{url, alt, caption}]
  const mountedRef = useRef(true);
  const imagesRef = useRef([]); // Keep a ref for images to avoid stale closure in onComplete
  const logo = useAppStore((s) => s.systemLogo);
  const logoUrl = logo ? `${UPLOAD_BASE_URL}/${logo}` : undefined;

  // Use refs for values that change but shouldn't trigger listener re-registration
  const sessionIdRef = useRef(sessionId);
  const onCompleteRef = useRef(onComplete);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  useEffect(() => { onCompleteRef.current = onComplete; }, [onComplete]);

  // Register socket listeners ONCE per chatService instance.
  // Callbacks read from refs so they always have current values
  // without causing the useEffect to re-run.
  useEffect(() => {
    if (!chatService) return;
    mountedRef.current = true;

    chatService.onThinking((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      setStatus("thinking");
      setThinkingText(data.status || `Iteration ${data.iteration}...`);
    });

    chatService.onToolCall((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      setStatus("streaming");
      setToolCalls((prev) => [
        ...prev,
        {
          tool: data.tool,
          params: data.params,
          result: null,
          durationMs: null,
          isPending: true,
          reasoning: data.reasoning,
        },
      ]);
    });

    chatService.onToolResult((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      setToolCalls((prev) => {
        const updated = [...prev];
        for (let i = updated.length - 1; i >= 0; i--) {
          if (updated[i].tool === data.tool && updated[i].isPending) {
            updated[i] = {
              ...updated[i],
              result: data.result,
              durationMs: data.duration_ms,
              isPending: false,
            };
            break;
          }
        }
        return updated;
      });
    });

    chatService.onToken((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      setStatus("streaming");
      setContent((prev) => prev + (data.content || ""));
    });

    chatService.onComplete((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      setStatus("complete");
      if (data.response) {
        setContent(data.response);
      }
      if (data.token_usage && (data.token_usage.input_tokens || data.token_usage.output_tokens)) {
        setTokenUsage(data.token_usage);
      }
      if (onCompleteRef.current) {
        // Merge images from socket events with any from the complete payload
        const socketImages = imagesRef.current || [];
        const backendImages = (data.generated_images || []).map((img) => ({
          url: img.url,
          alt: img.alt || "Generated image",
          caption: img.caption || "",
        }));
        // Deduplicate by URL
        const seenUrls = new Set(socketImages.map((i) => i.url));
        const mergedImages = [
          ...socketImages,
          ...backendImages.filter((i) => !seenUrls.has(i.url)),
        ];
        onCompleteRef.current({
          content: data.response || "",
          toolCalls: data.steps || [],
          iterations: data.iterations || 0,
          aborted: data.aborted || false,
          sessionId: data.session_id,
          tokenUsage: data.token_usage || null,
          generatedImages: mergedImages,
        });
      }
    });

    chatService.onError((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      setStatus("error");
      setErrorMsg(data.error || "Unknown error");
    });

    chatService.onImage((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      const newImg = {
        url: data.image_url,
        alt: data.alt || "Generated image",
        caption: data.caption || "",
      };
      setImages((prev) => {
        const updated = [...prev, newImg];
        imagesRef.current = updated;
        return updated;
      });
    });

    chatService.onVideo((data) => {
      if (!mountedRef.current || data.session_id !== sessionIdRef.current) return;
      const newVid = {
        url: data.video_url,
        alt: data.alt || "Generated video",
        caption: data.caption || "",
        type: "video",
      };
      setImages((prev) => {
        const updated = [...prev, newVid];
        imagesRef.current = updated;
        return updated;
      });
    });

    return () => {
      mountedRef.current = false;
      chatService.cleanup();
    };
  }, [chatService]); // Only re-run when chatService instance changes

  // Don't render if idle (no events yet)
  if (status === "idle") {
    return (
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
        <Avatar sx={{ bgcolor: "primary.main", width: 32, height: 32 }}>
          {logoUrl ? (
            <Box component="img" src={logoUrl} sx={{ width: 32, height: 32 }} />
          ) : (
            <GuaardvarkLogo size={20} />
          )}
        </Avatar>
        <Paper
          elevation={2}
          sx={{
            p: 1.5,
            maxWidth: "85%",
            bgcolor: "background.paper",
            borderRadius: 2,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <CircularProgress size={14} />
            <Typography variant="body2" color="text.secondary">
              Processing...
            </Typography>
          </Box>
        </Paper>
      </Box>
    );
  }

  const isActive = status === "thinking" || status === "streaming";
  const borderColor =
    status === "error"
      ? "error.main"
      : status === "complete"
      ? "divider"
      : "warning.main";

  return (
    <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
      <Avatar
        src={logoUrl}
        sx={{
          bgcolor: isActive ? "warning.main" : "primary.main",
          width: 32,
          height: 32,
          border: 1,
          borderColor: "divider",
        }}
      >
        {isActive ? (
          <GuaardvarkLogo size={20} variant="warning" animate />
        ) : !logo ? (
          <GuaardvarkLogo size={20} />
        ) : null}
      </Avatar>

      <Paper
        elevation={2}
        sx={{
          p: 1.5,
          maxWidth: "85%",
          bgcolor: "background.paper",
          borderTopLeftRadius: 4,
          borderTopRightRadius: 16,
          borderBottomLeftRadius: 16,
          borderBottomRightRadius: 16,
          border: 1,
          borderColor,
          minWidth: 200,
        }}
      >
        {/* Thinking indicator */}
        {status === "thinking" && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: toolCalls.length > 0 ? 1 : 0 }}>
            <CircularProgress size={14} color="warning" />
            <Typography variant="body2" color="text.secondary">
              {thinkingText}
            </Typography>
          </Box>
        )}

        {/* Tool call cards */}
        {toolCalls.map((tc, i) => (
          <ToolCallCard
            key={`${tc.tool}-${i}`}
            toolName={tc.tool}
            params={tc.params}
            result={tc.result}
            durationMs={tc.durationMs}
            isPending={tc.isPending}
          />
        ))}

        {/* Inline images and videos (from tool results) */}
        {images.length > 0 && (
          <Box sx={{ mb: 1 }}>
            {images.map((img, idx) => (
              <Box key={idx} sx={{ mb: 1 }}>
                {img.type === "video" ? (
                  <Box
                    component="video"
                    controls
                    autoPlay
                    loop
                    muted
                    sx={{
                      maxWidth: 400,
                      maxHeight: 300,
                      width: "auto",
                      height: "auto",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      display: "block",
                    }}
                    src={img.url}
                  />
                ) : (
                  <CardMedia
                    component="img"
                    sx={{
                      maxWidth: 400,
                      maxHeight: 300,
                      width: "auto",
                      height: "auto",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      objectFit: "contain",
                    }}
                    image={img.url}
                    alt={img.alt}
                  />
                )}
                {img.caption && (
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ mt: 0.5, display: "block", fontStyle: "italic" }}
                  >
                    {img.caption}
                  </Typography>
                )}
              </Box>
            ))}
          </Box>
        )}

        {/* Text content */}
        {content && (
          <Box
            sx={{
              mt: toolCalls.length > 0 ? 1 : 0,
              fontSize: "0.75rem",
              "& p": { fontSize: "0.75rem", margin: "0.25rem 0" },
              "& pre": { fontSize: "0.7rem" },
              "& code": { fontSize: "0.7rem" },
              "& ul, & ol": { fontSize: "0.75rem", paddingLeft: "1.25rem" },
              "& li": { fontSize: "0.75rem", margin: "0.125rem 0" },
              "& h1, & h2, & h3, & h4, & h5, & h6": {
                fontSize: "0.85rem",
                fontWeight: "bold",
                margin: "0.5rem 0",
              },
            }}
          >
            <ReactMarkdown
              components={{
                code({ inline, className, children, ...props }) {
                  const match = /language-(\w+)/.exec(className || "");
                  return !inline && match ? (
                    <SyntaxHighlighter
                      style={a11yDark}
                      language={match[1]}
                      PreTag="div"
                      {...props}
                    >
                      {String(children).replace(/\n$/, "")}
                    </SyntaxHighlighter>
                  ) : (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
              }}
            >
              {content}
            </ReactMarkdown>
          </Box>
        )}

        {/* Error display */}
        {status === "error" && (
          <Typography variant="body2" color="error" sx={{ mt: 0.5 }}>
            Error: {errorMsg}
          </Typography>
        )}

        {/* Token usage */}
        {status === "complete" && tokenUsage && (
          <Box sx={{ mt: 0.75, display: "flex", gap: 0.5, flexWrap: "wrap" }}>
            <Chip
              label={`↑ ${tokenUsage.input_tokens.toLocaleString()} in`}
              size="small"
              variant="outlined"
              sx={{ fontSize: "0.6rem", height: 18, color: "text.disabled", borderColor: "divider" }}
            />
            <Chip
              label={`↓ ${tokenUsage.output_tokens.toLocaleString()} out`}
              size="small"
              variant="outlined"
              sx={{ fontSize: "0.6rem", height: 18, color: "text.disabled", borderColor: "divider" }}
            />
          </Box>
        )}
      </Paper>
    </Box>
  );
};

StreamingMessage.propTypes = {
  chatService: PropTypes.object.isRequired,
  sessionId: PropTypes.string.isRequired,
  onComplete: PropTypes.func,
};

export default StreamingMessage;
