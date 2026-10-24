// frontend/src/components/common/DidYouKnowTip.jsx
import React, { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Box, Button, IconButton, Paper, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import LightbulbOutlinedIcon from "@mui/icons-material/LightbulbOutlined";
import { useShallow } from "zustand/react/shallow";
import { useAppStore } from "../../stores/useAppStore";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { NAV_CHROME, navChromeWidth } from "../../config/navCatalog";
import { chatSurfacesFor } from "../../config/profile";
import { isFloatingChatHiddenRoute } from "../../config/floatingChat";
import { spacing } from "../../theme/tokens";
import { TIPS } from "../../config/tips";
import { SHOW_SHORTCUTS_EVENT } from "./KeyboardShortcutsOverlay";
import {
  markTipSeen,
  markTipShownThisSession,
  pickTip,
  readSeenTips,
  tipShownThisSession,
} from "./didYouKnowModel";
import { useUiTranslation } from "../../i18n";

// Long enough for the page to settle before anything else asks for attention.
export const TIP_DELAY_MS = 6000;
const DIALOG_RETRY_MS = 5000;
const GAP = 16;

const dialogOpen = () =>
  typeof document !== "undefined" && Boolean(document.querySelector(".MuiDialog-root:not(.MuiModal-hidden)"));

/** What a tip's "Show me" does when it is not a page link. */
export const TIP_ACTIONS = {
  shortcuts: () => window.dispatchEvent(new Event(SHOW_SHORTCUTS_EVENT)),
  "floating-chat": () => useFloatingChatStore.getState().setIsOpen(true),
};

/**
 * A small "Did you know" card at the bottom left of the canvas, one per browser
 * session. It waits for the first-run profile choice, stays clear of the
 * navigation, the footer bar, the update notice and chat pages, and sits below
 * dialogs. Settings → General turns it off, as does "Don't show tips".
 *
 * @param {{tips?: Array<object>, delayMs?: number}} props  `tips` and `delayMs` are test seams.
 */
const DidYouKnowTip = ({ tips = TIPS, delayMs = TIP_DELAY_MS }) => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { tipsEnabled, setTipsEnabled, ready, updateNoticeVisible, profile, navChrome, sidebarExpanded } =
    useAppStore(
      useShallow((s) => ({
        tipsEnabled: s.tipsEnabled,
        setTipsEnabled: s.setTipsEnabled,
        ready: s.systemInfoLoaded && !s.profileFirstRun,
        updateNoticeVisible: s.updateNoticeVisible,
        profile: s.profile,
        navChrome: s.navChrome,
        sidebarExpanded: s.sidebarExpanded,
      })),
    );
  const [tip, setTip] = useState(null);
  const onChatPage = isFloatingChatHiddenRoute(pathname, chatSurfacesFor(profile));
  const hiddenRoutes = profile?.hidden_routes;

  useEffect(() => {
    if (!tipsEnabled || !ready || tip || onChatPage || tipShownThisSession()) return undefined;
    let timer = null;
    const attempt = () => {
      if (dialogOpen()) {
        timer = setTimeout(attempt, DIALOG_RETRY_MS);
        return;
      }
      const next = pickTip(tips, readSeenTips(), { hiddenRoutes });
      if (!next) return;
      markTipShownThisSession();
      markTipSeen(next.id);
      setTip(next);
    };
    timer = setTimeout(attempt, delayMs);
    return () => clearTimeout(timer);
  }, [tipsEnabled, ready, tip, onChatPage, tips, hiddenRoutes, delayMs]);

  const close = useCallback(() => setTip(null), []);

  const nextTip = useCallback(() => {
    const next = pickTip(tips, readSeenTips(), { hiddenRoutes, excludeId: tip?.id });
    if (!next) return;
    markTipSeen(next.id);
    setTip(next);
  }, [tips, hiddenRoutes, tip]);

  const stopTips = useCallback(() => {
    setTipsEnabled(false);
    setTip(null);
  }, [setTipsEnabled]);

  const showMe = useCallback(() => {
    if (!tip) return;
    if (tip.route) navigate(tip.route);
    TIP_ACTIONS[tip.action]?.();
    setTip(null);
  }, [tip, navigate]);

  if (!tip || !tipsEnabled || updateNoticeVisible || onChatPage) return null;

  const left = navChromeWidth(navChrome || NAV_CHROME.SIDEBAR, sidebarExpanded) + GAP;
  const hasShowMe = Boolean(tip.route || TIP_ACTIONS[tip.action]);

  return (
    <Paper
      role="region"
      aria-label={t("layoutCommon.didYouKnow.regionLabel")}
      elevation={6}
      sx={(theme) => ({
        position: "fixed",
        left,
        bottom: spacing.footerHeight + GAP,
        width: 320,
        maxWidth: `calc(100vw - ${left + GAP}px)`,
        zIndex: theme.zIndex.speedDial,
        borderRadius: "8px",
        border: `1px solid ${theme.palette.divider}`,
        px: 1.75,
        pt: 1.25,
        pb: 1,
      })}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.5 }}>
        <LightbulbOutlinedIcon sx={{ fontSize: 16, color: "primary.main" }} />
        <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: 0.5, flexGrow: 1 }}>
          {t("layoutCommon.didYouKnow.title")}
        </Typography>
        <IconButton size="small" aria-label={t("layoutCommon.didYouKnow.closeTip")} onClick={close} sx={{ p: 0.25, mr: -0.75 }}>
          <CloseIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Box>
      <Typography variant="body2" sx={{ color: "text.primary", lineHeight: 1.45 }}>
        {tip.text}
      </Typography>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 1, mx: -0.75 }}>
        {hasShowMe && (
          <Button size="small" onClick={showMe} sx={{ textTransform: "none" }}>
            {t("layoutCommon.didYouKnow.showMe")}
          </Button>
        )}
        <Button size="small" onClick={nextTip} sx={{ textTransform: "none" }}>
          {t("layoutCommon.didYouKnow.nextTip")}
        </Button>
        <Button
          size="small"
          color="inherit"
          onClick={stopTips}
          sx={{ textTransform: "none", ml: "auto", color: "text.secondary" }}
        >
          {t("layoutCommon.didYouKnow.stopTips")}
        </Button>
      </Box>
    </Paper>
  );
};

export default DidYouKnowTip;
