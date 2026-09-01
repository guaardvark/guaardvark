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

const TYPE_LABELS = {
  wan: "Video",
  cogvideox: "Video",
  ltx: "Video",
  hunyuan: "Video",
  minimax: "Video",
  facerestore: "Face restore",
  upscaler: "Upscale",
  flux: "Keyframe image",
  vae: "Dependency",
  encoder: "Dependency",
  clip_vision: "Dependency",
  lora: "Speed / identity LoRA",
  embedding: "Dependency",
};

const VideoModelsModal = ({ open, onClose, showMessage, highlightModelId }) => {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  // When the parent opens us pointed at a specific model (because the user tried
  // to generate with an uninstalled one), scroll it into view and pulse it.
  const highlightRef = useRef(null);
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
          showMessageRef.current?.("Model download completed!", "success");
          fetchModels();
          setDownloadStatus((prev) => ({ ...prev, status: "idle", current_model: null }));
        } else if (!status.is_downloading && status.status === "failed") {
          // A stall, a backend restart, or a real error all land here now (issue
          // #36) — surface the reason and refresh so the model's true install
          // state shows and the Install button is live again for a retry.
          showMessageRef.current?.(`Download failed: ${status.error || "unknown error"}`, "error");
          fetchModels();
          setDownloadStatus((prev) => ({ ...prev, status: "idle", current_model: null }));
        }
      }
    } catch (err) {
      console.error("Failed to fetch download status", err);
    }
  }, [fetchModels]);

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

  // Once the model list is rendered, bring the highlighted row into view.
  useEffect(() => {
    if (open && highlightModelId && !loading && highlightRef.current) {
      highlightRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [open, highlightModelId, loading, models]);

  const handleDownload = async (modelId) => {
    try {
      const res = await axios.post("/api/batch-video/models/download", { model_id: modelId });
      if (res.data.success) {
        const model = models.find((m) => m.id === modelId);
        showMessageRef.current?.(`Started downloading ${model?.name || modelId}...`, "info");
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
        showMessageRef.current?.(res.data.error || "Failed to start download", "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessageRef.current?.("A download is already in progress.", "warning");
      } else {
        showMessageRef.current?.(err.message || "Error starting download", "error");
      }
    }
  };

  const isDownloading = downloadStatus.is_downloading;
  const currentModel = downloadStatus.current_model;

  const generationModels = models.filter((m) => GENERATION_TYPES.has(m.type));
  const postModels = models.filter((m) => !GENERATION_TYPES.has(m.type));

  const renderModelRow = (model) => {
    const isThis = isDownloading && currentModel === model.id;
    const isHighlight = !!highlightModelId && model.id === highlightModelId;
    const typeLabel = TYPE_LABELS[model.type] || model.type;
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
              <Typography variant="body1" fontWeight={500}>
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
          ) : (model.is_ready ?? model.is_downloaded) ? (
            <Chip
              icon={<CheckCircleIcon />}
              label="Installed"
              color="success"
              size="small"
              variant="outlined"
            />
          ) : (
            <Box sx={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 0.5 }}>
              <Button
                variant="outlined"
                size="small"
                startIcon={<CloudDownloadIcon />}
                onClick={() => handleDownload(model.id)}
                disabled={isDownloading}
              >
                {model.install_size_gb ? `Install (${model.install_size_gb} GB)` : "Install"}
              </Button>
              {model.requires?.length > 0 && (
                <Typography variant="caption" color="text.secondary" noWrap>
                  includes {model.requires.length} required file{model.requires.length > 1 ? "s" : ""}
                </Typography>
              )}
              {model.is_downloaded && !(model.is_ready ?? true) && (
                <Typography variant="caption" color="warning.main" noWrap>
                  model present — missing dependencies
                </Typography>
              )}
              {!(model.is_ready ?? model.is_downloaded) && model.missing_files?.length > 0 && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  noWrap
                  title={model.missing_files.join("\n")}
                >
                  {model.missing_files.length} file{model.missing_files.length > 1 ? "s" : ""} missing
                </Typography>
              )}
            </Box>
          )}
        </Box>
      </ListItem>
    );
  };

  return (
    <Dialog open={open} onClose={() => !isDownloading && onClose()} maxWidth="sm" fullWidth>
      <DialogTitle>Manage Video Models</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Box mb={2}>
            <Typography color="error">{error}</Typography>
          </Box>
        )}

        {/* First-run discoverability (issue #36): if nothing is installed yet,
            say so up front — video generation can't run without a model, and
            there's no auto-download. */}
        {!loading && models.length > 0 && !models.some((m) => m.is_ready ?? m.is_downloaded) && (
          <Box sx={{ mb: 2, p: 1.5, border: 1, borderColor: "warning.main", borderRadius: 1 }}>
            <Typography variant="body2" color="warning.main">
              No video model is installed yet. Install one below to enable video generation — each
              model's download size and VRAM requirement is shown so you can pick what fits your machine.
            </Typography>
          </Box>
        )}

        {loading ? (
          <Box display="flex" justifyContent="center" p={3}>
            <CircularProgress />
          </Box>
        ) : (
          <List disablePadding>
            {generationModels.length > 0 && (
              <>
                <Typography variant="overline" color="text.secondary" sx={{ px: 2, pt: 1, display: "block" }}>
                  Video generation
                </Typography>
                {generationModels.map(renderModelRow)}
              </>
            )}
            {postModels.length > 0 && (
              <>
                <Typography variant="overline" color="text.secondary" sx={{ px: 2, pt: 2, display: "block" }}>
                  Post-processing &amp; dependencies
                </Typography>
                {postModels.map(renderModelRow)}
              </>
            )}
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
