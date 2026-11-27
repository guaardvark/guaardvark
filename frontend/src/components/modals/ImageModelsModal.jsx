// frontend/src/components/modals/ImageModelsModal.jsx
import React, { useState, useEffect, useCallback, useRef } from "react";
import { formatUiError } from "../../utils/uiError";
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
import ImageIcon from "@mui/icons-material/Image";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";
import { ActionButton, ConfirmActionDialog, SettingChip } from "../settings/ui";
import AddImageModelDialog from "./AddImageModelDialog";
import { translate, useUiTranslation, Trans } from "../../i18n";
import {
  useDownloadStatus,
  useInstallAllPending,
  useModelDownloadsStore,
  useModelsChanged,
} from "../../stores/useModelDownloadsStore";

const IDLE_DOWNLOAD = {
  is_downloading: false,
  current_model: null,
  progress: 0,
  status: "idle",
  speed_mbps: 0,
  downloaded_gb: 0,
  total_gb: 0,
};

const startingStatus = (currentModel, sizeGb) => ({
  ...IDLE_DOWNLOAD,
  is_downloading: true,
  current_model: currentModel,
  status: "starting",
  total_gb: sizeGb || 0,
});

const modelMatchesDownload = (model, currentModel) =>
  !!currentModel && (currentModel === model.path || currentModel === model.id);

// "21 GB", "10.7 GB": one decimal, none when it is whole.
const gbLabel = (gb) => `${Number(gb.toFixed(1))} GB`;

// installModelId: opened from a page's Install button. That row is brought into
// view and its install started, as if the person had clicked Install on it.
const ImageModelsModal = ({ open, onClose, showMessage, installModelId }) => {
  const { t } = useUiTranslation();
  const [models, setModels] = useState([]);
  const [adapters, setAdapters] = useState([]);
  const [encoders, setEncoders] = useState([]);
  // Chat photo-tool packs (Qwen-Image-Edit, Kontext, PuLID, background removal).
  const [editing, setEditing] = useState([]);
  const [addOpen, setAddOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState(null);
  // A model whose RAM need this system can never hold, waiting on the
  // person's "Install anyway" (fits_ram from /batch-image/models).
  const [ramWarnTarget, setRamWarnTarget] = useState(null);
  // The RAM this system sees and the admission floor (the list's `machine`).
  const [machine, setMachine] = useState(null);
  const [loading, setLoading] = useState(true);
  // The download runs on the server and the shared store follows it, so this
  // dialog can close mid-download and pick the progress back up on reopen.
  const downloadStatus = useDownloadStatus("image", IDLE_DOWNLOAD);
  const refreshDownload = useModelDownloadsStore((s) => s.refresh);
  const markStarted = useModelDownloadsStore((s) => s.markStarted);
  const installAll = useModelDownloadsStore((s) => s.installAll);
  const installAllPending = useInstallAllPending("image");
  const [error, setError] = useState(null);
  const [unavailable, setUnavailable] = useState([]);
  const [checked, setChecked] = useState(false);
  // The first download-status read since opening has returned.
  const [statusRead, setStatusRead] = useState(false);
  const installStartedRef = useRef(false);
  const installRowRef = useRef(null);

  // check: ask Hugging Face whether each model not on disk can be fetched. Only
  // on the person's click; listing the models never contacts anyone.
  const fetchModels = useCallback(async (check = false) => {
    try {
      setLoading(true);
      const res = await axios.get("/api/batch-image/models", { params: check ? { check: 1 } : {} });
      if (res.data.success) {
        setModels(res.data.data.models);
        setAdapters(res.data.data.adapters || []);
        setEncoders(res.data.data.encoders || []);
        setEditing(res.data.data.editing || []);
        setMachine(res.data.data.machine || null);
        setUnavailable(check ? res.data.data.unavailable_models || [] : []);
        setChecked(check);
      } else {
        setError(translate("models.shared.failedToLoadModels"));
      }
    } catch (err) {
      setError(err.message || translate("models.shared.errorFetchingModels"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchModels();
      refreshDownload("image").finally(() => setStatusRead(true));
    } else {
      setModels([]);
      setError(null);
      setStatusRead(false);
      installStartedRef.current = false;
    }
  }, [open, fetchModels, refreshDownload]);

  // A download ended (the store toasts how): show the new install state.
  useModelsChanged("image", () => {
    if (open) fetchModels();
  });

  const handleDownload = async (model) => {
    const modelRef = model.path || model.id;
    try {
      const res = await axios.post("/api/batch-image/models/download", { model_path: modelRef });
      if (res.data.success && res.data.data?.already_installed) {
        fetchModels();
      } else if (res.data.success) {
        const startedPath = res.data.data?.model_path || modelRef;
        showMessage?.(t("models.shared.startedDownloadingDots", { name: model.name || model.id }), "info");
        markStarted("image", startingStatus(startedPath, model.size_gb), model.name || model.id);
      } else {
        showMessage?.(res.data.error || t("models.shared.failedToStartDownload"), "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessage?.(t("models.shared.downloadInProgress"), "warning");
      } else {
        showMessage?.(formatUiError(err.response?.data?.error) || err.message || t("models.shared.errorStartingDownload"), "error");
      }
    }
  };

  // Install, after a warning when the model needs more RAM than this system has.
  const requestInstall = (model) => {
    if (model.fits_ram === false) setRamWarnTarget(model);
    else handleDownload(model);
  };

  // Waits for the list and the first status read, so a download that is
  // already running shows its progress instead of being started twice.
  useEffect(() => {
    if (!open || !installModelId || loading || !statusRead || installStartedRef.current) return;
    const target = models.find((m) => m.id === installModelId);
    if (!target) return;
    installStartedRef.current = true;
    installRowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (target.is_downloaded) return;
    if (downloadStatus.is_downloading) {
      if (!modelMatchesDownload(target, downloadStatus.current_model)) {
        showMessage?.(translate("models.shared.downloadInProgress"), "warning");
      }
      return;
    }
    requestInstall(target);
  }, [open, installModelId, loading, statusRead, models, downloadStatus]);

  const handleRemove = async () => {
    if (!removeTarget) return;
    try {
      const res = await axios.delete(`/api/batch-image/models/user/${encodeURIComponent(removeTarget.id)}`, {
        params: { delete_files: removeTarget.deleteFiles ? "1" : "0" },
      });
      if (res.data.success) {
        showMessage?.(t("models.imageModels.removed", { name: removeTarget.name || removeTarget.id }), "info");
        fetchModels();
      } else {
        showMessage?.(res.data.error?.message || t("models.shared.couldNotRemove"), "error");
      }
    } catch (err) {
      showMessage?.(formatUiError(err.response?.data?.error) || err.message || t("models.shared.couldNotRemove"), "error");
    } finally {
      setRemoveTarget(null);
    }
  };

  const isDownloading = downloadStatus.is_downloading;
  const currentModel = downloadStatus.current_model;

  const editingMissing = editing.filter((p) => !p.installed);
  const editingMissingGb = editingMissing.reduce((sum, p) => sum + (p.download_gb || 0), 0);

  // One pack after another: the downloader takes one plan at a time. The
  // store starts each when the one before it ends, dialog open or not.
  const handleInstallAllEditing = () => {
    installAll(
      "image",
      editingMissing.map((pack) => {
        const ref = pack.path || pack.id;
        return {
          body: { model_path: ref },
          name: pack.name || pack.id,
          status: startingStatus(ref, pack.download_gb || pack.size_gb),
        };
      }),
    );
  };

  // Install / Installed / progress cell, shared by model rows and LoRA rows.
  const renderInstallCell = (model) => {
    const incomplete = Object.entries(model.incomplete_files || {});
    const isThis = isDownloading && modelMatchesDownload(model, currentModel);
    if (isThis) {
      return (
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
            {(downloadStatus.downloaded_gb || 0).toFixed(1)} / {(downloadStatus.total_gb || 0).toFixed(1)} GB
          </Typography>
        </Box>
      );
    }
    return (
      <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 0.5 }}>
        {model.is_downloaded ? (
          <Chip icon={<CheckCircleIcon />} label={t("models.shared.installed")} color="success" size="small" variant="outlined" />
        ) : (
          <Button
            variant="outlined"
            size="small"
            startIcon={<CloudDownloadIcon />}
            onClick={() => requestInstall(model)}
            disabled={isDownloading}
          >
            {t("dialogs.shared.install")}
          </Button>
        )}
        {incomplete.length > 0 && (
          <Typography
            variant="caption"
            color="warning.main"
            title={incomplete.map(([file, reason]) => `${file}: ${reason}`).join("\n")}
          >
            {t("models.shared.filesIncomplete", { count: incomplete.length })}
          </Typography>
        )}
        {model.user && (
          <ActionButton kind="destructive" onClick={() => setRemoveTarget(model)} disabled={isDownloading}>
            {t("dialogs.shared.remove")}
          </ActionButton>
        )}
      </Box>
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("models.imageModels.title")}</DialogTitle>
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

        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {models.map((model) => {
              const isInstallTarget = !!installModelId && model.id === installModelId;
              return (
                <ListItem
                  key={model.id}
                  divider
                  ref={isInstallTarget ? installRowRef : undefined}
                  sx={{
                    py: 1.5,
                    ...(isInstallTarget && { border: 2, borderColor: "warning.main", borderRadius: 1 }),
                  }}
                >
                  <ListItemIcon>
                    <ImageIcon color={model.is_downloaded ? "primary" : "action"} />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                        <Typography variant="body1" sx={{ fontWeight: 500 }}>
                          {model.name || model.id}
                        </Typography>
                        {model.recommended && (
                          <Chip label={t("models.imageModels.recommended")} size="small" color="primary" variant="outlined" />
                        )}
                        {model.user && <Chip label={t("models.imageModels.yours", { family: model.family || t("models.imageModels.modelFallback") })} size="small" variant="outlined" />}
                        {model.size_gb > 0 && (
                          <Chip label={`${model.size_gb} GB`} size="small" variant="outlined" />
                        )}
                        {model.ram_gb > 0 && (
                          <Chip
                            label={t("models.imageModels.ramChip", { size: gbLabel(model.ram_gb) })}
                            title={machine?.total_ram_gb
                              ? t("models.imageModels.ramChipTooltip", { total: gbLabel(machine.total_ram_gb) })
                              : undefined}
                            size="small"
                            color={model.fits_ram === false ? "warning" : "default"}
                            variant="outlined"
                          />
                        )}
                        {model.vram_mb > 0 && (
                          <Chip
                            label={t("models.imageModels.vramChip", { size: gbLabel(model.vram_mb / 1024) })}
                            size="small"
                            variant="outlined"
                          />
                        )}
                        {checked && !model.is_downloaded && (
                          <Chip
                            label={model.gated ? t("models.imageModels.gatedToken") : t("models.imageModels.available")}
                            size="small"
                            color={model.gated ? "warning" : "success"}
                            variant="outlined"
                          />
                        )}
                      </Box>
                    }
                    secondary={
                      <>
                        {model.description && (
                          <Typography
                            variant="body2"
                            component="span"
                            sx={{ color: "text.secondary", display: "block" }}
                          >
                            {model.description}
                          </Typography>
                        )}
                        <Typography
                          variant="caption"
                          component="span"
                          sx={{ color: "text.secondary", display: "block" }}
                        >
                          {model.path}
                        </Typography>
                      </>
                    }
                  />

                  <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(model)}</Box>
                </ListItem>
              );
            })}
            {unavailable.length > 0 && (
              <Typography variant="overline" sx={{ color: "text.secondary", display: "block", mt: 2 }}>
                {t("models.imageModels.notAvailable")}
              </Typography>
            )}
            {unavailable.map((model) => (
              <ListItem key={`unavailable-${model.id}`} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ImageIcon color="disabled" />
                </ListItemIcon>
                <ListItemText
                  primary={model.name || model.id}
                  secondary={model.reason}
                  slotProps={{
                    secondary: { variant: "body2" }
                  }}
                />
              </ListItem>
            ))}
            {adapters.length > 0 && (
              <Typography variant="overline" sx={{ color: "text.secondary", display: "block", mt: 2 }}>
                {t("models.imageModels.yourLoras")}
              </Typography>
            )}
            {adapters.map((a) => (
              <ListItem key={a.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ImageIcon color={a.is_downloaded ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography variant="body1" sx={{ fontWeight: 500 }}>
                        {a.name}
                      </Typography>
                      <Chip label={`LoRA · ${a.family}`} size="small" variant="outlined" />
                      {a.size_gb > 0 && <Chip label={`${a.size_gb} GB`} size="small" variant="outlined" />}
                    </Box>
                  }
                  secondary={
                    <Typography
                      variant="caption"
                      component="span"
                      sx={{ color: "text.secondary", display: "block" }}
                    >
                      {a.hf_repo}
                    </Typography>
                  }
                />
                <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(a)}</Box>
              </ListItem>
            ))}
            {encoders.length > 0 && (
              <Typography variant="overline" sx={{ color: "text.secondary", display: "block", mt: 2 }}>
                {t("models.imageModels.yourEncoders")}
              </Typography>
            )}
            {encoders.map((e) => (
              <ListItem key={e.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ImageIcon color={e.is_downloaded ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography variant="body1" sx={{ fontWeight: 500 }}>
                        {e.name}
                      </Typography>
                      <Chip
                        label={t("models.imageModels.encoderChip", { slot: e.slot_label, family: e.family })}
                        size="small"
                        variant="outlined"
                      />
                      {e.size_gb > 0 && <Chip label={`${e.size_gb} GB`} size="small" variant="outlined" />}
                    </Box>
                  }
                  secondary={
                    <Typography
                      variant="caption"
                      component="span"
                      sx={{ color: "text.secondary", display: "block" }}
                    >
                      {e.hf_repo}
                      {e.shared_with ? ` · ${t("models.imageModels.sharedFile")}` : ""}
                    </Typography>
                  }
                />
                <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(e)}</Box>
              </ListItem>
            ))}
            {editing.length > 0 && (
              <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mt: 2 }}>
                <Typography variant="overline" sx={{ color: "text.secondary" }}>
                  {t("models.imageModels.imageEditing")}
                </Typography>
                {editingMissing.length > 0 && (
                  <Button
                    size="small"
                    startIcon={<CloudDownloadIcon />}
                    onClick={handleInstallAllEditing}
                    disabled={isDownloading || installAllPending}
                  >
                    {t("models.imageModels.installAllEditing", { size: editingMissingGb.toFixed(1) })}
                  </Button>
                )}
              </Box>
            )}
            {editing.map((pack) => (
              <ListItem key={pack.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ImageIcon color={pack.installed ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Typography variant="body1" sx={{ fontWeight: 500 }}>
                        {pack.name}
                      </Typography>
                      {(pack.installed ? pack.size_gb : pack.download_gb) > 0 && (
                        <Chip
                          label={`${pack.installed ? pack.size_gb : pack.download_gb} GB`}
                          size="small"
                          variant="outlined"
                        />
                      )}
                    </Box>
                  }
                  secondary={
                    <>
                      <Typography
                        variant="body2"
                        component="span"
                        sx={{ color: "text.secondary", display: "block" }}
                      >
                        {pack.description}
                      </Typography>
                      <Typography
                        variant="caption"
                        component="span"
                        sx={{ color: "text.secondary", display: "block" }}
                      >
                        {t("models.imageModels.chatTools", { tools: (pack.tools || []).join(", ") })}
                      </Typography>
                    </>
                  }
                />
                <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>{renderInstallCell(pack)}</Box>
              </ListItem>
            ))}
            {models.length === 0 && !loading && (
              <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                {t("models.imageModels.noModels")}
              </Typography>
            )}
          </List>
        )}

        {/* FLUX.1-dev stills install from this list (ComfyUI UNET). Community
            FLUX UNETs and LoRAs add via Add new model. Schnell keyframe GGUF
            companions still live under Video Models. */}
        <Box sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
          <Typography variant="caption" sx={{ color: "text.secondary" }}>
            <Trans i18nKey="models.imageModels.fluxNote" components={{ bold: <strong /> }} />
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={() => setAddOpen(true)} disabled={isDownloading}>
          {t("models.shared.addNewModel")}
        </ActionButton>
        <ActionButton
          onClick={() => fetchModels(true)}
          disabled={isDownloading || loading}
          tooltip={t("models.imageModels.checkAccessTooltip")}
        >
          {t("models.imageModels.checkAccess")}
        </ActionButton>
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose}>{t("dialogs.shared.close")}</Button>
      </DialogActions>
      <AddImageModelDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        showMessage={showMessage}
        onAdded={() => {
          fetchModels();
          refreshDownload("image");
        }}
      />
      <ConfirmActionDialog
        open={Boolean(removeTarget)}
        title={t("models.shared.removeTitle", { name: removeTarget?.name || "" })}
        description={t("models.imageModels.removeDescription")}
        extra={
          <SettingChip
            label={t("models.shared.deleteDownloadedFiles")}
            on={Boolean(removeTarget?.deleteFiles)}
            onToggle={(next) => setRemoveTarget((t) => (t ? { ...t, deleteFiles: next } : t))}
          />
        }
        keeps={t("models.imageModels.removeKeeps")}
        confirmLabel={t("dialogs.shared.remove")}
        onClose={() => setRemoveTarget(null)}
        onConfirm={handleRemove}
      />
      <ConfirmActionDialog
        open={Boolean(ramWarnTarget)}
        reversible
        title={t("models.imageModels.ramWarnTitle", { name: ramWarnTarget?.name || ramWarnTarget?.id || "" })}
        description={
          machine?.wsl ? t("models.imageModels.ramWarnWsl") : t("models.imageModels.ramWarnDescription")
        }
        facts={[
          {
            label: t("models.imageModels.ramWarnNeeds"),
            value: t("models.imageModels.ramWarnNeedsValue", {
              need: gbLabel(ramWarnTarget?.ram_gb || 0),
              floor: gbLabel(machine?.ram_floor_gb || 0),
            }),
          },
          ...(machine?.total_ram_gb
            ? [{
                label: machine.wsl ? t("models.imageModels.ramWarnHasWsl") : t("models.imageModels.ramWarnHas"),
                value: gbLabel(machine.total_ram_gb),
              }]
            : []),
        ]}
        confirmLabel={t("models.imageModels.installAnyway")}
        onClose={() => setRamWarnTarget(null)}
        onConfirm={() => {
          const target = ramWarnTarget;
          setRamWarnTarget(null);
          if (target) handleDownload(target);
        }}
      />
    </Dialog>
  );
};

export default ImageModelsModal;
