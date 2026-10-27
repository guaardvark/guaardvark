// frontend/src/components/common/DidYouKnowTip.jsx
import React, { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Box, Button, IconButton, Paper, Typography } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import LightbulbOutlinedIcon from "@mui/icons-material/LightbulbOutlined";
import StarBorderIcon from "@mui/icons-material/StarBorder";
import { useShallow } from "zustand/react/shallow";
import { useAppStore } from "../../stores/useAppStore";
import { useFloatingChatStore } from "../../stores/useFloatingChatStore";
import { NAV_CHROME, navChromeWidth } from "../../config/navCatalog";
import { chatSurfacesFor } from "../../config/profile";
import { isFloatingChatHiddenRoute } from "../../config/floatingChat";
import { brand } from "../../config/brand";
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
import {
  STAR_ASK_WIN_EVENT,
  markStarAskDone,
  markStarAsked,
  recordDayOpened,
  starAskDue,
} from "./starAskModel";
import { useUiTranslation } from "../../i18n";

// Long enough for the page to settle before anything else asks for attention.
export const TIP_DELAY_MS = 6000;
// After a render finishes, long enough for the result to land on screen first.
export const STAR_ASK_AFTER_WIN_MS = 4000;
const DIALOG_RETRY_MS = 5000;
const GAP = 16;
// The theme's wide letter spacing would break each label onto two lines; in
// longer languages the row wraps instead, between buttons.
const starButtonSx = { textTransform: "none", whiteSpace: "nowrap", letterSpacing: 0.2, minWidth: 0, px: 1 };

// Only the stock brand asks; a downstream brand.jsx leaves the flag out.
const STOCK_STAR_REPO = brand.askForGithubStar ? brand.supportLinks?.githubRepo || null : null;

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
 * The same card slot asks, at most twice ever, for a star on GitHub: right
 * after a render finishes when no card is showing, or in place of the session's
 * tip once a render has finished or on the third day of use (starAskModel.js).
 *
 * @param {{tips?: Array<object>, delayMs?: number, starRepoUrl?: string|null, starDelayMs?: number}} props
 *   All four are test seams.
 */
const DidYouKnowTip = ({
  tips = TIPS,
  delayMs = TIP_DELAY_MS,
  starRepoUrl = STOCK_STAR_REPO,
  starDelayMs = STAR_ASK_AFTER_WIN_MS,
}) => {
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
  const [starAsk, setStarAsk] = useState(false);
  const onChatPage = isFloatingChatHiddenRoute(pathname, chatSurfacesFor(profile));
  const hiddenRoutes = profile?.hidden_routes;

  const showStarAsk = useCallback(() => {
    markTipShownThisSession();
    markStarAsked();
    setStarAsk(true);
  }, []);

  useEffect(() => {
    if (starRepoUrl) recordDayOpened();
  }, [starRepoUrl]);

  useEffect(() => {
    if (!tipsEnabled || !ready || tip || starAsk || onChatPage || tipShownThisSession()) return undefined;
    let timer = null;
    const attempt = () => {
      if (dialogOpen()) {
        timer = setTimeout(attempt, DIALOG_RETRY_MS);
        return;
      }
      if (starRepoUrl && starAskDue()) {
        showStarAsk();
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
  }, [tipsEnabled, ready, tip, starAsk, onChatPage, tips, hiddenRoutes, delayMs, starRepoUrl, showStarAsk]);

  // A finished render asks right away when the slot is free; otherwise the
  // next session's card does.
  const latest = useRef({});
  latest.current = { tipsEnabled, ready, tip, starAsk, onChatPage, updateNoticeVisible };
  useEffect(() => {
    if (!starRepoUrl) return undefined;
    let timer = null;
    const onWin = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const s = latest.current;
        const slotFree = s.tipsEnabled && s.ready && !s.tip && !s.starAsk && !s.onChatPage && !s.updateNoticeVisible;
        if (slotFree && !dialogOpen() && starAskDue()) showStarAsk();
      }, starDelayMs);
    };
    window.addEventListener(STAR_ASK_WIN_EVENT, onWin);
    return () => {
      window.removeEventListener(STAR_ASK_WIN_EVENT, onWin);
      clearTimeout(timer);
    };
  }, [starRepoUrl, starDelayMs, showStarAsk]);

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

  const starNow = useCallback(() => {
    window.open(starRepoUrl, "_blank", "noopener,noreferrer");
    markStarAskDone();
    setStarAsk(false);
  }, [starRepoUrl]);

  // "Later" and the close button leave one more ask, a few days of use away.
  const starLater = useCallback(() => setStarAsk(false), []);

  const starNever = useCallback(() => {
    markStarAskDone();
    setStarAsk(false);
  }, []);

  if ((!tip && !starAsk) || !tipsEnabled || updateNoticeVisible || onChatPage) return null;

  const left = navChromeWidth(navChrome || NAV_CHROME.SIDEBAR, sidebarExpanded) + GAP;
  const cardSx = (theme) => ({
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
  });

  if (starAsk) {
    const appName = brand.appName;
    return (
      <Paper role="region" aria-label={t("layoutCommon.starAsk.regionLabel")} elevation={6} sx={cardSx}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, mb: 0.5 }}>
          <StarBorderIcon sx={{ fontSize: 16, color: "primary.main" }} />
          <Typography variant="caption" sx={{ fontWeight: 700, letterSpacing: 0.5, flexGrow: 1 }}>
            {t("layoutCommon.starAsk.title", { appName })}
          </Typography>
          <IconButton
            size="small"
            aria-label={t("layoutCommon.starAsk.close")}
            onClick={starLater}
            sx={{ p: 0.25, mr: -0.75 }}
          >
            <CloseIcon sx={{ fontSize: 16 }} />
          </IconButton>
        </Box>
        <Typography variant="body2" sx={{ color: "text.primary", lineHeight: 1.45 }}>
          {t("layoutCommon.starAsk.text", { appName })}
        </Typography>
        <Box sx={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 0.25, mt: 1, mx: -0.75 }}>
          <Button
            size="small"
            variant="contained"
            disableElevation
            startIcon={<StarBorderIcon sx={{ fontSize: 16 }} />}
            onClick={starNow}
            sx={{ ...starButtonSx, ml: 0.75 }}
          >
            {t("layoutCommon.starAsk.star")}
          </Button>
          <Button size="small" onClick={starLater} sx={starButtonSx}>
            {t("layoutCommon.starAsk.later")}
          </Button>
          <Button
            size="small"
            color="inherit"
            onClick={starNever}
            sx={{ ...starButtonSx, ml: "auto", color: "text.secondary" }}
          >
            {t("layoutCommon.starAsk.never")}
          </Button>
        </Box>
      </Paper>
    );
  }

  const hasShowMe = Boolean(tip.route || TIP_ACTIONS[tip.action]);

  return (
    <Paper role="region" aria-label={t("layoutCommon.didYouKnow.regionLabel")} elevation={6} sx={cardSx}>
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
