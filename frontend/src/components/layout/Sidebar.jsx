
import React, { useState, useEffect } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
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

import DashboardIcon from "@mui/icons-material/Dashboard";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import ArticleIcon from "@mui/icons-material/Article";
import FolderIcon from "@mui/icons-material/Folder";
import LanguageIcon from "@mui/icons-material/Language";
import RuleFolderIcon from "@mui/icons-material/RuleFolder";
import SettingsIcon from "@mui/icons-material/Settings";
import AccountBoxIcon from "@mui/icons-material/AccountBox";
import { GuaardvarkLogo } from "../branding";
import BarChartIcon from "@mui/icons-material/BarChart";
import PetsIcon from "@mui/icons-material/Pets";
import ImageIcon from "@mui/icons-material/Image";
import CodeIcon from "@mui/icons-material/Code";
import LibraryBooksIcon from "@mui/icons-material/LibraryBooks";
import BuildIcon from "@mui/icons-material/Build";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";

import SystemMetricsModal from "../modals/SystemMetricsModal";

const COLLAPSED_WIDTH = spacing.sidebarCollapsed;
const EXPANDED_WIDTH = spacing.sidebarExpanded;

const navGroups = [
  {
    label: "Main",
    items: [
      { text: "Dashboard", icon: <DashboardIcon />, path: "/" },
      { text: "Chat", icon: <ChatBubbleOutlineIcon />, path: "/chat" },
      { text: "Code Editor", icon: <CodeIcon />, path: "/code-editor" },
      { text: "Documents", icon: <ArticleIcon />, path: "/documents" },
    ],
  },
  {
    label: "Management",
    items: [
      { text: "Clients", icon: <AccountBoxIcon />, path: "/clients" },
      { text: "Projects", icon: <FolderIcon />, path: "/projects" },
      { text: "Websites", icon: <LanguageIcon />, path: "/websites" },
      { text: "Images", icon: <ImageIcon />, path: "/images" },
      { text: "Tasks", icon: <TaskAltIcon />, path: "/tasks" },
    ],
  },
  {
    label: "Configuration",
    items: [
      { text: "Rules & Prompts", icon: <RuleFolderIcon />, path: "/rules" },
      { text: "Agent Tools", icon: <BuildIcon />, path: "/tools" },
      { text: "Agents", icon: <SmartToyIcon />, path: "/agents" },
      { text: "FileGen", icon: <PetsIcon />, path: "/file-generation" },
      { text: "Content Library", icon: <LibraryBooksIcon />, path: "/content-library" },
      { text: "Settings", icon: <SettingsIcon />, path: "/settings" },
    ],
  },
];

const Sidebar = () => {
  const location = useLocation();
  const theme = useTheme();
  const systemName = useAppStore((state) => state.systemName);
  const systemLogo = useAppStore((state) => state.systemLogo);
  const isExpanded = useAppStore((state) => state.sidebarExpanded);
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const setSidebarExpanded = useAppStore((state) => state.setSidebarExpanded);
  const [metricsModalOpen, setMetricsModalOpen] = useState(false);
  const isBelowMd = useMediaQuery(theme.breakpoints.down("md"));

  useEffect(() => {
    if (isBelowMd && isExpanded) {
      setSidebarExpanded(false);
    }
  }, [isBelowMd, isExpanded, setSidebarExpanded]);

  const drawerWidth = isExpanded ? EXPANDED_WIDTH : COLLAPSED_WIDTH;

  useEffect(() => {
    activateResourceManager();
  }, []);

  const getNavLinkStyle = (isActive) => ({
    backgroundColor: isActive ? theme.palette.action.selected : "transparent",
    color: "inherit",
    width: "100%",
    minHeight: 40,
    justifyContent: isExpanded ? "flex-start" : "center",
    px: isExpanded ? 2 : 1.5,
    py: 0.75,
    mb: 0.25,
    borderRadius: "6px",
    "&:hover": {
      backgroundColor: isActive
        ? theme.palette.action.selected
        : theme.palette.action.hover,
      "& .MuiListItemIcon-root svg": { color: theme.palette.primary.main },
    },
    "& .MuiListItemIcon-root": {
      minWidth: isExpanded ? 36 : 0,
      justifyContent: "center",
      color: isActive
        ? theme.palette.primary.main
        : theme.palette.text.secondary,
      "& svg": { fontSize: 22 },
    },
  });

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
              src={systemLogo ? `/api/uploads/${systemLogo}` : undefined}
              sx={{
                width: 36,
                height: 36,
                border: 1,
                borderColor: "divider",
                bgcolor: "primary.main",
                flexShrink: 0,
              }}
            >
              {!systemLogo && <GuaardvarkLogo size={24} />}
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
                {systemName || "Guaardvark"}
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
                    const isActive = item.path === "/"
                      ? location.pathname === "/"
                      : location.pathname.startsWith(item.path);

                    const button = (
                      <ListItemButton
                        component={NavLink}
                        to={item.path}
                        sx={() => getNavLinkStyle(isActive)}
                      >
                        <ListItemIcon>{item.icon}</ListItemIcon>
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
    </>
  );
};

export default Sidebar;
