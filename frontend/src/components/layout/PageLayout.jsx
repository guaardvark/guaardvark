// frontend/src/components/layout/PageLayout.jsx
// Shared page layout component — standardizes page chrome across all pages.

import React from "react";
import {
  Box,
  Paper,
  Typography,
  ToggleButton,
  ToggleButtonGroup,
  Chip,
  IconButton,
  useTheme,
} from "@mui/material";
import ViewModuleIcon from "@mui/icons-material/ViewModule";
import ViewListIcon from "@mui/icons-material/ViewList";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import { useNavigate } from "react-router-dom";
import { spacing, typography as typoTokens } from "../../theme/tokens";

/**
 * PageLayout — wraps all pages with consistent chrome.
 *
 * @param {string}  title        — Page title (rendered in header)
 * @param {string}  variant      — "standard" | "fullscreen" | "grid"
 * @param {ReactNode} actions    — Right-side header action buttons
 * @param {{ mode: string, onToggle: function }} viewToggle — Card/table view toggle
 * @param {boolean} modelStatus  — Show active model chip in header
 * @param {boolean} noPadding    — Disable content padding (useful for fullscreen)
 * @param {ReactNode} headerContent — Extra content below the header bar
 * @param {ReactNode} children   — Page content
 */
const PageLayout = ({
  title,
  variant = "standard",
  actions,
  viewToggle,
  modelStatus,
  activeModel,
  noPadding = false,
  headerContent,
  children,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const showHeader = variant !== "fullscreen";
  const contentPadding = noPadding || variant === "grid" ? 0 : { xs: 1.5, sm: spacing.sectionGap };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {showHeader && (
        <Paper
          elevation={0}
          square
          sx={{
            borderBottom: 1,
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Box
            sx={{
              px: { xs: 1.5, sm: 2 },
              py: 1,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              minHeight: spacing.headerHeight,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
              <IconButton
                size="small"
                onClick={() => navigate(-1)}
                sx={{ opacity: 0.5, "&:hover": { opacity: 1 } }}
              >
                <ChevronLeftIcon fontSize="small" />
              </IconButton>
              <IconButton
                size="small"
                onClick={() => navigate(1)}
                sx={{ opacity: 0.5, "&:hover": { opacity: 1 }, mr: 1.5 }}
              >
                <ChevronRightIcon fontSize="small" />
              </IconButton>
              <Typography
                variant={typoTokens.pageTitle.variant}
                sx={{
                  fontWeight: typoTokens.pageTitle.fontWeight,
                  fontSize: typoTokens.pageTitle.fontSize,
                  color: "text.primary",
                }}
              >
                {title}
              </Typography>
            </Box>

            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              {viewToggle && (
                <ToggleButtonGroup
                  value={viewToggle.mode}
                  exclusive
                  onChange={(e, val) => val && viewToggle.onToggle(val)}
                  size="small"
                  sx={{
                    "& .MuiToggleButton-root": {
                      px: 1,
                      py: 0.5,
                      border: 1,
                      borderColor: "divider",
                    },
                  }}
                >
                  <ToggleButton value="card">
                    <ViewModuleIcon sx={{ fontSize: 18 }} />
                  </ToggleButton>
                  <ToggleButton value="table">
                    <ViewListIcon sx={{ fontSize: 18 }} />
                  </ToggleButton>
                </ToggleButtonGroup>
              )}
              {actions}
              {modelStatus && activeModel && (
                <Chip
                  label={activeModel}
                  size="small"
                  color="primary"
                  variant="outlined"
                  sx={{ fontSize: "0.7rem", height: 24 }}
                />
              )}
            </Box>
          </Box>
          {headerContent}
        </Paper>
      )}

      <Box
        sx={{
          flexGrow: 1,
          overflow: "auto",
          p: contentPadding,
          ...((variant === "fullscreen" || variant === "grid") && {
            display: "flex",
            flexDirection: "column",
          }),
        }}
      >
        {children}
      </Box>
    </Box>
  );
};

export default PageLayout;
