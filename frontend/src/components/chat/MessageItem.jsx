// frontend/src/components/chat/MessageItem.jsx
// Version 1.1: Renders a single message bubble with appropriate styling.
// Added support for agent loop messages with step-by-step visualization.
/* eslint-env browser */
import React from "react";
import PropTypes from "prop-types";
import { Box, Paper, Avatar, CardMedia, Chip, CircularProgress, Typography } from "@mui/material";
import ReactMarkdown from "react-markdown";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import PersonIcon from "@mui/icons-material/Person";
import ImageIcon from "@mui/icons-material/Image";
import { GuaardvarkLogo } from "../branding";
import { useAppStore } from "../../stores/useAppStore";
import { BASE_URL } from "../../api/apiClient";
import AgentResultDisplay from "./AgentResultDisplay";
import { StatusChip } from "../../utils/familyColors";
import ToolCallCard from "./ToolCallCard";

const UPLOAD_BASE_URL = BASE_URL + "/uploads";

const MessageItem = ({ message }) => {
  const isUser = message.role === "user";
  const isAgentLoop = message.isAgentLoop;
  const logo = useAppStore((s) => s.systemLogo);

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

  return (
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
        elevation={2}
        sx={{
          p: 1.5,
          maxWidth: "80%",
          bgcolor: isUser ? "primary.main" : "background.paper",
          color: isUser ? "primary.contrastText" : "text.primary",
          // Prevent theme-level Paper gradients (e.g. Musk) from covering user bubble bgcolor
          ...(isUser && { backgroundImage: 'none' }),
          borderTopLeftRadius: isUser ? 16 : 4,
          borderTopRightRadius: isUser ? 4 : 16,
          borderBottomLeftRadius: 16,
          borderBottomRightRadius: 16,
        }}
      >
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
                    onClick={() => window.open(img.url, "_blank")}
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
                    onClick={() => window.open(img.url, "_blank")}
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
      {isUser && (
        <Avatar sx={{ bgcolor: "secondary.main", width: 32, height: 32 }}>
          <PersonIcon fontSize="small" />
        </Avatar>
      )}
    </Box>
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
