// frontend/src/pages/UpscalingPage.jsx
// Upscaling workspace for video and stills: upload, model selection, job tracking.
// Videos and image batches queue on the plugin's single GPU worker; a single
// still is upscaled inline and returned with the request.

import React, { useEffect, useState, useRef, useCallback, Suspense } from "react";
import {
  Box,
  Typography,
  Button,
  Grid,
  Stack,
  Chip,
  IconButton,
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
  ToggleButton,
  ToggleButtonGroup,
  Switch,
  FormControlLabel,
  Slider,
} from "@mui/material";
import PageLayout from "../components/layout/PageLayout";
import {
  Upload as UploadIcon,
  AutoFixHigh as EnhanceIcon,
  PlayArrow as PlayIcon,
  Download as DownloadIcon,
  Refresh as RefreshIcon,
  Cancel as CancelIcon,
  Speed as SpeedIcon,
  ArrowBack as BackIcon,
  Close as CloseIcon,
  Visibility as PreviewIcon,
  Movie as MovieIcon,
  Image as ImageIcon,
} from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import * as upscalingService from "../api/upscalingService";
import { listPlugins } from "../api/pluginsService";

const UpscalingModelsModal = React.lazy(() =>
  import("../components/modals/UpscalingModelsModal")
);

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const TARGET_PRESETS = {
  "4k": { label: "4K (3840px)", width: 3840 },
  "8k": { label: "8K (7680px)", width: 7680 },
};

const VIDEO_EXTENSIONS = ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv"];
const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"];

// null means "whatever the model's native factor is" — the plugin decides.
const IMAGE_SCALES = [
  { value: "", label: "Model default" },
  { value: "2", label: "2x" },
  { value: "3", label: "3x" },
  { value: "4", label: "4x" },
];

const extensionOf = (name) => (name.split(".").pop() || "").toLowerCase();
const isVideoFile = (name) => VIDEO_EXTENSIONS.includes(extensionOf(name));
const isImageFile = (name) => IMAGE_EXTENSIONS.includes(extensionOf(name));

const UpscalingPage = ({ embedded = false }) => {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);
  const pollingRef = useRef(null);

  // Service state
  const [serviceAvailable, setServiceAvailable] = useState(null);
  const [serviceHealth, setServiceHealth] = useState(null);
  const [models, setModels] = useState({ downloaded: [], available: [] });

  // Upload state — selectedFiles is an array so we can batch-queue a bunch at once
  const [dragActive, setDragActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploadProgress, setUploadProgress] = useState(null); // { current, total, name }

  // "video" or "image" — drives which files are accepted and which endpoint
  // the Upscale button calls. Switching kinds drops the pending selection.
  const [mediaKind, setMediaKind] = useState("video");

  // Settings
  const [selectedModel, setSelectedModel] = useState("");
  const [imageScale, setImageScale] = useState("");
  const [targetResolution, setTargetResolution] = useState("4k");
  const [twoPass, setTwoPass] = useState(false);
  const [faceEnhance, setFaceEnhance] = useState(false);
  const [doubleFps, setDoubleFps] = useState(false);
  const [sharpen, setSharpen] = useState(0.3);
  const [denoiseStrength, setDenoiseStrength] = useState(0.0);

  // Jobs
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Result of a single-image upscale: before/after pair shown on the right.
  const [imageResult, setImageResult] = useState(null);
  const imageResultRef = useRef(null);

  const [previewFile, setPreviewFile] = useState(null);
  const [previewOriginalUrl, setPreviewOriginalUrl] = useState(null);
  const [previewUpscaledUrl, setPreviewUpscaledUrl] = useState(null);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const videoRef = useRef(null);

  const [upscalingModelsModalOpen, setUpscalingModelsModalOpen] = useState(false);

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

  // The single-image "before" thumbnail is an object URL; release it when the
  // page goes away so a long session doesn't leak every image it upscaled.
  useEffect(() => {
    return () => {
      if (imageResultRef.current?.originalUrl) {
        URL.revokeObjectURL(imageResultRef.current.originalUrl);
      }
    };
  }, []);

  useEffect(() => {
    imageResultRef.current = imageResult;
  }, [imageResult]);

  const refreshModels = useCallback(
    async (options = {}) => {
      const { selectModel } = options;
      if (!serviceAvailable) return;
      try {
        const res = await upscalingService.getModels();
        const data = res.data || res;
        setModels(data);
        if (selectModel) {
          setSelectedModel(selectModel);
        } else if (data.downloaded?.length > 0) {
          setSelectedModel((current) => current || data.downloaded[0].name);
        }
      } catch {
        // ignore
      }
    },
    [serviceAvailable],
  );

  useEffect(() => {
    if (!serviceAvailable) return;
    refreshModels();
  }, [serviceAvailable, refreshModels]);

  const upscalingModelsShowMessage = useCallback((msg, type) => {
    if (type === "error") {
      setError(msg);
      setSuccess("");
    } else {
      setSuccess(msg);
      setError("");
    }
  }, []);

  const handleUpscalingModelInstalled = useCallback(
    (name) => {
      refreshModels({ selectModel: name });
    },
    [refreshModels],
  );

  // If the plugin goes down mid-session, kill the polling interval so it
  // doesn't keep firing with a stale fetchJobs closure that thinks the
  // service is still up. Re-enable will start a fresh interval next upscale.
  useEffect(() => {
    if (serviceAvailable === false && pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
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

  // Keep GPU Status (Active Model / VRAM / compile) live while jobs run.
  // Mount-only getHealth() freezes Active Model at "None" for the whole job.
  const refreshHealth = useCallback(async () => {
    if (serviceAvailable === false) return;
    try {
      const res = await upscalingService.getHealth();
      setServiceHealth(res.data || res);
    } catch {
      // ignore — same silence as fetchJobs; checkService handles hard downtime
    }
  }, [serviceAvailable]);

  const startPolling = useCallback(() => {
    if (pollingRef.current) return;
    pollingRef.current = setInterval(async () => {
      await Promise.all([fetchJobs(), refreshHealth()]);
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
  }, [fetchJobs, refreshHealth]);

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

  const acceptsFile = useCallback(
    (name) => (mediaKind === "image" ? isImageFile(name) : isVideoFile(name)),
    [mediaKind],
  );

  const addFiles = useCallback(
    (incoming) => {
      const accepted = incoming.filter((f) => acceptsFile(f.name));
      const rejected = incoming.length - accepted.length;
      if (accepted.length > 0) {
        setSelectedFiles((prev) => [...prev, ...accepted]);
      }
      if (rejected > 0) {
        const allowed = (mediaKind === "image" ? IMAGE_EXTENSIONS : VIDEO_EXTENSIONS)
          .map((ext) => `.${ext}`)
          .join(", ");
        setError(`Skipped ${rejected} unsupported file(s). Allowed: ${allowed}`);
      }
    },
    [acceptsFile, mediaKind],
  );

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files?.length > 0) {
      addFiles(Array.from(e.dataTransfer.files));
    }
  }, [addFiles]);

  const handleFileSelect = useCallback((e) => {
    if (e.target.files?.length > 0) {
      addFiles(Array.from(e.target.files));
    }
  }, [addFiles]);

  const handleRemoveFile = useCallback((idx) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const handleClearFiles = useCallback(() => {
    setSelectedFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  // A video selection means nothing in image mode (and vice versa), so the
  // pending queue and any single-image result reset on every switch.
  const handleKindChange = useCallback((_e, next) => {
    if (!next) return;
    setMediaKind(next);
    setSelectedFiles([]);
    setPreviewFile(null);
    setImageResult((prev) => {
      if (prev?.originalUrl) URL.revokeObjectURL(prev.originalUrl);
      return null;
    });
    setError("");
    setSuccess("");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  // --- Submit upscale ---
  // Loops through every selected file and submits a job per file. The backend
  // queues them, so they upscale sequentially without us having to coordinate.
  const handleUpscale = async () => {
    if (selectedFiles.length === 0) return;
    setIsUploading(true);
    setError("");
    setSuccess("");

    const total = selectedFiles.length;
    const failures = [];
    let succeeded = 0;

    for (let i = 0; i < selectedFiles.length; i++) {
      const file = selectedFiles[i];
      setUploadProgress({ current: i + 1, total, name: file.name });
      try {
        await upscalingService.uploadAndUpscale(file, {
          model: selectedModel || undefined,
          target_width: TARGET_PRESETS[targetResolution]?.width,
          two_pass: twoPass,
          face_enhance: faceEnhance,
          double_fps: doubleFps,
          sharpen: sharpen,
          denoise_strength: denoiseStrength,
        });
        succeeded += 1;
      } catch (e) {
        failures.push(`${file.name}: ${e.message || "failed"}`);
      }
    }

    setUploadProgress(null);
    if (succeeded > 0) {
      setSuccess(
        total === 1
          ? `Upscale job submitted for "${selectedFiles[0].name}"`
          : `Submitted ${succeeded} of ${total} upscale jobs`
      );
    }
    if (failures.length > 0) {
      setError(`Failed to submit: ${failures.join("; ")}`);
    }
    setSelectedFiles([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
    await Promise.all([fetchJobs(), refreshHealth()]);
    startPolling();
    setIsUploading(false);
  };

  // --- Submit image upscale ---
  // One file returns the finished still with the response; more than one goes
  // to the plugin queue as a single job so a folder of stills doesn't fight a
  // running video render for VRAM.
  const handleUpscaleImages = async () => {
    if (selectedFiles.length === 0) return;
    setIsUploading(true);
    setError("");
    setSuccess("");

    const options = {
      model: selectedModel || undefined,
      scale: imageScale || undefined,
      two_pass: twoPass,
      face_enhance: faceEnhance,
      sharpen,
      denoise_strength: denoiseStrength,
    };

    try {
      if (selectedFiles.length === 1) {
        const file = selectedFiles[0];
        setUploadProgress({ current: 1, total: 1, name: file.name });
        const res = await upscalingService.upscaleImage(file, options);
        const data = res.data || res;
        setImageResult((prev) => {
          if (prev?.originalUrl) URL.revokeObjectURL(prev.originalUrl);
          return {
            name: file.name,
            originalUrl: URL.createObjectURL(file),
            url: `${API_BASE}/upscaling/output/image/${encodeURIComponent(data.output_file)}`,
          };
        });
        setSuccess(`Upscaled "${file.name}"`);
        setSelectedFiles([]);
      } else {
        const res = await upscalingService.upscaleImages(selectedFiles, options);
        const data = res.data || res;
        setSuccess(`Queued ${data.queued ?? selectedFiles.length} images for upscaling`);
        if (data.rejected?.length > 0) {
          setError(`Skipped: ${data.rejected.join("; ")}`);
        }
        setSelectedFiles([]);
        await Promise.all([fetchJobs(), refreshHealth()]);
        startPolling();
      }
    } catch (e) {
      setError(e.message || "Image upscale failed");
    } finally {
      setUploadProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setIsUploading(false);
    }
  };

  const handleSubmit = () => (mediaKind === "image" ? handleUpscaleImages() : handleUpscale());

  const closeImageResult = () => {
    setImageResult((prev) => {
      if (prev?.originalUrl) URL.revokeObjectURL(prev.originalUrl);
      return null;
    });
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

  // --- Clear finished jobs ---
  const handleClearFinished = async () => {
    try {
      await upscalingService.clearFinishedJobs();
      await fetchJobs();
    } catch {
      // ignore
    }
  };

  const finishedCount = jobs.filter(
    (j) => j.status === "completed" || j.status === "failed" || j.status === "cancelled"
  ).length;

  // --- Preview Modal ---
  const handleGeneratePreview = async () => {
    if (!videoRef.current || !previewFile) return;
    setIsPreviewing(true);
    try {
      const video = videoRef.current;
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      const originalUrl = URL.createObjectURL(blob);
      setPreviewOriginalUrl(originalUrl);
      
      const upscaledUrl = await upscalingService.previewImage(blob, {
        model: selectedModel || undefined,
        scale: 2, 
        sharpen: sharpen,
        denoise_strength: denoiseStrength,
        two_pass: twoPass,
        face_enhance: faceEnhance,
      });
      setPreviewUpscaledUrl(upscaledUrl);
    } catch (err) {
      console.error(err);
      setError(`Preview failed: ${err.message}`);
    } finally {
      setIsPreviewing(false);
    }
  };

  const closePreview = () => {
    setPreviewFile(null);
    if (previewOriginalUrl) URL.revokeObjectURL(previewOriginalUrl);
    if (previewUpscaledUrl) URL.revokeObjectURL(previewUpscaledUrl);
    setPreviewOriginalUrl(null);
    setPreviewUpscaledUrl(null);
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

  const _formatDuration = (seconds) => {
    if (!seconds) return "";
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  };

  const gpu = serviceHealth?.gpu || "Unknown";
  const vramUsed = serviceHealth?.vram_used_mb || 0;
  const vramTotal = serviceHealth?.vram_total_mb || 0;
  const modelLoaded = serviceHealth?.model_loaded;

  const isImageMode = mediaKind === "image";

  const Wrapper = embedded ? React.Fragment : PageLayout;
  const wrapperProps = embedded ? {} : {
    title: "Upscaling",
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
                {isImageMode ? "Upload Images" : "Upload Video"}
              </Typography>

              <ToggleButtonGroup
                value={mediaKind}
                exclusive
                onChange={handleKindChange}
                size="small"
                fullWidth
                sx={{ mb: 2 }}
              >
                <ToggleButton value="video">
                  <MovieIcon fontSize="small" sx={{ mr: 1 }} />
                  Video
                </ToggleButton>
                <ToggleButton value="image">
                  <ImageIcon fontSize="small" sx={{ mr: 1 }} />
                  Image
                </ToggleButton>
              </ToggleButtonGroup>

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
                  accept={isImageMode ? "image/*" : "video/*"}
                  multiple
                  onChange={handleFileSelect}
                  style={{ display: "none" }}
                />
                <UploadIcon sx={{ fontSize: 48, color: "text.secondary", mb: 1 }} />
                {selectedFiles.length === 0 ? (
                  <Typography variant="body1" color="text.secondary">
                    {isImageMode
                      ? "Drag & drop images here, or click to browse"
                      : "Drag & drop videos here, or click to browse"}
                  </Typography>
                ) : (
                  <Stack spacing={0.5} sx={{ mt: 0.5 }} onClick={(e) => e.stopPropagation()}>
                    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ px: 0.5 }}>
                      <Typography variant="body2" color="text.secondary">
                        {selectedFiles.length} file{selectedFiles.length === 1 ? "" : "s"} ready
                      </Typography>
                      <Button size="small" onClick={handleClearFiles} disabled={isUploading}>
                        Clear all
                      </Button>
                    </Stack>
                    {selectedFiles.map((f, i) => (
                      <Stack
                        key={`${f.name}-${i}`}
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        sx={{
                          bgcolor: "action.hover",
                          px: 1,
                          py: 0.5,
                          borderRadius: 1,
                        }}
                      >
                        <Typography
                          variant="caption"
                          sx={{
                            flex: 1,
                            textAlign: "left",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {f.name}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {(f.size / (1024 * 1024)).toFixed(1)} MB
                        </Typography>
                        <Stack direction="row">
                          {!isImageMode && (
                            <IconButton
                              size="small"
                              onClick={() => setPreviewFile(f)}
                              disabled={isUploading}
                            >
                              <PreviewIcon fontSize="inherit" />
                            </IconButton>
                          )}
                          <IconButton
                            size="small"
                            onClick={() => handleRemoveFile(i)}
                            disabled={isUploading}
                          >
                            <CloseIcon fontSize="inherit" />
                          </IconButton>
                        </Stack>
                      </Stack>
                    ))}
                  </Stack>
                )}
              </Box>

              {/* Settings */}
              <Stack spacing={2}>
                <Stack direction="row" spacing={1} alignItems="flex-start">
                  <FormControl fullWidth size="small" sx={{ flex: 1 }}>
                    <InputLabel>Model</InputLabel>
                    <Select
                      value={selectedModel}
                      label="Model"
                      onChange={(e) => setSelectedModel(e.target.value)}
                    >
                      {models.downloaded?.map((m) => (
                        <MenuItem key={m.name} value={m.name}>
                          {m.name} ({m.scale != null ? `${m.scale}x` : "?"})
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={() => setUpscalingModelsModalOpen(true)}
                    disabled={!serviceAvailable}
                    sx={{ mt: 0.5, flexShrink: 0, whiteSpace: "nowrap" }}
                  >
                    Manage Upscaling Models
                  </Button>
                </Stack>

                {isImageMode ? (
                  <FormControl fullWidth size="small">
                    <InputLabel>Output Scale</InputLabel>
                    <Select
                      value={imageScale}
                      label="Output Scale"
                      onChange={(e) => setImageScale(e.target.value)}
                    >
                      {IMAGE_SCALES.map((option) => (
                        <MenuItem key={option.value || "native"} value={option.value}>
                          {option.label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                ) : (
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
                )}

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
                        Runs model twice for higher quality (slower)
                      </Typography>
                    </Stack>
                  }
                />

                <FormControlLabel
                  control={
                    <Switch
                      checked={faceEnhance}
                      onChange={(e) => setFaceEnhance(e.target.checked)}
                      size="small"
                    />
                  }
                  label={
                    <Stack>
                      <Typography variant="body2">Face Enhancement</Typography>
                      <Typography variant="caption" color="text.secondary">
                        Restores faces using GFPGAN
                      </Typography>
                    </Stack>
                  }
                />

                {!isImageMode && (
                  <FormControlLabel
                    control={
                      <Switch
                        checked={doubleFps}
                        onChange={(e) => setDoubleFps(e.target.checked)}
                        size="small"
                      />
                    }
                    label={
                      <Stack>
                        <Typography variant="body2">Double Framerate</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Interpolates frames for smoother motion (slower)
                        </Typography>
                      </Stack>
                    }
                  />
                )}

                <Box>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">
                      Sharpening
                    </Typography>
                    <Typography variant="body2">{sharpen.toFixed(1)}</Typography>
                  </Stack>
                  <Slider
                    value={sharpen}
                    onChange={(_, v) => setSharpen(v)}
                    min={0}
                    max={1.0}
                    step={0.1}
                    size="small"
                  />
                </Box>

                <Box>
                  <Stack direction="row" justifyContent="space-between">
                    <Typography variant="body2" color="text.secondary">
                      Denoising Pre-pass
                    </Typography>
                    <Typography variant="body2">{denoiseStrength.toFixed(1)}</Typography>
                  </Stack>
                  <Slider
                    value={denoiseStrength}
                    onChange={(_, v) => setDenoiseStrength(v)}
                    min={0}
                    max={1.0}
                    step={0.1}
                    size="small"
                  />
                </Box>

                <Button
                  variant="contained"
                  size="large"
                  startIcon={isUploading ? <CircularProgress size={20} color="inherit" /> : <EnhanceIcon />}
                  onClick={handleSubmit}
                  disabled={selectedFiles.length === 0 || isUploading || !serviceAvailable}
                  fullWidth
                  sx={{ mt: 1 }}
                >
                  {isUploading
                    ? uploadProgress
                      ? `Uploading ${uploadProgress.current}/${uploadProgress.total}...`
                      : "Uploading..."
                    : selectedFiles.length > 1
                      ? `Upscale ${selectedFiles.length} ${isImageMode ? "Images" : "Videos"}${twoPass ? " (2-Pass)" : ""}`
                      : `Upscale ${isImageMode ? "Image" : "Video"}${twoPass ? " (2-Pass)" : ""}`}
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

        {/* Right: Preview & Job History */}
        <Grid item xs={12} lg={7}>
          {imageResult && (
            <Card sx={{ boxShadow: 2, borderRadius: 2, mb: 3 }}>
              <CardContent sx={{ p: 3 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }} noWrap>
                    {imageResult.name}
                  </Typography>
                  <IconButton size="small" onClick={closeImageResult}>
                    <CloseIcon />
                  </IconButton>
                </Stack>
                <Grid container spacing={2}>
                  <Grid item xs={6}>
                    <Typography variant="subtitle2" align="center" gutterBottom>
                      Original
                    </Typography>
                    <img
                      src={imageResult.originalUrl}
                      alt="Original"
                      style={{ width: "100%", height: "auto", display: "block", borderRadius: "8px" }}
                    />
                  </Grid>
                  <Grid item xs={6}>
                    <Typography variant="subtitle2" align="center" gutterBottom>
                      Upscaled
                    </Typography>
                    <img
                      src={imageResult.url}
                      alt="Upscaled"
                      style={{ width: "100%", height: "auto", display: "block", borderRadius: "8px" }}
                    />
                  </Grid>
                </Grid>
                <Stack direction="row" spacing={1} justifyContent="center" sx={{ mt: 2 }}>
                  <Button
                    size="small"
                    startIcon={<PreviewIcon />}
                    component="a"
                    href={imageResult.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Open full size
                  </Button>
                  <Button
                    size="small"
                    startIcon={<DownloadIcon />}
                    component="a"
                    href={imageResult.url}
                    download
                  >
                    Download
                  </Button>
                </Stack>
              </CardContent>
            </Card>
          )}

          {previewFile && (
            <Card sx={{ boxShadow: 2, borderRadius: 2, mb: 3 }}>
              <CardContent sx={{ p: 3 }}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    Preview Upscale
                  </Typography>
                  <IconButton size="small" onClick={closePreview}>
                    <CloseIcon />
                  </IconButton>
                </Stack>

                {!previewUpscaledUrl ? (
                  <Box textAlign="center">
                    <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                      Seek to a frame and click "Generate Preview" to see the upscaled frame.
                    </Typography>
                    <Box
                      sx={{
                        "& video::-webkit-media-controls-start-playback-button": { display: "none" },
                        "& video::-webkit-media-controls-play-button": { display: "none" },
                        "& video": { pointerEvents: "auto" }
                      }}
                    >
                      <video
                        ref={videoRef}
                        src={URL.createObjectURL(previewFile)}
                        controls
                        style={{ maxWidth: "100%", maxHeight: "40vh", borderRadius: "8px" }}
                      />
                    </Box>
                    <Box sx={{ mt: 2 }}>
                      <Button
                        variant="contained"
                        onClick={handleGeneratePreview}
                        disabled={isPreviewing || !serviceAvailable}
                      >
                        {isPreviewing ? "Generating..." : "Generate Preview"}
                      </Button>
                    </Box>
                  </Box>
                ) : (
                  <Box>
                    <Grid container spacing={2}>
                      <Grid item xs={6}>
                        <Typography variant="subtitle2" align="center" gutterBottom>
                          Original Frame
                        </Typography>
                        <img
                          src={previewOriginalUrl}
                          alt="Original"
                          style={{ width: "100%", height: "auto", display: "block", borderRadius: "8px" }}
                        />
                      </Grid>
                      <Grid item xs={6}>
                        <Typography variant="subtitle2" align="center" gutterBottom>
                          Upscaled Frame
                        </Typography>
                        <img
                          src={previewUpscaledUrl}
                          alt="Upscaled"
                          style={{ width: "100%", height: "auto", display: "block", borderRadius: "8px" }}
                        />
                      </Grid>
                    </Grid>
                    <Box sx={{ mt: 2, textAlign: "center" }}>
                      <Button onClick={() => setPreviewUpscaledUrl(null)}>
                        Back to Video
                      </Button>
                    </Box>
                  </Box>
                )}
              </CardContent>
            </Card>
          )}

          <Card sx={{ boxShadow: 2, borderRadius: 2 }}>
            <CardContent sx={{ p: 3 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Upscale Jobs
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Button
                    size="small"
                    onClick={handleClearFinished}
                    disabled={finishedCount === 0}
                  >
                    Clear finished{finishedCount > 0 ? ` (${finishedCount})` : ""}
                  </Button>
                  <IconButton size="small" onClick={fetchJobs}>
                    <RefreshIcon />
                  </IconButton>
                </Stack>
              </Stack>

              {jobs.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center", py: 4 }}>
                  No upscale jobs yet. Upload a video or a batch of images to get started.
                </Typography>
              ) : (
                <Stack spacing={2}>
                  {jobs.map((job) => {
                    const isBatch = job.kind === "image_batch";
                    const done = job.frames_done || 0;
                    const total = job.frames_total || 0;
                    return (
                    <Card key={job.job_id} variant="outlined">
                      <CardContent sx={{ pb: 1 }}>
                        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography variant="subtitle2" noWrap>
                              {isBatch
                                ? `${job.item_count || total} image${(job.item_count || total) === 1 ? "" : "s"}`
                                : job.input_path?.split("/").pop() || job.job_id}
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
                                {isBatch ? "Image" : "Frame"} {done} / {total || "?"}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                {total > 0 ? `${Math.round((done / total) * 100)}%` : ""}
                              </Typography>
                            </Stack>
                            <LinearProgress
                              variant={total > 0 ? "determinate" : "indeterminate"}
                              value={total > 0 ? (done / total) * 100 : 0}
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
                      <CardActions sx={{ pt: 0, flexWrap: "wrap" }}>
                        {isBatch && job.outputs?.length > 0 && (
                          <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5 }}>
                            {job.outputs.map((outputPath) => {
                              const name = outputPath.split("/").pop();
                              return (
                                <Button
                                  key={outputPath}
                                  size="small"
                                  startIcon={<DownloadIcon />}
                                  component="a"
                                  href={`${API_BASE}/upscaling/output/image/${encodeURIComponent(name)}`}
                                  download
                                >
                                  {name}
                                </Button>
                              );
                            })}
                          </Stack>
                        )}
                        {!isBatch && job.status === "completed" && job.output_path && (
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
                    );
                  })}
                </Stack>
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      <Suspense fallback={null}>
        <UpscalingModelsModal
          open={upscalingModelsModalOpen}
          onClose={() => setUpscalingModelsModalOpen(false)}
          showMessage={upscalingModelsShowMessage}
          onInstalled={handleUpscalingModelInstalled}
        />
      </Suspense>
    </Wrapper>
  );
};

export default UpscalingPage;
