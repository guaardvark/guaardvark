// frontend/src/components/chat/MessageItem.jsx
// Version 1.1: Renders a single message bubble with appropriate styling.
// Added support for agent loop messages with step-by-step visualization.
/* eslint-env browser */
import React, { useState, useCallback, useEffect } from "react";
import PropTypes from "prop-types";
import { Box, Paper, Avatar, CardMedia, Chip, CircularProgress, Typography } from "@mui/material";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
// No user avatar icon — user messages are clean right-aligned bubbles
import ImageIcon from "@mui/icons-material/Image";
import ThumbUpOutlinedIcon from "@mui/icons-material/ThumbUpOutlined";
import ThumbDownOutlinedIcon from "@mui/icons-material/ThumbDownOutlined";
import ThumbUpIcon from "@mui/icons-material/ThumbUp";
import ThumbDownIcon from "@mui/icons-material/ThumbDown";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import MemoryIcon from "@mui/icons-material/Memory";
import Tooltip from "@mui/material/Tooltip";
import IconButton from "@mui/material/IconButton";
import { GuaardvarkLogo } from "../branding";
import { useAppStore } from "../../stores/useAppStore";
import { BASE_URL } from "../../api/apiClient";
import AgentResultDisplay from "./AgentResultDisplay";
import { StatusChip } from "../../utils/familyColors";
import ToolCallCard from "./ToolCallCard";
import ImageLightbox from "../images/ImageLightbox";
import NarrateButton from "../common/NarrateButton";

const UPLOAD_BASE_URL = BASE_URL + "/uploads";

const MessageItem = ({ message }) => {
  const isUser = message.role === "user";

  // Read narrate button visibility from voice settings (localStorage)
  const [showNarrate, setShowNarrate] = useState(() => {
    try {
      const vs = localStorage.getItem('guaardvark_voiceSettings');
      if (vs) {
        const parsed = JSON.parse(vs);
        return parsed.showNarrateButtons !== false;
      }
    } catch {}
    return true;
  });

  useEffect(() => {
    const handler = () => {
      try {
        const vs = localStorage.getItem('guaardvark_voiceSettings');
        if (vs) {
          const parsed = JSON.parse(vs);
          setShowNarrate(parsed.showNarrateButtons !== false);
        }
      } catch {}
    };
    window.addEventListener('voiceSettingsChanged', handler);
    window.addEventListener('storage', handler);
    return () => {
      window.removeEventListener('voiceSettingsChanged', handler);
      window.removeEventListener('storage', handler);
    };
  }, []);
  const isCommand = message.type === "command";
  const isProgress = message.type === "progress";
  const isAgentLoop = message.isAgentLoop;
  const logo = useAppStore((s) => s.systemLogo);
  const [lightbox, setLightbox] = useState(null);
  const [feedback, setFeedback] = useState(null); // null | "up" | "down"
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    const text = typeof message.content === "string" ? message.content : JSON.stringify(message.content, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // fallback for non-HTTPS contexts
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }, [message.content]);

  const handleFeedback = useCallback(async (positive) => {
    const newVal = positive ? "up" : "down";
    if (feedback === newVal) { setFeedback(null); return; }
    setFeedback(newVal);
    try {
      const content = typeof message.content === "string" ? message.content : "";
      await fetch(`${BASE_URL}/agent-control/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          positive,
          task: content.slice(0, 200),
          session_id: message.sessionId || null,
          type: "response",
        }),
      });
    } catch (err) {
      console.error("Feedback failed:", err);
    }
  }, [feedback, message.content, message.sessionId]);

  const openLightbox = useCallback((url, name, images, index) => {
    setLightbox({ url, name, images: images || [{ url, name }], index: index || 0 });
  }, []);

  const handleLightboxDownload = useCallback(() => {
    if (!lightbox) return;
    const img = lightbox.images[lightbox.index];
    const link = document.createElement("a");
    link.href = img.url;
    link.download = img.name || "image";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightbox]);

  // Construct the full logo URL if logo path exists
  const logoUrl = logo ? `${UPLOAD_BASE_URL}/${logo}` : undefined;

  // Handle agent loop messages specially
  if (isAgentLoop) {
    const isThinking = message.agentLoopStatus === "thinking";
    const isComplete = message.agentLoopStatus === "complete";
    const hasError = message.agentLoopStatus === "error";

    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "flex-start",
          flexDirection: "row",
          alignItems: "flex-start",
          gap: 1,
        }}
      >
        <Avatar
          sx={{
            bgcolor: isThinking ? "warning.main" : isComplete ? "success.main" : hasError ? "error.main" : "grey.500",
            width: 32,
            height: 32,
            border: 1,
            borderColor: "divider",
          }}
        >
          <GuaardvarkLogo
            size={20}
            variant={isThinking ? "warning" : isComplete ? "success" : hasError ? "error" : "default"}
            animate={isThinking}
          />
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
            borderColor: isThinking ? "warning.main" : isComplete ? "success.main" : hasError ? "error.main" : "grey.500",
          }}
        >
          {isThinking ? (
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <CircularProgress size={16} color="warning" />
              <Typography variant="body2" color="text.secondary">
                {message.content || "Agent is reasoning..."}
              </Typography>
            </Box>
          ) : (
            <>
              <AgentResultDisplay
                result={message.agentResult}
                isLoading={false}
              />
              {message.content && !message.agentResult?.final_answer && (
                <Typography variant="body2" sx={{ mt: 1 }}>
                  {message.content}
                </Typography>
              )}
            </>
          )}
        </Paper>
      </Box>
    );
  }

  // Progress messages (e.g., "Generating image...") — styled like active streaming messages
  if (isProgress) {
    return (
      <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1 }}>
        <Avatar
          src={logoUrl}
          sx={{
            bgcolor: "warning.main",
            width: 32,
            height: 32,
            border: 1,
            borderColor: "divider",
          }}
        >
          {!logo && <GuaardvarkLogo size={20} variant="warning" animate />}
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
            borderColor: "warning.main",
            minWidth: 200,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <CircularProgress size={14} color="warning" />
            <Typography variant="body2" color="text.secondary">
              {message.content || "Processing..."}
            </Typography>
          </Box>
        </Paper>
      </Box>
    );
  }

  return (
    <>
    <Box
      sx={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 1,
      }}
    >
      {!isUser && (
        <Avatar
          src={logoUrl}
          sx={{
            bgcolor: "primary.main",
            width: 32,
            height: 32,
            border: 1,
            borderColor: "divider"
          }}
        >
          {!logo && <GuaardvarkLogo size={20} />}
        </Avatar>
      )}
      <Paper
        elevation={isCommand ? 0 : 2}
        sx={{
          p: 1.5,
          maxWidth: "80%",
          bgcolor: isCommand ? "action.hover" : isUser ? "primary.main" : "background.paper",
          color: isCommand ? "text.secondary" : isUser ? "primary.contrastText" : "text.primary",
          // Command messages get a colored left border and subtle styling
          ...(isCommand && {
            borderLeft: "3px solid",
            borderLeftColor: "info.main",
            borderRadius: 1,
            fontSize: "0.85rem",
            fontFamily: "monospace",
          }),
          // Prevent theme-level Paper gradients (e.g. Musk) from covering user bubble bgcolor
          ...(isUser && !isCommand && { backgroundImage: 'none' }),
          ...(!isCommand && {
            borderTopLeftRadius: isUser ? 16 : 4,
            borderTopRightRadius: isUser ? 4 : 16,
            borderBottomLeftRadius: 16,
            borderBottomRightRadius: 16,
          }),
        }}
      >
        {/* Command badge */}
        {isCommand && (
          <Chip label="command" size="small" variant="outlined" color="info"
            sx={{ height: 18, fontSize: "0.65rem", mb: 0.5, fontFamily: "monospace" }} />
        )}
        {/* Display image if present */}
        {(message.imageUrl || message.relatedImageUrl) && (
          <Box sx={{ mb: 1 }}>
            <CardMedia
              component="img"
              sx={{
                maxWidth: 300,
                maxHeight: 200,
                width: 'auto',
                height: 'auto',
                borderRadius: 1,
                border: '1px solid',
                borderColor: 'divider',
                objectFit: 'contain'
              }}
              image={message.imageUrl || message.relatedImageUrl}
              alt={message.imageFileName || "Uploaded image"}
            />
            {message.imageFileName && (
              <Chip
                icon={<ImageIcon />}
                label={message.imageFileName}
                size="small"
                variant="outlined"
                sx={{ mt: 0.5 }}
              />
            )}
          </Box>
        )}

        {/* Generated images and videos (from agent tool calls) */}
        {message.generatedImages && message.generatedImages.length > 0 && (
          <Box sx={{ mb: 1 }}>
            {message.generatedImages.map((img, idx) => (
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
                      maxHeight: 400,
                      width: "auto",
                      height: "auto",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      display: "block",
                      cursor: "pointer",
                    }}
                    src={img.url}
                  />
                ) : (
                  <CardMedia
                    component="img"
                    sx={{
                      maxWidth: 400,
                      maxHeight: 400,
                      width: "auto",
                      height: "auto",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      objectFit: "contain",
                      cursor: "pointer",
                    }}
                    image={img.url}
                    alt={img.alt || "Generated image"}
                    onClick={() => {
                      const images = message.generatedImages.filter(i => i.type !== "video").map(i => ({ url: i.url, name: i.alt || i.caption || "Generated image" }));
                      const idx = images.findIndex(i => i.url === img.url);
                      openLightbox(img.url, img.alt || "Generated image", images, Math.max(0, idx));
                    }}
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

        {/* Thinking context — persisted from the streaming phase */}
        {message.thinkingText && message.toolCalls?.length > 0 && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1, mt: 0.5 }}>
            <CircularProgress size={14} color="warning" sx={{ opacity: 0.6 }} />
            <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
              {message.thinkingText}
            </Typography>
          </Box>
        )}

        {/* Unified chat tool call cards (displayed inline before the response text) */}
        {message.isUnifiedChat && message.toolCalls && message.toolCalls.length > 0 && (
          <Box sx={{ mb: 1 }}>
            {message.toolCalls.map((step, stepIdx) =>
              (step.tool_calls || []).map((tc, tcIdx) => (
                <ToolCallCard
                  key={`${stepIdx}-${tcIdx}`}
                  toolName={tc.tool_name}
                  params={tc.params}
                  result={tc.success != null ? {
                    success: tc.success,
                    output: tc.output_preview,
                    error: tc.success ? null : tc.output_preview,
                  } : null}
                  durationMs={tc.duration_ms}
                  isPending={false}
                  sessionId={message.sessionId}
                />
              ))
            )}
          </Box>
        )}

        <Box
          sx={{
            userSelect: 'text',
            WebkitUserSelect: 'text',
            MozUserSelect: 'text',
            msUserSelect: 'text',
            cursor: 'text',
            fontSize: '0.75rem', // Match other cards' font size
            '& p': {
              fontSize: '0.75rem',
              margin: '0.25rem 0',
            },
            '& pre': {
              fontSize: '0.7rem',
            },
            '& code': {
              fontSize: '0.7rem',
            },
            '& ul, & ol': {
              fontSize: '0.75rem',
              paddingLeft: '1.25rem',
            },
            '& li': {
              fontSize: '0.75rem',
              margin: '0.125rem 0',
            },
            '& h1, & h2, & h3, & h4, & h5, & h6': {
              fontSize: '0.85rem',
              fontWeight: 'bold',
              margin: '0.5rem 0',
            },
            '& img': {
              maxWidth: '100%',
              borderRadius: '4px',
              border: '1px solid',
              borderColor: 'divider',
              marginTop: '0.5rem',
              display: 'block',
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
            {typeof message.content === 'string' ? message.content : JSON.stringify(message.content, null, 2)}
          </ReactMarkdown>
        </Box>
        {/* Feedback + narrate for assistant messages */}
        {!isUser && message.content && typeof message.content === 'string' && message.content.length > 10 && (
          <Box sx={{ mt: 0.5, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 0.25 }}>
            {message.toolCalls && message.toolCalls.some(tc => tc.tool_name === "search_memory") && (
              <Tooltip title="Recalled from memory">
                <Box sx={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: 0.5, 
                  mr: 1, 
                  px: 0.75, 
                  py: 0.25, 
                  borderRadius: 1, 
                  bgcolor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider"
                }}>
                  <MemoryIcon sx={{ fontSize: 12, color: "primary.main" }} />
                  <Typography variant="caption" sx={{ fontSize: "0.65rem", color: "text.secondary" }}>
                    Recalled
                  </Typography>
                </Box>
              </Tooltip>
            )}
            <Tooltip title={copied ? "Copied" : "Copy"}>
              <IconButton size="small" onClick={handleCopy} sx={{ p: 0.25 }}>
                {copied ? (
                  <CheckIcon sx={{ fontSize: 14, color: "success.main" }} />
                ) : (
                  <ContentCopyIcon sx={{ fontSize: 14, opacity: 0.4 }} />
                )}
              </IconButton>
            </Tooltip>
            <Tooltip title="Good response">
              <IconButton size="small" onClick={() => handleFeedback(true)} sx={{ p: 0.25 }}>
                {feedback === "up" ? (
                  <ThumbUpIcon sx={{ fontSize: 14, color: "success.main" }} />
                ) : (
                  <ThumbUpOutlinedIcon sx={{ fontSize: 14, opacity: 0.4 }} />
                )}
              </IconButton>
            </Tooltip>
            <Tooltip title="Bad response">
              <IconButton size="small" onClick={() => handleFeedback(false)} sx={{ p: 0.25 }}>
                {feedback === "down" ? (
                  <ThumbDownIcon sx={{ fontSize: 14, color: "error.main" }} />
                ) : (
                  <ThumbDownOutlinedIcon sx={{ fontSize: 14, opacity: 0.4 }} />
                )}
              </IconButton>
            </Tooltip>
            {showNarrate && <NarrateButton text={message.content} size="small" />}
          </Box>
        )}
        {/* Source badge for Uncle Claude / Family / Self-Improvement responses */}
        {message.badge && (
          <Box sx={{ mt: 1, display: "flex", justifyContent: "flex-end" }}>
            <StatusChip
              source={message.source || "nephew"}
              status="connected"
              label={message.badge}
              sx={{ height: 20, fontSize: "0.65rem" }}
            />
          </Box>
        )}
      </Paper>
      {/* No avatar for user messages — clean right-aligned bubbles */}
    </Box>
    {lightbox && (
      <ImageLightbox
        imageUrl={lightbox.images[lightbox.index]?.url || lightbox.url}
        imageName={lightbox.images[lightbox.index]?.name || lightbox.name}
        onClose={() => setLightbox(null)}
        onPrev={() => setLightbox(prev => ({ ...prev, index: Math.max(0, prev.index - 1) }))}
        onNext={() => setLightbox(prev => ({ ...prev, index: Math.min(prev.images.length - 1, prev.index + 1) }))}
        onDownload={handleLightboxDownload}
        hasPrev={lightbox.index > 0}
        hasNext={lightbox.index < lightbox.images.length - 1}
      />
    )}
    </>
  );
};

MessageItem.propTypes = {
  message: PropTypes.shape({
    role: PropTypes.string,
    content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
    isAgentLoop: PropTypes.bool,
    agentLoopStatus: PropTypes.oneOf(["thinking", "complete", "error"]),
    agentResult: PropTypes.object,
    imageUrl: PropTypes.string,
    relatedImageUrl: PropTypes.string,
    imageFileName: PropTypes.string,
    isUnifiedChat: PropTypes.bool,
    toolCalls: PropTypes.array,
    generatedImages: PropTypes.array,
    badge: PropTypes.string,
    source: PropTypes.string,
  }).isRequired,
};

export default React.memo(MessageItem);
