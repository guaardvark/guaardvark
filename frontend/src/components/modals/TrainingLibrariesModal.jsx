// frontend/src/components/modals/TrainingLibrariesModal.jsx
// Settings > Training libraries: the optional Python libraries fine-tuning
// needs (Unsloth, TRL, Datasets). Lists them at their pinned versions with
// download sizes, installs them only on the Install click, shows pip's
// progress and result, and removes them again. On a machine the hardware
// policy calls not practical, the only install button is "Install anyway".
// Below them, the declared base models a fine-tune starts from, each
// downloaded or removed only on its own button, and the GGUF converter Export
// to Ollama runs, fetched from GitHub only on its Install click.

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
  Alert,
  Divider,
  Stack,
} from "@mui/material";
import ModelTrainingIcon from "@mui/icons-material/ModelTraining";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import { ActionButton, ConfirmActionDialog } from "../settings/ui";
import {
  getBaseModels,
  getGgufConverter,
  getTrainingLibraries,
  installGgufConverter,
  installTrainingBaseModel,
  installTrainingLibraries,
  removeGgufConverter,
  removeTrainingBaseModel,
  removeTrainingLibraries,
} from "../../api/trainingService";
import { translate, useUiTranslation, Trans } from "../../i18n";

const sizeLabel = (mb) => {
  const n = Number(mb) || 0;
  if (n <= 0) return null;
  if (n >= 1000) return `${(n / 1000).toFixed(1)} GB`;
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} MB`;
};

const bytesLabel = (bytes) => sizeLabel((Number(bytes) || 0) / 1e6);

const stateChip = (lib) => {
  if (lib.state === "installed") {
    return (
      <Chip icon={<CheckCircleIcon />} label={translate("dialogs.trainingLibraries.installed")} color="success" size="small" variant="outlined" />
    );
  }
  if (lib.state === "other_version") {
    return (
      <Chip
        label={translate("dialogs.trainingLibraries.otherVersionInstalled", { version: lib.installed_version })}
        color="warning"
        size="small"
        variant="outlined"
        title={translate("dialogs.trainingLibraries.pinned", { version: lib.version })}
      />
    );
  }
  return <Chip label={translate("dialogs.trainingLibraries.notInstalled")} size="small" variant="outlined" />;
};

/**
 * @param {boolean}  open
 * @param {function} onClose
 * @param {function} [showMessage]  snackbar: (message, severity)
 * @param {function} [onChanged]    called with the new status when an install or remove ends
 * @param {function} [onBaseModelsChanged]  called with the base-model status after a download or remove
 * @param {function} [onConverterChanged]  called with the GGUF converter status after an install or remove
 * @param {string}   [focus]  "gguf-converter" scrolls to that section once it has loaded
 */
const TrainingLibrariesModal = ({
  open,
  onClose,
  showMessage,
  onChanged,
  onBaseModelsChanged,
  onConverterChanged,
  focus,
}) => {
  const { t } = useUiTranslation();
  const [info, setInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // Why the last Install or Remove click was refused; kept until the next click.
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const lastRunState = useRef(null);

  const [baseInfo, setBaseInfo] = useState(null);
  const [baseError, setBaseError] = useState(null);
  const [baseBusy, setBaseBusy] = useState(false);
  const [confirmRemoveModel, setConfirmRemoveModel] = useState(null);
  const lastDownloadState = useRef(null);

  const [converter, setConverter] = useState(null);
  const [converterError, setConverterError] = useState(null);
  const [converterBusy, setConverterBusy] = useState(false);
  // "install" or "remove" while that confirmation is open.
  const [confirmConverter, setConfirmConverter] = useState(null);
  const lastConverterState = useRef(null);
  const converterSection = useRef(null);

  // The parent passes a fresh showMessage/onChanged on every render; keep them
  // in refs so the fetch callbacks stay stable and polling does not restart.
  const showMessageRef = useRef(showMessage);
  const onChangedRef = useRef(onChanged);
  const onBaseModelsChangedRef = useRef(onBaseModelsChanged);
  const onConverterChangedRef = useRef(onConverterChanged);
  useEffect(() => {
    showMessageRef.current = showMessage;
    onChangedRef.current = onChanged;
    onBaseModelsChangedRef.current = onBaseModelsChanged;
    onConverterChangedRef.current = onConverterChanged;
  }, [showMessage, onChanged, onBaseModelsChanged, onConverterChanged]);

  const fetchConverter = useCallback(async () => {
    try {
      const data = await getGgufConverter();
      if (!data) return;
      setConverter(data);
      setConverterError(null);
      const run = data.run || {};
      if (lastConverterState.current === "running" && run.state !== "running") {
        if (run.state === "completed") {
          showMessageRef.current?.(translate("dialogs.trainingLibraries.converterInstalled"), "success");
        } else if (run.state === "failed") {
          showMessageRef.current?.(translate("dialogs.trainingLibraries.converterInstallFailed", { error: run.error }), "error");
        }
        onConverterChangedRef.current?.(data);
      }
      lastConverterState.current = run.state;
    } catch (err) {
      setConverterError(err.message || translate("dialogs.trainingLibraries.converterReadFailed"));
    }
  }, []);

  const fetchBaseModels = useCallback(async () => {
    try {
      const data = await getBaseModels();
      setBaseInfo(data);
      setBaseError(null);
      const download = data?.download || {};
      if (lastDownloadState.current === "running" && download.state !== "running") {
        if (download.state === "completed") {
          showMessageRef.current?.(translate("dialogs.trainingLibraries.modelDownloaded", { model: download.model }), "success");
        } else if (download.state === "failed") {
          showMessageRef.current?.(translate("dialogs.trainingLibraries.modelDownloadFailed", { model: download.model, error: download.error }), "error");
        }
        onBaseModelsChangedRef.current?.(data);
      }
      lastDownloadState.current = download.state;
    } catch (err) {
      setBaseError(err.message || translate("dialogs.trainingLibraries.baseModelsReadFailed"));
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await getTrainingLibraries();
      setInfo(data);
      setError(null);
      const state = data?.run?.state;
      if (lastRunState.current === "running" && state !== "running") {
        const removing = data.run.action === "remove";
        if (state === "completed") {
          showMessageRef.current?.(
            removing ? translate("dialogs.trainingLibraries.librariesRemoved") : translate("dialogs.trainingLibraries.librariesInstalled"),
            "success",
          );
        } else if (state === "failed") {
          showMessageRef.current?.(
            removing
              ? translate("dialogs.trainingLibraries.librariesRemoveFailed", { error: data.run.error || translate("dialogs.trainingLibraries.unknownError") })
              : translate("dialogs.trainingLibraries.librariesInstallFailed", { error: data.run.error || translate("dialogs.trainingLibraries.unknownError") }),
            "error",
          );
        }
        onChangedRef.current?.(data);
      }
      lastRunState.current = state;
    } catch (err) {
      setError(err.message || translate("dialogs.trainingLibraries.librariesReadFailed"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      setLoading(true);
      lastRunState.current = null;
      lastDownloadState.current = null;
      lastConverterState.current = null;
      fetchStatus();
      fetchBaseModels();
      fetchConverter();
    } else {
      setInfo(null);
      setError(null);
      setActionError(null);
      setConfirmRemove(false);
      setBaseInfo(null);
      setBaseError(null);
      setConfirmRemoveModel(null);
      setConverter(null);
      setConverterError(null);
      setConfirmConverter(null);
    }
  }, [open, fetchStatus, fetchBaseModels, fetchConverter]);

  const running = info?.run?.state === "running";
  const downloading = baseInfo?.download?.state === "running";
  const converterRunning = converter?.run?.state === "running";

  useEffect(() => {
    if (!open || !running) return undefined;
    const interval = setInterval(fetchStatus, 1000);
    return () => clearInterval(interval);
  }, [open, running, fetchStatus]);

  useEffect(() => {
    if (!open || !downloading) return undefined;
    const interval = setInterval(fetchBaseModels, 1000);
    return () => clearInterval(interval);
  }, [open, downloading, fetchBaseModels]);

  useEffect(() => {
    if (!open || !converterRunning) return undefined;
    const interval = setInterval(fetchConverter, 1000);
    return () => clearInterval(interval);
  }, [open, converterRunning, fetchConverter]);

  const converterLoaded = Boolean(converter);
  useEffect(() => {
    if (open && focus === "gguf-converter" && converterLoaded) {
      converterSection.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }, [open, focus, converterLoaded]);

  // Like the other buttons: each click asks for its own plan token first.
  const runConverterAction = async (action) => {
    setConverterBusy(true);
    setConverterError(null);
    try {
      const plan = await getGgufConverter({ plan: true });
      const data =
        action === "install"
          ? await installGgufConverter(plan.plan_token)
          : await removeGgufConverter(plan.plan_token);
      lastConverterState.current = data?.run?.state || null;
      setConverter(data);
      if (action === "remove") {
        showMessageRef.current?.(t("dialogs.trainingLibraries.converterRemoved"), "success");
        onConverterChangedRef.current?.(data);
      }
    } catch (err) {
      const message = err.message || (action === "install" ? t("dialogs.trainingLibraries.converterInstallError") : t("dialogs.trainingLibraries.converterRemoveError"));
      showMessageRef.current?.(message, "error");
      setConverterError(message);
      fetchConverter();
    } finally {
      setConverterBusy(false);
      setConfirmConverter(null);
    }
  };

  // Like the library buttons: each click asks for its own plan token first.
  const runBaseAction = async (action, model) => {
    setBaseBusy(true);
    setBaseError(null);
    try {
      const plan = await getBaseModels({ plan: true });
      const data =
        action === "download"
          ? await installTrainingBaseModel(plan.plan_token, model.id)
          : await removeTrainingBaseModel(plan.plan_token, model.id);
      lastDownloadState.current = data?.download?.state || null;
      setBaseInfo(data);
      if (action === "remove") {
        showMessageRef.current?.(t("dialogs.trainingLibraries.modelRemoved", { model: model.name }), "success");
        onBaseModelsChangedRef.current?.(data);
      }
    } catch (err) {
      const message = err.message || (action === "download" ? t("dialogs.trainingLibraries.modelDownloadError", { model: model.name }) : t("dialogs.trainingLibraries.modelRemoveError", { model: model.name }));
      showMessageRef.current?.(message, "error");
      setBaseError(message);
      fetchBaseModels();
    } finally {
      setBaseBusy(false);
      setConfirmRemoveModel(null);
    }
  };

  // Each click asks for its own plan token, then sends it with the request;
  // the backend runs pip only for a request that carries one.
  const runAction = async (action, options = {}) => {
    setBusy(true);
    setActionError(null);
    try {
      const plan = await getTrainingLibraries({ plan: true });
      const data =
        action === "install"
          ? await installTrainingLibraries(plan.plan_token, options)
          : await removeTrainingLibraries(plan.plan_token);
      lastRunState.current = data?.run?.state || "running";
      setInfo(data);
    } catch (err) {
      const message = err.message || (action === "install" ? t("dialogs.trainingLibraries.startInstallError") : t("dialogs.trainingLibraries.startRemoveError"));
      showMessageRef.current?.(message, "error");
      setActionError(message);
      fetchStatus();
    } finally {
      setBusy(false);
      setConfirmRemove(false);
    }
  };

  const libraries = info?.libraries || [];
  const hardware = info?.hardware || {};
  const missing = libraries.filter((l) => l.state === "missing");
  const otherVersions = libraries.filter((l) => l.state === "other_version");
  const unmet = info?.unmet || [];
  const removal = info?.removal || { remove: [], restore: {} };
  const restoreList = Object.entries(removal.restore || {});
  const run = info?.run || {};
  const notPractical = Boolean(info) && hardware.practical === false;

  // The install button's words; on a machine the hardware policy calls not
  // practical every form says "anyway", so choosing it is the insisting.
  let installLabel = null;
  if (missing.length > 0) {
    const size = sizeLabel(info?.download_mb);
    if (size) {
      installLabel = notPractical ? t("dialogs.trainingLibraries.installAnywayAbout", { size }) : t("dialogs.trainingLibraries.installAbout", { size });
    } else {
      installLabel = notPractical ? t("dialogs.trainingLibraries.installAnyway") : t("dialogs.trainingLibraries.install");
    }
  } else if (unmet.length > 0) {
    installLabel = notPractical ? t("dialogs.trainingLibraries.repairAnyway") : t("dialogs.trainingLibraries.repair");
  } else if (otherVersions.length > 0) {
    installLabel = notPractical ? t("dialogs.trainingLibraries.installPinnedAnyway") : t("dialogs.trainingLibraries.installPinned");
  }
  const restartHint = t("dialogs.trainingLibraries.restartHint");

  const hardwareLine = () => {
    if (!info) return null;
    const parts = [];
    if (hardware.gpu) parts.push(hardware.gpu);
    if (hardware.vram_mb) parts.push(`${(hardware.vram_mb / 1024).toFixed(0)} GB VRAM`);
    if (hardware.ram_gb) parts.push(`${hardware.ram_gb} GB RAM`);
    return parts.join(", ");
  };

  return (
    <Dialog open={open} onClose={() => !running && !busy && !converterBusy && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>{t("dialogs.trainingLibraries.title")}</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("dialogs.trainingLibraries.intro")}
        </Typography>

        {(error || actionError) && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {actionError || error}
          </Alert>
        )}

        {notPractical && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            <Trans
              i18nKey="dialogs.trainingLibraries.notPractical"
              values={{ reason: hardware.reason }}
              components={{ bold: <strong /> }}
            />
          </Alert>
        )}
        {info && hardware.practical && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
            {t("dialogs.trainingLibraries.thisMachine", { hardware: hardwareLine() || hardware.reason })}
          </Typography>
        )}

        {loading && !info ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {libraries.map((lib) => (
              <ListItem key={lib.id} divider sx={{ py: 1.5 }}>
                <ListItemIcon>
                  <ModelTrainingIcon color={lib.state === "installed" ? "primary" : "action"} />
                </ListItemIcon>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Typography variant="body1" fontWeight={500}>
                        {lib.name}
                      </Typography>
                      <Chip
                        label={lib.version}
                        size="small"
                        variant="outlined"
                        title={lib.pinned_because || ""}
                        sx={{ height: 20, fontSize: "0.65rem" }}
                      />
                      {lib.state !== "installed" && sizeLabel(lib.download_mb) && (
                        <Chip
                          label={sizeLabel(lib.download_mb)}
                          size="small"
                          variant="outlined"
                          title={lib.download_includes ? t("dialogs.trainingLibraries.includes", { what: lib.download_includes }) : ""}
                        />
                      )}
                    </Box>
                  }
                  secondary={lib.role}
                />
                <Box sx={{ ml: 2, minWidth: 110, textAlign: "right" }}>{stateChip(lib)}</Box>
              </ListItem>
            ))}
          </List>
        )}

        {unmet.length > 0 && !running && (
          <Alert severity="warning" sx={{ mt: 2 }}>
            {t("dialogs.trainingLibraries.unmetIntro")}
            <Box component="ul" sx={{ m: 0, pl: 2 }}>
              {unmet.map((u) => (
                <li key={u}>
                  <Typography variant="caption">{u}</Typography>
                </li>
              ))}
            </Box>
          </Alert>
        )}

        {info && installLabel && (info.changes?.length > 0 || info.left_out?.length > 0) && (
          <Box sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}>
            {info.changes?.map((c) => (
              <Typography key={c.name} variant="caption" color="text.secondary" sx={{ display: "block" }}>
                <Trans
                  i18nKey="dialogs.trainingLibraries.alsoMoves"
                  values={{ name: c.name, installed: c.installed, needs: c.needs, why: c.why }}
                  components={{ bold: <strong /> }}
                />
              </Typography>
            ))}
            {info.left_out?.map((l) => (
              <Typography key={l.name} variant="caption" color="text.secondary" sx={{ display: "block" }}>
                <Trans i18nKey="dialogs.trainingLibraries.leavesOut" values={{ name: l.name, why: l.why }} components={{ bold: <strong /> }} />
              </Typography>
            ))}
          </Box>
        )}

        {(running || run.state === "completed" || run.state === "failed") && (
          <Box sx={{ mt: 2 }}>
            {running && (
              <>
                <Typography variant="body2" noWrap>
                  {run.action === "remove"
                    ? t("dialogs.trainingLibraries.removingPhase", { phase: run.phase })
                    : t("dialogs.trainingLibraries.installingPhase", { phase: run.phase })}
                </Typography>
                <LinearProgress
                  variant={run.progress > 1 ? "determinate" : "indeterminate"}
                  value={run.progress || 0}
                  sx={{ mt: 0.5, mb: 1 }}
                />
              </>
            )}
            {run.state === "completed" && (
              <Alert severity="success" sx={{ mb: 1 }}>
                {run.action === "remove" ? t("dialogs.trainingLibraries.removed") : t("dialogs.trainingLibraries.installedDone")} {restartHint}
                {run.check?.ok && (
                  <Typography variant="caption" sx={{ display: "block" }}>
                    {t("dialogs.trainingLibraries.trainerCheck", {
                      device: run.check.cuda_device || "GPU",
                      bf16: run.check.bf16 ? t("dialogs.trainingLibraries.yes") : t("dialogs.trainingLibraries.no"),
                      trl: run.check.libraries?.trl,
                      unsloth: run.check.libraries?.unsloth,
                    })}
                  </Typography>
                )}
              </Alert>
            )}
            {run.pip_check_new?.length > 0 && (
              <Alert severity="warning" sx={{ mb: 1 }}>
                {t("dialogs.trainingLibraries.pipCheck")}
                <Box component="ul" sx={{ m: 0, pl: 2 }}>
                  {run.pip_check_new.map((line) => (
                    <li key={line}>
                      <Typography variant="caption">{line}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}
            {run.state === "failed" && (
              <Alert severity="error" sx={{ mb: 1 }}>
                {run.error || t("dialogs.trainingLibraries.failed")}
              </Alert>
            )}
            {run.log?.length > 0 && (
              <Box
                sx={{
                  maxHeight: 180,
                  overflow: "auto",
                  p: 1,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                  fontFamily: "monospace",
                  fontSize: "0.7rem",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {run.log.join("\n")}
              </Box>
            )}
          </Box>
        )}
        {run.restart_needed && !running && run.state !== "completed" && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {restartHint}
          </Alert>
        )}

        <Divider sx={{ my: 2 }} />
        <Typography variant="subtitle1" fontWeight={500}>
          {t("dialogs.trainingLibraries.baseModels")}
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {t("dialogs.trainingLibraries.baseModelsIntro")}
        </Typography>
        {baseError && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {baseError}
          </Alert>
        )}
        {baseInfo?.download?.state === "failed" && (
          <Alert severity="error" sx={{ mb: 1 }}>
            {t("dialogs.trainingLibraries.didNotDownload", { model: baseInfo.download.model, error: baseInfo.download.error })}
          </Alert>
        )}
        {!baseInfo && !baseError ? (
          <Box display="flex" justifyContent="center" p={2}>
            <CircularProgress size={24} />
          </Box>
        ) : (
          <List disablePadding>
            {(baseInfo?.models || []).map((model) => {
              const active = downloading && baseInfo.download.model === model.id;
              return (
                <ListItem key={model.id} divider sx={{ py: 1.5, alignItems: "flex-start" }}>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                        <Typography variant="body1" fontWeight={500}>
                          {model.name}
                        </Typography>
                        <Chip label={sizeLabel(model.size_gb * 1000)} size="small" variant="outlined" />
                        <Chip label={model.license} size="small" variant="outlined" />
                      </Box>
                    }
                    secondaryTypographyProps={{ component: "div" }}
                    secondary={
                      <>
                        {model.description}
                        {model.trainable === false && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                            {t("dialogs.trainingLibraries.notMeasured")}
                          </Typography>
                        )}
                        {!model.fits && (
                          <Typography variant="caption" color="warning.main" sx={{ display: "block" }}>
                            {t("dialogs.trainingLibraries.doesNotFitReason", { reason: model.fit_reason })}
                          </Typography>
                        )}
                        {active && (
                          <Box sx={{ mt: 1 }}>
                            <LinearProgress
                              variant={baseInfo.download.progress > 0 ? "determinate" : "indeterminate"}
                              value={baseInfo.download.progress || 0}
                            />
                            <Typography variant="caption">
                              {t("dialogs.trainingLibraries.downloadProgress", { downloaded: baseInfo.download.downloaded_gb || 0, total: model.size_gb })}
                            </Typography>
                          </Box>
                        )}
                      </>
                    }
                  />
                  <Box sx={{ ml: 2, minWidth: 130, textAlign: "right" }}>
                    {model.installed ? (
                      <Stack spacing={1} alignItems="flex-end">
                        <Chip icon={<CheckCircleIcon />} label={t("dialogs.trainingLibraries.downloaded")} color="success" size="small" variant="outlined" />
                        <ActionButton
                          kind="destructive"
                          onClick={() => setConfirmRemoveModel(model)}
                          disabled={baseBusy || downloading}
                        >
                          {t("dialogs.shared.remove")}
                        </ActionButton>
                      </Stack>
                    ) : model.fits ? (
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={<CloudDownloadIcon />}
                        onClick={() => runBaseAction("download", model)}
                        disabled={baseBusy || downloading}
                      >
                        {active ? t("dialogs.trainingLibraries.downloading") : t("dialogs.shared.download")}
                      </Button>
                    ) : (
                      <Chip label={t("dialogs.trainingLibraries.doesNotFit")} size="small" variant="outlined" />
                    )}
                  </Box>
                </ListItem>
              );
            })}
          </List>
        )}

        <Divider sx={{ my: 2 }} />
        <Box ref={converterSection}>
          <Typography variant="subtitle1" fontWeight={500}>
            {t("dialogs.trainingLibraries.converter")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t("dialogs.trainingLibraries.converterIntro")}
          </Typography>
          {converterError && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {converterError}
            </Alert>
          )}
          {converter?.run?.state === "failed" && (
            <Alert severity="error" sx={{ mb: 1 }}>
              {converter.run.error}
            </Alert>
          )}
          {converter && converter.problem && !converterRunning && (
            <Alert severity="warning" sx={{ mb: 1 }}>
              {t("dialogs.trainingLibraries.converterUnusable", { problem: converter.problem })}
            </Alert>
          )}
          {!converter && !converterError ? (
            <Box display="flex" justifyContent="center" p={2}>
              <CircularProgress size={24} />
            </Box>
          ) : converter ? (
            <List disablePadding>
              <ListItem divider sx={{ py: 1.5, alignItems: "flex-start" }}>
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                      <Typography variant="body1" fontWeight={500}>
                        {t("dialogs.trainingLibraries.converterName", { project: converter.project })}
                      </Typography>
                      <Chip
                        label={converter.tag}
                        size="small"
                        variant="outlined"
                        title={converter.pinned_because || ""}
                        sx={{ height: 20, fontSize: "0.65rem" }}
                      />
                      {converter.state !== "installed" && (
                        <Chip
                          label={bytesLabel(converter.download_bytes)}
                          size="small"
                          variant="outlined"
                          title={t("dialogs.trainingLibraries.fileCount", { count: converter.files })}
                        />
                      )}
                      <Chip label={converter.license} size="small" variant="outlined" />
                    </Box>
                  }
                  secondaryTypographyProps={{ component: "div" }}
                  secondary={
                    <>
                      {converter.role}
                      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                        {t("dialogs.trainingLibraries.checkedWith", {
                          architectures: (converter.architectures || []).join(", ") || t("dialogs.trainingLibraries.noneYet"),
                          levels: (converter.levels || []).map((l) => l.label).join(", "),
                        })}
                      </Typography>
                      {converterRunning && (
                        <Box sx={{ mt: 1 }}>
                          <LinearProgress
                            variant={converter.run.progress > 1 ? "determinate" : "indeterminate"}
                            value={converter.run.progress || 0}
                          />
                          <Typography variant="caption">
                            {t("dialogs.trainingLibraries.fetchProgress", {
                              fetched: bytesLabel(converter.run.fetched_bytes) || "0 MB",
                              total: bytesLabel(converter.download_bytes),
                            })}
                          </Typography>
                        </Box>
                      )}
                    </>
                  }
                />
                <Box sx={{ ml: 2, minWidth: 130, textAlign: "right" }}>
                  {converter.state === "installed" ? (
                    <Stack spacing={1} alignItems="flex-end">
                      <Chip icon={<CheckCircleIcon />} label={t("dialogs.trainingLibraries.installed")} color="success" size="small" variant="outlined" />
                      <ActionButton
                        kind="destructive"
                        onClick={() => setConfirmConverter("remove")}
                        disabled={converterBusy || converterRunning}
                      >
                        {t("dialogs.shared.remove")}
                      </ActionButton>
                    </Stack>
                  ) : (
                    <Stack spacing={1} alignItems="flex-end">
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={<CloudDownloadIcon />}
                        onClick={() => setConfirmConverter("install")}
                        disabled={converterBusy || converterRunning}
                      >
                        {converterRunning ? t("dialogs.trainingLibraries.installing") : converter.state === "missing" ? t("dialogs.shared.install") : t("dialogs.trainingLibraries.installAgain")}
                      </Button>
                      {converter.state !== "missing" && !converterRunning && (
                        <ActionButton
                          kind="destructive"
                          onClick={() => setConfirmConverter("remove")}
                          disabled={converterBusy}
                        >
                          {t("dialogs.shared.remove")}
                        </ActionButton>
                      )}
                    </Stack>
                  )}
                </Box>
              </ListItem>
            </List>
          ) : null}
        </Box>
      </DialogContent>
      <DialogActions>
        {removal.remove?.length > 0 && (
          <ActionButton kind="destructive" onClick={() => setConfirmRemove(true)} disabled={running || busy}>
            {t("dialogs.shared.remove")}
          </ActionButton>
        )}
        <Box sx={{ flex: 1 }} />
        {installLabel && (
          <Button
            variant="outlined"
            color={notPractical ? "warning" : "primary"}
            size="small"
            startIcon={<CloudDownloadIcon />}
            onClick={() => runAction("install", { anyway: notPractical })}
            disabled={running || busy}
          >
            {installLabel}
          </Button>
        )}
        <Button onClick={onClose} disabled={running || busy}>
          {running ? (run.action === "remove" ? t("dialogs.trainingLibraries.removing") : t("dialogs.trainingLibraries.installing")) : t("dialogs.shared.close")}
        </Button>
      </DialogActions>
      <ConfirmActionDialog
        open={confirmRemove}
        title={t("dialogs.trainingLibraries.removeLibrariesTitle")}
        description={t("dialogs.trainingLibraries.removeLibrariesDescription")}
        facts={[
          { label: t("dialogs.trainingLibraries.uninstalls"), value: (removal.remove || []).join(", ") || t("dialogs.trainingLibraries.nothing") },
          ...(restoreList.length > 0
            ? [
                {
                  label: t("dialogs.trainingLibraries.putsBack"),
                  value: restoreList.map(([name, version]) => `${name} ${version}`).join(", "),
                },
              ]
            : []),
        ]}
        keeps={t("dialogs.trainingLibraries.keepsTrained")}
        confirmLabel={t("dialogs.shared.remove")}
        busy={busy}
        onConfirm={() => runAction("remove")}
        onClose={() => !busy && setConfirmRemove(false)}
      />
      <ConfirmActionDialog
        open={Boolean(confirmRemoveModel)}
        title={t("dialogs.trainingLibraries.removeModelTitle", { name: confirmRemoveModel?.name || t("dialogs.trainingLibraries.thisBaseModel") })}
        description={t("dialogs.trainingLibraries.removeModelDescription")}
        facts={[{ label: t("dialogs.trainingLibraries.freesAbout"), value: sizeLabel((confirmRemoveModel?.size_gb || 0) * 1000) || "-" }]}
        keeps={t("dialogs.trainingLibraries.keepsTrained")}
        confirmLabel={t("dialogs.shared.remove")}
        busy={baseBusy}
        onConfirm={() => runBaseAction("remove", confirmRemoveModel)}
        onClose={() => !baseBusy && setConfirmRemoveModel(null)}
      />
      <ConfirmActionDialog
        open={confirmConverter === "install"}
        reversible
        title={t("dialogs.trainingLibraries.installConverterTitle")}
        description={t("dialogs.trainingLibraries.installConverterDescription")}
        facts={
          converter
            ? [
                { label: t("dialogs.trainingLibraries.downloads"), value: t("dialogs.trainingLibraries.downloadsValue", { count: converter.files, size: bytesLabel(converter.download_bytes) }) },
                {
                  label: t("dialogs.trainingLibraries.from"),
                  value: t("dialogs.trainingLibraries.fromValue", {
                    host: converter.download_host,
                    repo: converter.repo,
                    tag: converter.tag,
                    commit: converter.commit,
                  }),
                },
                { label: t("dialogs.trainingLibraries.license"), value: `${converter.license} (llama.cpp)` },
                { label: t("dialogs.trainingLibraries.pythonPackages"), value: converter.python_packages },
                { label: t("dialogs.trainingLibraries.installsInto"), value: converter.location },
              ]
            : []
        }
        confirmLabel={t("dialogs.shared.install")}
        busy={converterBusy}
        onConfirm={() => runConverterAction("install")}
        onClose={() => !converterBusy && setConfirmConverter(null)}
      />
      <ConfirmActionDialog
        open={confirmConverter === "remove"}
        title={t("dialogs.trainingLibraries.removeConverterTitle")}
        description={t("dialogs.trainingLibraries.removeConverterDescription")}
        facts={[{ label: t("dialogs.trainingLibraries.deletes"), value: converter?.location || "-" }]}
        keeps={t("dialogs.trainingLibraries.keepsConverter")}
        confirmLabel={t("dialogs.shared.remove")}
        busy={converterBusy}
        onConfirm={() => runConverterAction("remove")}
        onClose={() => !converterBusy && setConfirmConverter(null)}
      />
    </Dialog>
  );
};

export default TrainingLibrariesModal;
