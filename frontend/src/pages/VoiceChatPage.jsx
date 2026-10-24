import React, { useCallback, useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItem,
  ListItemText,
  Paper,
  Typography,
} from "@mui/material";
import MicNoneIcon from "@mui/icons-material/MicNone";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useShallow } from "zustand/react/shallow";
import voiceService from "../api/voiceService";
import GlobalMicButton from "../components/voice/GlobalMicButton";
import VoiceListeningSettings from "../components/voice/VoiceListeningSettings";
import SpeechModelSelect from "../components/voice/SpeechModelSelect";
import { useVoiceSession, useVoiceSessionState } from "../contexts/VoiceSessionContext";
import { useAppStore } from "../stores/useAppStore";
import brand from "../config/brand";
import { describeVoicePhase, OUTCOME_LABELS } from "../utils/voicePhase";
import { translate, uiLocale, useUiTranslation } from "../i18n";

/** Microphone permission as the browser reports it, without asking for it. */
function useMicPermission() {
  const [permission, setPermission] = useState("unknown");
  useEffect(() => {
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      setPermission("insecure");
      return undefined;
    }
    let status = null;
    let cancelled = false;
    const update = () => !cancelled && status && setPermission(status.state);
    navigator.permissions
      ?.query?.({ name: "microphone" })
      .then((result) => {
        status = result;
        update();
        status.onchange = update;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (status) status.onchange = null;
    };
  }, []);
  return permission;
}

const permissionText = () => ({
  granted: [translate("voice.page.permission.granted"), "success"],
  prompt: [translate("voice.page.permission.prompt"), "default"],
  denied: [translate("voice.page.permission.denied"), "error"],
  insecure: [translate("voice.page.permission.insecure"), "error"],
  unknown: [translate("voice.page.permission.prompt"), "default"],
});

const StatusRow = ({ label, value, tone = "default", detail }) => (
  <Box sx={{ display: "flex", alignItems: "baseline", gap: 1.5, py: 0.75, flexWrap: "wrap" }}>
    <Typography variant="body2" sx={{ minWidth: 160, color: "text.secondary" }}>
      {label}
    </Typography>
    <Chip size="small" label={value} color={tone} variant={tone === "default" ? "outlined" : "filled"} />
    {detail && (
      <Typography variant="caption" color="text.secondary">
        {detail}
      </Typography>
    )}
  </Box>
);

/**
 * The Voice page: whether voice works on this machine and how the global mic
 * listens. Talking happens through the mic in the top bar (or sidebar), which
 * sends to the floating chat or the Chat page.
 */
const VoiceChatPage = () => {
  const { t } = useUiTranslation();
  const voice = useVoiceSession();
  const systemName = useAppStore((s) => s.systemName) || brand.appName;
  const mode = useAppStore((s) => s.voiceActivationMode);
  const session = useVoiceSessionState(
    useShallow((s) => ({
      phase: s.phase,
      listenMode: s.listenMode,
      push: s.push,
      session: s.session,
      error: s.error,
      errorCode: s.errorCode,
      notice: s.notice,
    }))
  );
  const transcripts = useVoiceSessionState((s) => s.transcripts);
  const permission = useMicPermission();
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadStatus = useCallback(async () => {
    setLoading(true);
    setStatusError(null);
    try {
      setStatus(await voiceService.getStatus());
    } catch (err) {
      setStatusError({ message: err?.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  const { title, hint } = describeVoicePhase(session, { systemName, mode });
  const piperVoices = status?.available_voices || [];
  const permissions = permissionText();
  const [permissionLabel, permissionTone] = permissions[permission] || permissions.unknown;

  return (
    <Paper sx={{ display: "flex", flexDirection: "column", height: "calc(100vh - 104px)", overflow: "hidden" }}>
      <Box sx={{ p: 3, borderBottom: 1, borderColor: "divider", display: "flex", alignItems: "center", gap: 2 }}>
        <MicNoneIcon sx={{ fontSize: 32, color: "primary.main" }} />
        <Box sx={{ flexGrow: 1 }}>
          <Typography variant="h4" component="h1" fontWeight="bold">
            {t("voice.page.title")}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t("voice.page.intro")}
          </Typography>
        </Box>
        <GlobalMicButton variant="inline" label={t("common.voice")} showMenu={false} />
      </Box>

      <Box sx={{ flex: 1, overflow: "auto", p: 3, display: "grid", gap: 3, gridTemplateColumns: { xs: "1fr", lg: "1fr 1fr" }, alignContent: "start" }}>
        <Paper variant="outlined" sx={{ p: 2.5 }}>
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 1 }}>
            <Typography variant="h6" component="h2">
              {t("voice.page.thisMachine")}
            </Typography>
            <Button size="small" startIcon={loading ? <CircularProgress size={14} /> : <RefreshIcon />} onClick={loadStatus} disabled={loading}>
              {t("voice.page.refresh")}
            </Button>
          </Box>
          {statusError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {statusError.message || t("voice.page.statusFailed")}
            </Alert>
          )}
          <StatusRow label={t("voice.page.microphone")} value={permissionLabel} tone={permissionTone} />
          {status && (
            <>
              <StatusRow
                label={t("voice.page.speechRecognition")}
                value={status.speech_model_installed ? t("voice.page.ready") : t("voice.page.modelNotInstalled")}
                tone={status.speech_model_installed ? "success" : "warning"}
                detail={
                  status.speech_model_installed
                    ? t("voice.page.whisperOnMachine", { model: status.speech_model_id || "" })
                    : t("voice.page.installSpeechModel")
                }
              />
              <StatusRow
                label={t("voice.page.spokenReplies")}
                value={piperVoices.length ? t("voice.page.voicesInstalled", { count: piperVoices.length }) : t("voice.page.noVoiceInstalled")}
                tone={piperVoices.length ? "success" : "warning"}
                detail={
                  piperVoices.length
                    ? t("voice.page.voicesDetail", { voices: piperVoices.join(", ") })
                    : t("voice.page.installVoice")
                }
              />
              <StatusRow
                label="FFmpeg"
                value={status.ffmpeg_available ? t("voice.page.installed") : t("voice.page.missing")}
                tone={status.ffmpeg_available ? "success" : "warning"}
              />
            </>
          )}
          {status && (!status.speech_model_installed || !piperVoices.length) && (
            <Button component={RouterLink} to="/settings#settings-voice" size="small" sx={{ mt: 1 }}>
              {t("voice.page.openVoiceSettings")}
            </Button>
          )}
          <Box sx={{ mt: 2 }}>
            <SpeechModelSelect reloadKey={status} onChange={loadStatus} />
          </Box>

          <Divider sx={{ my: 2 }} />
          <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
            {t("voice.page.rightNow", { state: title })}
          </Typography>
          {hint && (
            <Typography variant="body2" color="text.secondary">
              {hint}
            </Typography>
          )}
          {session.error && (
            <Alert severity="warning" sx={{ mt: 1 }} onClose={() => voice.clearError()}>
              {session.error}
            </Alert>
          )}
          {session.notice && (
            <Typography variant="body2" color="warning.main" sx={{ mt: 1 }}>
              {session.notice.message}
            </Typography>
          )}

          <Divider sx={{ my: 2 }} />
          <Typography variant="h6" component="h2" sx={{ mb: 1 }}>
            {t("voice.page.recentlyHeard")}
          </Typography>
          {transcripts.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("voice.page.nothingHeard")}
            </Typography>
          ) : (
            <List dense disablePadding>
              {transcripts.map((heard, i) => (
                <ListItem key={`${heard.at}-${i}`} disableGutters divider>
                  <ListItemText
                    primary={t("voice.session.heardText", { text: heard.text })}
                    secondary={`${new Date(heard.at).toLocaleTimeString(uiLocale())} · ${OUTCOME_LABELS[heard.outcome] || heard.outcome}`}
                  />
                </ListItem>
              ))}
            </List>
          )}
        </Paper>

        <Paper variant="outlined" sx={{ p: 2.5 }}>
          <Typography variant="h6" component="h2" sx={{ mb: 1.5 }}>
            {t("voice.page.howMicListens")}
          </Typography>
          <VoiceListeningSettings />
        </Paper>
      </Box>
    </Paper>
  );
};

export default VoiceChatPage;
