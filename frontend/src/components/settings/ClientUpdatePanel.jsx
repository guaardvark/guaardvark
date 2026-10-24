// frontend/src/components/settings/ClientUpdatePanel.jsx
// Simplified update panel for client machines
// Shows "Updates Available" status and one-click update functionality

import React, { useState, useEffect, useCallback } from "react";
import {
  Box,
  Typography,
  Paper,
  Button,
  Chip,
  CircularProgress,
  LinearProgress,
  Alert,
  Collapse,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Tooltip,
  Divider,
} from "@mui/material";
import {
  CheckCircle as CheckIcon,
  Download as DownloadIcon,
  Refresh as RefreshIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Folder as FolderIcon,
  Code as CodeIcon,
  NewReleases as NewIcon,
  Edit as EditIcon,
  Info as InfoIcon,
} from "@mui/icons-material";
import * as interconnectorApi from "../../api/interconnectorService";
import { useSnackbar } from "../common/SnackbarProvider";
import { uiLocale, useUiTranslation } from "../../i18n";

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

const ClientUpdatePanel = ({ masterUrl, _masterApiKey, isEnabled }) => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();

  // Update status state
  const [updateStatus, setUpdateStatus] = useState({
    checking: false,
    available: false,
    count: 0,
    newFiles: 0,
    modifiedFiles: 0,
    driftFiles: 0,
    summary: { backend: 0, frontend: 0, other: 0 },
    masterVersion: null,
    localVersion: null,
    lastChecked: null,
    error: null,
  });

  // Master core-files registry (authoritative tally lives on master only)
  const [syncStatus, setSyncStatus] = useState({
    masterTrackedFiles: null,
    masterManifestTimestamp: null,
    lastApplyAt: null,
  });

  // Preview state
  const [showPreview, setShowPreview] = useState(false);
  const [previewData, setPreviewData] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);

  // Update application state
  const [applying, setApplying] = useState(false);
  const [applyProgress, setApplyProgress] = useState(0);
  const [lastUpdateResult, setLastUpdateResult] = useState(null);

  // Define checkForUpdates first so it can be used in useEffect
  const checkForUpdates = useCallback(async () => {
    if (!isEnabled || !masterUrl) {
      debugLog("[ClientUpdatePanel] Skipping update check - not enabled or no master URL");
      return;
    }

    debugLog("[ClientUpdatePanel] Checking for updates from master");
    setUpdateStatus((prev) => ({ ...prev, checking: true, error: null }));

    try {
      const response = await interconnectorApi.checkForUpdates();
      debugLog("[ClientUpdatePanel] Update check response", {
        success: response?.success,
        hasData: Boolean(response?.data),
      });

      if (response.error) {
        console.error("[ClientUpdatePanel] Update check error:", response.error);
        setUpdateStatus((prev) => ({
          ...prev,
          checking: false,
          error: response.error,
        }));
        return;
      }

      const data = response.data || response;
      debugLog("[ClientUpdatePanel] Update check data", {
        available: data.available,
        count: data.count,
        summary: data.summary
      });
      
      setUpdateStatus({
        checking: false,
        available: data.available || false,
        count: data.count || 0,
        newFiles: data.new_files || 0,
        modifiedFiles: data.modified_files || 0,
        driftFiles: data.drift_files || 0,
        summary: data.summary || { backend: 0, frontend: 0, other: 0 },
        masterVersion: data.master_version,
        localVersion: data.local_version,
        lastChecked: new Date().toLocaleString(uiLocale()),
        error: null,
      });

      // Pull the real registry summary so the UI shows durable synced state, not just a clock.
      const statusResp = await interconnectorApi.getSyncStatus();
      const statusData = statusResp?.data || statusResp;
      if (statusData && !statusResp?.error) {
        setSyncStatus({
          masterTrackedFiles: statusData.master_tracked_files ?? statusData.tracked_files ?? null,
          masterManifestTimestamp: statusData.master_manifest_timestamp ?? statusData.manifest_timestamp ?? null,
          lastApplyAt: statusData.last_apply_at ?? statusData.last_synced_at ?? null,
        });
      }

      // Clear preview if no updates available
      if (!data.available) {
        setShowPreview(false);
        setPreviewData(null);
      }
    } catch (err) {
      console.error("[ClientUpdatePanel] Update check exception:", err);
      setUpdateStatus((prev) => ({
        ...prev,
        checking: false,
        error: err.message || t("settingsInterconnector.clientUpdate.checkFailed"),
      }));
    }
  }, [isEnabled, masterUrl]);

  // Check for updates on mount and periodically
  useEffect(() => {
    if (!isEnabled || !masterUrl) {
      debugLog("[ClientUpdatePanel] Not checking", { isEnabled, hasMasterUrl: Boolean(masterUrl) });
      return;
    }

    // Initial check with small delay to allow component to fully mount
    const initialCheck = setTimeout(() => {
      checkForUpdates();
    }, 500);

    // Check every 5 minutes
    const interval = setInterval(checkForUpdates, 5 * 60 * 1000);
    
    return () => {
      clearTimeout(initialCheck);
      clearInterval(interval);
    };
  }, [isEnabled, masterUrl, checkForUpdates]);

  const loadPreview = async () => {
    setLoadingPreview(true);
    try {
      const response = await interconnectorApi.previewUpdates();

      if (response.error) {
        showMessage(t("settingsInterconnector.clientUpdate.previewFailed", { error: response.error }), "error");
        setLoadingPreview(false);
        return;
      }

      setPreviewData(response.data || response);
      setShowPreview(true);
    } catch (err) {
      showMessage(t("settingsInterconnector.clientUpdate.previewFailed", { error: err.message }), "error");
    } finally {
      setLoadingPreview(false);
    }
  };

  const handleApplyUpdates = async () => {
    if (!window.confirm(t("settingsInterconnector.clientUpdate.confirmApply"))) {
      return;
    }

    setApplying(true);
    setApplyProgress(10);
    setLastUpdateResult(null);

    try {
      setApplyProgress(30);
      const response = await interconnectorApi.applyUpdates([]);
      setApplyProgress(90);

      if (response.error) {
        showMessage(t("settingsInterconnector.clientUpdate.updateFailed", { error: response.error }), "error");
        setLastUpdateResult({ success: false, error: response.error });
      } else {
        const data = response.data || response;
        const counts = { applied: data.applied || 0, created: data.created || 0, updated: data.updated || 0 };
        const message = data.restart_required
          ? t("settingsInterconnector.clientUpdate.updatedRestart", counts)
          : t("settingsInterconnector.clientUpdate.updated", counts);
        showMessage(message, "success");
        setLastUpdateResult({
          success: true,
          applied: data.applied,
          created: data.created,
          updated: data.updated,
          backedUp: data.backed_up,
          backupPath: data.backup_path,
        });

        // Clear the updates available state immediately after successful update
        setUpdateStatus((prev) => ({
          ...prev,
          available: false,
          count: 0,
          newFiles: 0,
          modifiedFiles: 0,
          summary: { backend: 0, frontend: 0, other: 0 },
        }));
        
        // Clear preview
        setShowPreview(false);
        setPreviewData(null);

        // Refresh update status after a short delay to confirm with server
        // (in case there are still more updates)
        setTimeout(async () => {
          debugLog("[ClientUpdatePanel] Re-checking for updates after apply");
          await checkForUpdates();
        }, 1000);
      }
    } catch (err) {
      showMessage(t("settingsInterconnector.clientUpdate.updateFailed", { error: err.message }), "error");
      setLastUpdateResult({ success: false, error: err.message });
    } finally {
      setApplying(false);
      setApplyProgress(100);
    }
  };

  // Render helper for action icon based on file action
  const getActionIcon = (action) => {
    if (action === "create") return <NewIcon fontSize="small" color="success" />;
    if (action === "update") return <EditIcon fontSize="small" color="primary" />;
    if (action === "drift") return <EditIcon fontSize="small" color="warning" />;
    return <InfoIcon fontSize="small" />;
  };

  const formatSyncedAt = (iso) => {
    if (!iso) return null;
    try { return new Date(iso).toLocaleString(uiLocale()); } catch { return iso; }
  };

  // Not enabled or not configured
  if (!isEnabled || !masterUrl) {
    return null;
  }

  return (
    <Paper elevation={2} sx={{ p: 2.5 }}>
      {/* Header */}
      <Box display="flex" alignItems="center" justifyContent="space-between" mb={2}>
        <Box display="flex" alignItems="center" gap={1}>
          <CodeIcon color="primary" />
          <Typography variant="h6">{t("settingsInterconnector.clientUpdate.title")}</Typography>
        </Box>
        <Tooltip title={t("settingsInterconnector.clientUpdate.checkTooltip")}>
          <span>
            <IconButton
              onClick={checkForUpdates}
              disabled={updateStatus.checking}
              size="small"
            >
              {updateStatus.checking ? (
                <CircularProgress size={20} />
              ) : (
                <RefreshIcon />
              )}
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {/* Error State */}
      {updateStatus.error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {updateStatus.error}
        </Alert>
      )}

      {/* Checking State */}
      {updateStatus.checking && !updateStatus.error && (
        <Box textAlign="center" py={2}>
          <CircularProgress size={32} />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {t("settingsInterconnector.clientUpdate.checking")}
          </Typography>
        </Box>
      )}

      {/* Up to Date State */}
      {!updateStatus.checking && !updateStatus.error && !updateStatus.available && (
        <Box textAlign="center" py={2}>
          <CheckIcon sx={{ fontSize: 48, color: "success.main", mb: 1 }} />
          <Typography variant="h6" color="success.main">
            {t("settingsInterconnector.clientUpdate.upToDate")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {t("settingsInterconnector.clientUpdate.lastChecked", { when: updateStatus.lastChecked || t("settingsInterconnector.clientUpdate.never") })}
          </Typography>
          {syncStatus.masterTrackedFiles != null && (
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
              {t("settingsInterconnector.clientUpdate.masterTracks", { count: syncStatus.masterTrackedFiles })}
              {syncStatus.lastApplyAt ? ` · ${t("settingsInterconnector.clientUpdate.lastApply", { when: formatSyncedAt(syncStatus.lastApplyAt) })}` : ""}
            </Typography>
          )}
        </Box>
      )}

      {/* Updates Available State */}
      {!updateStatus.checking && !updateStatus.error && updateStatus.available && (
        <Box>
          {/* Status Badge - Yellow background, black text, squared chip style */}
          <Box
            sx={{
              bgcolor: "#FFD700",
              borderRadius: 1,
              p: 1.5,
              mb: 2,
              display: "flex",
              alignItems: "center",
              gap: 1.5,
            }}
          >
            <DownloadIcon sx={{ fontSize: 28, color: "#000" }} />
            <Box flex={1}>
              <Typography variant="subtitle1" sx={{ color: "#000", fontWeight: 600 }}>
                {t("settingsInterconnector.clientUpdate.updatesAvailable", { count: updateStatus.count })}
              </Typography>
              <Box display="flex" gap={0.5} flexWrap="wrap" mt={0.5}>
                {updateStatus.summary.backend > 0 && (
                  <Chip
                    label={t("settingsInterconnector.clientUpdate.backendCount", { count: updateStatus.summary.backend })}
                    size="small"
                    sx={{
                      bgcolor: "rgba(0,0,0,0.15)",
                      color: "#000",
                      borderRadius: 1,
                      height: 22,
                      "& .MuiChip-label": { px: 1, fontSize: "0.75rem" }
                    }}
                  />
                )}
                {updateStatus.summary.frontend > 0 && (
                  <Chip
                    label={t("settingsInterconnector.clientUpdate.frontendCount", { count: updateStatus.summary.frontend })}
                    size="small"
                    sx={{
                      bgcolor: "rgba(0,0,0,0.15)",
                      color: "#000",
                      borderRadius: 1,
                      height: 22,
                      "& .MuiChip-label": { px: 1, fontSize: "0.75rem" }
                    }}
                  />
                )}
                {updateStatus.summary.other > 0 && (
                  <Chip
                    label={t("settingsInterconnector.clientUpdate.otherCount", { count: updateStatus.summary.other })}
                    size="small"
                    sx={{
                      bgcolor: "rgba(0,0,0,0.15)",
                      color: "#000",
                      borderRadius: 1,
                      height: 22,
                      "& .MuiChip-label": { px: 1, fontSize: "0.75rem" }
                    }}
                  />
                )}
                {updateStatus.driftFiles > 0 && (
                  <Chip
                    label={t("settingsInterconnector.clientUpdate.driftCount", { count: updateStatus.driftFiles })}
                    size="small"
                    title={t("settingsInterconnector.clientUpdate.driftTooltip")}
                    sx={{
                      bgcolor: "rgba(180,83,9,0.25)",
                      color: "#000",
                      borderRadius: 1,
                      height: 22,
                      "& .MuiChip-label": { px: 1, fontSize: "0.75rem" }
                    }}
                  />
                )}
              </Box>
            </Box>
          </Box>

          {/* Action Buttons */}
          <Box display="flex" gap={2} mb={2}>
            <Button
              variant="contained"
              color="primary"
              onClick={handleApplyUpdates}
              disabled={applying}
              startIcon={applying ? <CircularProgress size={16} /> : <DownloadIcon />}
            >
              {applying ? t("settingsInterconnector.clientUpdate.updating") : t("settingsInterconnector.clientUpdate.updateNow")}
            </Button>
            <Button
              variant="outlined"
              onClick={() => (showPreview ? setShowPreview(false) : loadPreview())}
              disabled={loadingPreview}
              endIcon={showPreview ? <ExpandLessIcon /> : <ExpandMoreIcon />}
            >
              {loadingPreview ? t("settingsInterconnector.clientUpdate.loading") : showPreview ? t("settingsInterconnector.clientUpdate.hideDetails") : t("settingsInterconnector.clientUpdate.viewChanges")}
            </Button>
          </Box>

          {/* Progress Bar */}
          {applying && (
            <Box mb={2}>
              <LinearProgress variant="determinate" value={applyProgress} />
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                {t("settingsInterconnector.clientUpdate.applying")}
              </Typography>
            </Box>
          )}

          {/* Preview Section */}
          <Collapse in={showPreview && previewData}>
            <Box
              sx={{
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                overflow: "hidden",
                mb: 2,
              }}
            >
              <Box
                sx={{
                  bgcolor: "background.default",
                  px: 2,
                  py: 1,
                  borderBottom: 1,
                  borderColor: "divider",
                }}
              >
                <Typography variant="subtitle2">
                  {t("settingsInterconnector.clientUpdate.filesToUpdate", {
                    count: previewData?.count || 0,
                    size: previewData?.total_size_display || t("settingsSections.units.b", { size: 0 }),
                  })}
                </Typography>
              </Box>
              <TableContainer sx={{ maxHeight: 300 }}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell width={40}>{t("settingsInterconnector.clientUpdate.columns.action")}</TableCell>
                      <TableCell>{t("settingsInterconnector.clientUpdate.columns.file")}</TableCell>
                      <TableCell align="right" width={80}>{t("settingsInterconnector.clientUpdate.columns.size")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {previewData?.files?.map((file, index) => (
                      <TableRow key={index} hover>
                        <TableCell>
                          <Tooltip title={file.action === "create" ? t("settingsInterconnector.clientUpdate.newFile") : t("settingsInterconnector.clientUpdate.modified")}>
                            {getActionIcon(file.action)}
                          </Tooltip>
                        </TableCell>
                        <TableCell>
                          <Typography
                            variant="body2"
                            sx={{ fontFamily: "monospace", fontSize: "0.8rem" }}
                          >
                            {file.path}
                          </Typography>
                        </TableCell>
                        <TableCell align="right">
                          <Typography variant="caption" color="text.secondary">
                            {file.size_display}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
              <Box sx={{ p: 1.5, bgcolor: "warning.dark", borderTop: 1, borderColor: "divider" }}>
                <Typography variant="caption" color="warning.contrastText">
                  {t("settingsInterconnector.clientUpdate.backupNote")}
                </Typography>
              </Box>
            </Box>
          </Collapse>

          {/* Last Checked */}
          <Typography variant="caption" color="text.secondary">
            {t("settingsInterconnector.clientUpdate.lastChecked", { when: updateStatus.lastChecked || t("settingsInterconnector.clientUpdate.never") })}
          </Typography>
        </Box>
      )}

      {/* Last Update Result */}
      {lastUpdateResult && (
        <Box mt={2}>
          <Divider sx={{ mb: 2 }} />
          {lastUpdateResult.success ? (
            <Alert severity="success">
              <Typography variant="body2" fontWeight="bold">
                {t("settingsInterconnector.clientUpdate.completed")}
              </Typography>
              <Typography variant="caption" display="block">
                {t("settingsInterconnector.clientUpdate.applied", {
                  applied: lastUpdateResult.applied,
                  created: lastUpdateResult.created,
                  updated: lastUpdateResult.updated,
                })}
              </Typography>
              {lastUpdateResult.backupPath && (
                <Typography variant="caption" display="block" sx={{ mt: 0.5 }}>
                  <FolderIcon sx={{ fontSize: 14, verticalAlign: "middle", mr: 0.5 }} />
                  {t("settingsInterconnector.clientUpdate.backupsSaved", { path: lastUpdateResult.backupPath })}
                </Typography>
              )}
            </Alert>
          ) : (
            <Alert severity="error">
              {t("settingsInterconnector.clientUpdate.updateFailed", { error: lastUpdateResult.error })}
            </Alert>
          )}
        </Box>
      )}
    </Paper>
  );
};

export default ClientUpdatePanel;
