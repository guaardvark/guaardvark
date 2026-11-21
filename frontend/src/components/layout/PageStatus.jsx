// A page's read-only status in the header bar: what is loaded, at what size,
// what is queued. On a page that shows status the title makes way for it.

import React from "react";
import { createPortal } from "react-dom";
import PropTypes from "prop-types";
import { Box, Tooltip, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { usePageHeaderSlot } from "./pageHeaderSlot";

const TONE_KEY = { ok: "success", warn: "warning", error: "error", off: null };

/**
 * @param {string} label
 * @param {ReactNode} value
 * @param {string} [sub]      detail shown on hover
 * @param {"ok"|"warn"|"error"|"off"} [tone]  dot before the label; omit for none
 * @param {function} [onClick]  makes the chip a button
 */
export const StatusChip = ({ label, value, sub, tone, onClick }) => {
  const chip = (
    <Box
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => (e.key === "Enter" || e.key === " ") && onClick() : undefined}
      sx={(theme) => ({
        display: "inline-flex",
        alignItems: "baseline",
        gap: 0.75,
        minWidth: 0,
        maxWidth: 320,
        px: 1.25,
        py: 0.4,
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: "8px",
        cursor: onClick ? "pointer" : "default",
        "&:hover": onClick ? { borderColor: alpha(theme.palette.primary.main, 0.4) } : {},
      })}
    >
      <Typography
        component="span"
        sx={(theme) => ({
          display: "inline-flex",
          alignItems: "center",
          gap: 0.6,
          fontSize: "0.62rem",
          letterSpacing: 1,
          textTransform: "uppercase",
          color: "text.secondary",
          flexShrink: 0,
          "&::before": tone
            ? {
                content: '""',
                width: 6,
                height: 6,
                borderRadius: "50%",
                bgcolor: TONE_KEY[tone] ? theme.palette[TONE_KEY[tone]].main : alpha(theme.palette.text.primary, 0.25),
              }
            : undefined,
        })}
      >
        {label}
      </Typography>
      <Typography component="span" noWrap sx={{ fontSize: "0.8rem", color: "text.primary", minWidth: 0 }}>
        {value}
      </Typography>
    </Box>
  );
  return sub ? <Tooltip title={sub}>{chip}</Tooltip> : chip;
};

StatusChip.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.node,
  sub: PropTypes.node,
  tone: PropTypes.oneOf(["ok", "warn", "error", "off"]),
  onClick: PropTypes.func,
};

/** Shows its children (StatusChips) in the page header; nothing on a page without one. */
const PageStatus = ({ children }) => {
  const slot = usePageHeaderSlot("status");
  return slot ? createPortal(children, slot) : null;
};

PageStatus.propTypes = { children: PropTypes.node };

/** Shows the page's main action (Generate, Add to queue) in the header, where no card arrangement moves it. */
export const PagePrimaryAction = ({ children }) => {
  const slot = usePageHeaderSlot("primary");
  return slot ? createPortal(children, slot) : null;
};

PagePrimaryAction.propTypes = { children: PropTypes.node };

export default PageStatus;
