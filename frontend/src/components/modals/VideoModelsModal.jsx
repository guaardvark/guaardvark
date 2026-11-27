// frontend/src/components/modals/VideoModelsModal.jsx
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
} from "@mui/material";
import MovieCreationIcon from "@mui/icons-material/MovieCreation";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import MemoryIcon from "@mui/icons-material/Memory";
import axios from "axios";

import { GENERATION_TYPES } from "../../constants/videoGeneratorPresets";
import { ActionButton, ConfirmActionDialog, SettingChip } from "../settings/ui";
import AddVideoModelDialog from "./AddVideoModelDialog";
import { translate, useUiTranslation } from "../../i18n";
import { useDownloadStatus, useModelDownloadsStore, useModelsChanged } from "../../stores/useModelDownloadsStore";

const IDLE_DOWNLOAD = {
  is_downloading: false,
  current_model: null,
  progress: 0,
  status: "idle",
  speed_mbps: 0,
  downloaded_gb: 0,
  total_gb: 0,
};

const typeLabels = () => ({
  wan: translate("models.videoModels.types.video"),
  cogvideox: translate("models.videoModels.types.video"),
  ltx: translate("models.videoModels.types.video"),
  hunyuan: translate("models.videoModels.types.video"),
  minimax: translate("models.videoModels.types.video"),
  facerestore: translate("models.videoModels.types.faceRestore"),
  upscaler: translate("models.videoModels.types.upscale"),
  flux: translate("models.videoModels.types.keyframeImage"),
  vae: translate("models.videoModels.types.dependency"),
  encoder: translate("models.videoModels.types.dependency"),
  clip_vision: translate("models.videoModels.types.dependency"),
  lora: translate("models.videoModels.types.speedLora"),
  embedding: translate("models.videoModels.types.dependency"),
  audio: translate("models.videoModels.types.music"),
  "qwen-edit": translate("models.videoModels.types.imageEditing"),
  "qwen-image": translate("models.videoModels.types.imageGeneration"),
  "moss-image": translate("models.videoModels.types.imageGeneration"),
  "minimax-image": translate("models.videoModels.types.imageGeneration"),
  pulid: translate("models.videoModels.types.imageEditing"),
  editing: translate("models.videoModels.types.imageEditing"),
});

const VideoModelsModal = ({ open, onClose, showMessage, highlightModelId }) => {
  const { t } = useUiTranslation();
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  // When the parent opens us pointed at a specific model (because the user tried
  // to generate with an uninstalled one), scroll it into view and pulse it.
  const highlightRef = useRef(null);
  // Followed by the shared store, so the dialog can close mid-download.
  const downloadStatus = useDownloadStatus("video", IDLE_DOWNLOAD);
  const refreshDownload = useModelDownloadsStore((s) => s.refresh);
  const markStarted = useModelDownloadsStore((s) => s.markStarted);
  const [error, setError] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState(null);
  const [removeBusy, setRemoveBusy] = useState(false);

  // The parent passes a fresh `showMessage` closure on every render. Keep it in a
  // ref so our fetch callbacks (and the effects that depend on them) stay stable —
  // otherwise the parent's polling re-renders recreate fetchDownloadStatus every
  // tick, re-firing the mount effect and making the modal flash/refetch endlessly.
  const showMessageRef = useRef(showMessage);
  useEffect(() => {
    showMessageRef.current = showMessage;
  }, [showMessage]);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/batch-video/models");
      if (res.data.success) {
        setModels(res.data.data.models);
      } else {
        setError(translate("models.videoModels.failedToLoad"));
      }
    } catch (err) {
      setError(err.message || translate("models.videoModels.errorFetching"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchModels();
      refreshDownload("video");
    } else {
      setModels([]);
      setError(null);
    }
  }, [open, fetchModels, refreshDownload]);

  // A download ended, a stall or backend restart included (issue #36); the
  // store toasts the reason. Refresh so the true install state shows and
  // Install is live again for a retry.
  useModelsChanged("video", () => {
    if (open) fetchModels();
  });

  // Once the model list is rendered, bring the highlighted row into view.
  useEffect(() => {
    if (open && highlightModelId && !loading && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [open, highlightModelId, loading, models]);

  const handleRemove = async () => {
    if (!removeTarget) return;
    setRemoveBusy(true);
    try {
      const res = await axios.delete(`/api/batch-video/models/user/${encodeURIComponent(removeTarget.id)}`, {
        params: { delete_files: removeTarget.deleteFiles !== false },
      });
      if (res.data.success) {
        showMessageRef.current?.(t("models.videoModels.removed", { name: removeTarget.name }), "success");
        setRemoveTarget(null);
        fetchModels();
      } else {
        showMessageRef.current?.(res.data.error?.message || res.data.message || t("models.shared.couldNotRemove"), "error");
      }
    } catch (err) {
      showMessageRef.current?.(err.response?.data?.error?.message || err.message || t("models.shared.couldNotRemove"), "error");
    } finally {
      setRemoveBusy(false);
    }
  };

  const handleDownload = async (modelId) => {
    try {
      const res = await axios.post("/api/batch-video/models/download", { model_id: modelId });
      if (res.data.success && res.data.data?.already_installed) {
        fetchModels();
      } else if (res.data.success) {
        const model = models.find((m) => m.id === modelId);
        showMessageRef.current?.(t("models.shared.startedDownloadingDots", { name: model?.name || modelId }), "info");
        markStarted(
          "video",
          {
            ...IDLE_DOWNLOAD,
            is_downloading: true,
            current_model: modelId,
            status: "starting",
            total_gb: model?.size_gb || 0,
          },
          model?.name || modelId,
        );
      } else {
        showMessageRef.current?.(res.data.error || t("models.shared.failedToStartDownload"), "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessageRef.current?.(t("models.shared.downloadInProgress"), "warning");
      } else {
        showMessageRef.current?.(err.message || t("models.shared.errorStartingDownload"), "error");
      }
    }
  };

  const isDownloading = downloadStatus.is_downloading;
  const currentModel = downloadStatus.current_model;

  const generationModels = models.filter((m) => GENERATION_TYPES.has(m.type));
  const postModels = models.filter((m) => !GENERATION_TYPES.has(m.type));

  const labels = typeLabels();

  const renderModelRow = (model) => {
    const isThis = isDownloading && currentModel === model.id;
    const isHighlight = !!highlightModelId && model.id === highlightModelId;
    const typeLabel = model.user && model.type === "encoder" ? t("models.videoModels.types.textEncoder") : labels[model.type] || model.type;
    return (
      <ListItem
        key={model.id}
        divider
        ref={isHighlight ? highlightRef : undefined}
        sx={{
          py: 1.5,
          ...(isHighlight && {
            border: 2,
            borderColor: "warning.main",
            borderRadius: 1,
            "@keyframes vmPulse": {
              "0%, 100%": { boxShadow: "0 0 0 0 rgba(255,167,38,0)" },
              "50%": { boxShadow: "0 0 0 4px rgba(255,167,38,0.55)" },
            },
            animation: "vmPulse 1.2s ease-in-out 3",
          }),
        }}
      >
        <ListItemIcon>
          <MovieCreationIcon color={(model.is_ready ?? model.is_downloaded) ? "primary" : "action"} />
        </ListItemIcon>
        <ListItemText
          primary={
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
              <Typography variant="body1" sx={{ fontWeight: 500 }}>
                {model.name}
              </Typography>
              <Chip label={typeLabel} size="small" variant="outlined" sx={{ height: 20, fontSize: "0.65rem" }} />
              <Chip label={`${model.size_gb} GB`} size="small" variant="outlined" />
              {model.vram_mb > 0 && (
                <Chip
                  icon={<MemoryIcon />}
                  label={`${(model.vram_mb / 1024).toFixed(0)}GB VRAM`}
                  size="small"
                  variant="outlined"
                />
              )}
              {model.license?.name && (
                <Chip
                  label={model.license.name}
                  size="small"
                  color="warning"
                  variant="outlined"
                  title={model.license.note || ""}
                  sx={{ height: 20, fontSize: "0.65rem" }}
                />
              )}
            </Box>
          }
          secondary={
            model.license?.note ? (
              <>
                {model.description}
                <Typography
                  component="span"
                  variant="caption"
                  sx={{ color: "text.secondary", display: "block", mt: 0.5 }}
                >
                  {model.license.note}{" "}
                  {model.license.form_url && (
                    <a href={model.license.form_url} target="_blank" rel="noreferrer noopener">
                      {t("models.videoModels.applicationForm")}
                    </a>
                  )}
                  {model.license.url && (
                    <>
                      {" · "}
                      <a href={model.license.url} target="_blank" rel="noreferrer noopener">
                        {t("models.videoModels.licenseText")}
                      </a>
                    </>
                  )}
                </Typography>
              </>
            ) : (
              model.description
            )
          }
        />

        <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>
          {isThis ? (
            <Box sx={{ width: 130 }}>
              <Typography variant="caption" noWrap>
                {downloadStatus.status === "starting"
                  ? t("models.shared.startingDots")
                  : t("models.shared.progressSpeed", { progress: downloadStatus.progress, speed: downloadStatus.speed_mbps })}
              </Typography>
              <LinearProgress
                variant={downloadStatus.progress > 0 ? "determinate" : "indeterminate"}
                value={downloadStatus.progress}
                sx={{ mt: 0.5 }}
              />
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {downloadStatus.downloaded_gb.toFixed(1)} / {downloadStatus.total_gb.toFixed(1)} GB
              </Typography>
            </Box>
          ) : (model.is_ready ?? model.is_downloaded) ? (
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 0.5 }}>
              <Chip
                icon={<CheckCircleIcon />}
                label={t("models.shared.installed")}
                color="success"
                size="small"
                variant="outlined"
              />
              {model.user && (
                <ActionButton
                  kind="destructive"
                  onClick={() => setRemoveTarget({ ...model, deleteFiles: true })}
                  disabled={isDownloading}
                >
                  {t("dialogs.shared.remove")}
                </ActionButton>
              )}
            </Box>
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 0.5 }}>
              <Button
                variant="outlined"
                size="small"
                startIcon={<CloudDownloadIcon />}
                onClick={() => handleDownload(model.id)}
                disabled={isDownloading}
              >
                {model.install_size_gb ? t("models.videoModels.installWithSize", { size: model.install_size_gb }) : t("dialogs.shared.install")}
              </Button>
              {model.user && (
                <ActionButton
                  kind="destructive"
                  onClick={() => setRemoveTarget({ ...model, deleteFiles: true })}
                  disabled={isDownloading}
                >
                  {t("dialogs.shared.remove")}
                </ActionButton>
              )}
              {model.requires?.length > 0 && (
                <Typography variant="caption" noWrap sx={{ color: "text.secondary" }}>
                  {t("models.videoModels.includesRequired", { count: model.requires.length })}
                </Typography>
              )}
              {model.is_downloaded && !(model.is_ready ?? true) && (
                <Typography variant="caption" noWrap sx={{ color: "warning.main" }}>
                  {t("models.videoModels.missingDependencies")}
                </Typography>
              )}
              {!(model.is_ready ?? model.is_downloaded) && model.missing_files?.length > 0 && (
                <Typography
                  variant="caption"
                  noWrap
                  title={model.missing_files.join("\n")}
                  sx={{ color: "text.secondary" }}
                >
                  {t("models.videoModels.filesMissing", { count: model.missing_files.length })}
                </Typography>
              )}
              {Object.keys(model.incomplete_files || {}).length > 0 && (
                <Typography
                  variant="caption"
                  color="warning.main"
                  noWrap
                  title={Object.entries(model.incomplete_files)
                    .map(([file, reason]) => `${file}: ${reason}`)
                    .join("\n")}
                >
                  {t("models.shared.filesIncomplete", {
                    count: Object.keys(model.incomplete_files).length,
                  })}
                </Typography>
              )}
            </Box>
          )}
        </Box>
      </ListItem>
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("models.videoModels.title")}</DialogTitle>
      <DialogContent dividers>
        {isDownloading && (
          <Typography variant="caption" component="p" sx={{ color: "text.secondary", mb: 1 }}>
            {t("models.shared.continuesInBackground")}
          </Typography>
        )}
        {error && (
          <Box sx={{ mb: 2 }}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}

        {/* First-run discoverability (issue #36): if nothing is installed yet,
            say so up front — video generation can't run without a model, and
            there's no auto-download. */}
        {!loading && models.length > 0 && !models.some((m) => m.is_ready ?? m.is_downloaded) && (
          <Box sx={{ mb: 2, p: 1.5, border: 1, borderColor: "warning.main", borderRadius: 1 }}>
            <Typography variant="body2" sx={{ color: "warning.main" }}>
              {t("models.videoModels.noneInstalled")}
            </Typography>
          </Box>
        )}

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {generationModels.length > 0 && (
              <>
                <Typography
                  variant="overline"
                  sx={{ color: "text.secondary", px: 2, pt: 1, display: "block" }}
                >
                  {t("models.videoModels.generation")}
                </Typography>
                {generationModels.map(renderModelRow)}
              </>
            )}
            {postModels.length > 0 && (
              <>
                <Typography
                  variant="overline"
                  sx={{ color: "text.secondary", px: 2, pt: 2, display: "block" }}
                >
                  {t("models.videoModels.postProcessing")}
                </Typography>
                {postModels.map(renderModelRow)}
              </>
            )}
            {models.length === 0 && !loading && (
              <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                {t("models.videoModels.noModels")}
              </Typography>
            )}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={() => setAddOpen(true)} disabled={isDownloading}>
          {t("models.shared.addNewModel")}
        </ActionButton>
        <Button onClick={onClose}>{t("dialogs.shared.close")}</Button>
      </DialogActions>
      <AddVideoModelDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        models={models}
        showMessage={showMessageRef.current}
        onAdded={() => {
          fetchModels();
          refreshDownload("video");
        }}
      />
      <ConfirmActionDialog
        open={Boolean(removeTarget)}
        title={t("models.shared.removeTitle", { name: removeTarget?.name || t("models.videoModels.thisModel") })}
        description={t("models.videoModels.removeDescription")}
        extra={
          <SettingChip
            label={t("models.shared.deleteDownloadedFiles")}
            on={removeTarget?.deleteFiles !== false}
            onToggle={(next) => setRemoveTarget((t) => (t ? { ...t, deleteFiles: next } : t))}
          />
        }
        keeps={t("models.videoModels.removeKeeps")}
        confirmLabel={t("dialogs.shared.remove")}
        busy={removeBusy}
        onConfirm={handleRemove}
        onClose={() => !removeBusy && setRemoveTarget(null)}
      />
    </Dialog>
  );
};

export default VideoModelsModal;
