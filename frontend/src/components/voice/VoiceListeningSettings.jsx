import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  FormControlLabel,
  Slider,
  Switch,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useShallow } from "zustand/react/shallow";
import { useVoiceSession, useVoiceSessionState } from "../../contexts/VoiceSessionContext";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceSettings } from "../../hooks/useVoiceSettings";
import brand from "../../config/brand";
import {
  ACTIVATION_MODES,
  ACTIVATION_MODE_HELP,
  ACTIVATION_MODE_LABELS,
  VOICE_LIMITS,
  updateVoiceSettings,
} from "../../config/voiceDefaults";
import { useUiTranslation } from "../../i18n";

const METER_RANGE = VOICE_LIMITS.silenceThreshold.max;

/**
 * Live microphone level with the speech threshold drawn on it. "Test mic"
 * holds the global session's microphone open (acquire/release), so it never
 * fights the mic buttons for the device.
 */
export const MicLevelMeter = ({ threshold }) => {
  const { t } = useUiTranslation();
  const voice = useVoiceSession();
  const { level, micOpen, error } = useVoiceSessionState(
    useShallow((s) => ({ level: s.level, micOpen: s.micOpen, error: s.error }))
  );
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (!testing) return undefined;
    return () => voice.release("meter");
  }, [testing, voice]);

  const toggleTest = async () => {
    if (testing) {
      setTesting(false);
      return;
    }
    const ok = await voice.acquire("meter");
    if (ok) setTesting(true);
  };

  const shown = micOpen ? level : 0;
  const pct = (v) => `${Math.min(100, (v / METER_RANGE) * 100)}%`;
  return (
    <Box sx={{ mt: 1, mb: 2 }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1 }}>
        <Typography variant="caption" color="text.secondary">
          {t("voice.listening.meterCaption")}
        </Typography>
        <Button size="small" variant="outlined" onClick={toggleTest}>
          {testing ? t("voice.listening.stopTest") : t("voice.listening.testMic")}
        </Button>
      </Box>
      <Box
        role="meter"
        aria-label={t("voice.listening.meterLabel")}
        aria-valuemin={0}
        aria-valuemax={METER_RANGE}
        aria-valuenow={Number(shown.toFixed(3))}
        sx={{
          position: "relative",
          height: 20,
          bgcolor: "background.paper",
          borderRadius: 1,
          border: "1px solid",
          borderColor: "divider",
          overflow: "hidden",
        }}
      >
        <Box
          sx={{
            position: "absolute",
            left: 0,
            top: 0,
            bottom: 0,
            width: pct(shown),
            bgcolor: shown > threshold ? "success.main" : "primary.main",
            transition: "width 0.08s linear, background-color 0.2s",
          }}
        />
        <Box sx={{ position: "absolute", left: pct(threshold), top: 0, bottom: 0, width: 2, bgcolor: "error.main" }} />
      </Box>
      {testing && error && (
        <Alert severity="warning" sx={{ mt: 1, py: 0 }}>
          {error}
        </Alert>
      )}
      {!testing && error && (
        <Typography variant="caption" color="warning.main">
          {error}
        </Typography>
      )}
    </Box>
  );
};

MicLevelMeter.propTypes = { threshold: PropTypes.number.isRequired };

const SliderRow = ({ label, value, limits, format, onChange }) => {
  const { t } = useUiTranslation();
  return (
    <Box sx={{ mb: 1.5 }}>
      <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.75rem", mb: 0.5 }}>
        {t("voice.listening.sliderValue", { label, amount: format(value) })}
      </Typography>
      <Slider
        value={value}
        min={limits.min}
        max={limits.max}
        step={limits.step}
        onChange={(_e, v) => onChange(v)}
        valueLabelDisplay="auto"
        valueLabelFormat={format}
        size="small"
        aria-label={label}
      />
    </Box>
  );
};

SliderRow.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.number.isRequired,
  limits: PropTypes.object.isRequired,
  format: PropTypes.func.isRequired,
  onChange: PropTypes.func.isRequired,
};

const seconds = (ms) => `${(ms / 1000).toFixed(ms % 1000 ? 1 : 0)}s`;

/**
 * How the global mic listens: what a click does, the wake phrase, the speech
 * detector and talking over replies. Used by Settings → Voice and the Voice
 * page; both read the same defaults (config/voiceDefaults.js).
 *
 * @param {{settings?: object, onChange?: (key: string, value: any) => void, systemName?: string}} props
 *   Without `settings`/`onChange` it reads and writes the stored voice settings itself.
 */
const VoiceListeningSettings = ({ settings: settingsProp, onChange, systemName: systemNameProp }) => {
  const { t } = useUiTranslation();
  const stored = useVoiceSettings();
  const settings = settingsProp ? { ...stored, ...settingsProp } : stored;
  const change = onChange || ((key, value) => updateVoiceSettings({ [key]: value }));
  const mode = useAppStore((s) => s.voiceActivationMode);
  const setMode = useAppStore((s) => s.setVoiceActivationMode);
  const storeName = useAppStore((s) => s.systemName);
  const systemName = systemNameProp || storeName || brand.appName;

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1, fontSize: "0.85rem" }}>
        {t("voice.listening.micButton")}
      </Typography>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={mode}
        onChange={(_e, next) => next && setMode(next)}
        aria-label={t("voice.listening.modeGroupLabel")}
      >
        {ACTIVATION_MODES.map((m) => (
          <ToggleButton key={m} value={m} sx={{ textTransform: "none", fontSize: "0.8rem" }}>
            {ACTIVATION_MODE_LABELS[m]}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5, mb: 2 }}>
        {ACTIVATION_MODE_HELP[mode]} {t("voice.listening.holdHint")}
      </Typography>

      <Typography variant="subtitle2" sx={{ mb: 1, fontSize: "0.85rem" }}>
        {t("voice.listening.speechDetection")}
      </Typography>
      <SliderRow
        label={t("voice.listening.threshold")}
        value={settings.silenceThreshold}
        limits={VOICE_LIMITS.silenceThreshold}
        format={(v) => v.toFixed(2)}
        onChange={(v) => change("silenceThreshold", v)}
      />
      <MicLevelMeter threshold={settings.silenceThreshold} />
      <SliderRow
        label={t("voice.listening.silenceTimeout")}
        value={settings.silenceTimeout}
        limits={VOICE_LIMITS.silenceTimeout}
        format={seconds}
        onChange={(v) => change("silenceTimeout", v)}
      />
      <SliderRow
        label={t("voice.listening.maxSegment")}
        value={settings.maxSegmentDuration}
        limits={VOICE_LIMITS.maxSegmentDuration}
        format={seconds}
        onChange={(v) => change("maxSegmentDuration", v)}
      />

      <Typography variant="subtitle2" sx={{ mt: 2, mb: 0.5, fontSize: "0.85rem" }}>
        {t("voice.listening.wakePhrase")}
      </Typography>
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={settings.wakeWordEnabled}
            onChange={(e) => change("wakeWordEnabled", e.target.checked)}
          />
        }
        label={
          <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.8rem" }}>
            {t("voice.listening.wakeToggle", { systemName })}
          </Typography>
        }
      />
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        {t("voice.listening.wakeHelp")}
      </Typography>
      {settings.wakeWordEnabled && (
        <SliderRow
          label={t("voice.listening.awakeFor")}
          value={settings.activeListeningDuration}
          limits={VOICE_LIMITS.activeListeningDuration}
          format={seconds}
          onChange={(v) => change("activeListeningDuration", v)}
        />
      )}

      <FormControlLabel
        control={
          <Switch size="small" checked={settings.bargeIn} onChange={(e) => change("bargeIn", e.target.checked)} />
        }
        label={
          <Typography variant="body2" color="text.secondary" sx={{ fontSize: "0.8rem" }}>
            {t("voice.listening.bargeIn")}
          </Typography>
        }
      />
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        {t("voice.listening.bargeInHelp")}
      </Typography>
    </Box>
  );
};

VoiceListeningSettings.propTypes = {
  settings: PropTypes.object,
  onChange: PropTypes.func,
  systemName: PropTypes.string,
};

export default VoiceListeningSettings;
