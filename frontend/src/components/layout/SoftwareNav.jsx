import React, { useMemo, useState } from "react";
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
import { BrandLogo } from "../branding";
import brand from "../../config/brand";
import { landingRouteFor } from "../../config/profile";
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
import { usePendingApprovals } from "../../hooks/usePendingApprovals";
import SystemMetricsModal from "../modals/SystemMetricsModal";
import AgentScreenViewer from "../agent/AgentScreenViewer";

const square = { borderRadius: 0 };

// A brand may supply live counts for its own badge keys (brand.useNavBadgeCounts);
// the hook identity is fixed at module load so React sees a stable call order.
const EMPTY_COUNTS = Object.freeze({});
const useBrandBadgeCounts = brand.useNavBadgeCounts || (() => EMPTY_COUNTS);

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
  const brandCounts = useBrandBadgeCounts();
  const badgeCounts = { pendingApprovals, ...brandCounts };
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
  const pins = pinnedItems(catalog);

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
        aria-label="Workspace"
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
                  {count > 0 ? (
                    <Badge badgeContent={count} color="warning" sx={{ "& .MuiBadge-badge": { fontSize: "0.6rem" } }}>
                      {workspace.label}
                    </Badge>
                  ) : (
                    workspace.label
                  )}
                </ButtonBase>
              );
              return button;
            })}
          </Box>

          <Box sx={{ display: "flex", alignItems: "center", flexShrink: 0, ml: 0.5 }}>
            {metricsAction && (
              <Tooltip title={metricsAction.label}>
                <ButtonBase
                  onClick={() => setMetricsModalOpen((open) => !open)}
                  aria-label={metricsAction.label}
                  sx={pinButtonSx(metricsModalOpen, "primary.main")}
                >
                  {metricsAction.icon}
                </ButtonBase>
              </Tooltip>
            )}
            {agentScreenAction && (
              <Tooltip title={agentScreenAction.label}>
                <ButtonBase
                  onClick={() => setAgentScreenOpen(!agentScreenOpen)}
                  aria-label={agentScreenAction.label}
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
                <Tooltip key={item.id} title={item.label}>
                  <ButtonBase
                    component={NavLink}
                    to={hrefForItem(item)}
                    aria-label={item.label}
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
          </Box>
        </Box>

        {showStrip && (
          <Box
            role="tablist"
            aria-label="Workspace tools"
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
              const active = pathIsActive(item.path, location.pathname);
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
                  {count > 0 ? (
                    <Badge badgeContent={count} color="warning">
                      {item.icon}
                    </Badge>
                  ) : (
                    item.icon
                  )}
                  {item.label}
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
