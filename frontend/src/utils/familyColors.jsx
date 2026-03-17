// frontend/src/utils/familyColors.js
// Shared visual identity for Uncle Claude / Nephew / Family features.
import React from "react";
import PropTypes from "prop-types";
import { Chip } from "@mui/material";
import {
  Psychology as PsychologyIcon,
  SmartToy as SmartToyIcon,
  Hub as HubIcon,
  AutoFixHigh as AutoFixHighIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Warning as WarningIcon,
  Lock as LockIcon,
  Schedule as ScheduleIcon,
} from "@mui/icons-material";

// Uncle Claude: warm amber/gold — authoritative mentor
// Nephew (Guaardvark): theme primary — the local AI
// Family (other nodes): steel blue — networked peers
// Self-Improvement: theme success — autonomous fixes
export const UNCLE_GOLD = "#FFB300";
export const FAMILY_BLUE = "#5C8AE6";

export const getFamilyColors = (theme) => ({
  uncle: UNCLE_GOLD,
  nephew: theme.palette.primary.main,
  family: FAMILY_BLUE,
  selfImprovement: theme.palette.success.main,
  danger: theme.palette.error.main,
});

export const SOURCE_ICONS = {
  uncle_claude: PsychologyIcon,
  nephew: SmartToyIcon,
  family: HubIcon,
  self_improvement: AutoFixHighIcon,
};

export const SOURCE_LABELS = {
  uncle_claude: "Uncle Claude",
  nephew: "Guaardvark",
  family: "Family Node",
  self_improvement: "Self-Improvement",
};

const STATUS_MAP = {
  connected: { color: "success", icon: <CheckCircleIcon fontSize="inherit" /> },
  online: { color: "success", icon: <CheckCircleIcon fontSize="inherit" /> },
  success: { color: "success", icon: <CheckCircleIcon fontSize="inherit" /> },
  offline: { color: "default", icon: <ErrorIcon fontSize="inherit" /> },
  error: { color: "error", icon: <ErrorIcon fontSize="inherit" /> },
  failed: { color: "error", icon: <ErrorIcon fontSize="inherit" /> },
  warning: { color: "warning", icon: <WarningIcon fontSize="inherit" /> },
  locked: { color: "error", icon: <LockIcon fontSize="inherit" /> },
  running: { color: "warning", icon: <ScheduleIcon fontSize="inherit" /> },
  enabled: { color: "success", icon: <CheckCircleIcon fontSize="inherit" /> },
  disabled: { color: "default", icon: <ErrorIcon fontSize="inherit" /> },
};

// Source-to-background color mapping for chip variants
const SOURCE_COLORS = {
  uncle_claude: UNCLE_GOLD,
  family: FAMILY_BLUE,
};

/**
 * Unified status chip used across all Uncle/Nephew/Family surfaces.
 * @param {string} source - "uncle_claude" | "family" | "self_improvement" | "nephew"
 * @param {string} status - "connected" | "offline" | "success" | "failed" | "locked" | "enabled" | "disabled" | "running"
 * @param {string} [label] - Override label text (defaults to SOURCE_LABELS[source] or status)
 * @param {object} [sx] - Additional sx overrides
 */
export const StatusChip = ({ source, status, label, sx, ...props }) => {
  const Icon = SOURCE_ICONS[source];
  const statusInfo = STATUS_MAP[status] || STATUS_MAP.offline;
  const chipLabel = label || SOURCE_LABELS[source] || status;
  const bgColor = SOURCE_COLORS[source];

  return (
    <Chip
      icon={Icon ? <Icon fontSize="small" /> : statusInfo.icon}
      label={chipLabel}
      size="small"
      variant={bgColor ? "filled" : "outlined"}
      color={bgColor ? undefined : statusInfo.color}
      sx={{
        ...(bgColor && {
          bgcolor: bgColor,
          color: "#000",
          "& .MuiChip-icon": { color: "#000" },
        }),
        fontWeight: 500,
        fontSize: "0.7rem",
        ...sx,
      }}
      {...props}
    />
  );
};

StatusChip.propTypes = {
  source: PropTypes.string.isRequired,
  status: PropTypes.string.isRequired,
  label: PropTypes.string,
  sx: PropTypes.object,
};
