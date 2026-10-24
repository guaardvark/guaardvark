// frontend/src/components/modals/UpscalingModelsModal.jsx
// Install registered upscaling weights (plugin model registry).

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
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import * as upscalingService from "../../api/upscalingService";
import { translate, useUiTranslation } from "../../i18n";

function normalizeModelsPayload(res) {
  const raw = res?.data ?? res;
  if (raw?.downloaded || raw?.available) return raw;
  return { downloaded: [], available: [] };
}

function buildRows(payload) {
  const downloaded = (payload.downloaded || []).map((m) => ({
    name: m.name,
    scale: m.scale,
    size_mb: m.size_mb,
    is_downloaded: true,
  }));
  const available = (payload.available || []).map((m) => ({
    name: m.name,
    scale: m.scale,
    size_mb: m.size_mb,
    is_downloaded: false,
  }));
  const rows = [...downloaded, ...available];
  rows.sort((a, b) => a.name.localeCompare(b.name));
  return rows;
}

// installModelName: opened from a page's Install button. That row's install
// starts once the list has loaded, as if the person had clicked Install on it.
const UpscalingModelsModal = ({ open, onClose, showMessage, onInstalled, installModelName }) => {
  const { t } = useUiTranslation();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [downloadingName, setDownloadingName] = useState(null);
  const installStartedRef = useRef(false);

  const isDownloading = downloadingName != null;

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await upscalingService.getModels();
      const payload = normalizeModelsPayload(res);
      setRows(buildRows(payload));
      setError(null);
    } catch (err) {
      setError(err.message || translate("models.upscalingModels.errorFetching"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      fetchModels();
    } else {
      setRows([]);
      setError(null);
      setDownloadingName(null);
      installStartedRef.current = false;
    }
  }, [open, fetchModels]);

  const handleDownload = async (name) => {
    try {
      setDownloadingName(name);
      await upscalingService.downloadModel(name);
      showMessage?.(t("models.upscalingModels.installedName", { name }), "success");
      await fetchModels();
      onInstalled?.(name);
    } catch (err) {
      const msg =
        err?.data?.error?.message ||
        err?.data?.message ||
        err?.message ||
        t("models.upscalingModels.downloadFailed");
      showMessage?.(msg, "error");
    } finally {
      setDownloadingName(null);
    }
  };

  useEffect(() => {
    if (!open || !installModelName || loading || installStartedRef.current) return;
    const row = rows.find((r) => r.name === installModelName);
    if (!row) return;
    installStartedRef.current = true;
    if (!row.is_downloaded) handleDownload(row.name);
  }, [open, installModelName, loading, rows]);

  return (
    <Dialog open={open} onClose={() => !isDownloading && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>{t("models.upscalingModels.title")}</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Box mb={2}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}

        {loading ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {rows.map((model) => {
              const isThis = downloadingName === model.name;
              return (
                <ListItem key={model.name} divider sx={{ py: 1.5 }}>
                  <ListItemIcon>
                    <AutoFixHighIcon color={model.is_downloaded ? "primary" : "action"} />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                        <Typography variant="body1" fontWeight={500}>
                          {model.name}
                        </Typography>
                        <Chip
                          label={model.scale != null ? `${model.scale}x` : "—"}
                          size="small"
                          variant="outlined"
                        />
                        {model.size_mb != null && (
                          <Chip label={`${model.size_mb} MB`} size="small" variant="outlined" />
                        )}
                      </Box>
                    }
                    secondary={
                      model.is_downloaded
                        ? t("models.upscalingModels.ready")
                        : t("models.upscalingModels.notInstalled")
                    }
                  />

                  <Box sx={{ ml: 2, minWidth: 130, textAlign: "right" }}>
                    {isThis ? (
                      <Box sx={{ width: 130 }}>
                        <Typography variant="caption" noWrap>
                          {t("models.shared.downloading")}
                        </Typography>
                        <LinearProgress sx={{ mt: 0.5 }} />
                      </Box>
                    ) : model.is_downloaded ? (
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
                        onClick={() => handleDownload(model.name)}
                        disabled={isDownloading}
                      >
                        {t("dialogs.shared.install")}
                      </Button>
                    )}
                  </Box>
                </ListItem>
              );
            })}
            {rows.length === 0 && !loading && (
              <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                {t("models.upscalingModels.noModels")}
              </Typography>
            )}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isDownloading}>
          {isDownloading ? t("models.shared.downloading") : t("dialogs.shared.close")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default UpscalingModelsModal;
