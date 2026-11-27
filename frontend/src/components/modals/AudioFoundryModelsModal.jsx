// frontend/src/components/modals/AudioFoundryModelsModal.jsx
// Check and install Audio Foundry weights (voice, music, FX) plus the sidecar.

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Typography,
  CircularProgress,
  Box,
  Chip,
  LinearProgress,
  Divider,
} from "@mui/material";
import GraphicEqIcon from "@mui/icons-material/GraphicEq";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";
import { ActionButton, StatusPill } from "../settings/ui";
import { enablePlugin, pluginGateRefusal, startPlugin } from "../../api/pluginsService";
import { startPluginsForRoute } from "../../hooks/useGpuIntent";
import { translate, useUiTranslation, Trans } from "../../i18n";
import {
  useDownloadStatus,
  useInstallAllPending,
  useModelDownloadsStore,
  useModelsChanged,
} from "../../stores/useModelDownloadsStore";

const IDLE_DOWNLOAD = {
  is_downloading: false,
  current_id: null,
  progress: 0,
  status: "idle",
  speed_mbps: 0,
  downloaded_gb: 0,
  total_gb: 0,
  error: null,
};

const startingStatus = (assetId, sizeGb) => ({
  ...IDLE_DOWNLOAD,
  is_downloading: true,
  current_id: assetId,
  status: "starting",
  total_gb: sizeGb || 0,
});

const groupLabels = () => ({
  voice: translate("models.audioFoundryModels.groups.voice"),
  music: translate("models.audioFoundryModels.groups.music"),
  fx: translate("models.audioFoundryModels.groups.fx"),
});

const GROUP_ORDER = ["voice", "music", "fx"];

const sizeLabel = (gb) => {
  const n = Number(gb) || 0;
  if (n >= 1) return `${n.toFixed(n >= 10 ? 0 : 1)} GB`;
  if (n <= 0) return null;
  return `${Math.round(n * 1024)} MB`;
};

// startRoute: the page whose route path starts the plugin (Audio Studio passes
// "/audio", so a plugin started here stops when the person leaves that page).
// Without it, Start is the Plugins page's start and the plugin stays up.
// plugin: the page's live plugin state, used instead of this modal's own read.
const AudioFoundryModelsModal = ({
  open,
  onClose,
  showMessage,
  highlightModelId,
  onChanged,
  startRoute,
  plugin: livePlugin,
}) => {
  const { t } = useUiTranslation();
  const [models, setModels] = useState([]);
  const [fetchedPlugin, setPlugin] = useState({ running: false, enabled: false, status: "unknown" });
  const plugin = livePlugin || fetchedPlugin;
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState(null);
  // Followed by the shared store, so the dialog can close mid-download.
  const dl = useDownloadStatus("audio", IDLE_DOWNLOAD);
  const refreshDownload = useModelDownloadsStore((s) => s.refresh);
  const markStarted = useModelDownloadsStore((s) => s.markStarted);
  const installAll = useModelDownloadsStore((s) => s.installAll);
  const installAllPending = useInstallAllPending("audio");
  const highlightRef = useRef(null);
  const showMessageRef = useRef(showMessage);
  useEffect(() => {
    showMessageRef.current = showMessage;
  }, [showMessage]);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/audio-foundry/models");
      if (res.data?.success) {
        setModels(res.data.models || []);
        setPlugin(res.data.plugin || { running: false, enabled: false, status: "unknown" });
        setError(null);
      } else {
        setError(translate("models.audioFoundryModels.failedToLoad"));
      }
    } catch (err) {
      setError(err.message || translate("models.audioFoundryModels.errorFetching"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchModels();
      refreshDownload("audio");
    } else {
      setModels([]);
      setError(null);
    }
  }, [open, fetchModels, refreshDownload]);

  // A download ended (the store toasts how): show the new install state. The
  // host page follows the store itself, so onChanged is not needed here.
  useModelsChanged("audio", () => {
    if (open) fetchModels();
  });

  useEffect(() => {
    if (open && highlightModelId && !loading && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [open, highlightModelId, loading, models]);

  const handleInstall = async (assetId) => {
    try {
      const res = await axios.post("/api/audio-foundry/models/download", { id: assetId });
      if (res.data?.success) {
        if (res.data.already_installed) {
          showMessageRef.current?.(t("models.audioFoundryModels.alreadyInstalled"), "info");
          fetchModels();
          onChanged?.();
          return;
        }
        const asset = models.find((m) => m.id === assetId);
        showMessageRef.current?.(t("models.shared.startedDownloading", { name: asset?.name || assetId }), "info");
        markStarted("audio", startingStatus(assetId, asset?.size_gb), asset?.name || assetId);
      } else {
        showMessageRef.current?.(res.data?.error || t("models.shared.failedToStart"), "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessageRef.current?.(t("models.shared.anotherDownloadRunning"), "warning");
      } else {
        const msg =
          err.response?.data?.error ||
          err.message ||
          t("models.shared.errorStartingDownload");
        showMessageRef.current?.(msg, "error");
      }
    }
  };

  // Optional models (e.g. ACE-Step 1.5) install only from their own row.
  const missing = models.filter((m) => !m.installed && !m.optional);
  const missingTotalGb = missing.reduce((sum, m) => sum + (m.size_gb || 0), 0);

  // One after another, started by the store as each ends, dialog open or not.
  const handleInstallAllMissing = () => {
    installAll(
      "audio",
      missing.map((m) => ({ body: { id: m.id }, name: m.name || m.id, status: startingStatus(m.id, m.size_gb) })),
    );
  };

  // Returns why the plugin did not start, or null once it runs.
  const requestStart = async () => {
    if (startRoute) {
      const res = await startPluginsForRoute(startRoute);
      return res?.success ? null : res?.error || t("models.audioFoundryModels.pluginStartFailed");
    }
    try {
      await enablePlugin("audio_foundry");
    } catch {
      /* already enabled is fine */
    }
    try {
      await startPlugin("audio_foundry");
    } catch (err) {
      // A gate refusal (cooling down, GPU busy) is a 409 carrying the gate's reason.
      return pluginGateRefusal(err)?.message || err?.message || t("models.audioFoundryModels.pluginStartFailed");
    }
    return null;
  };

  const handleStartPlugin = async () => {
    setStarting(true);
    try {
      const refused = await requestStart();
      if (refused) {
        showMessageRef.current?.(refused, "error");
      } else {
        showMessageRef.current?.(t("models.audioFoundryModels.pluginStarted"), "success");
      }
      await fetchModels();
      onChanged?.();
    } catch (err) {
      showMessageRef.current?.(
        err?.message || t("models.audioFoundryModels.pluginStartFailed"),
        "error",
      );
    } finally {
      setStarting(false);
    }
  };

  const pluginRunning = !!plugin.running;
  const pluginTone = pluginRunning ? "ok" : plugin.status === "starting" ? "warn" : "error";

  const renderRow = (m) => {
    const isThis = dl.is_downloading && dl.current_id === m.id;
    const isHighlight = !!highlightModelId && m.id === highlightModelId;
    const size = sizeLabel(m.size_gb);
    return (
      <ListItem
        key={m.id}
        ref={isHighlight ? highlightRef : undefined}
        divider
        sx={{
          py: 1.5,
          alignItems: "flex-start",
          bgcolor: isHighlight ? "action.selected" : undefined,
        }}
      >
        <ListItemIcon sx={{ mt: 0.5 }}>
          <GraphicEqIcon color={m.installed ? "primary" : "action"} />
        </ListItemIcon>
        <ListItemText
          primary={
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
              <Typography variant="body1" sx={{ fontWeight: 500 }}>
                {m.name}
              </Typography>
              {size && <Chip label={size} size="small" variant="outlined" />}
              {m.gated && (
                <Chip label={t("models.audioFoundryModels.gated")} size="small" color="warning" variant="outlined" />
              )}
              {m.license && <Chip label={m.license} size="small" variant="outlined" />}
            </Box>
          }
          secondary={
            <Box component="span" sx={{ display: "block", mt: 0.5 }}>
              <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                {m.description}
              </Typography>
              {m.vram_note && (
                <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                  {m.vram_note}
                </Typography>
              )}
              {m.gated && m.terms_url && !m.installed && (
                <Typography variant="caption" sx={{ display: "block", mt: 0.25 }}>
                  <Trans
                    i18nKey="models.audioFoundryModels.acceptTerms"
                    values={{ repo: m.hf_repo }}
                    components={{ anchor: <a href={m.terms_url} target="_blank" rel="noreferrer" /> }}
                  />
                </Typography>
              )}
              {m.hf_repo && (
                <Typography
                  variant="caption"
                  sx={{ display: "block", color: "text.secondary", fontFamily: "monospace", mt: 0.25 }}
                >
                  {m.hf_repo}
                </Typography>
              )}
            </Box>
          }
        />
        <Box sx={{ ml: 2, minWidth: 160, textAlign: "right" }}>
          {isThis ? (
            <Box sx={{ width: 160 }}>
              <Typography variant="caption" noWrap>
                {dl.status === "starting"
                  ? t("models.shared.starting")
                  : dl.status === "installing"
                    ? t("models.audioFoundryModels.buildingEnvironment")
                    : t("models.shared.progressSpeed", { progress: dl.progress, speed: dl.speed_mbps })}
              </Typography>
              <LinearProgress
                variant={dl.progress > 0 ? "determinate" : "indeterminate"}
                value={dl.progress}
                sx={{ mt: 0.5 }}
              />
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {(dl.downloaded_gb || 0).toFixed(2)} / {(dl.total_gb || 0).toFixed(2)} GB
              </Typography>
            </Box>
          ) : m.installed ? (
            <Chip
              icon={<CheckCircleIcon />}
              label={t("models.shared.installed")}
              color="success"
              size="small"
              variant="outlined"
            />
          ) : (
            <Button
              variant="outlined"
              size="small"
              startIcon={<CloudDownloadIcon />}
              onClick={() => handleInstall(m.id)}
              disabled={dl.is_downloading}
            >
              {t("dialogs.shared.install")}
            </Button>
          )}
        </Box>
      </ListItem>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="md"
      fullWidth
    >
      <DialogTitle>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <GraphicEqIcon />
          {t("models.audioFoundryModels.title")}
        </Box>
        <Typography variant="body2" sx={{ color: "text.secondary", mt: 0.5 }}>
          {t("models.audioFoundryModels.intro")}
        </Typography>
      </DialogTitle>

      <DialogContent dividers>
        {dl.is_downloading && (
          <Typography variant="caption" component="p" sx={{ color: "text.secondary", mb: 1 }}>
            {t("models.shared.continuesInBackground")}
          </Typography>
        )}
        {error && (
          <Box sx={{ mb: 2 }}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}

        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 2, flexWrap: "wrap" }}>
          <StatusPill
            tone={pluginTone}
            label={
              pluginRunning
                ? t("models.audioFoundryModels.pluginRunning")
                : t("models.audioFoundryModels.pluginStatus", { status: plugin.status || t("models.audioFoundryModels.stopped") })
            }
          />
          {!pluginRunning && (
            <ActionButton onClick={handleStartPlugin} loading={starting || plugin.status === "starting"}>
              {t("models.audioFoundryModels.startPlugin")}
            </ActionButton>
          )}
        </Box>

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress />
          </Box>
        ) : (
          GROUP_ORDER.map((group, idx) => {
            const labels = groupLabels();
            const rows = models.filter((m) => m.group === group);
            if (!rows.length) return null;
            return (
              <Box key={group}>
                {idx > 0 && <Divider sx={{ my: 1.5 }} />}
                <Typography
                  variant="subtitle2"
                  sx={{ color: "text.secondary", mb: 0.5, px: 1 }}
                >
                  {labels[group] || group}
                </Typography>
                <List disablePadding>{rows.map(renderRow)}</List>
              </Box>
            );
          })
        )}
      </DialogContent>

      <DialogActions>
        {missingTotalGb > 0 && (
          <Button
            onClick={handleInstallAllMissing}
            disabled={dl.is_downloading || installAllPending || loading || starting}
            startIcon={<CloudDownloadIcon />}
          >
            {t("models.shared.installAllMissing", { size: missingTotalGb.toFixed(1) })}
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>{t("dialogs.shared.close")}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default AudioFoundryModelsModal;
