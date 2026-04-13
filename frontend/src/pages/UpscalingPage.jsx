// frontend/src/pages/UpscalingPage.jsx
// Dedicated upscaling page with video upload, model selection, and job tracking

import React, { useEffect, useState, useRef, useCallback } from "react";
import {
  Box,
  Paper,
  Typography,
  Button,
  Grid,
  Stack,
  Chip,
  IconButton,
  Tooltip,
  Card,
  CardContent,
  CardActions,
  LinearProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Alert,
  CircularProgress,
  Divider,
  ToggleButton,
  ToggleButtonGroup,
  Switch,
  FormControlLabel,
} from "@mui/material";
import PageLayout from "../components/layout/PageLayout";
import {
  Upload as UploadIcon,
  AutoFixHigh as EnhanceIcon,
  Close as CloseIcon,
  PlayArrow as PlayIcon,
  Download as DownloadIcon,
  Refresh as RefreshIcon,
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  Error as ErrorIcon,
  Schedule as ScheduleIcon,
  Speed as SpeedIcon,
  ArrowBack as BackIcon,
} from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import * as upscalingService from "../api/upscalingService";
import { listPlugins } from "../api/pluginsService";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const TARGET_PRESETS = {
  "4k": { label: "4K (3840px)", width: 3840 },
  "8k": { label: "8K (7680px)", width: 7680 },
};

const UpscalingPage = ({ embedded = false }) => {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);
  const pollingRef = useRef(null);

  // Service state
  const [serviceAvailable, setServiceAvailable] = useState(null);
  const [serviceHealth, setServiceHealth] = useState(null);
  const [models, setModels] = useState({ downloaded: [], available: [] });

  // Upload state
  const [dragActive, setDragActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  // Settings
  const [selectedModel, setSelectedModel] = useState("");
  const [targetResolution, setTargetResolution] = useState("4k");
  const [twoPass, setTwoPass] = useState(false);

  // Jobs
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // --- Init ---
  useEffect(() => {
    checkService();
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  // Check the plugin manager first — if upscaling isn't running, we skip the
  // direct plugin calls entirely. Calling a disabled plugin's endpoints just
  // spams the console with 503s, even though the page handles them silently.
  const checkService = async () => {
    try {
      const pluginsRes = await listPlugins();
      const plugins = pluginsRes?.data?.plugins || [];
      const upscaling = plugins.find((p) => p.id === "upscaling");
      if (!upscaling || upscaling.status !== "running") {
        setServiceAvailable(false);
        return;
      }
      const res = await upscalingService.getHealth();
      setServiceAvailable(true);
      setServiceHealth(res.data || res);
      fetchJobs();
    } catch {
      setServiceAvailable(false);
    }
  };

  useEffect(() => {
    if (!serviceAvailable) return;
    const loadModels = async () => {
      try {
        const res = await upscalingService.getModels();
        const data = res.data || res;
        setModels(data);
        if (data.downloaded?.length > 0 && !selectedModel) {
          setSelectedModel(data.downloaded[0].name);
        }
      } catch {
        // ignore
      }
    };
    loadModels();
  }, [serviceAvailable]);

  // --- Job polling ---
  // Bail out if the plugin isn't up — otherwise we'd pelt /api/upscaling/jobs
  // with requests that will just 503 and clutter the console.
  const fetchJobs = useCallback(async () => {
    if (serviceAvailable === false) return;
    try {
      const res = await upscalingService.listJobs();
      const data = res.data || res;
      setJobs(Array.isArray(data) ? data : []);
    } catch {
      // ignore
    }
  }, [serviceAvailable]);

  const startPolling = useCallback(() => {
    if (pollingRef.current) return;
    pollingRef.current = setInterval(async () => {
      await fetchJobs();
      // Stop polling if no active jobs
      setJobs(prev => {
        const hasActive = prev.some(j =>
          j.status === "pending" || j.status === "running"
        );
        if (!hasActive && pollingRef.current) {
          clearInterval(pollingRef.current);
          pollingRef.current = null;
        }
        return prev;
      });
    }, 2000);
  }, [fetchJobs]);

  // --- Drag & Drop ---
  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  }, []);

  const handleDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  }, []);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.length > 0) {
      const file = e.dataTransfer.files[0];
      if (isVideoFile(file.name)) {
        setSelectedFile(file);
      } else {
        setError("Please upload a video file (.mp4, .mkv, .avi, .mov, .webm)");
      }
    }
  }, []);

  const handleFileSelect = useCallback((e) => {
    if (e.target.files?.length > 0) {
      setSelectedFile(e.target.files[0]);
    }
  }, []);

  const isVideoFile = (name) => {
    const ext = name.split(".").pop().toLowerCase();
    return ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv"].includes(ext);
  };

  // --- Submit upscale ---
  const handleUpscale = async () => {
    if (!selectedFile) return;
    setIsUploading(true);
    setError("");
    setSuccess("");

    try {
      const res = await upscalingService.uploadAndUpscale(selectedFile, {
        model: selectedModel || undefined,
        target_width: TARGET_PRESETS[targetResolution]?.width,
        two_pass: twoPass,
      });
      setSuccess(`Upscale job submitted for "${selectedFile.name}"`);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await fetchJobs();
      startPolling();
    } catch (e) {
      setError(e.message || "Failed to submit upscale job");
    } finally {
      setIsUploading(false);
    }
  };

  // --- Cancel job ---
  const handleCancelJob = async (jobId) => {
    try {
      await upscalingService.cancelJob(jobId);
      await fetchJobs();
    } catch {
      // ignore
    }
  };

  // --- Job status helpers ---
  const statusColor = (status) => {
    switch (status) {
      case "completed": return "success";
      case "running": return "primary";
      case "pending": return "default";
      case "failed": return "error";
      case "cancelled": return "warning";
      default: return "default";
    }
  };

  const formatDuration = (seconds) => {
    if (!seconds) return "";
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  const gpu = serviceHealth?.gpu || "Unknown";
  const vramUsed = serviceHealth?.vram_used_mb || 0;
  const vramTotal = serviceHealth?.vram_total_mb || 0;
  const modelLoaded = serviceHealth?.model_loaded;

  const Wrapper = embedded ? React.Fragment : PageLayout;
  const wrapperProps = embedded ? {} : {
    title: "Video Upscaling",
    variant: "standard",
    actions: (
      <Stack direction="row" spacing={1} alignItems="center">
        <Button size="small" startIcon={<BackIcon />} onClick={() => navigate("/video")}>
          Video Gen
        </Button>
        <IconButton size="small" onClick={() => { checkService(); fetchJobs(); }}>
          <RefreshIcon />
        </IconButton>
      </Stack>
    ),
  };

  return (
    <Wrapper {...wrapperProps}>
      {/* Service Status */}
      {serviceAvailable === false && (
        <Alert severity="warning" sx={{ mb: 3 }}>
          Upscaling service is not running. Start it from the Plugins page.
        </Alert>
      )}

      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError("")}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 3 }} onClose={() => setSuccess("")}>
          {success}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* Left: Upload & Settings */}
        <Grid item xs={12} lg={5}>
          <Card sx={{ boxShadow: 2, borderRadius: 2, mb: 3 }}>
            <CardContent sx={{ p: 3 }}>
              <Typography variant="h6" sx={{ fontWeight: 600, mb: 2 }}>
                Upload Video
              </Typography>

              {/* Drop zone */}
              <Box
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                sx={{
                  border: "2px dashed",
                  borderColor: dragActive ? "primary.main" : "divider",
                  borderRadius: 2,
                  p: 4,
                  textAlign: "center",
                  cursor: "pointer",
                  bgcolor: dragActive ? "action.hover" : "background.default",
                  transition: "all 0.2s",
                  "&:hover": { borderColor: "primary.light", bgcolor: "action.hover" },
                  mb: 2,
                }}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="video/*"
                  onChange={handleFileSelect}
                  style={{ display: "none" }}
                />
                <UploadIcon sx={{ fontSize: 48, color: "text.secondary", mb: 1 }} />
                <Typography variant="body1" color="text.secondary">
                  {selectedFile
                    ? selectedFile.name
                    : "Drag & drop a video here, or click to browse"}
                </Typography>
                {selectedFile && (
                  <Typography variant="caption" color="text.secondary">
                    {(selectedFile.size / (1024 * 1024)).toFixed(1)} MB
                  </Typography>
                )}
              </Box>

              {/* Settings */}
              <Stack spacing={2}>
                <FormControl fullWidth size="small">
                  <InputLabel>Model</InputLabel>
                  <Select
                    value={selectedModel}
                    label="Model"
                    onChange={(e) => setSelectedModel(e.target.value)}
                  >
                    {models.downloaded?.map((m) => (
                      <MenuItem key={m.name} value={m.name}>
                        {m.name} ({m.scale}x)
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <Box>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                    Target Resolution
                  </Typography>
                  <ToggleButtonGroup
                    value={targetResolution}
                    exclusive
                    onChange={(_, v) => v && setTargetResolution(v)}
                    size="small"
                    fullWidth
                  >
                    {Object.entries(TARGET_PRESETS).map(([key, preset]) => (
                      <ToggleButton key={key} value={key}>
                        {preset.label}
                      </ToggleButton>
                    ))}
                  </ToggleButtonGroup>
                </Box>

                <FormControlLabel
                  control={
                    <Switch
                      checked={twoPass}
                      onChange={(e) => setTwoPass(e.target.checked)}
                      size="small"
                    />
                  }
                  label={
                    <Stack>
                      <Typography variant="body2">Two-Pass Mode</Typography>
                      <Typography variant="caption" color="text.secondary">
                        Runs 2x model twice for higher quality (slower)
                      </Typography>
                    </Stack>
                  }
                />

                <Button
                  variant="contained"
                  size="large"
                  startIcon={isUploading ? <CircularProgress size={20} color="inherit" /> : <EnhanceIcon />}
                  onClick={handleUpscale}
                  disabled={!selectedFile || isUploading || !serviceAvailable}
                  fullWidth
                  sx={{ mt: 1 }}
                >
                  {isUploading ? "Uploading..." : twoPass ? "Upscale Video (2-Pass)" : "Upscale Video"}
                </Button>
              </Stack>
            </CardContent>
          </Card>

          {/* GPU Info */}
          {serviceAvailable && serviceHealth && (
            <Card sx={{ boxShadow: 2, borderRadius: 2 }}>
              <CardContent sx={{ p: 3 }}>
                <Typography variant="h6" sx={{ fontWeight: 600, mb: 2 }}>
                  GPU Status
                </Typography>
                <Stack spacing={1}>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">GPU</Typography>
                    <Typography variant="body2">{gpu}</Typography>
                  </Stack>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">VRAM</Typography>
                    <Typography variant="body2">
                      {vramUsed} / {vramTotal} MB
                    </Typography>
                  </Stack>
                  <LinearProgress
                    variant="determinate"
                    value={vramTotal > 0 ? (vramUsed / vramTotal) * 100 : 0}
                    sx={{ height: 6, borderRadius: 1 }}
                  />
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">Active Model</Typography>
                    <Typography variant="body2">{modelLoaded || "None"}</Typography>
                  </Stack>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">torch.compile</Typography>
                    <Chip
                      label={serviceHealth.compile_enabled ? "Enabled" : "Disabled"}
                      size="small"
                      color={serviceHealth.compile_enabled ? "success" : "default"}
                    />
                  </Stack>
                </Stack>
              </CardContent>
            </Card>
          )}
        </Grid>

        {/* Right: Job History */}
        <Grid item xs={12} lg={7}>
          <Card sx={{ boxShadow: 2, borderRadius: 2 }}>
            <CardContent sx={{ p: 3 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Upscale Jobs
                </Typography>
                <IconButton size="small" onClick={fetchJobs}>
                  <RefreshIcon />
                </IconButton>
              </Stack>

              {jobs.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center", py: 4 }}>
                  No upscale jobs yet. Upload a video to get started.
                </Typography>
              ) : (
                <Stack spacing={2}>
                  {jobs.map((job) => (
                    <Card key={job.job_id} variant="outlined">
                      <CardContent sx={{ pb: 1 }}>
                        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography variant="subtitle2" noWrap>
                              {job.input_path?.split("/").pop() || job.job_id}
                            </Typography>
                            <Stack direction="row" spacing={0.5} sx={{ mt: 0.5, flexWrap: "wrap" }}>
                              <Chip
                                label={job.status?.toUpperCase()}
                                size="small"
                                color={statusColor(job.status)}
                              />
                              {job.model && (
                                <Chip label={job.model} size="small" variant="outlined" />
                              )}
                              {job.fps > 0 && (
                                <Chip
                                  icon={<SpeedIcon sx={{ fontSize: 14 }} />}
                                  label={`${job.fps.toFixed(1)} fps`}
                                  size="small"
                                  variant="outlined"
                                />
                              )}
                            </Stack>
                          </Box>
                        </Stack>

                        {/* Progress bar for running jobs */}
                        {job.status === "running" && (
                          <Box sx={{ mt: 1.5 }}>
                            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
                              <Typography variant="caption" color="text.secondary">
                                Frame {job.current_frame || 0} / {job.total_frames || "?"}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                {job.total_frames > 0
                                  ? `${Math.round(((job.current_frame || 0) / job.total_frames) * 100)}%`
                                  : ""}
                              </Typography>
                            </Stack>
                            <LinearProgress
                              variant={job.total_frames > 0 ? "determinate" : "indeterminate"}
                              value={job.total_frames > 0 ? ((job.current_frame || 0) / job.total_frames) * 100 : 0}
                            />
                          </Box>
                        )}

                        {/* Error message */}
                        {job.error && (
                          <Typography variant="caption" color="error" display="block" sx={{ mt: 1 }}>
                            {job.error}
                          </Typography>
                        )}
                      </CardContent>
                      <CardActions sx={{ pt: 0 }}>
                        {job.status === "completed" && job.output_path && (
                          <>
                            <Button
                              size="small"
                              startIcon={<PlayIcon />}
                              onClick={() => {
                                const filename = job.output_path.split("/").pop();
                                window.open(`${API_BASE}/upscaling/output/${encodeURIComponent(filename)}`, "_blank");
                              }}
                            >
                              Play
                            </Button>
                            <Button
                              size="small"
                              startIcon={<DownloadIcon />}
                              component="a"
                              href={`${API_BASE}/upscaling/output/${encodeURIComponent(job.output_path.split("/").pop())}`}
                              download
                            >
                              Download
                            </Button>
                          </>
                        )}
                        {(job.status === "running" || job.status === "pending") && (
                          <Button
                            size="small"
                            color="error"
                            startIcon={<CancelIcon />}
                            onClick={() => handleCancelJob(job.job_id)}
                          >
                            Cancel
                          </Button>
                        )}
                      </CardActions>
                    </Card>
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Wrapper>
  );
};

export default UpscalingPage;
