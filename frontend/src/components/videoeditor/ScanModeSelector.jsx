// What auto-editor uses to decide kept vs cut: audio loudness, frame motion,
// or both (AND/OR). Default "both-and" — only segments where BOTH detectors
// agree are kept (tightest). User picks "both-or" for looser, "audio" for
// silent-removal classic.

import React from "react";
import { Box, ToggleButton, ToggleButtonGroup, Stack, Typography, Tooltip } from "@mui/material";

const MODES = [
  { value: "audio", label: "Audio", tip: "Cut silence based on audio loudness." },
  { value: "motion", label: "Motion", tip: "Cut still / motionless sections." },
  { value: "both-and", label: "Both (strict)", tip: "Keep only frames where BOTH detectors fire. Fewer kept frames, tighter edits." },
  { value: "both-or", label: "Both (loose)", tip: "Keep frames where EITHER detector fires. More kept frames, looser edits." },
];

const ScanModeSelector = ({ value, onChange, disabled }) => {
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">Detection mode</Typography>
      <ToggleButtonGroup
        size="small"
        value={value}
        exclusive
        onChange={(_, v) => v && onChange(v)}
        disabled={disabled}
        aria-label="Detection mode"
      >
        {MODES.map((m) => (
          <Tooltip key={m.value} title={m.tip}>
            <ToggleButton value={m.value} sx={{ textTransform: "none", px: 1, fontSize: "0.75rem" }}>
              {m.label}
            </ToggleButton>
          </Tooltip>
        ))}
      </ToggleButtonGroup>
    </Stack>
  );
};

export default ScanModeSelector;
