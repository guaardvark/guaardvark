import React from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, CardMedia, CircularProgress, Link, Typography } from "@mui/material";
import { guardedMediaSrc } from "../../utils/assetGuard";
import { useUiTranslation } from "../../i18n";

/**
 * The pictures, videos and spoken audio a reply produced (`message.generatedImages`:
 * `{url, alt, caption, type}`, `type: "video"` for a clip, `"audio"` for speech), as a chat bubble
 * shows them. Clicking a picture calls `onOpenPicture(pictures, index)` with
 * the reply's pictures as `{url, name}`, so the host can open its lightbox on
 * that one and step through the rest.
 *
 * `pending` lists clips the reply queued that are still rendering
 * (pendingMedia.js); each shows as a placeholder until the clip replaces it.
 */
const GeneratedMedia = ({ items, pending, onOpenPicture, maxWidth = 400, maxHeight = 400 }) => {
  const { t } = useUiTranslation();
  const list = Array.isArray(items) ? items : [];
  const waiting = Array.isArray(pending) ? pending : [];
  if (list.length === 0 && waiting.length === 0) return null;

  const openPicture = (img) => {
    if (!onOpenPicture) return;
    const pictures = list
      .filter((i) => i.type !== "video" && i.type !== "audio")
      .map((i) => ({ url: i.url, name: i.alt || i.caption || t("chat.generatedMedia.image") }));
    const idx = pictures.findIndex((i) => i.url === img.url);
    onOpenPicture(pictures, Math.max(0, idx));
  };

  return (
    <Box sx={{ mb: 1 }}>
      {list.map((img, idx) => (
        <Box key={idx} sx={{ mb: 1 }}>
          {img.type === "audio" ? (
            <Box
              component="audio"
              controls
              aria-label={img.alt || t("chat.generatedMedia.audio")}
              sx={{ width: "100%", maxWidth, display: "block" }}
              src={guardedMediaSrc(img.url)}
            />
          ) : img.type === "video" ? (
            <Box
              component="video"
              controls
              autoPlay
              loop
              muted
              sx={{
                maxWidth,
                maxHeight,
                width: "auto",
                height: "auto",
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                display: "block",
                cursor: "pointer",
              }}
              src={guardedMediaSrc(img.url)}
              poster={img.poster ? guardedMediaSrc(img.poster) : undefined}
            />
          ) : (
            <CardMedia
              component="img"
              sx={{
                maxWidth,
                maxHeight,
                width: "auto",
                height: "auto",
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                objectFit: "contain",
                cursor: "pointer",
              }}
              image={guardedMediaSrc(img.url)}
              alt={img.alt || t("chat.generatedMedia.image")}
              onClick={() => openPicture(img)}
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
      {waiting.map((clip) => (
        <Box
          key={clip.batch_id}
          role="status"
          sx={{
            mb: 1,
            p: 1.5,
            maxWidth,
            borderRadius: 1,
            border: "1px dashed",
            borderColor: clip.status === "failed" ? "error.main" : "divider",
            display: "flex",
            alignItems: "center",
            gap: 1.25,
          }}
        >
          {clip.status !== "failed" && <CircularProgress size={16} />}
          <Box>
            <Typography variant="body2" color={clip.status === "failed" ? "error" : "text.secondary"}>
              {clip.status === "failed"
                ? t("chat.generatedMedia.videoFailed", { error: clip.error || "" })
                : t("chat.generatedMedia.videoPending")}
            </Typography>
            {clip.studio_url && (
              <Link component={RouterLink} to={clip.studio_url} variant="caption">
                {t("chat.generatedMedia.openInVideoGen")}
              </Link>
            )}
          </Box>
        </Box>
      ))}
    </Box>
  );
};

GeneratedMedia.propTypes = {
  items: PropTypes.arrayOf(
    PropTypes.shape({
      url: PropTypes.string,
      alt: PropTypes.string,
      caption: PropTypes.string,
      type: PropTypes.string,
      poster: PropTypes.string,
    })
  ),
  pending: PropTypes.arrayOf(
    PropTypes.shape({
      batch_id: PropTypes.string,
      studio_url: PropTypes.string,
      status: PropTypes.string,
      error: PropTypes.string,
    })
  ),
  onOpenPicture: PropTypes.func,
  maxWidth: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  maxHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

export default GeneratedMedia;
