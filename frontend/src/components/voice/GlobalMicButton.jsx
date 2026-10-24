import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, CircularProgress, Tooltip, Typography } from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import MicIcon from "@mui/icons-material/Mic";
import MicNoneIcon from "@mui/icons-material/MicNone";
import MicOffIcon from "@mui/icons-material/MicOff";
import HearingIcon from "@mui/icons-material/Hearing";
import VolumeUpIcon from "@mui/icons-material/VolumeUp";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { useShallow } from "zustand/react/shallow";
import { useVoiceSession, useVoiceSessionState } from "../../contexts/VoiceSessionContext";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceChatEnabled } from "../../hooks/useVoiceChatEnabled";
import { describeVoicePhase } from "../../utils/voicePhase";
import brand from "../../config/brand";
import { translate, useUiTranslation } from "../../i18n";
import VoiceSessionPopover from "./VoiceSessionPopover";

// A press longer than this is push-to-talk; shorter is a click.
const HOLD_MS = 300;

const SIZES = {
  bar: { box: 28, icon: 18, radius: 0 },
  rail: { box: 36, icon: 22, radius: "6px" },
  inline: { box: 40, icon: 22, radius: "50%" },
  compact: { box: 24, icon: 15, radius: "50%" },
};

function phaseVisual(phase, listenMode, theme) {
  switch (phase) {
    case "listening":
      return listenMode === "passive"
        ? { Icon: HearingIcon, color: theme.palette.info.main, ring: true }
        : { Icon: MicIcon, color: theme.palette.primary.main, ring: true };
    case "capturing":
      return { Icon: MicIcon, color: theme.palette.error.main, ring: true };
    case "requesting-permission":
    case "transcribing":
    case "sending":
      return { Icon: MicIcon, color: theme.palette.primary.main, spinner: true };
    case "responding":
      return { Icon: MicIcon, color: theme.palette.primary.main, ring: true };
    case "speaking":
      return { Icon: VolumeUpIcon, color: theme.palette.success.main };
    case "denied":
      return { Icon: MicOffIcon, color: theme.palette.error.main };
    case "error":
      return { Icon: MicOffIcon, color: theme.palette.warning.main };
    default:
      return { Icon: MicNoneIcon, color: theme.palette.text.secondary };
  }
}

/**
 * The microphone button bound to the global voice session.
 *
 * Every mic in the app is one of these, so they all show the same state and
 * share one microphone. Click does what the activation mode says (hands-free
 * by default), press and hold talks, right-click (or the arrow on the bar and
 * rail variants) opens the status popover. Hidden when Settings turns voice
 * chat off.
 *
 * @param {{variant?: "bar"|"rail"|"inline"|"compact", expanded?: boolean,
 *          label?: string, showMenu?: boolean, primary?: boolean}} props
 *   `primary` marks the always-visible top-bar or sidebar button, which opens
 *   its popover on its own when something goes wrong.
 */
const GlobalMicButton = ({
  variant = "bar",
  expanded = false,
  label = translate("common.voice"),
  showMenu = variant === "bar" || variant === "rail",
  primary = false,
}) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const voice = useVoiceSession();
  const enabled = useVoiceChatEnabled();
  const state = useVoiceSessionState(
    useShallow((s) => ({
      phase: s.phase,
      listenMode: s.listenMode,
      push: s.push,
      session: s.session,
      error: s.error,
    }))
  );
  const level = useVoiceSessionState((s) => s.level);
  const mode = useAppStore((s) => s.voiceActivationMode);
  const systemName = useAppStore((s) => s.systemName) || brand.appName;
  const [anchorEl, setAnchorEl] = useState(null);
  const buttonRef = useRef(null);
  const holdTimerRef = useRef(null);
  const pushingRef = useRef(false);
  const pressedRef = useRef(false);
  const suppressClickRef = useRef(false);

  // Something went wrong (blocked mic, missing speech model): show why.
  const lastErrorRef = useRef(null);
  useEffect(() => {
    if (primary && state.error && state.error !== lastErrorRef.current && buttonRef.current) {
      setAnchorEl(buttonRef.current);
    }
    lastErrorRef.current = state.error;
  }, [primary, state.error]);

  useEffect(() => () => clearTimeout(holdTimerRef.current), []);

  if (!enabled || !voice.available) return null;

  const size = SIZES[variant] || SIZES.bar;
  const visual = phaseVisual(state.phase, state.listenMode, theme);
  const { title, hint } = describeVoicePhase(state, { systemName, mode });
  const active = state.phase !== "idle";
  const ringScale = visual.ring ? 1 + Math.min(1, level * 6) * 0.6 : 1;

  const onPointerDown = (event) => {
    if (event.button !== 0) return;
    pressedRef.current = true;
    pushingRef.current = false;
    clearTimeout(holdTimerRef.current);
    holdTimerRef.current = setTimeout(() => {
      holdTimerRef.current = null;
      pushingRef.current = true;
      voice.pushStart();
    }, HOLD_MS);
  };
  // `released` is a pointerup on the button, which a click event follows.
  const endPress = (released) => {
    if (!pressedRef.current) return;
    pressedRef.current = false;
    clearTimeout(holdTimerRef.current);
    holdTimerRef.current = null;
    if (released) suppressClickRef.current = true;
    if (pushingRef.current) {
      pushingRef.current = false;
      voice.pushEnd();
    } else if (released) {
      voice.toggle();
    }
  };
  // Keyboard activation (Enter/Space on the focused button) arrives as a click with no pointer press.
  const onClick = () => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    voice.toggle();
  };
  const openMenu = (event) => {
    event.preventDefault();
    setAnchorEl(buttonRef.current);
  };

  const icon = (
    <Box
      sx={{
        position: "relative",
        width: size.icon + 6,
        height: size.icon + 6,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      {visual.ring && (
        <Box
          aria-hidden
          sx={{
            position: "absolute",
            inset: 0,
            borderRadius: "50%",
            bgcolor: alpha(visual.color, 0.22),
            transform: `scale(${ringScale})`,
            transition: "transform 80ms linear",
          }}
        />
      )}
      <visual.Icon sx={{ fontSize: size.icon, color: visual.color, position: "relative" }} />
      {visual.spinner && (
        <CircularProgress
          size={size.icon + 6}
          thickness={3}
          sx={{ position: "absolute", inset: 0, color: visual.color }}
        />
      )}
    </Box>
  );

  const tooltip = (
    <Box>
      <div>{title}</div>
      {hint && <div style={{ opacity: 0.8 }}>{hint}</div>}
      {!state.session && state.phase === "idle" && <div style={{ opacity: 0.8 }}>{t("voice.mic.holdToTalkShortcut")}</div>}
    </Box>
  );

  const mainButton = (
    <ButtonBase
      ref={buttonRef}
      aria-label={t("voice.mic.buttonLabel", { label, title })}
      aria-pressed={Boolean(state.session || state.push)}
      data-voice-phase={state.phase}
      onPointerDown={onPointerDown}
      onPointerUp={() => endPress(true)}
      onPointerLeave={() => endPress(false)}
      onPointerCancel={() => endPress(false)}
      onClick={onClick}
      onContextMenu={openMenu}
      sx={{
        height: size.box,
        minWidth: size.box,
        width: variant === "rail" ? "100%" : size.box,
        borderRadius: size.radius,
        justifyContent: variant === "rail" && expanded ? "flex-start" : "center",
        px: variant === "rail" && expanded ? 2 : 0,
        gap: 1.5,
        color: active ? visual.color : "text.secondary",
        backgroundColor: active ? theme.palette.action.selected : "transparent",
        touchAction: "none",
        "&:hover": { backgroundColor: theme.palette.action.hover },
      }}
    >
      {icon}
      {variant === "rail" && expanded && (
        <Typography variant="body2" sx={{ fontSize: "0.825rem", flexGrow: 1, textAlign: "left" }}>
          {label}
        </Typography>
      )}
    </ButtonBase>
  );

  return (
    <>
      <Box sx={{ display: "flex", alignItems: "center", width: variant === "rail" ? "100%" : "auto" }}>
        <Tooltip title={tooltip} placement={variant === "rail" ? "right" : "bottom"} arrow>
          {mainButton}
        </Tooltip>
        {showMenu && (
          <Tooltip title={t("voice.mic.options")}>
            <ButtonBase
              aria-label={t("voice.mic.options")}
              aria-haspopup="dialog"
              onClick={openMenu}
              sx={{
                width: 14,
                height: size.box,
                borderRadius: 0,
                color: "text.secondary",
                "&:hover": { backgroundColor: theme.palette.action.hover, color: "text.primary" },
              }}
            >
              <ExpandMoreIcon sx={{ fontSize: 14 }} />
            </ButtonBase>
          </Tooltip>
        )}
      </Box>
      <VoiceSessionPopover
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={() => setAnchorEl(null)}
        placement={variant === "rail" ? "right" : "below"}
      />
    </>
  );
};

GlobalMicButton.propTypes = {
  variant: PropTypes.oneOf(["bar", "rail", "inline", "compact"]),
  expanded: PropTypes.bool,
  label: PropTypes.string,
  showMenu: PropTypes.bool,
  primary: PropTypes.bool,
};

export default GlobalMicButton;
