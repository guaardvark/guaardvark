// frontend/src/components/modals/ImageModelsModal.jsx
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
import ImageIcon from "@mui/icons-material/Image";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";

const ImageModelsModal = ({ open, onClose, showMessage }) => {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [downloadStatus, setDownloadStatus] = useState({
    is_downloading: false,
    current_model: null,
    progress: 0,
    status: "idle",
    speed_mbps: 0,
    downloaded_gb: 0,
    total_gb: 0,
  });
  const [error, setError] = useState(null);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/batch-image/models");
      if (res.data.success) {
        setModels(res.data.data.models);
      } else {
        setError("Failed to load models");
      }
    } catch (err) {
      setError(err.message || "Error fetching models");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDownloadStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/batch-image/models/download-status");
      if (res.data.success) {
        const status = res.data.data;
        setDownloadStatus(status);
        if (!status.is_downloading && status.status === "completed") {
          showMessage?.("Model download completed!", "success");
          fetchModels();
          setDownloadStatus((prev) => ({ ...prev, status: "idle", current_model: null }));
        } else if (!status.is_downloading && status.status === "failed") {
          showMessage?.(`Download failed: ${status.error}`, "error");
          setDownloadStatus((prev) => ({ ...prev, status: "idle", current_model: null }));
        }
      }
    } catch (err) {
      console.error("Failed to fetch download status", err);
    }
  }, [fetchModels, showMessage]);

  useEffect(() => {
    if (open) {
      fetchModels();
      fetchDownloadStatus();
    } else {
      setModels([]);
      setError(null);
    }
  }, [open, fetchModels, fetchDownloadStatus]);

  useEffect(() => {
    let interval;
    if (open && downloadStatus.is_downloading) {
      interval = setInterval(fetchDownloadStatus, 1000);
    }
    return () => clearInterval(interval);
  }, [open, downloadStatus.is_downloading, fetchDownloadStatus]);

  const handleDownload = async (model_path) => {
    try {
      const res = await axios.post("/api/batch-image/models/download", { model_path });
      if (res.data.success) {
        const model = models.find((m) => m.path === model_path);
        showMessage?.(`Started downloading ${model?.name || model_path}...`, "info");
        setDownloadStatus({
          is_downloading: true,
          current_model: model_path,
          progress: 0,
          status: "starting",
          speed_mbps: 0,
          downloaded_gb: 0,
          total_gb: model?.size_gb || 0,
        });
      } else {
        showMessage?.(res.data.error || "Failed to start download", "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessage?.("A download is already in progress.", "warning");
      } else {
        showMessage?.(err.message || "Error starting download", "error");
      }
    }
  };

  const isDownloading = downloadStatus.is_downloading;
  const currentModel = downloadStatus.current_model;

  return (
    <Dialog open={open} onClose={() => !isDownloading && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>Manage Image Generation Models</DialogTitle>
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
            {models.map((model) => {
              const isThis = isDownloading && currentModel === model.path;
              return (
                <ListItem key={model.id} divider sx={{ py: 1.5 }}>
                  <ListItemIcon>
                    <ImageIcon color={model.is_downloaded ? "primary" : "action"} />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                        <Typography variant="body1" fontWeight={500}>
                          {model.name || model.id}
                        </Typography>
                        {model.size_gb > 0 && (
                          <Chip label={`${model.size_gb} GB`} size="small" variant="outlined" />
                        )}
                      </Box>
                    }
                    secondary={model.path}
                  />

                  <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>
                    {isThis ? (
                      <Box sx={{ width: 130 }}>
                        <Typography variant="caption" noWrap>
                          {downloadStatus.status === "starting"
                            ? "Starting..."
                            : `${downloadStatus.progress}% \u2014 ${downloadStatus.speed_mbps} MB/s`}
                        </Typography>
                        <LinearProgress
                          variant={downloadStatus.progress > 0 ? "determinate" : "indeterminate"}
                          value={downloadStatus.progress}
                          sx={{ mt: 0.5 }}
                        />
                        <Typography variant="caption" color="text.secondary">
                          {(downloadStatus.downloaded_gb || 0).toFixed(1)} / {(downloadStatus.total_gb || 0).toFixed(1)} GB
                        </Typography>
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
                        onClick={() => handleDownload(model.path)}
                        disabled={isDownloading}
                      >
                        Install
                      </Button>
                    )}
                  </Box>
                </ListItem>
              );
            })}
            {models.length === 0 && !loading && (
              <Typography variant="body2" color="textSecondary" align="center" sx={{ py: 3 }}>
                No models available in configuration.
              </Typography>
            )}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isDownloading}>
          {isDownloading ? "Downloading..." : "Close"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ImageModelsModal;
