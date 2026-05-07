// frontend/src/components/videoeditor/FolderTile.jsx
//
// Renders a folder ("batch") in the Media Library panel. Shows up to
// three child thumbnails stacked behind a count badge in grid mode, or
// a single-row folder + count in list mode. Click → drill into the
// folder. NOT draggable on purpose: dragging a folder onto a track is
// ambiguous and multi-clip-per-track is v2 work.
import React from "react";
import { Box, Paper, Typography, Chip } from "@mui/material";
import {
  Folder as FolderIcon,
  ChevronRight as ChevronRightIcon,
} from "@mui/icons-material";

// Filter helper: a thumbnail strip with all-null entries (audio folder
// with no thumbs, mixed batch with thumbs missing) just renders an
// icon. Mixed null+url strips collapse to the urls only.
const _renderableThumbs = (preview_thumbs = []) =>
  preview_thumbs.filter(Boolean).slice(0, 3);

const FolderTile = ({ group, variant = "grid", onOpen }) => {
  if (!group?.folder) return null;
  const { folder, items, preview_thumbs = [] } = group;
  const count = items?.length ?? 0;
  const thumbs = _renderableThumbs(preview_thumbs);

  if (variant === "list") {
    return (
      <Paper
        variant="outlined"
        onClick={() => onOpen?.(group)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1,
          py: 0.75,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover", borderColor: "warning.main" },
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
            color: "warning.main",
          }}
        >
          <FolderIcon sx={{ fontSize: 22 }} />
        </Box>
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.75rem",
              fontWeight: 600,
              display: "block",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              lineHeight: 1.2,
            }}
            title={folder.name}
          >
            {folder.name}
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: "0.65rem", lineHeight: 1.2 }}
          >
            {count} {count === 1 ? "item" : "items"}
          </Typography>
        </Box>
        <ChevronRightIcon fontSize="small" sx={{ color: "text.disabled" }} />
      </Paper>
    );
  }

  // Grid — square tile with stacked thumbnail preview if any thumbs
  // exist, otherwise a folder icon. Count badge top-right always shows.
  return (
    <Paper
      variant="outlined"
      onClick={() => onOpen?.(group)}
      sx={{
        p: 1,
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 0.5,
        position: "relative",
        "&:hover": { bgcolor: "action.hover", borderColor: "warning.main" },
        aspectRatio: "1 / 1",
        justifyContent: "center",
        textAlign: "center",
      }}
    >
      <Chip
        size="small"
        label={count}
        color="warning"
        sx={{
          position: "absolute",
          top: 4,
          right: 4,
          height: 18,
          fontSize: "0.65rem",
          fontWeight: 700,
          minWidth: 22,
          "& .MuiChip-label": { px: 0.5 },
        }}
      />
      {thumbs.length > 0 ? (
        <Box sx={{ position: "relative", width: 60, height: 60 }}>
          {thumbs.map((url, idx) => (
            <Box
              key={url + idx}
              component="img"
              src={url}
              loading="lazy"
              onError={(e) => {
                // Hide a single broken thumb without nuking the others
                e.target.style.opacity = "0";
              }}
              sx={{
                position: "absolute",
                top: idx * 4,
                left: idx * 4,
                width: 52,
                height: 52,
                objectFit: "cover",
                borderRadius: 0.5,
                border: "1px solid rgba(0,0,0,0.15)",
                bgcolor: "background.paper",
                zIndex: thumbs.length - idx,
              }}
            />
          ))}
        </Box>
      ) : (
        <FolderIcon sx={{ fontSize: 42, color: "warning.main" }} />
      )}
      <Typography
        variant="caption"
        sx={{
          fontSize: "0.65rem",
          fontWeight: 600,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          width: "100%",
          lineHeight: 1.2,
        }}
        title={folder.name}
      >
        {folder.name}
      </Typography>
    </Paper>
  );
};

export default FolderTile;
