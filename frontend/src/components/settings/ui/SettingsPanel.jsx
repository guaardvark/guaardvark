// frontend/src/components/settings/ui/SettingsPanel.jsx
// One panel of the Settings page: a title, an optional one-line description,
// optional header actions, and a body of clusters. `help` explains the panel on
// hover over the title. `danger` is reserved for the single panel that holds
// destructive actions.

import React from "react";
import { Box, Paper, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import HelpTitle from "./HelpTitle";

const SettingsPanel = ({ id, title, help, description, actions, danger = false, children, sx }) => (
  <Paper
    id={id}
    component="section"
    elevation={0}
    sx={(theme) => ({
      display: "flex",
      flexDirection: "column",
      minWidth: 0,
      border: "1px solid",
      borderColor: danger ? alpha(theme.palette.error.main, 0.35) : theme.palette.divider,
      borderRadius: "8px",
      overflow: "hidden",
      ...sx,
    })}
  >
    <Box
      sx={(theme) => ({
        display: "flex",
        alignItems: "center",
        gap: 1.25,
        px: 1.75,
        py: 1.1,
        borderBottom: "1px solid",
        borderColor: danger ? alpha(theme.palette.error.main, 0.25) : theme.palette.divider,
      })}
    >
      <Typography
        component="h2"
        sx={{ fontSize: "0.85rem", fontWeight: 700, color: danger ? "error.main" : "text.primary", whiteSpace: "nowrap" }}
      >
        <HelpTitle help={help}>{title}</HelpTitle>
      </Typography>
      {description && (
        <Typography
          variant="caption"
          sx={{ color: "text.secondary", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {description}
        </Typography>
      )}
      {actions && <Box sx={{ ml: "auto", display: "flex", gap: 0.75, alignItems: "center", flexShrink: 0 }}>{actions}</Box>}
    </Box>
    <Box sx={{ px: 1.75, pt: 1.25, pb: 1.5, display: "flex", flexDirection: "column", gap: 1.25 }}>{children}</Box>
  </Paper>
);

export default SettingsPanel;
