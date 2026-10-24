import { useUiTranslation } from "../../i18n";
import { navigationLabel } from "../../i18n/navigation";
import React, { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  Badge,
  Box,
  ButtonBase,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useAppStore } from "../../stores/useAppStore";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { isFloatingChatHiddenRoute } from "../../config/floatingChat";
import { BrandLogo } from "../branding";
import brand from "../../config/brand";
import { chatSurfacesFor, landingRouteFor } from "../../config/profile";
import { extensionNavGroups } from "../../extensions";
import {
  buildNavCatalog,
  filterCatalog,
  hrefForItem,
  matchCatalogItem,
  pathIsActive,
  pinnedItems,
  toolsForWorkspace,
  visibleWorkspaces,
  workspaceBadge,
} from "../../config/navCatalog";
import { useNavBadgeCounts } from "../../config/navBadges";
import NavCount from "./NavCount";
import { usePendingApprovals } from "../../hooks/usePendingApprovals";
import SystemMetricsModal from "../modals/SystemMetricsModal";
import AgentScreenViewer from "../agent/AgentScreenViewer";
import GlobalMicButton from "../voice/GlobalMicButton";

const square = { borderRadius: 0 };


const barButtonSx = (theme, { active, accent }) => ({
  px: 1.25,
  py: 0.5,
  minHeight: 28,
  flexShrink: 0,
  whiteSpace: "nowrap",
  ...square,
  color: active ? "text.primary" : "text.secondary",
  fontWeight: active ? 600 : 500,
  fontSize: "0.8rem",
  letterSpacing: "0.01em",
  backgroundColor: active ? theme.palette.action.selected : "transparent",
  boxShadow: active && accent ? `inset 0 -2px 0 ${accent}` : "none",
  "&:hover": {
    backgroundColor: active ? theme.palette.action.selected : theme.palette.action.hover,
    color: "text.primary",
  },
});

const pinButtonSx = (active, activeColor) => ({
  ...square,
  width: 28,
  height: 28,
  color: active ? activeColor : "text.secondary",
  backgroundColor: active ? "action.selected" : "transparent",
  "&:hover": {
    backgroundColor: active ? "action.selected" : "action.hover",
    color: activeColor,
  },
  "& .MuiSvgIcon-root": { fontSize: 18 },
});

const SoftwareNav = () => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const location = useLocation();
  const navigate = useNavigate();
  const profile = useAppStore((state) => state.profile);
  const systemName = useAppStore((state) => state.systemName);
  const systemLogo = useAppStore((state) => state.systemLogo);
  const agentScreenOpen = useAppStore((state) => state.agentScreenOpen);
  const setAgentScreenOpen = useAppStore((state) => state.setAgentScreenOpen);
  const [metricsModalOpen, setMetricsModalOpen] = useState(false);
  const { count: pendingApprovals } = usePendingApprovals();
  // Core keys first; a distribution's navBadges.js adds its own.
  const badgeCounts = { pendingApprovals, ...useNavBadgeCounts() };
  const homeRoute = landingRouteFor(profile) || "/dashboard";

  const catalog = useMemo(
    () =>
      filterCatalog(
        buildNavCatalog(extensionNavGroups(), brand.navCatalog, brand.workspaces),
        profile?.hidden_routes,
      ),
    [profile],
  );

  const workspaces = useMemo(() => visibleWorkspaces(catalog, brand.workspaces), [catalog]);
  const currentItem = matchCatalogItem(catalog, location.pathname);
  const activeWorkspaceId = currentItem?.workspace || null;
  const stripTools = activeWorkspaceId ? toolsForWorkspace(catalog, activeWorkspaceId) : [];
  const showStrip = stripTools.length > 1;
  // Pinned right-hand buttons exist only when the brand's catalog carries them.
  const byId = (id) => catalog.find((item) => item.id === id);
  const metricsAction = byId("system-metrics");
  const agentScreenAction = byId("agent-screen");
  const chatAction = byId("floating-chat");
  const voiceAction = byId("voice-mic");
  const pins = pinnedItems(catalog);

  const chatOpen = useFloatingChatStore((state) => state.isOpen);
  const toggleChat = useFloatingChatStore((state) => state.toggleOpen);
  const setBarChatEntry = useFloatingChatStore((state) => state.setBarChatEntry);
  // A page that is a chat surface of its own has no floating chat to open.
  const chatUnavailable = isFloatingChatHiddenRoute(location.pathname, chatSurfacesFor(profile));
  const hasChatAction = Boolean(chatAction);
  useEffect(() => {
    setBarChatEntry(hasChatAction);
    return () => setBarChatEntry(false);
  }, [hasChatAction, setBarChatEntry]);

  const goWorkspace = (workspaceId) => {
    const tools = toolsForWorkspace(catalog, workspaceId);
    if (tools.length === 0) return;
    if (activeWorkspaceId === workspaceId) return;
    navigate(hrefForItem(tools[0]));
  };

  return (
    <>
      <Box
        component="nav"
        aria-label={t("navigation.workspace")}
        sx={{
          flexShrink: 0,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 0.5,
            minHeight: 40,
            px: 1,
          }}
        >
          <Box
            component={NavLink}
            to={homeRoute}
            aria-label={systemName || brand.appName}
            sx={{
              width: 28,
              height: 28,
              ...square,
              border: "1px solid rgba(255, 255, 255, 0.24)",
              bgcolor: "#000",
              color: "#fff",
              flexShrink: 0,
              textDecoration: "none",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              p: systemLogo ? 0.4 : 0,
              mr: 0.5,
              overflow: "hidden",
              "& img": {
                width: "100%",
                height: "100%",
                objectFit: "contain",
              },
            }}
          >
            {systemLogo ? (
              <img src={`/api/uploads/${systemLogo}`} alt="" />
            ) : (
              <BrandLogo size={18} color="#fff" />
            )}
          </Box>
          <Typography
            variant="subtitle2"
            noWrap
            sx={{
              fontWeight: 600,
              fontSize: "0.8rem",
              mr: 1,
              maxWidth: 140,
              display: { xs: "none", sm: "block" },
            }}
          >
            {systemName || brand.appName}
          </Typography>
          {brand.workspaceBarStart && <brand.workspaceBarStart />}

          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.25,
              flexGrow: 1,
              minWidth: 0,
              overflowX: "auto",
              "&::-webkit-scrollbar": { display: "none" },
            }}
          >
            {workspaces.map((workspace) => {
              const active = workspace.id === activeWorkspaceId;
              const count = workspaceBadge(catalog, workspace.id, badgeCounts);
              const button = (
                <ButtonBase
                  key={workspace.id}
                  onClick={() => goWorkspace(workspace.id)}
                  aria-current={active ? "page" : undefined}
                  sx={barButtonSx(theme, { active, accent: theme.palette.primary.main })}
                >
                  {navigationLabel(workspace.label)}
                  {count > 0 && <NavCount count={count} />}
                </ButtonBase>
              );
              return button;
            })}
          </Box>

          <Box sx={{ display: "flex", alignItems: "center", flexShrink: 0, ml: 0.5 }}>
            {metricsAction && (
              <Tooltip title={navigationLabel(metricsAction.label, metricsAction)}>
                <ButtonBase
                  onClick={() => setMetricsModalOpen((open) => !open)}
                  aria-label={navigationLabel(metricsAction.label, metricsAction)}
                  sx={pinButtonSx(metricsModalOpen, "primary.main")}
                >
                  {metricsAction.icon}
                </ButtonBase>
              </Tooltip>
            )}
            {agentScreenAction && (
              <Tooltip title={navigationLabel(agentScreenAction.label, agentScreenAction)}>
                <ButtonBase
                  onClick={() => setAgentScreenOpen(!agentScreenOpen)}
                  aria-label={navigationLabel(agentScreenAction.label, agentScreenAction)}
                  sx={pinButtonSx(agentScreenOpen, "success.main")}
                >
                  {agentScreenAction.icon}
                </ButtonBase>
              </Tooltip>
            )}
            {pins.map((item) => {
              const active = pathIsActive(item.path, location.pathname);
              const count = item.badge ? badgeCounts[item.badge] || 0 : 0;
              return (
                <Tooltip key={item.id} title={navigationLabel(item.label, item)}>
                  <ButtonBase
                    component={NavLink}
                    to={hrefForItem(item)}
                    aria-label={navigationLabel(item.label, item)}
                    sx={pinButtonSx(active, "primary.main")}
                  >
                    {count > 0 ? (
                      <Badge
                        badgeContent={count}
                        max={99}
                        color="warning"
                        sx={{ "& .MuiBadge-badge": { fontSize: "0.6rem", minWidth: 16, height: 16 } }}
                      >
                        {item.icon}
                      </Badge>
                    ) : (
                      item.icon
                    )}
                  </ButtonBase>
                </Tooltip>
              );
            })}
            {voiceAction && <GlobalMicButton variant="bar" label={navigationLabel(voiceAction.label, voiceAction)} primary />}
            {chatAction && (
              <Tooltip title={chatUnavailable ? t("navigation.thisPageHasItsOwnChat") : navigationLabel(chatAction.label, chatAction)}>
                <span>
                  <ButtonBase
                    onClick={toggleChat}
                    disabled={chatUnavailable}
                    aria-label={navigationLabel(chatAction.label, chatAction)}
                    aria-pressed={chatOpen}
                    sx={{
                      ...pinButtonSx(chatOpen, "primary.main"),
                      "&.Mui-disabled": { opacity: 0.35 },
                    }}
                  >
                    {chatAction.icon}
                  </ButtonBase>
                </span>
              </Tooltip>
            )}
          </Box>
        </Box>

        {showStrip && (
          <Box
            role="tablist"
            aria-label={t("navigation.workspaceTools")}
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.25,
              minHeight: 36,
              px: 1,
              pb: 0.5,
              overflowX: "auto",
              borderTop: "1px solid",
              borderColor: "divider",
              "&::-webkit-scrollbar": { height: 4 },
            }}
          >
            {stripTools.map((item) => {
              const active = item.id === currentItem?.id;
              const accent = theme.palette.moduleAccents?.[item.path];
              const count = item.badge ? badgeCounts[item.badge] || 0 : 0;
              return (
                <ButtonBase
                  key={item.id}
                  component={NavLink}
                  to={hrefForItem(item)}
                  role="tab"
                  aria-selected={active}
                  sx={{
                    ...barButtonSx(theme, { active, accent }),
                    fontSize: "0.75rem",
                    gap: 0.75,
                    "& .MuiSvgIcon-root": {
                      fontSize: 16,
                      color: active ? accent || "primary.main" : "text.secondary",
                    },
                  }}
                >
                  {item.icon}
                  {navigationLabel(item.label, item)}
                  {count > 0 && <NavCount count={count} />}
                </ButtonBase>
              );
            })}
          </Box>
        )}
      </Box>

      {metricsAction && (
        <SystemMetricsModal
          open={metricsModalOpen}
          onClose={() => setMetricsModalOpen(false)}
        />
      )}
      {agentScreenAction && (
        <AgentScreenViewer
          open={agentScreenOpen}
          onClose={() => setAgentScreenOpen(false)}
        />
      )}
    </>
  );
};

SoftwareNav.displayName = "SoftwareNav";

export default SoftwareNav;
