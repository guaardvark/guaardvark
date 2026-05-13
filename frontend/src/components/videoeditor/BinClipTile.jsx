// One clip in the project bin. Shows thumbnail + a kept-vs-cut strip after Plan.
// Click to select, X to remove. Drag from MediaLibrary or OS desktop adds; no
// drag-out (the bin owns its clips once they're in).

import React from "react";
import { Box, IconButton, Tooltip, Typography, Chip } from "@mui/material";
import { Close as CloseIcon, WarningAmber as WarningIcon } from "@mui/icons-material";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// keptRanges is an array of [start, end] (seconds). We draw a horizontal strip
// the width of the tile, with green for kept, red for cut.
function KeptRangesStrip({ keptRanges, durationSeconds }) {
  if (!keptRanges || keptRanges.length === 0 || !durationSeconds) {
    return null;
  }
  const segments = [];
  let cursor = 0;
  const sorted = [...keptRanges].sort((a, b) => a[0] - b[0]);
  for (const [start, end] of sorted) {
    if (start > cursor) {
      segments.push({ start: cursor, end: start, kept: false });
    }
    segments.push({ start, end, kept: true });
    cursor = end;
  }
  if (cursor < durationSeconds) {
    segments.push({ start: cursor, end: durationSeconds, kept: false });
  }
  return (
    <Box sx={{ display: "flex", height: 6, width: "100%", borderRadius: 1, overflow: "hidden", mt: 0.5 }}>
      {segments.map((seg, i) => (
        <Box
          key={i}
          sx={{
            flexGrow: seg.end - seg.start,
            backgroundColor: seg.kept ? "success.main" : "error.dark",
            opacity: seg.kept ? 0.85 : 0.35,
          }}
        />
      ))}
    </Box>
  );
}

const BinClipTile = ({ clip, selected, onSelect, onRemove, warning }) => {
  const thumbUrl = clip.documentId
    ? `${API_BASE}/files/thumbnail?document_id=${clip.documentId}`
    : null;

  return (
    <Box
      onClick={() => onSelect(clip.clipId)}
      sx={{
        position: "relative",
        border: 2,
        borderColor: selected ? "primary.main" : "divider",
        borderRadius: 1,
        p: 0.75,
        cursor: "pointer",
        backgroundColor: selected ? "action.selected" : "background.paper",
        "&:hover": { borderColor: "primary.light" },
      }}
    >
      <Box
        sx={{
          aspectRatio: "16/9",
          width: "100%",
          backgroundColor: "background.default",
          borderRadius: 0.5,
          backgroundImage: thumbUrl ? `url(${thumbUrl})` : "none",
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      />
      <Tooltip title="Remove from bin">
        <IconButton
          size="small"
          onClick={(e) => { e.stopPropagation(); onRemove(clip.clipId); }}
          sx={{
            position: "absolute",
            top: 2, right: 2,
            backgroundColor: "rgba(0,0,0,0.6)",
            color: "common.white",
            width: 22, height: 22,
            "&:hover": { backgroundColor: "rgba(0,0,0,0.85)" },
          }}
        >
          <CloseIcon sx={{ fontSize: 14 }} />
        </IconButton>
      </Tooltip>
      {warning && (
        <Tooltip title={warning}>
          <Chip
            size="small"
            icon={<WarningIcon sx={{ fontSize: 14 }} />}
            label="scan"
            color="warning"
            sx={{
              position: "absolute",
              top: 2, left: 2,
              height: 18,
              fontSize: "0.65rem",
              "& .MuiChip-icon": { ml: 0.5, mr: -0.5 },
            }}
          />
        </Tooltip>
      )}
      <Typography
        variant="caption"
        sx={{ display: "block", mt: 0.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        title={clip.filename}
      >
        {clip.filename}
      </Typography>
      <KeptRangesStrip keptRanges={clip.keptRanges} durationSeconds={clip.durationSeconds} />
    </Box>
  );
};

export default BinClipTile;
