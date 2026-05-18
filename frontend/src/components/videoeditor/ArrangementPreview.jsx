// Ordered list of "clip A 0:00-0:03 [filter] → transition → clip B ...".
// Renders the Plan pipeline's arrangement.clips. Read-only in A1; A3 adds
// drag-to-reorder + per-cut transition swap.

import React from "react";
import { Box, Stack, Chip, Typography } from "@mui/material";

const fmtTime = (s) => {
  const m = Math.floor(s / 60);
  const r = (s - m * 60).toFixed(1);
  return `${m}:${r.padStart(4, "0")}`;
};

const ArrangementPreview = ({ arrangement }) => {
  if (!arrangement || !arrangement.clips || arrangement.clips.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary">
        Hit Plan to generate an arrangement.
      </Typography>
    );
  }

  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">
        Arrangement · style: {arrangement.style_recipe_name} · seed: {arrangement.seed}
      </Typography>
      {arrangement.clips.map((c, i) => (
        <Box
          key={`${c.clip_id}-${i}`}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            p: 0.5,
            borderRadius: 0.5,
            border: 1,
            borderColor: "divider",
            backgroundColor: "background.paper",
          }}
        >
          <Chip size="small" label={c.section_label} sx={{ minWidth: 60 }} />
          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
            {fmtTime(c.timeline_start)}-{fmtTime(c.timeline_end)}
          </Typography>
          <Typography variant="caption" sx={{ flexGrow: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {c.clip_id}
          </Typography>
          {c.filter_preset && c.filter_preset !== "none" && (
            <Chip size="small" label={c.filter_preset} color="primary" variant="outlined" />
          )}
          {i < arrangement.clips.length - 1 && c.transition_to_next !== "hard-cut" && (
            <Chip size="small" label={`→ ${c.transition_to_next}`} variant="outlined" />
          )}
        </Box>
      ))}
    </Stack>
  );
};

export default ArrangementPreview;
