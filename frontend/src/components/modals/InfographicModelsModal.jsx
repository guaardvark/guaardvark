// frontend/src/components/modals/InfographicModelsModal.jsx
// Manage the four Flux schnell assets the Infographic tab needs.
// Mirrors ImageModelsModal's UX so the modals feel like siblings.

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
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import axios from "axios";

const InfographicModelsModal = ({ open, onClose, showMessage }) => {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dl, setDl] = useState({
    is_downloading: false,
    current_id: null,
    progress: 0,
    status: "idle",
    speed_mbps: 0,
    downloaded_gb: 0,
    total_gb: 0,
    error: null,
  });

  const fetchModels = useCallback(async () => {
    try {
      setLoading(true);
      const res = await axios.get("/api/infographic/models");
      if (res.data?.success) {
        setModels(res.data.models || []);
        setError(null);
      } else {
        setError("Failed to load models");
      }
    } catch (err) {
      setError(err.message || "Error fetching models");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get("/api/infographic/models/download-status");
      if (res.data?.success) {
        const next = res.data;
        setDl((prev) => {
          // Refresh list when a download flips to completed / failed
          if (prev.is_downloading && !next.is_downloading) {
            if (next.status === "completed") {
              showMessage?.("Model installed", "success");
              fetchModels();
            } else if (next.status === "failed") {
              showMessage?.(`Download failed: ${next.error || "unknown"}`, "error");
            }
          }
          return next;
        });
      }
    } catch {
      /* transient, ignore */
    }
  }, [fetchModels, showMessage]);

  useEffect(() => {
    if (open) {
      fetchModels();
      fetchStatus();
    } else {
      setModels([]);
      setError(null);
    }
  }, [open, fetchModels, fetchStatus]);

  useEffect(() => {
    let id;
    if (open && dl.is_downloading) {
      id = setInterval(fetchStatus, 1000);
    }
    return () => clearInterval(id);
  }, [open, dl.is_downloading, fetchStatus]);

  const handleInstall = async (assetId) => {
    try {
      const res = await axios.post("/api/infographic/models/download", { id: assetId });
      if (res.data?.success) {
        const asset = models.find((m) => m.id === assetId);
        showMessage?.(`Started downloading ${asset?.name || assetId}…`, "info");
        setDl({
          is_downloading: true,
          current_id: assetId,
          progress: 0,
          status: "starting",
          speed_mbps: 0,
          downloaded_gb: 0,
          total_gb: asset?.size_gb || 0,
          error: null,
        });
      } else {
        showMessage?.(res.data?.error || "Failed to start", "error");
      }
    } catch (err) {
      if (err.response?.status === 409) {
        showMessage?.("Another download is already running.", "warning");
      } else {
        showMessage?.(err.message || "Error starting download", "error");
      }
    }
  };

  // Total size of everything not yet installed — surfaced so the user
  // sees the commit before clicking "Install all missing".
  const missingTotalGb = models
    .filter((m) => !m.installed)
    .reduce((sum, m) => sum + (m.size_gb || 0), 0);

  const handleInstallAllMissing = async () => {
    const missing = models.filter((m) => !m.installed);
    if (!missing.length) return;
    // Sequential — backend rejects concurrent downloads anyway.
    // Poll the backend directly between rounds; relying on the local
    // `dl` ref would capture a stale closure here.
    for (const m of missing) {
      // eslint-disable-next-line no-await-in-loop
      await handleInstall(m.id);
      // eslint-disable-next-line no-await-in-loop
      await new Promise((resolve) => {
        const check = setInterval(async () => {
          try {
            const res = await axios.get("/api/infographic/models/download-status");
            if (res.data?.success && !res.data.is_downloading) {
              clearInterval(check);
              resolve();
            }
          } catch {
            // Keep waiting — the periodic fetchStatus elsewhere will recover
          }
        }, 700);
      });
    }
    fetchModels();
  };

  return (
    <Dialog open={open} onClose={() => !dl.is_downloading && onClose()} maxWidth="md" fullWidth>
      <DialogTitle>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
          <AutoAwesomeIcon />
          Infographic Models (Flux schnell)
        </Box>
        <Typography variant="body2" sx={{ color: "text.secondary", mt: 0.5 }}>
          Four files power the Infographic tab — diffusion model, two text encoders, and a VAE.
          They install into ComfyUI's model tree, so the comfyui plugin must be enabled to use them.
        </Typography>
      </DialogTitle>

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
            {models.map((m) => {
              const isThis = dl.is_downloading && dl.current_id === m.id;
              return (
                <ListItem key={m.id} divider sx={{ py: 1.5, alignItems: "flex-start" }}>
                  <ListItemIcon sx={{ mt: 0.5 }}>
                    <AutoAwesomeIcon color={m.installed ? "primary" : "action"} />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                        <Typography variant="body1" fontWeight={500}>
                          {m.name}
                        </Typography>
                        <Chip label={`${m.size_gb} GB`} size="small" variant="outlined" />
                      </Box>
                    }
                    secondary={
                      <Box component="span" sx={{ display: "block", mt: 0.5 }}>
                        <Typography variant="caption" sx={{ display: "block", color: "text.secondary" }}>
                          {m.description}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{ display: "block", color: "text.disabled", fontFamily: "monospace", mt: 0.25 }}
                        >
                          {m.rel_path}
                        </Typography>
                      </Box>
                    }
                  />

                  <Box sx={{ ml: 2, minWidth: 160, textAlign: "right" }}>
                    {isThis ? (
                      <Box sx={{ width: 160 }}>
                        <Typography variant="caption" noWrap>
                          {dl.status === "starting"
                            ? "Starting…"
                            : `${dl.progress}% — ${dl.speed_mbps} MB/s`}
                        </Typography>
                        <LinearProgress
                          variant={dl.progress > 0 ? "determinate" : "indeterminate"}
                          value={dl.progress}
                          sx={{ mt: 0.5 }}
                        />
                        <Typography variant="caption" color="text.secondary">
                          {(dl.downloaded_gb || 0).toFixed(2)} / {(dl.total_gb || 0).toFixed(2)} GB
                        </Typography>
                      </Box>
                    ) : m.installed ? (
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
                        onClick={() => handleInstall(m.id)}
                        disabled={dl.is_downloading}
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
                No Flux assets configured.
              </Typography>
            )}
          </List>
        )}
      </DialogContent>

      <DialogActions>
        {missingTotalGb > 0 && (
          <Button
            onClick={handleInstallAllMissing}
            disabled={dl.is_downloading || loading}
            startIcon={<CloudDownloadIcon />}
          >
            Install all missing ({missingTotalGb.toFixed(1)} GB)
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        <Button onClick={onClose} disabled={dl.is_downloading}>
          {dl.is_downloading ? "Downloading…" : "Close"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default InfographicModelsModal;
