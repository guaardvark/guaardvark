import React from "react";
import PropTypes from "prop-types";
import { Box, CardMedia, Typography } from "@mui/material";
import { guardedMediaSrc } from "../../utils/assetGuard";
import { useUiTranslation } from "../../i18n";

/**
 * The pictures and videos a reply produced (`message.generatedImages`:
 * `{url, alt, caption, type}`, `type: "video"` for a clip), as a chat bubble
 * shows them. Clicking a picture calls `onOpenPicture(pictures, index)` with
 * the reply's pictures as `{url, name}`, so the host can open its lightbox on
 * that one and step through the rest.
 */
const GeneratedMedia = ({ items, onOpenPicture, maxWidth = 400, maxHeight = 400 }) => {
  const { t } = useUiTranslation();
  if (!Array.isArray(items) || items.length === 0) return null;

  const openPicture = (img) => {
    if (!onOpenPicture) return;
    const pictures = items
      .filter((i) => i.type !== "video")
      .map((i) => ({ url: i.url, name: i.alt || i.caption || t("chat.generatedMedia.image") }));
    const idx = pictures.findIndex((i) => i.url === img.url);
    onOpenPicture(pictures, Math.max(0, idx));
  };

  return (
    <Box sx={{ mb: 1 }}>
      {items.map((img, idx) => (
        <Box key={idx} sx={{ mb: 1 }}>
          {img.type === "video" ? (
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
    })
  ),
  onOpenPicture: PropTypes.func,
  maxWidth: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  maxHeight: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
};

export default GeneratedMedia;
