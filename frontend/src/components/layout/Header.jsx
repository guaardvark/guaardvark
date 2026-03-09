import React from "react";
import { AppBar, Toolbar, Typography, IconButton, Box } from "@mui/material";
import MenuIcon from "@mui/icons-material/Menu";
import ModelStatusBar from "./ModelStatusBar";

// --- MODIFIED: Removed isSidebarOpen prop, accept only handleDrawerToggle ---
const Header = ({ handleDrawerToggle }) => {
  return (
    <AppBar
      position="fixed"
      sx={{
        // --- MODIFIED: Remove width and ml adjustments ---
        // AppBar will now naturally span full width and not be affected by sidebar
        // --- End Modification ---
        zIndex: (theme) => theme.zIndex.drawer + 1, // Ensure Header is above the Drawer
        boxShadow: "none",
        borderBottom: "1px solid",
        borderColor: "divider",
        backgroundColor: "background.paper",
        // Add transition for consistency IF sidebar pushes header (not in this setup)
        // transition: (theme) => theme.transitions.create(['width', 'margin'], { ... })
      }}
    >
      <Toolbar>
        {/* IconButton remains unchanged - always visible */}
        <IconButton
          color="inherit"
          aria-label="toggle drawer"
          edge="start"
          onClick={handleDrawerToggle}
          sx={{
            marginRight: 2,
            color: "text.primary",
          }}
        >
          <MenuIcon />
        </IconButton>

        <Typography
          variant="h6"
          noWrap
          component="div"
          sx={{ flexGrow: 1, color: "text.primary" }}
        >
          Dashboard {/* Title can be dynamic later */}
        </Typography>
        {/* Model Status Bar with multimodal capability dots */}
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <ModelStatusBar />
        </Box>
      </Toolbar>
    </AppBar>
  );
};

export default Header;
