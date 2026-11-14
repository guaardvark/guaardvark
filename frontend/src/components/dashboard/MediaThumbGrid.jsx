// Thumbnails of the latest pictures or clips on a dashboard card. A click opens
// the Media page's details window (how it was made, Regenerate, Adjust & Retry);
// right-click is that page's file menu. A clip plays muted while pointed at.

import React, { useRef, useState } from "react";
import { Alert, Box, CircularProgress, Typography } from "@mui/material";
import { PlayArrow } from "@mui/icons-material";
import EntityContextMenu from "../common/EntityContextMenu";
import useContextMenu from "../../hooks/useContextMenu";
import useMediaActions from "../media/useMediaActions";
import { guardedMediaSrc } from "../../utils/assetGuard";
import { mediaPrompt, mediaUrl, thumbnailUrl } from "./recentMedia";
import { useUiTranslation } from "../../i18n";

// Pointing at a clip this long starts its preview; a pass across the grid does not.
const PREVIEW_DELAY_MS = 250;

/**
 * @param {Array} items   library document rows, newest first
 * @param {boolean} loading
 * @param {string|null} error
 * @param {string} emptyText   shown when the library answered with none
 * @param {boolean} video   the items are clips
 * @param {function} onChanged   an item was edited or a copy was made
 */
const MediaThumbGrid = ({ items, loading, error, emptyText, video = false, onChanged }) => {
  const { t } = useUiTranslation();
  const media = useMediaActions({ onEdited: onChanged });
  const menu = useContextMenu();
  const [previewId, setPreviewId] = useState(null);
  const previewTimer = useRef(null);

  const startPreview = (id) => {
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => setPreviewId(id), PREVIEW_DELAY_MS);
  };
  const stopPreview = () => {
    clearTimeout(previewTimer.current);
    setPreviewId(null);
  };

  if (loading && items.length === 0) {
    return <CircularProgress size={22} sx={{ display: "block", mx: "auto", my: 2 }} />;
  }
  if (error) {
    return (
      <Alert severity="warning" sx={{ my: 1 }}>
        {error}
      </Alert>
    );
  }
  if (items.length === 0) {
    return (
      <Typography variant="body2" sx={{ color: "text.secondary", my: 2, textAlign: "center" }}>
        {emptyText}
      </Typography>
    );
  }

  return (
    <>
      <Box
        className="non-draggable"
        sx={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(88px, 1fr))",
          gap: 0.5,
          mb: 1,
        }}
      >
        {items.map((doc) => {
          const name = doc.filename || "";
          const label = mediaPrompt(doc) || name;
          return (
            <Box
              key={doc.id}
              component="button"
              type="button"
              title={label}
              aria-label={t("dashboard.media.open", { name })}
              onClick={() => media.onFileClick(doc, items)}
              onContextMenu={(e) => menu.open(e, doc)}
              onMouseEnter={video ? () => startPreview(doc.id) : undefined}
              onMouseLeave={video ? stopPreview : undefined}
              sx={{
                position: "relative",
                aspectRatio: "1 / 1",
                p: 0,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                overflow: "hidden",
                cursor: "pointer",
                bgcolor: "grey.900",
                lineHeight: 0,
                transition: "transform 0.12s ease, border-color 0.12s ease",
                "&:hover, &:focus-visible": { borderColor: "primary.main", transform: "scale(1.03)", zIndex: 1 },
                "& img, & video": { position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" },
              }}
            >
              <img src={guardedMediaSrc(thumbnailUrl(doc))} alt="" loading="lazy" draggable={false} />
              {video && previewId === doc.id && (
                <video src={mediaUrl(doc)} muted loop autoPlay playsInline />
              )}
              {video && previewId !== doc.id && (
                <PlayArrow
                  sx={{
                    position: "absolute",
                    right: 2,
                    bottom: 2,
                    fontSize: 20,
                    color: "common.white",
                    bgcolor: "rgba(0,0,0,0.55)",
                    borderRadius: "50%",
                  }}
                />
              )}
            </Box>
          );
        })}
      </Box>
      <EntityContextMenu
        anchorPosition={menu.anchorPosition}
        onClose={menu.close}
        actions={menu.payload ? media.fileMenuActions(menu.payload) : []}
      />
      {media.overlays}
    </>
  );
};

export default MediaThumbGrid;
