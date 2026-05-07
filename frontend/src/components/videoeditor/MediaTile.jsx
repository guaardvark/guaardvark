// frontend/src/components/videoeditor/MediaTile.jsx
//
// Renders a single media item (video / audio / image) for the Media
// Library panel. Supports two display variants:
//   - 'grid'  → square tile with thumb + filename caption (matches the
//               original VideoEditorPage look)
//   - 'list'  → compact horizontal row with 32px thumb + filename + a
//               metadata sub-line (duration / size when available)
//
// Drag handlers are forwarded — the panel attaches them so the timeline
// drop-targets keep working unchanged.
import React from "react";
import { Box, Paper, Typography } from "@mui/material";
import {
  MovieFilter as VideoIcon,
  GraphicEq as AudioIcon,
  Image as ImageIcon,
} from "@mui/icons-material";

const ICONS = {
  video: VideoIcon,
  audio: AudioIcon,
  image: ImageIcon,
};

const ICON_COLORS = {
  video: "primary.main",
  audio: "#9c27b0",
  image: "info.main",
};

const formatDuration = (sec) => {
  if (sec == null || !isFinite(sec)) return null;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
};

const formatSize = (bytes) => {
  if (!bytes || !isFinite(bytes)) return null;
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)}KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)}GB`;
};

// Single-row metadata line for list mode. Duration first (more useful),
// size second. Returns null if neither is known so we don't render a
// sad empty line.
const _metaLine = (item) => {
  const dur = formatDuration(item?.metadata?.duration_seconds);
  const size = formatSize(item?.size);
  const parts = [dur, size].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
};

// Common drag wiring — keeps both variants aligned on what they hand
// over to the timeline drop-target. dataTransfer payload shape was set
// by the original page; we preserve it exactly.
const _attachDrag = (item, kind, onDragStart) => ({
  draggable: true,
  onDragStart: (e) => onDragStart && onDragStart(e, item, kind),
});

const MediaTile = ({ item, kind, variant = "grid", onClick, onDragStart, selected = false }) => {
  const Icon = ICONS[kind] || VideoIcon;
  const iconColor = ICON_COLORS[kind] || "primary.main";
  const thumb = item?.thumbnail_url || null;
  const drag = _attachDrag(item, kind, onDragStart);

  if (variant === "list") {
    return (
      <Paper
        variant="outlined"
        {...drag}
        onClick={() => onClick?.(item, kind)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1,
          py: 0.75,
          cursor: "grab",
          borderColor: selected ? iconColor : "divider",
          "&:hover": { bgcolor: "action.hover", borderColor: iconColor },
          "&:active": { cursor: "grabbing" },
        }}
      >
        <Box
          sx={{
            width: 32,
            height: 32,
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            bgcolor: thumb ? "transparent" : "rgba(0,0,0,0.04)",
            borderRadius: 0.5,
            overflow: "hidden",
          }}
        >
          {thumb ? (
            <Box
              component="img"
              src={thumb}
              loading="lazy"
              sx={{ width: "100%", height: "100%", objectFit: "cover" }}
              onError={(e) => {
                // Thumb missing → swap to icon. Keeps the row from
                // showing a broken-image glyph if ffmpeg fails.
                e.target.style.display = "none";
                e.target.nextSibling && (e.target.nextSibling.style.display = "flex");
              }}
            />
          ) : null}
          <Box
            sx={{
              display: thumb ? "none" : "flex",
              alignItems: "center",
              justifyContent: "center",
              width: "100%",
              height: "100%",
            }}
          >
            <Icon sx={{ fontSize: 18, color: iconColor }} />
          </Box>
        </Box>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.75rem",
              display: "block",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              lineHeight: 1.2,
            }}
            title={item.filename}
          >
            {item.filename}
          </Typography>
          {_metaLine(item) && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ fontSize: "0.65rem", lineHeight: 1.2 }}
            >
              {_metaLine(item)}
            </Typography>
          )}
        </Box>
      </Paper>
    );
  }

  // Grid (default) — preserves the look from the original page.
  return (
    <Paper
      variant="outlined"
      {...drag}
      onClick={() => onClick?.(item, kind)}
      sx={{
        p: 1,
        cursor: "grab",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 0.5,
        borderColor: selected ? iconColor : "divider",
        "&:hover": { bgcolor: "action.hover", borderColor: iconColor },
        "&:active": { cursor: "grabbing" },
        aspectRatio: "1 / 1",
        justifyContent: "center",
        textAlign: "center",
      }}
    >
      {thumb ? (
        <Box
          component="img"
          src={thumb}
          loading="lazy"
          sx={{ maxWidth: "100%", maxHeight: 60, objectFit: "contain", borderRadius: 0.5 }}
          onError={(e) => {
            // Same fallback dance as list mode.
            e.target.style.display = "none";
            e.target.nextSibling && (e.target.nextSibling.style.display = "flex");
          }}
        />
      ) : null}
      <Box
        sx={{
          display: thumb ? "none" : "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Icon sx={{ fontSize: 36, color: iconColor }} />
      </Box>
      <Typography
        variant="caption"
        sx={{
          fontSize: "0.65rem",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          width: "100%",
          lineHeight: 1.2,
        }}
        title={item.filename}
      >
        {item.filename}
      </Typography>
    </Paper>
  );
};

export default MediaTile;
