
import React, { useState, useEffect, useMemo } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Badge,
  Drawer,
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Tooltip,
  Typography,
  useTheme,
  useMediaQuery,
  Avatar,
  IconButton,
  Divider,
} from "@mui/material";
import { useAppStore } from "../../stores/useAppStore";
import { activateResourceManager } from "../../utils/resource_manager";
import { spacing } from "../../theme/tokens";

import { BrandLogo } from "../branding";
import brand from "../../config/brand";
import { filterNavGroups, landingRouteFor } from "../../config/profile";
import { usePendingApprovals } from "../../hooks/usePendingApprovals";
import BarChartIcon from "@mui/icons-material/BarChart";
import DesktopWindowsIcon from "@mui/icons-material/DesktopWindows";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";

import SystemMetricsModal from "../modals/SystemMetricsModal";
import AgentScreenViewer from "../agent/AgentScreenViewer";

const COLLAPSED_WIDTH = spacing.sidebarCollapsed;
const EXPANDED_WIDTH = spacing.sidebarExpanded;

const Sidebar = () => {
  const location = useLocation();
  const theme = useTheme();
  // Navigation layout is brand-owned (config/brand.jsx navGroups); the active
  // profile decides which of those items are listed. Read here, not at module
  // scope, so the sidebar follows the profile once it arrives from the backend.
  const profile = useAppStore((state) => state.profile);
  const navGroups = useMemo(
    () => filterNavGroups(brand.navGroups, profile?.hidden_routes),
    [profile],
  );
  const homeRoute = landingRouteFor(profile) || "/dashboard";
  const systemName = useAppStore((state) => state.systemName);
  const systemLogo = useAppStore((state) => state.systemLogo);
  const isExpanded = useAppStore((state) => state.sidebarExpanded);
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const setSidebarExpanded = useAppStore((state) => state.setSidebarExpanded);
  const [metricsModalOpen, setMetricsModalOpen] = useState(false);
  // Store-backed so slash commands (/agent, /chat) can flip the viewer
  // alongside session mode. Previously this was local useState, which made
  // the viewer state non-shareable and lost on every reload.
  const agentScreenOpen = useAppStore((s) => s.agentScreenOpen);
  const setAgentScreenOpen = useAppStore((s) => s.setAgentScreenOpen);
  const isBelowMd = useMediaQuery(theme.breakpoints.down("md"));
  // Live counts for nav items that declare a `badge` key.
  const { count: pendingApprovals } = usePendingApprovals();
  const badgeCounts = { pendingApprovals };

  useEffect(() => {
    if (isBelowMd && isExpanded) {
      setSidebarExpanded(false);
    }
  }, [isBelowMd, isExpanded, setSidebarExpanded]);

  const drawerWidth = isExpanded ? EXPANDED_WIDTH : COLLAPSED_WIDTH;

  useEffect(() => {
    activateResourceManager();
  }, []);

  // Each nav item can carry its area's hue from the theme's `moduleAccents`
  // map, so the sidebar is scannable by colour. Themes without one fall back to
  // the primary/secondary pair and look exactly as before.
  const getNavLinkStyle = (isActive, accent) => {
    const activeColor = accent || theme.palette.primary.main;
    return {
      backgroundColor: isActive ? theme.palette.action.selected : "transparent",
      color: "inherit",
      width: "100%",
      minHeight: 40,
      justifyContent: isExpanded ? "flex-start" : "center",
      px: isExpanded ? 2 : 1.5,
      py: 0.75,
      mb: 0.25,
      borderRadius: "6px",
      borderLeft: "2px solid",
      borderLeftColor: isActive && accent ? accent : "transparent",
      "&:hover": {
        backgroundColor: isActive
          ? theme.palette.action.selected
          : theme.palette.action.hover,
        "& .MuiListItemIcon-root svg": { color: activeColor },
      },
      "& .MuiListItemIcon-root": {
        minWidth: isExpanded ? 36 : 0,
        justifyContent: "center",
        // Inactive icons keep a muted tint of their own hue rather than a flat
        // grey, which is what makes the collapsed rail readable.
        color: isActive ? activeColor : accent || theme.palette.text.secondary,
        opacity: isActive ? 1 : 0.75,
        "& svg": { fontSize: 22 },
      },
    };
  };

  return (
    <>
      <Drawer
        variant="permanent"
        sx={{
          width: drawerWidth,
          flexShrink: 0,
          "& .MuiDrawer-paper": {
            width: drawerWidth,
            boxSizing: "border-box",
            overflowX: "hidden",
            borderRight: "none",
            transition: theme.transitions.create("width", {
              duration: 200,
              easing: theme.transitions.easing.easeInOut,
            }),
          },
        }}
      >
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
          }}
        >
          {}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              px: isExpanded ? 2 : 0,
              py: 1.5,
              justifyContent: isExpanded ? "flex-start" : "center",
              minHeight: 56,
            }}
          >
            <Avatar
              component={NavLink}
              // Brand home: the profile's landing route, else the dashboard.
              to={homeRoute}
              src={systemLogo ? `/api/uploads/${systemLogo}` : undefined}
              sx={{
                width: 36,
                height: 36,
                border: "1px solid rgba(255, 255, 255, 0.24)",
                bgcolor: "#000",
                color: "#fff",
                flexShrink: 0,
                textDecoration: "none",
                p: systemLogo ? 0.5 : 0,
                transition: theme.transitions.create(["border-color", "box-shadow"], {
                  duration: 150,
                }),
                "&:hover": {
                  borderColor: "rgba(255, 255, 255, 0.6)",
                  boxShadow: "0 0 0 2px rgba(255, 255, 255, 0.08)",
                },
                "& .MuiAvatar-img": {
                  width: "100%",
                  height: "100%",
                  objectFit: "contain",
                },
              }}
            >
              {!systemLogo && <BrandLogo size={24} color="#fff" />}
            </Avatar>
            {isExpanded && (
              <Typography
                variant="subtitle2"
                noWrap
                sx={{
                  fontWeight: 600,
                  color: "text.primary",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                }}
              >
                {systemName || brand.appName}
              </Typography>
            )}
          </Box>

          <Divider />

          {}
          <Box sx={{ flexGrow: 1, overflow: "auto", px: 0.75, pt: 1 }}>
            {navGroups.map((group, groupIdx) => (
              <React.Fragment key={group.label}>
                {groupIdx > 0 && <Divider sx={{ my: 0.75 }} />}
                {isExpanded && (
                  <Typography
                    variant="caption"
                    sx={{
                      px: 1.5,
                      py: 0.5,
                      display: "block",
                      color: "text.secondary",
                      fontWeight: 600,
                      fontSize: "0.65rem",
                      textTransform: "uppercase",
                      letterSpacing: "0.08em",
                    }}
                  >
                    {group.label}
                  </Typography>
                )}
                <List disablePadding>
                  {group.items.map((item) => {
                    // Match on whole path segments, not raw string prefix.
                    // A bare startsWith() makes "/video" (Video Gen) light up
                    // when the route is "/video-editor" or "/video-text-overlay",
                    // because both literally start with "/video". Requiring an
                    // exact match or a "/" boundary keeps nested routes
                    // (e.g. /clients/123 -> Clients) highlighting correctly
                    // while killing the sibling-collision.
                    const isActive = item.path === "/"
                      ? location.pathname === "/"
                      : location.pathname === item.path ||
                        location.pathname.startsWith(item.path + "/");

                    // A nav item may carry a live count. Collapsed, the badge is
                    // the only signal there is, so it rides the icon in both
                    // states rather than the label.
                    const badgeCount = item.badge ? badgeCounts[item.badge] || 0 : 0;

                    const button = (
                      <ListItemButton
                        component={NavLink}
                        to={item.path}
                        sx={() => getNavLinkStyle(isActive, theme.palette.moduleAccents?.[item.path])}
                      >
                        <ListItemIcon>
                          {badgeCount > 0 ? (
                            <Badge badgeContent={badgeCount} color="warning">
                              {item.icon}
                            </Badge>
                          ) : (
                            item.icon
                          )}
                        </ListItemIcon>
                        {isExpanded && (
                          <ListItemText
                            primary={item.text}
                            primaryTypographyProps={{
                              fontSize: "0.825rem",
                              fontWeight: isActive ? 600 : 400,
                              noWrap: true,
                            }}
                          />
                        )}
                      </ListItemButton>
                    );

                    return (
                      <ListItem key={item.text} disablePadding sx={{ display: "block" }}>
                        {isExpanded ? (
                          button
                        ) : (
                          <Tooltip title={item.text} placement="right" arrow>
                            {button}
                          </Tooltip>
                        )}
                      </ListItem>
                    );
                  })}
                </List>
              </React.Fragment>
            ))}
          </Box>

          {}
          <Box sx={{ borderTop: 1, borderColor: "divider", p: 0.75 }}>
            {}
            <Tooltip title={isExpanded ? "" : "System Metrics"} placement="right" arrow>
              <IconButton
                onClick={() => setMetricsModalOpen(!metricsModalOpen)}
                sx={{
                  width: "100%",
                  height: 36,
                  borderRadius: "6px",
                  justifyContent: isExpanded ? "flex-start" : "center",
                  px: isExpanded ? 2 : 0,
                  gap: 1.5,
                  color: metricsModalOpen ? theme.palette.primary.main : theme.palette.text.secondary,
                  backgroundColor: metricsModalOpen ? theme.palette.action.selected : "transparent",
                  "&:hover": {
                    backgroundColor: metricsModalOpen
                      ? theme.palette.action.selected
                      : theme.palette.action.hover,
                    color: theme.palette.primary.main,
                  },
                }}
              >
                <BarChartIcon sx={{ fontSize: 22 }} />
                {isExpanded && (
                  <Typography variant="body2" sx={{ fontSize: "0.825rem" }}>
                    System Metrics
                  </Typography>
                )}
              </IconButton>
            </Tooltip>

            {/* Agent Screen toggle */}
            <Tooltip title={isExpanded ? "" : "Agent Screen"} placement="right" arrow>
              <IconButton
                onClick={() => setAgentScreenOpen(!agentScreenOpen)}
                sx={{
                  width: "100%",
                  height: 36,
                  borderRadius: "6px",
                  justifyContent: isExpanded ? "flex-start" : "center",
                  px: isExpanded ? 2 : 0,
                  gap: 1.5,
                  color: agentScreenOpen ? theme.palette.success.main : theme.palette.text.secondary,
                  backgroundColor: agentScreenOpen ? theme.palette.action.selected : "transparent",
                  "&:hover": {
                    backgroundColor: agentScreenOpen
                      ? theme.palette.action.selected
                      : theme.palette.action.hover,
                    color: theme.palette.success.main,
                  },
                }}
              >
                <DesktopWindowsIcon sx={{ fontSize: 22 }} />
                {isExpanded && (
                  <Typography variant="body2" sx={{ fontSize: "0.825rem" }}>
                    Agent Screen
                  </Typography>
                )}
              </IconButton>
            </Tooltip>

            {}
            <IconButton
              onClick={toggleSidebar}
              sx={{
                width: "100%",
                height: 36,
                borderRadius: "6px",
                mt: 0.5,
                justifyContent: isExpanded ? "flex-start" : "center",
                px: isExpanded ? 2 : 0,
                gap: 1.5,
                color: theme.palette.text.secondary,
                "&:hover": {
                  backgroundColor: theme.palette.action.hover,
                  color: theme.palette.primary.main,
                },
              }}
            >
              {isExpanded ? (
                <>
                  <ChevronLeftIcon sx={{ fontSize: 22 }} />
                  <Typography variant="body2" sx={{ fontSize: "0.825rem" }}>
                    Collapse
                  </Typography>
                </>
              ) : (
                <ChevronRightIcon sx={{ fontSize: 22 }} />
              )}
            </IconButton>
          </Box>
        </Box>
      </Drawer>

      <SystemMetricsModal
        open={metricsModalOpen}
        onClose={() => setMetricsModalOpen(false)}
      />

      <AgentScreenViewer
        open={agentScreenOpen}
        onClose={() => setAgentScreenOpen(false)}
      />
    </>
  );
};

export default Sidebar;
