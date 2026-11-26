// frontend/src/components/layout/PageLayout.jsx
// Shared page layout component — standardizes page chrome across all pages.

import React, { useMemo, useState } from "react";
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
import { useLocation, useNavigate } from "react-router-dom";
import { spacing, typography as typoTokens } from "../../theme/tokens";
import brand from "../../config/brand";
import { PageHeaderSlotContext } from "./pageHeaderSlot";
import PowerMenu from "../power/PowerMenu";
import { useAppStore } from "../../stores/useAppStore";
import { NAV_CHROME } from "../../config/navCatalog";

/**
 * Colour for the current route from the active theme's `moduleAccents` map,
 * longest prefix wins. Returns undefined when the theme defines none.
 */
function accentForPath(accents, pathname) {
  if (!accents) return undefined;
  let best;
  let bestLength = -1;
  Object.keys(accents).forEach((prefix) => {
    const matches = pathname === prefix || pathname.startsWith(`${prefix}/`);
    if (matches && prefix.length > bestLength) {
      best = accents[prefix];
      bestLength = prefix.length;
    }
  });
  return best;
}

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
 * @param {boolean} hideHeader   — Drop the title bar; the page places its own actions
 * @param {string}  accent       — Override the route's theme accent colour
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
  hideHeader = false,
  accent,
  children,
}) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const accentColor =
    accent ?? accentForPath(theme.palette.moduleAccents, pathname);
  const showHeader = variant !== "fullscreen" && !hideHeader;
  // The buttons only step the browser's own history, which the router fills
  // on every navigation, so they are off unless a brand turns them on.
  const showHistoryButtons = brand.pageHistoryButtons === true;
  // The workspace bar carries its own power button.
  const showPower = useAppStore((state) => state.navChrome) !== NAV_CHROME.SOFTWARE;
  // A page embedded without a header hands its controls to the header above it.
  const outerSlots = React.useContext(PageHeaderSlotContext);
  const [actionsSlot, setActionsSlot] = useState(null);
  const [statusSlot, setStatusSlot] = useState(null);
  const [primarySlot, setPrimarySlot] = useState(null);
  const ownSlots = useMemo(
    () => ({ actions: actionsSlot, status: statusSlot, primary: primarySlot }),
    [actionsSlot, statusSlot, primarySlot],
  );
  const headerSlots = showHeader ? ownSlots : outerSlots;
  const contentPadding = noPadding || variant === "grid" ? 0 : { xs: 1.5, sm: spacing.sectionGap };

  return (
    <PageHeaderSlotContext.Provider value={headerSlots}>
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
            borderTop: accentColor ? 3 : 0,
            borderTopColor: accentColor,
            borderTopStyle: "solid",
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
              // On a phone the title, actions and model chip do not fit one
              // line; wrapping keeps the actions on screen.
              flexWrap: "wrap",
              columnGap: 1,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25, minWidth: 0 }}>
              {showHistoryButtons && (
                <>
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
                </>
              )}
              {/* A page's status takes the title's place. */}
              <Box
                ref={setStatusSlot}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 0.75,
                  minWidth: 0,
                  "&:empty": { display: "none" },
                  "&:not(:empty) ~ .page-title": { display: "none" },
                }}
              />
              <Typography
                className="page-title"
                variant={typoTokens.pageTitle.variant}
                sx={{
                  fontWeight: typoTokens.pageTitle.fontWeight,
                  fontSize: typoTokens.pageTitle.fontSize,
                  color: accentColor || "text.primary",
                }}
              >
                {title}
              </Typography>
            </Box>

            <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
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
              {/* The page's main action, set apart from the other buttons so it is always in one place. */}
              <Box ref={setPrimarySlot} sx={{ display: "flex", mr: 10, "&:empty": { display: "none" } }} />
              <Box ref={setActionsSlot} sx={{ display: "contents" }} />
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
              {showPower && <PowerMenu />}
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
    </PageHeaderSlotContext.Provider>
  );
};

export default PageLayout;
