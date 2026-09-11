import React, { useState, useCallback, useRef, useEffect } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  IconButton,
  ListItem,
  Tooltip,
  useTheme,
} from "@mui/material";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import ThinkingCard from "./ThinkingCard";

/**
 * FloatingChatMessage - Renders an individual message item inside FloatingChatCard.
 * Supports copying prompts (user messages) and responses (assistant/system messages)
 * to clipboard with feedback tooltip and check icon matching ChatPage's MessageItem.
 */
const FloatingChatMessage = ({ message, msg, formatTime }) => {
  const m = message || msg || {};
  const theme = useTheme();
  const [copied, setCopied] = useState(false);
  const timerRef = useRef(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
  }, []);

  const isUser = m.role === "user";
  const isAssistant = m.role === "assistant";

  const handleCopy = useCallback(
    async (e) => {
      if (e) {
        e.stopPropagation();
      }

      const text =
        typeof m.content === "string"
          ? m.content
          : m.content != null
          ? JSON.stringify(m.content, null, 2)
          : "";
      if (!text) return;

      try {
        if (navigator?.clipboard?.writeText) {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          if (timerRef.current) clearTimeout(timerRef.current);
          timerRef.current = setTimeout(() => setCopied(false), 1500);
        } else {
          throw new Error("Clipboard API not available");
        }
      } catch {
        // Fallback for non-HTTPS or unsupported contexts
        try {
          const ta = document.createElement("textarea");
          ta.value = text;
          ta.style.position = "fixed";
          ta.style.opacity = "0";
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          setCopied(true);
          if (timerRef.current) clearTimeout(timerRef.current);
          timerRef.current = setTimeout(() => setCopied(false), 1500);
        } catch (err) {
          console.error("Copy failed:", err);
        }
      }
    },
    [m.content]
  );

  const formattedTime = formatTime
    ? formatTime(m.timestamp)
    : m.timestamp
    ? new Date(m.timestamp).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

  if (isUser) {
    return (
      <ListItem
        disableGutters
        disablePadding
        sx={{
          flexDirection: "column",
          alignItems: "flex-end",
          py: 0.5,
          width: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 0.5,
            width: "100%",
            // Reveal the user-message copy affordance on hover (matching ChatPage)
            "&:hover .msg-user-copy": { opacity: 1 },
            "&:focus-within .msg-user-copy": { opacity: 1 },
          }}
        >
          {Boolean(m.content) && (
            <Tooltip title={copied ? "Copied" : "Copy"}>
              <IconButton
                className="msg-user-copy"
                size="small"
                onClick={handleCopy}
                aria-label="Copy message"
                sx={{
                  p: 0.25,
                  alignSelf: "center",
                  opacity: copied ? 1 : 0,
                  transition: "opacity 0.15s",
                  "@media (hover: none)": { opacity: 0.6 },
                  "&:hover": { opacity: 1 },
                }}
              >
                {copied ? (
                  <CheckIcon sx={{ fontSize: 14, color: "success.main" }} />
                ) : (
                  <ContentCopyIcon sx={{ fontSize: 14, opacity: 0.6 }} />
                )}
              </IconButton>
            </Tooltip>
          )}
          <Box
            sx={{
              maxWidth: "85%",
              bgcolor: "primary.main",
              color: "#fff",
              borderRadius: "12px 12px 2px 12px",
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
              {m.content || ""}
            </Typography>
          </Box>
        </Box>
        {formattedTime && (
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.65rem",
              color: "text.disabled",
              mt: 0.25,
              px: 0.5,
            }}
          >
            {formattedTime}
          </Typography>
        )}
      </ListItem>
    );
  }

  // Response (assistant / system / error)
  return (
    <ListItem
      disableGutters
      disablePadding
      sx={{
        flexDirection: "column",
        alignItems: "flex-start",
        py: 0.5,
        width: "100%",
        "&:hover .msg-response-copy": { opacity: 0.9 },
        "&:focus-within .msg-response-copy": { opacity: 0.9 },
      }}
    >
      <Box
        sx={{
          maxWidth: "85%",
          bgcolor:
            m.role === "system"
              ? "error.dark"
              : theme.palette.mode === "dark"
              ? "rgba(255,255,255,0.06)"
              : "rgba(0,0,0,0.04)",
          color: m.role === "system" ? "#fff" : "text.primary",
          borderRadius: "12px 12px 12px 2px",
          px: 1.5,
          py: 0.75,
        }}
      >
        {isAssistant &&
          typeof m.thinking === "string" &&
          m.thinking.trim() && (
            <ThinkingCard
              text={m.thinking}
              streaming={false}
              defaultExpanded={false}
            />
          )}
        <Typography
          variant="body2"
          sx={{
            fontSize: "0.82rem",
            wordBreak: "break-word",
            whiteSpace: "pre-wrap",
            lineHeight: 1.5,
          }}
        >
          {m.content || ""}
        </Typography>
        {m.truncated === true && (
          <Typography
            variant="caption"
            sx={{
              display: "block",
              mt: 0.5,
              fontStyle: "italic",
              color: "text.secondary",
              opacity: 0.8,
            }}
          >
            Response reached the output limit.
          </Typography>
        )}
        {Boolean(m.content) && (
          <Box
            sx={{
              mt: 0.25,
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
            }}
          >
            <Tooltip title={copied ? "Copied" : "Copy"}>
              <IconButton
                className="msg-response-copy"
                size="small"
                onClick={handleCopy}
                aria-label="Copy message"
                sx={{
                  p: 0.25,
                  color: "inherit",
                  opacity: copied ? 1 : 0.45,
                  transition: "opacity 0.15s",
                  "&:hover": { opacity: 1 },
                }}
              >
                {copied ? (
                  <CheckIcon sx={{ fontSize: 14, color: "success.main" }} />
                ) : (
                  <ContentCopyIcon sx={{ fontSize: 14 }} />
                )}
              </IconButton>
            </Tooltip>
          </Box>
        )}
      </Box>
      {formattedTime && (
        <Typography
          variant="caption"
          sx={{
            fontSize: "0.65rem",
            color: "text.disabled",
            mt: 0.25,
            px: 0.5,
          }}
        >
          {formattedTime}
        </Typography>
      )}
    </ListItem>
  );
};

FloatingChatMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string,
    role: PropTypes.string,
    content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
    thinking: PropTypes.string,
    truncated: PropTypes.bool,
    timestamp: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  }),
  msg: PropTypes.shape({
    id: PropTypes.string,
    role: PropTypes.string,
    content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
    thinking: PropTypes.string,
    truncated: PropTypes.bool,
    timestamp: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  }),
  formatTime: PropTypes.func,
};

export default React.memo(FloatingChatMessage);
