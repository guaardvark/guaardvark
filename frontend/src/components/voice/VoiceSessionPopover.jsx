import React, { useEffect } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Divider,
  FormControlLabel,
  LinearProgress,
  Link,
  MenuItem,
  Popover,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useVoice } from "../../contexts/VoiceContext";
import { useVoiceSession, useVoiceSessionState } from "../../contexts/VoiceSessionContext";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceSettings } from "../../hooks/useVoiceSettings";
import {
  ACTIVATION_MODES,
  ACTIVATION_MODE_HELP,
  ACTIVATION_MODE_LABELS,
  updateVoiceSettings,
} from "../../config/voiceDefaults";
import { describeVoicePhase, OUTCOME_LABELS } from "../../utils/voicePhase";
import brand from "../../config/brand";
import { useUiTranslation, Trans } from "../../i18n";

// The worklet's RMS for ordinary speech sits well under 0.3; scale it to the bar.
const LEVEL_SCALE = 300;

// How /voices names the engine that speaks replies, in the chooser's words.
const ENGINE_LABELS = { kokoro: "Kokoro, Audio Foundry", "piper-tts": "Piper" };

/**
 * Status and quick settings for the global mic: what it is doing, the last
 * thing it heard, start/stop, mode, wake phrase and reply voice.
 */
const PLACEMENTS = {
  below: {
    anchorOrigin: { vertical: "bottom", horizontal: "right" },
    transformOrigin: { vertical: "top", horizontal: "right" },
  },
  // Beside a sidebar button, opening into the page.
  right: {
    anchorOrigin: { vertical: "bottom", horizontal: "right" },
    transformOrigin: { vertical: "bottom", horizontal: "left" },
  },
};

const VoiceSessionPopover = ({ anchorEl, open, onClose, placement = "below" }) => {
  const { t } = useUiTranslation();
  const voice = useVoiceSession();
  const state = useVoiceSessionState(
    useShallow((s) => ({
      phase: s.phase,
      session: s.session,
      listenMode: s.listenMode,
      push: s.push,
      error: s.error,
      errorCode: s.errorCode,
      notice: s.notice,
      lastTranscript: s.lastTranscript,
      speaking: s.speaking,
    }))
  );
  const level = useVoiceSessionState((s) => s.level);
  const mode = useAppStore((s) => s.voiceActivationMode);
  const setMode = useAppStore((s) => s.setVoiceActivationMode);
  const systemName = useAppStore((s) => s.systemName) || brand.appName;
  const settings = useVoiceSettings();
  const { availableVoices = [], voiceEngine, defaultVoice, speakError, refreshVoices } = useVoice() || {};
  // The engine that speaks replies can change while the page is open (Audio
  // Foundry started or stopped); re-read the list once per open so the chooser
  // names the engine that will speak now. A failed read keeps the last list.
  useEffect(() => {
    if (open && refreshVoices) refreshVoices().catch(() => {});
  }, [open, refreshVoices]);
  const installedVoices = availableVoices.filter((v) => v.available !== false);
  // /voices lists the engine that speaks replies now, so the choice reaches it.
  const engineLabel = ENGINE_LABELS[voiceEngine] || voiceEngine || null;
  const savedVoiceListed = installedVoices.some((v) => v.id === settings.voice);
  const effectiveVoice = savedVoiceListed
    ? settings.voice
    : installedVoices.some((v) => v.id === defaultVoice) ? defaultVoice : "";
  const effectiveVoiceName = installedVoices.find((v) => v.id === effectiveVoice)?.name || effectiveVoice;
  const { title, hint } = describeVoicePhase(state, { systemName, mode });
  const running = Boolean(state.session);
  const fixInSettings = state.errorCode === "model" || state.errorCode === "mic-off";

  return (
    <Popover
      open={open}
      anchorEl={anchorEl}
      onClose={onClose}
      {...(PLACEMENTS[placement] || PLACEMENTS.below)}
      // Above the floating chat card (zIndex 1400), which it often overlaps.
      sx={{ zIndex: 1500 }}
      slotProps={{ paper: { sx: { width: 320, p: 2 } } }}
    >
      <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
        {title}
      </Typography>
      {hint && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {hint}
        </Typography>
      )}
      <LinearProgress
        variant="determinate"
        value={Math.min(100, level * LEVEL_SCALE)}
        aria-label={t("voice.listening.meterLabel")}
        sx={{ my: 1.5, height: 6, borderRadius: 1 }}
      />

      {state.error && (
        <Alert
          severity={state.errorCode === "denied" ? "warning" : "error"}
          sx={{ mb: 1.5, py: 0 }}
          onClose={() => voice.clearError()}
          action={
            fixInSettings ? (
              <Button component={RouterLink} to="/settings#settings-voice" size="small" color="inherit" onClick={onClose}>
                {t("voice.session.settings")}
              </Button>
            ) : undefined
          }
        >
          {state.error}
        </Alert>
      )}
      {state.notice && !state.error && (
        <Typography variant="caption" color="warning.main" sx={{ display: "block", mb: 1 }}>
          {state.notice.message}
        </Typography>
      )}

      {state.lastTranscript && (
        <Box sx={{ mb: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            {t("voice.session.lastHeard", {
              outcome: OUTCOME_LABELS[state.lastTranscript.outcome] || state.lastTranscript.outcome,
            })}
          </Typography>
          <Typography variant="body2" sx={{ fontStyle: "italic", wordBreak: "break-word" }}>
            {t("voice.session.heardText", { text: state.lastTranscript.text })}
          </Typography>
        </Box>
      )}

      <Box sx={{ display: "flex", gap: 1, mb: 1.5 }}>
        {running ? (
          <Button variant="contained" color="error" size="small" onClick={() => voice.stop()}>
            {t("voice.session.stopListening")}
          </Button>
        ) : (
          <Button
            variant="contained"
            size="small"
            onClick={() => voice.start(mode === "handsfree" ? "handsfree" : "utterance")}
          >
            {mode === "handsfree" ? t("voice.session.startListening") : t("voice.session.talkNow")}
          </Button>
        )}
        {state.speaking && (
          <Button variant="outlined" size="small" onClick={() => voice.stopSpeaking()}>
            {t("voice.session.stopSpeaking")}
          </Button>
        )}
      </Box>

      <Divider sx={{ mb: 1.5 }} />

      <Typography variant="caption" color="text.secondary">
        {t("voice.listening.micButton")}
      </Typography>
      <ToggleButtonGroup
        exclusive
        size="small"
        fullWidth
        value={mode}
        onChange={(_e, next) => next && setMode(next)}
        sx={{ mt: 0.5 }}
      >
        {ACTIVATION_MODES.map((m) => (
          <ToggleButton key={m} value={m} sx={{ textTransform: "none", fontSize: "0.75rem", py: 0.25 }}>
            {ACTIVATION_MODE_LABELS[m]}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
        {ACTIVATION_MODE_HELP[mode]}
      </Typography>

      <FormControlLabel
        sx={{ mt: 0.5 }}
        control={
          <Switch
            size="small"
            checked={settings.wakeWordEnabled}
            disabled={mode !== "handsfree"}
            onChange={(e) => updateVoiceSettings({ wakeWordEnabled: e.target.checked })}
          />
        }
        label={<Typography variant="body2">{t("voice.session.wakeToggle", { systemName })}</Typography>}
      />
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={settings.ttsEnabled}
            onChange={(e) => updateVoiceSettings({ ttsEnabled: e.target.checked })}
          />
        }
        label={<Typography variant="body2">{t("voice.session.speakReplies")}</Typography>}
      />
      {installedVoices.length > 0 && (
        <TextField
          select
          size="small"
          fullWidth
          label={engineLabel ? t("voice.session.replyVoiceEngine", { engine: engineLabel }) : t("voice.session.replyVoice")}
          value={effectiveVoice}
          onChange={(e) => updateVoiceSettings({ voice: e.target.value })}
          disabled={!settings.ttsEnabled}
          helperText={
            !savedVoiceListed && effectiveVoice
              ? engineLabel
                ? t("voice.session.savedVoiceMissing", { engine: engineLabel, voice: effectiveVoiceName })
                : t("voice.session.savedVoiceMissingThisEngine", { voice: effectiveVoiceName })
              : undefined
          }
          sx={{ mt: 1 }}
          SelectProps={{ MenuProps: { sx: { zIndex: 1600 } } }}
        >
          {installedVoices.map((v) => (
            <MenuItem key={v.id} value={v.id}>
              {v.name || v.id}
            </MenuItem>
          ))}
        </TextField>
      )}
      {settings.ttsEnabled && installedVoices.length === 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
          <Trans
            i18nKey="voice.session.noVoiceInstalled"
            components={{
              settingsLink: <Link component={RouterLink} to="/settings#settings-voice" onClick={onClose} />,
            }}
          />
        </Typography>
      )}
      {speakError && (
        <Alert severity="warning" sx={{ mt: 1, py: 0, fontSize: "0.75rem" }}>
          {speakError}
        </Alert>
      )}

      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mt: 1.5 }}>
        <Typography variant="caption" color="text.secondary">
          {t("voice.session.shortcutHint")}
        </Typography>
      </Box>
      <Box sx={{ display: "flex", gap: 2, mt: 0.5 }}>
        <Link component={RouterLink} to="/voice-chat" variant="body2" onClick={onClose}>
          {t("voice.session.voicePage")}
        </Link>
        <Link component={RouterLink} to="/settings#settings-voice" variant="body2" onClick={onClose}>
          {t("voice.session.voiceSettings")}
        </Link>
      </Box>
    </Popover>
  );
};

VoiceSessionPopover.propTypes = {
  anchorEl: PropTypes.object,
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  placement: PropTypes.oneOf(["below", "right"]),
};

export default VoiceSessionPopover;
