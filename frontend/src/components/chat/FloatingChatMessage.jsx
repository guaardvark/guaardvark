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
import { alpha } from "@mui/material/styles";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import MicIcon from "@mui/icons-material/Mic";
import ThinkingCard from "./ThinkingCard";
import GeneratedMedia from "./GeneratedMedia";
import ImageLightbox from "../images/ImageLightbox";
import { agentNoteCaption } from "./agentNoteCaptions";
import copyText from "../../utils/copyText";
import { useUiTranslation, uiLocale } from "../../i18n";

/**
 * FloatingChatMessage - Renders an individual message item inside FloatingChatCard.
 * Supports copying prompts (user messages) and responses (assistant/system messages)
 * to clipboard with feedback tooltip and check icon matching ChatPage's MessageItem.
 * A reply's generated pictures and videos render as in MessageItem, and a picture
 * opens in the same lightbox.
 */
const FloatingChatMessage = ({ message, msg, formatTime }) => {
  const { t } = useUiTranslation();
  const m = message || msg || {};
  const theme = useTheme();
  const [copied, setCopied] = useState(false);
  const [lightbox, setLightbox] = useState(null); // { images: [{url, name}], index }
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

      // copyText falls back to execCommand for non-HTTPS or unsupported contexts.
      if (await copyText(text)) {
        setCopied(true);
        if (timerRef.current) clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => setCopied(false), 1500);
      } else {
        console.error("Copy failed");
      }
    },
    [m.content]
  );

  const handleLightboxDownload = useCallback(() => {
    const img = lightbox?.images[lightbox.index];
    if (!img) return;
    const link = document.createElement("a");
    link.href = img.url;
    link.download = img.name || "image";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightbox]);

  const formattedTime = formatTime
    ? formatTime(m.timestamp)
    : m.timestamp
    ? new Date(m.timestamp).toLocaleTimeString(uiLocale(), {
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
            <Tooltip title={copied ? t("chat.message.copied") : t("chat.message.copy")}>
              <IconButton
                className="msg-user-copy"
                size="small"
                onClick={handleCopy}
                aria-label={t("chat.message.copyMessage")}
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
              // Half-opaque so the bubble reads over whatever the floating
              // card is sitting on without hiding it entirely.
              bgcolor: alpha(theme.palette.primary.main, 0.5),
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
              {m.voice && (
                <MicIcon
                  titleAccess={t("chat.message.spoken")}
                  data-testid="voice-message-badge"
                  sx={{ fontSize: 13, verticalAlign: "-2px", mr: 0.5, opacity: 0.85 }}
                />
              )}
              {m.content || ""}
            </Typography>
            {m.agentNote && (
              <Typography
                variant="caption"
                sx={{ display: "block", fontSize: "0.68rem", fontStyle: "italic", opacity: 0.85 }}
              >
                {agentNoteCaption(m.agentNote)}
              </Typography>
            )}
          </Box>
        </Box>
        {formattedTime && (
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.65rem",
              color: "text.secondary",
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
          // Half-opaque paper: the old 6% tint let text get lost against
          // bright page content behind the floating card.
          bgcolor:
            m.role === "system"
              ? "error.dark"
              : alpha(theme.palette.background.paper, 0.5),
          color: m.role === "system" ? "#fff" : "text.primary",
          borderRadius: "12px 12px 12px 2px",
          px: 1.5,
          py: 0.75,
        }}
      >
        {isAssistant && (
          <GeneratedMedia
            items={m.generatedImages}
            onOpenPicture={(images, index) => setLightbox({ images, index })}
            maxWidth="100%"
            maxHeight={300}
          />
        )}
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
            {t("chat.message.outputLimit")}
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
            <Tooltip title={copied ? t("chat.message.copied") : t("chat.message.copy")}>
              <IconButton
                className="msg-response-copy"
                size="small"
                onClick={handleCopy}
                aria-label={t("chat.message.copyMessage")}
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
            color: "text.secondary",
            mt: 0.25,
            px: 0.5,
          }}
        >
          {formattedTime}
        </Typography>
      )}
      {lightbox && (
        <ImageLightbox
          imageUrl={lightbox.images[lightbox.index]?.url}
          imageName={lightbox.images[lightbox.index]?.name}
          onClose={() => setLightbox(null)}
          onPrev={() => setLightbox((prev) => ({ ...prev, index: Math.max(0, prev.index - 1) }))}
          onNext={() => setLightbox((prev) => ({ ...prev, index: Math.min(prev.images.length - 1, prev.index + 1) }))}
          onDownload={handleLightboxDownload}
          hasPrev={lightbox.index > 0}
          hasNext={lightbox.index < lightbox.images.length - 1}
        />
      )}
    </ListItem>
  );
};

const mediaItemShape = PropTypes.shape({
  url: PropTypes.string,
  alt: PropTypes.string,
  caption: PropTypes.string,
  type: PropTypes.string,
});

FloatingChatMessage.propTypes = {
  message: PropTypes.shape({
    id: PropTypes.string,
    role: PropTypes.string,
    content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
    thinking: PropTypes.string,
    truncated: PropTypes.bool,
    generatedImages: PropTypes.arrayOf(mediaItemShape),
    timestamp: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  }),
  msg: PropTypes.shape({
    id: PropTypes.string,
    role: PropTypes.string,
    content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
    thinking: PropTypes.string,
    truncated: PropTypes.bool,
    generatedImages: PropTypes.arrayOf(mediaItemShape),
    timestamp: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  }),
  formatTime: PropTypes.func,
};

export default React.memo(FloatingChatMessage);
