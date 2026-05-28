// One item in the project bin — a compact row: thumbnail/icon + name (+ kept-vs-
// cut strip after Plan) + remove. Click to select; X to remove. Drag from
// MediaLibrary or OS desktop adds; no drag-out (the bin owns its items).

import React from "react";
import { Box, IconButton, Tooltip, Typography } from "@mui/material";
import {
  Close as CloseIcon,
  WarningAmber as WarningIcon,
  Star as StarIcon,
} from "@mui/icons-material";
import MediaThumb from "./MediaThumb";

// keptRanges is an array of [start, end] (seconds) — a thin strip under the
// name, green for kept, red for cut. Only meaningful for video clips post-Plan.
function KeptRangesStrip({ keptRanges, durationSeconds }) {
  if (!keptRanges || keptRanges.length === 0 || !durationSeconds) return null;
  const segments = [];
  let cursor = 0;
  const sorted = [...keptRanges].sort((a, b) => a[0] - b[0]);
  for (const [start, end] of sorted) {
    if (start > cursor) segments.push({ start: cursor, end: start, kept: false });
    segments.push({ start, end, kept: true });
    cursor = end;
  }
  if (cursor < durationSeconds) segments.push({ start: cursor, end: durationSeconds, kept: false });
  return (
    <Box sx={{ display: "flex", height: 4, width: "100%", borderRadius: 1, overflow: "hidden", mt: 0.4 }}>
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

const BinClipTile = ({ clip, selected, onSelect, onRemove, warning, keptRanges, durationSeconds }) => {
  return (
    <Box
      onClick={() => onSelect(clip.clipId)}
      sx={{
        display: "flex",
        alignItems: "center",
        gap: 1,
        border: 2,
        borderColor: selected ? "primary.main" : "divider",
        borderRadius: 1,
        p: 0.5,
        cursor: "pointer",
        backgroundColor: selected ? "action.selected" : "background.paper",
        "&:hover": { borderColor: "primary.light" },
      }}
    >
      <MediaThumb documentId={clip.documentId} kind={clip.kind || "video"} size={44} />

      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          {clip.isMasterSong && (
            <Tooltip title="Master soundtrack">
              <StarIcon sx={{ fontSize: 14, color: "warning.main", flexShrink: 0 }} />
            </Tooltip>
          )}
          <Typography
            variant="caption"
            sx={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            title={clip.filename}
          >
            {clip.filename}
          </Typography>
          {warning && (
            <Tooltip title={warning}>
              <WarningIcon sx={{ fontSize: 14, color: "warning.main", flexShrink: 0 }} />
            </Tooltip>
          )}
        </Box>
        <KeptRangesStrip
          keptRanges={keptRanges ?? clip.keptRanges}
          durationSeconds={durationSeconds ?? clip.durationSeconds}
        />
      </Box>

      <Tooltip title="Remove from bin">
        <IconButton
          size="small"
          onClick={(e) => { e.stopPropagation(); onRemove(clip.clipId); }}
          sx={{ flexShrink: 0, width: 24, height: 24 }}
        >
          <CloseIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Tooltip>
    </Box>
  );
};

export default BinClipTile;
