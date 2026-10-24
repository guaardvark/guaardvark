// frontend/src/components/settings/ui/SettingChip.jsx
// A setting that is on or off. Pill shape marks it as state, as opposed to the
// rectangular ActionButton: lit (tonal fill) when on, thin outline when off, no
// glyph either way. Clicking flips it at once; a deferred effect (next start,
// nightly) is said in `note`, never implied.

import React from "react";
import { Box, ButtonBase, Tooltip, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import { useUiTranslation } from "../../../i18n";

export const chipSx = (theme, on, disabled) => ({
  height: 28,
  px: 1.5,
  borderRadius: "14px",
  border: "1px solid",
  borderColor: on ? alpha(theme.palette.primary.main, 0.55) : alpha(theme.palette.text.primary, 0.18),
  bgcolor: on ? alpha(theme.palette.primary.main, 0.18) : "transparent",
  color: on ? theme.palette.text.primary : theme.palette.text.secondary,
  fontSize: "0.78rem",
  fontFamily: theme.typography.fontFamily,
  fontWeight: on ? 500 : 400,
  lineHeight: 1,
  whiteSpace: "nowrap",
  gap: 0.75,
  opacity: disabled ? 0.45 : 1,
  transition: "background-color 120ms, border-color 120ms",
  "&:hover": disabled
    ? {}
    : { bgcolor: on ? alpha(theme.palette.primary.main, 0.26) : alpha(theme.palette.text.primary, 0.06) },
  "&.Mui-focusVisible": { outline: `2px solid ${alpha(theme.palette.primary.main, 0.6)}`, outlineOffset: 1 },
});

/**
 * @param {string}   label
 * @param {boolean}  on
 * @param {function} onToggle       called with the next boolean
 * @param {boolean}  [disabled]
 * @param {string}   [tooltip]      why it is disabled, or what it does
 * @param {string}   [note]         small trailing text: a count, "next start", a size
 * @param {function} [onSettings]   shows a gear that opens the feature's own settings
 * @param {string}   [testId]
 */
const SettingChip = ({ label, on, onToggle, disabled = false, tooltip = "", note, onSettings, testId, sx }) => {
  const { t } = useUiTranslation();
  const chip = (
    <ButtonBase
      role="switch"
      aria-checked={Boolean(on)}
      aria-label={label}
      disabled={disabled}
      onClick={() => !disabled && onToggle?.(!on)}
      data-testid={testId}
      sx={(theme) => ({ ...chipSx(theme, on, disabled), ...(onSettings ? { pr: 0.5 } : {}), ...(typeof sx === "function" ? sx(theme) : sx) })}
    >
      <span>{label}</span>
      {note && (
        <Typography component="span" sx={{ fontSize: "0.7rem", color: "text.secondary", opacity: on ? 0.9 : 0.75 }}>
          {note}
        </Typography>
      )}
      {onSettings && (
        <Box
          component="span"
          role="button"
          aria-label={t("settings.ui.chipSettings", { label })}
          onClick={(e) => {
            e.stopPropagation();
            onSettings();
          }}
          sx={(theme) => ({
            ml: 0.25,
            pl: 0.75,
            display: "inline-flex",
            alignItems: "center",
            borderLeft: "1px solid",
            borderColor: alpha(theme.palette.text.primary, 0.12),
            color: "text.secondary",
            "&:hover": { color: "text.primary" },
          })}
        >
          <SettingsOutlinedIcon sx={{ fontSize: 14 }} />
        </Box>
      )}
    </ButtonBase>
  );
  if (!tooltip) return chip;
  return (
    <Tooltip title={tooltip}>
      <span style={{ display: "inline-flex" }}>{chip}</span>
    </Tooltip>
  );
};

export default SettingChip;
