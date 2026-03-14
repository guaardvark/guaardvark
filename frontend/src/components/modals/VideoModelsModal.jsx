// frontend/src/components/modals/VideoModelsModal.jsx
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
  Stack,
} from "@mui/material";
import MovieCreationIcon from "@mui/icons-material/MovieCreation";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import MemoryIcon from "@mui/icons-material/Memory";
import axios from "axios";

const VideoModelsModal = ({ open, onClose, showMessage }) => {
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
      const res = await axios.get("/api/batch-video/models");
      if (res.data.success) {
        setModels(res.data.data.models);
      } else {
        setError("Failed to load video models");
      }
    } catch (err) {
      setError(err.message || "Error fetching video models");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDownloadStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/batch-video/models/download-status");
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

  const handleDownload = async (modelId) => {
    try {
      const res = await axios.post("/api/batch-video/models/download", { model_id: modelId });
      if (res.data.success) {
        const model = models.find((m) => m.id === modelId);
        showMessage?.(`Started downloading ${model?.name || modelId}...`, "info");
        setDownloadStatus({
          is_downloading: true,
          current_model: modelId,
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
      <DialogTitle>Manage Video Generation Models</DialogTitle>
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
              const isThis = isDownloading && currentModel === model.id;
              return (
                <ListItem key={model.id} divider sx={{ py: 1.5 }}>
                  <ListItemIcon>
                    <MovieCreationIcon color={model.is_downloaded ? "primary" : "action"} />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                        <Typography variant="body1" fontWeight={500}>
                          {model.name}
                        </Typography>
                        <Chip label={`${model.size_gb} GB`} size="small" variant="outlined" />
                        {model.vram_mb > 0 && (
                          <Chip
                            icon={<MemoryIcon />}
                            label={`${(model.vram_mb / 1024).toFixed(0)}GB VRAM`}
                            size="small"
                            variant="outlined"
                          />
                        )}
                      </Box>
                    }
                    secondary={model.description}
                  />

                  <Box sx={{ ml: 2, minWidth: 120, textAlign: "right" }}>
                    {isThis ? (
                      <Box sx={{ width: 130 }}>
                        <Typography variant="caption" noWrap>
                          {downloadStatus.status === "starting"
                            ? "Starting..."
                            : `${downloadStatus.progress}% — ${downloadStatus.speed_mbps} MB/s`}
                        </Typography>
                        <LinearProgress
                          variant={downloadStatus.progress > 0 ? "determinate" : "indeterminate"}
                          value={downloadStatus.progress}
                          sx={{ mt: 0.5 }}
                        />
                        <Typography variant="caption" color="text.secondary">
                          {downloadStatus.downloaded_gb.toFixed(1)} / {downloadStatus.total_gb.toFixed(1)} GB
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
                        onClick={() => handleDownload(model.id)}
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
                No video models configured.
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

export default VideoModelsModal;
