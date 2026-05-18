// frontend/src/components/modals/UpscalingModelsModal.jsx
// Install registered upscaling weights (plugin model registry).

import React, { useState, useEffect, useCallback } from "react";
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

const UpscalingModelsModal = ({ open, onClose, showMessage, onInstalled }) => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [downloadingName, setDownloadingName] = useState(null);

  const isDownloading = downloadingName != null;

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await upscalingService.getModels();
      const payload = normalizeModelsPayload(res);
      setRows(buildRows(payload));
      setError(null);
    } catch (err) {
      setError(err.message || "Error fetching upscaling models");
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
    }
  }, [open, fetchModels]);

  const handleDownload = async (name) => {
    try {
      setDownloadingName(name);
      await upscalingService.downloadModel(name);
      showMessage?.(`Installed ${name}`, "success");
      await fetchModels();
      onInstalled?.(name);
    } catch (err) {
      const msg =
        err?.data?.error?.message ||
        err?.data?.message ||
        err?.message ||
        "Download failed";
      showMessage?.(msg, "error");
    } finally {
      setDownloadingName(null);
    }
  };

  return (
    <Dialog open={open} onClose={() => !isDownloading && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>Manage Upscaling Models</DialogTitle>
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
                        ? "Ready to use in the upscaler"
                        : "Not installed — download to enable in the model dropdown"
                    }
                  />

                  <Box sx={{ ml: 2, minWidth: 130, textAlign: "right" }}>
                    {isThis ? (
                      <Box sx={{ width: 130 }}>
                        <Typography variant="caption" noWrap>
                          Downloading…
                        </Typography>
                        <LinearProgress sx={{ mt: 0.5 }} />
                      </Box>
                    ) : model.is_downloaded ? (
                      <Chip
                        icon={<CheckCircleIcon />}
                        label="Installed"
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
                        Install
                      </Button>
                    )}
                  </Box>
                </ListItem>
              );
            })}
            {rows.length === 0 && !loading && (
              <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                No upscaling models returned from the service.
              </Typography>
            )}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isDownloading}>
          {isDownloading ? "Downloading…" : "Close"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default UpscalingModelsModal;
