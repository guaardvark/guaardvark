// frontend/src/components/settings/SettingsCardWrapper.jsx
// Fixed-size card wrapper for settings sections.

import React from "react";
import { Paper, Box, Typography, Divider } from "@mui/material";

const SettingsCardWrapper = React.forwardRef(
  ({ title, icon, children, ...props }, ref) => {
    // Destructure react-grid-layout props to prevent them leaking to DOM
    const {
      i, x, y, w, h,
      minW, maxW, minH, maxH,
      isDraggable, isResizable, isBounded,
      static: staticProp, moved,
      resizeHandles, className, style,
      defaultCollapsed,
      ...restProps
    } = props;

    return (
      <Paper
        ref={ref}
        style={style}
        className={`settings-card ${className || ""}`}
        elevation={0}
        sx={{
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          border: 1,
          borderColor: "divider",
          borderRadius: "8px",
          ...restProps.sx,
        }}
        {...restProps}
      >
        {/* Header */}
        <Box
          className="settings-card-header"
          sx={{
            display: "flex",
            alignItems: "center",
            px: 1.5,
            py: 1,
            userSelect: "none",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.75 }}>
            {icon && (
              <Box sx={{ color: "text.secondary", display: "flex", alignItems: "center", opacity: 0.7 }}>
                {icon}
              </Box>
            )}
            <Typography
              variant="subtitle2"
              sx={{ fontWeight: 600, fontSize: "0.85rem" }}
            >
              {title}
            </Typography>
          </Box>
        </Box>

        {/* Content — always visible */}
        <Divider />
        <Box
          sx={{
            overflowX: "hidden",
            p: "15px",
          }}
        >
          {children}
        </Box>
      </Paper>
    );
  }
);

SettingsCardWrapper.displayName = "SettingsCardWrapper";

export default SettingsCardWrapper;
