// frontend/src/components/settings/SettingsRow.jsx
// Single row: label (left) + control (right) for SettingsPage
// stacked: label on top, content below (for wide content like RAG stats)

import React from "react";
import { Box, Typography } from "@mui/material";

const SettingsRow = ({ label, icon, children, stacked, ...props }) => {
  if (stacked) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 1.5,
          py: 1.5,
          px: 0,
          borderBottom: 1,
          borderColor: "divider",
          "&:last-of-type": { borderBottom: 0 },
        }}
        {...props}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          {icon && (
            <Box sx={{ color: "text.secondary", display: "flex", opacity: 0.7 }}>
              {icon}
            </Box>
          )}
          <Typography variant="body2" fontWeight={500}>
            {label}
          </Typography>
        </Box>
        <Box sx={{ width: "100%" }}>{children}</Box>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        py: 1.25,
        px: 0,
        gap: 2,
        borderBottom: 1,
        borderColor: "divider",
        "&:last-of-type": { borderBottom: 0 },
      }}
      {...props}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
        {icon && (
          <Box sx={{ color: "text.secondary", display: "flex", opacity: 0.7 }}>
            {icon}
          </Box>
        )}
        <Typography variant="body2" fontWeight={500}>
          {label}
        </Typography>
      </Box>
      <Box sx={{ flexShrink: 0 }}>{children}</Box>
    </Box>
  );
};

export default SettingsRow;
