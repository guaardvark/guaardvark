import React from "react";
import { alpha } from "@mui/material/styles";
import { SettingChip } from "../settings/ui";
import { synthesizedLabel, synthesizedTooltip } from "./synthesizedAnswer";
import { useUiTranslation } from "../../i18n";

/**
 * Read-only kit chip. SettingChip is the state pill (no glyph); it is not
 * toggled here — the flag is a fact about the turn.
 */
const SynthesizedAnswerChip = () => {
  useUiTranslation();
  return (
    <SettingChip
      label={synthesizedLabel()}
      on
      tooltip={synthesizedTooltip()}
      testId="synthesized-chip"
      sx={(theme) => ({
        cursor: "default",
        pointerEvents: "none",
        "&:hover": { bgcolor: alpha(theme.palette.primary.main, 0.18) },
      })}
    />
  );
};

export default SynthesizedAnswerChip;
