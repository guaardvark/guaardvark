// frontend/src/components/modals/VoiceModelsModal.jsx
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
  Divider,
} from "@mui/material";
import MicIcon from "@mui/icons-material/Mic";
import RecordVoiceOverIcon from "@mui/icons-material/RecordVoiceOver";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";

const VoiceModelsModal = ({ open, onClose, showMessage }) => {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [downloadStatus, setDownloadStatus] = useState({
    is_downloading: false,
    current_model: null,
    model_type: null,
    progress: 0,
    status: "idle",
    speed_mbps: 0,
    downloaded_mb: 0,
    total_mb: 0,
  });
  const [error, setError] = useState(null);

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/voice/models/all");
      if (res.data.success) {
        setModels(res.data.data.models);
      } else {
        setError("Failed to load voice models");
      }
    } catch (err) {
      setError(err.message || "Error fetching voice models");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchDownloadStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/voice/models/download-status");
      if (res.data.success) {
        const status = res.data.data;
        setDownloadStatus(status);
        if (!status.is_downloading && status.status === "completed") {
          showMessage?.("Voice model download completed!", "success");
          fetchModels();
          setDownloadStatus((prev) => ({
            ...prev,
            status: "idle",
            current_model: null,
          }));
        } else if (!status.is_downloading && status.status === "failed") {
          showMessage?.(`Download failed: ${status.error}`, "error");
          setDownloadStatus((prev) => ({
            ...prev,
            status: "idle",
            current_model: null,
          }));
        }
      }
    } catch (err) {
      console.error("Failed to fetch voice download status", err);
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

  const handleDownload = async (modelId, modelType) => {
    try {
      const res = await axios.post("/api/voice/models/download", {
        model_id: modelId,
        model_type: modelType,
      });
      if (res.data.success) {
        const model = models.find(
          (m) => m.id === modelId && m.model_type === modelType
        );
        showMessage?.(
          `Started downloading ${model?.name || modelId}...`,
          "info"
        );
        setDownloadStatus({
          is_downloading: true,
          current_model: modelId,
          model_type: modelType,
          progress: 0,
          status: "starting",
          speed_mbps: 0,
          downloaded_mb: 0,
          total_mb: model?.size_mb || 0,
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

  // Group models by category
  const whisperModels = models.filter((m) => m.model_type === "whisper");
  const piperModels = models.filter((m) => m.model_type === "piper");

  const renderModel = (model) => {
    const isThis =
      isDownloading &&
      downloadStatus.current_model === model.id &&
      downloadStatus.model_type === model.model_type;
    const IconComponent =
      model.model_type === "whisper" ? MicIcon : RecordVoiceOverIcon;

    return (
      <ListItem key={`${model.model_type}-${model.id}`} divider sx={{ py: 1.5 }}>
        <ListItemIcon>
          <IconComponent color={model.is_downloaded ? "primary" : "action"} />
        </ListItemIcon>
        <ListItemText
          primary={
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Typography variant="body1" fontWeight={500}>
                {model.name}
              </Typography>
              {model.size_mb > 0 && (
                <Chip
                  label={
                    model.size_mb >= 1024
                      ? `${(model.size_mb / 1024).toFixed(1)} GB`
                      : `${Math.round(model.size_mb)} MB`
                  }
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
                  : `${downloadStatus.progress}% \u2014 ${downloadStatus.speed_mbps} MB/s`}
              </Typography>
              <LinearProgress
                variant={downloadStatus.progress > 0 ? "determinate" : "indeterminate"}
                value={downloadStatus.progress}
                sx={{ mt: 0.5 }}
              />
              <Typography variant="caption" color="text.secondary">
                {(downloadStatus.downloaded_mb || 0).toFixed(0)} /{" "}
                {(downloadStatus.total_mb || 0).toFixed(0)} MB
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
              onClick={() => handleDownload(model.id, model.model_type)}
              disabled={isDownloading}
            >
              Install
            </Button>
          )}
        </Box>
      </ListItem>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={() => !isDownloading && onClose()}
      maxWidth="sm"
      fullWidth
    >
      <DialogTitle>Manage Voice Models</DialogTitle>
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
          <>
            {/* Whisper STT section */}
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mt: 1, mb: 0.5, px: 1 }}
            >
              Speech-to-Text (Whisper)
            </Typography>
            <List disablePadding>
              {whisperModels.map(renderModel)}
            </List>

            <Divider sx={{ my: 2 }} />

            {/* Piper TTS section */}
            <Typography
              variant="subtitle2"
              color="text.secondary"
              sx={{ mb: 0.5, px: 1 }}
            >
              Text-to-Speech (Piper)
            </Typography>
            <List disablePadding>
              {piperModels.map(renderModel)}
            </List>

            {models.length === 0 && (
              <Typography
                variant="body2"
                color="textSecondary"
                align="center"
                sx={{ py: 3 }}
              >
                No voice models configured.
              </Typography>
            )}
          </>
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

export default VoiceModelsModal;
