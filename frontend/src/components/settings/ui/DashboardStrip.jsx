// frontend/src/components/settings/ui/DashboardStrip.jsx
// The read-only strip above the panels: what is loaded, how full the GPU is,
// what needs attention. Every tile reads an endpoint the page already polls.

import React from "react";
import { Box, LinearProgress, Paper, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import HelpTitle from "./HelpTitle";

const TONE_KEY = { ok: "success", warn: "warning", error: "error", off: null };

/**
 * @param {string} label
 * @param {ReactNode} [help]   shown on hover over the label
 * @param {ReactNode} value
 * @param {string} [sub]
 * @param {"ok"|"warn"|"error"|"off"} [tone]   dot beside the label; omit for none
 * @param {number} [progress]   0..100 draws a bar under the value
 * @param {function} [onClick]  makes the tile a link
 * @param {"normal"|"large"} [size]  large is about 1.5x, for tiles that are a page's main content;
 *   its value may take two lines
 */
export const DashboardTile = ({ label, help, value, sub, tone, progress, onClick, size = "normal" }) => {
  const large = size === "large";
  return (
    <Paper
      elevation={0}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => (e.key === "Enter" || e.key === " ") && onClick() : undefined}
      sx={(theme) => ({
        minWidth: 0,
        px: large ? 2.25 : 1.5,
        py: large ? 1.65 : 1.1,
        display: "flex",
        flexDirection: "column",
        gap: large ? 0.5 : 0.25,
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: "8px",
        cursor: onClick ? "pointer" : "default",
        "&:hover": onClick ? { borderColor: alpha(theme.palette.primary.main, 0.4) } : {},
      })}
    >
      <Typography
        component="span"
        sx={(theme) => ({
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          fontSize: large ? "0.78rem" : "0.66rem",
          letterSpacing: 1,
          textTransform: "uppercase",
          color: "text.secondary",
          "&::before": tone
            ? {
                content: '""',
                width: large ? 8 : 6,
                height: large ? 8 : 6,
                borderRadius: "50%",
                bgcolor: TONE_KEY[tone] ? theme.palette[TONE_KEY[tone]].main : alpha(theme.palette.text.primary, 0.25),
                flexShrink: 0,
              }
            : undefined,
        })}
      >
        <HelpTitle help={help}>{label}</HelpTitle>
      </Typography>
      <Typography
        component="span"
        sx={
          large
            ? {
                fontSize: "1rem",
                lineHeight: 1.35,
                color: "text.primary",
                overflow: "hidden",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
              }
            : { fontSize: "0.82rem", color: "text.primary", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }
        }
      >
        {value}
      </Typography>
      {typeof progress === "number" ? (
        <LinearProgress
          variant="determinate"
          value={Math.max(0, Math.min(100, progress))}
          color={progress > 90 ? "error" : progress > 70 ? "warning" : "success"}
          sx={{ height: 5, borderRadius: 3, mt: 0.5 }}
        />
      ) : (
        sub && (
          <Typography
            variant="caption"
            sx={{
              color: "text.secondary",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              ...(large ? { fontSize: "0.82rem" } : {})
            }}
          >
            {sub}
          </Typography>
        )
      )}
    </Paper>
  );
};

const DashboardStrip = ({ children }) => (
  <Box
    sx={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
      gap: 1.25,
    }}
  >
    {children}
  </Box>
);

export default DashboardStrip;
