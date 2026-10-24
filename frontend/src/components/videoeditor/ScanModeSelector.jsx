// What auto-editor uses to decide kept vs cut: audio loudness, frame motion,
// or both (AND/OR). Default "both-and" — only segments where BOTH detectors
// agree are kept (tightest). User picks "both-or" for looser, "audio" for
// silent-removal classic.

import React from "react";
import { ToggleButton, ToggleButtonGroup, Stack, Typography, Tooltip } from "@mui/material";
import { useUiTranslation, translate } from "../../i18n";

const modes = () => [
  { value: "audio", label: translate("videoEditor.scanMode.audio"), tip: translate("videoEditor.scanMode.audioTip") },
  { value: "motion", label: translate("videoEditor.scanMode.motion"), tip: translate("videoEditor.scanMode.motionTip") },
  { value: "both-and", label: translate("videoEditor.scanMode.bothAnd"), tip: translate("videoEditor.scanMode.bothAndTip") },
  { value: "both-or", label: translate("videoEditor.scanMode.bothOr"), tip: translate("videoEditor.scanMode.bothOrTip") },
];

const ScanModeSelector = ({ value, onChange, disabled }) => {
  const { t } = useUiTranslation();
  return (
    <Stack spacing={0.5}>
      <Typography variant="caption" color="text.secondary">{t("videoEditor.scanMode.title")}</Typography>
      <ToggleButtonGroup
        size="small"
        value={value}
        exclusive
        onChange={(_, v) => v && onChange(v)}
        disabled={disabled}
        aria-label={t("videoEditor.scanMode.title")}
      >
        {modes().map((m) => (
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
