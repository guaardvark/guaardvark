// frontend/src/components/settings/SettingsSection.jsx
// Reusable section wrapper for SettingsPage - Cursor/VS Code style

import React from "react";
import { Box, Typography } from "@mui/material";

const SettingsSection = ({ title, children, ...props }) => {
  return (
    <Box {...props}>
      <Typography
        variant="overline"
        sx={{
          fontSize: "0.75rem",
          letterSpacing: 1,
          color: "text.secondary",
          display: "block",
          mb: 1.5,
        }}
      >
        {title}
      </Typography>
      <Box sx={{ display: "flex", flexDirection: "column", gap: 0 }}>
        {children}
      </Box>
    </Box>
  );
};

export default SettingsSection;
