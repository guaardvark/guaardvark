// frontend/src/pages/VideoGeneratorPage.jsx
// Standalone Video Generation page with preset-based UI

import React, { useEffect, useMemo, useState, useRef, useCallback } from "react";
import {
  Box,
  Paper,
  Typography,
  ToggleButton,
  ToggleButtonGroup,
  TextField,
  Button,
  Grid,
  Stack,
  Divider,
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
  Collapse,
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Checkbox,
  CircularProgress,
  Switch,
  FormControlLabel,
} from "@mui/material";
import PageLayout from "../components/layout/PageLayout";
import {
  PlayArrow as PlayIcon,
  Refresh as RefreshIcon,
  Download as DownloadIcon,
  Delete as DeleteIcon,
  VideoLibrary as VideoIcon,
  Image as ImageIcon,
  MovieCreation as MovieCreationIcon,
  DriveFileRenameOutline as RenameIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  Settings as SettingsIcon,
  Speed as SpeedIcon,
  Timer as TimerIcon,
  Animation as MotionIcon,
  Upload as UploadIcon,
  Collections as GalleryIcon,
  Close as CloseIcon,
  CheckCircle as CheckCircleIcon,
  Add as AddIcon,
} from "@mui/icons-material";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Preset configurations for easy selection
const QUALITY_PRESETS = {
  fast: {
    label: "⚡ Fast",
    description: "Quick preview (10 steps)",
    num_inference_steps: 10,
    width: 512,
    height: 512,
  },
  standard: {
    label: "✨ Standard",
    description: "Good balance (15 steps)",
    num_inference_steps: 15,
    width: 512,
    height: 512,
  },
  high: {
    label: "🎬 High Quality",
    description: "Better details (25 steps)",
    num_inference_steps: 25,
    width: 512,
    height: 512,
  },
  maximum: {
    label: "🏆 Maximum",
    description: "Best quality (40 steps)",
    num_inference_steps: 40,
    width: 512,
    height: 512,
  },
};

// Duration presets for SVD models
const SVD_DURATION_PRESETS = {
  short: {
    label: "Short",
    description: "~2 seconds",
    duration_frames: 14,
    fps: 7,
  },
  medium: {
    label: "Medium",
    description: "~3 seconds",
    duration_frames: 21,
    fps: 7,
  },
  long: {
    label: "Long",
    description: "~4 seconds",
    duration_frames: 25,
    fps: 6,
  },
};

// Duration presets for CogVideoX models (49 frames max @ 8fps = 6 seconds)
const COGVIDEOX_DURATION_PRESETS = {
  short: {
    label: "Short",
    description: "~3 seconds",
    duration_frames: 24,
    fps: 8,
  },
  medium: {
    label: "Medium",
    description: "~4 seconds",
    duration_frames: 33,
    fps: 8,
  },
  long: {
    label: "Long",
    description: "~6 seconds",
    duration_frames: 49,
    fps: 8,
  },
};

const MOTION_PRESETS = {
  subtle: {
    label: "🌊 Subtle",
    description: "Gentle movement",
    motion_strength: 0.5,
  },
  normal: {
    label: "🎯 Normal",
    description: "Balanced motion",
    motion_strength: 1.0,
  },
  dynamic: {
    label: "💨 Dynamic",
    description: "More movement",
    motion_strength: 1.5,
  },
  intense: {
    label: "🔥 Intense",
    description: "Maximum motion",
    motion_strength: 2.0,
  },
};

// Aspect ratio presets
const ASPECT_RATIO_PRESETS = {
  "16:9": {
    label: "16:9",
    description: "Widescreen",
    ratio: 16 / 9,
  },
  "9:16": {
    label: "9:16",
    description: "Portrait/Vertical",
    ratio: 9 / 16,
  },
  "1:1": {
    label: "1:1",
    description: "Square",
    ratio: 1,
  },
  "4:3": {
    label: "4:3",
    description: "Standard",
    ratio: 4 / 3,
  },
  "3:2": {
    label: "3:2",
    description: "Classic",
    ratio: 3 / 2,
  },
};

// Video size presets (base width, height calculated from aspect ratio)
const VIDEO_SIZE_PRESETS = {
  small: {
    label: "Small",
    description: "512px (faster)",
    baseSize: 512,
  },
  medium: {
    label: "Medium",
    description: "576px",
    baseSize: 576,
  },
  large: {
    label: "Large",
    description: "720px (CogVideoX native)",
    baseSize: 720,
  },
};

const MODEL_OPTIONS = {
  // CogVideoX models (recommended - better quality, longer videos)
  "cogvideox-2b": {
    label: "CogVideoX 2B",
    description: "6s videos, fast (~12GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 30,
  },
  "cogvideox-5b": {
    label: "CogVideoX 5B",
    description: "6s videos, best quality (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
  },
  "cogvideox-5b-i2v": {
    label: "CogVideoX 5B I2V",
    description: "Image-to-video, 6s (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    requiresImage: true,
  },
  // SVD models (legacy)
  svd: {
    label: "SVD (legacy)",
    description: "2s videos, 512x512",
    type: "svd",
    maxFrames: 14,
    resolution: [512, 512],
    defaultSteps: 25,
  },
  "svd-xt": {
    label: "SVD-XT (legacy)",
    description: "3.5s videos, 512x512",
    type: "svd",
    maxFrames: 25,
    resolution: [512, 512],
    defaultSteps: 25,
  },
};

// Helper to check if model is CogVideoX
const isCogVideoXModel = (model) => MODEL_OPTIONS[model]?.type === "cogvideox";

const VideoGeneratorPage = () => {
  const [inputMode, setInputMode] = useState("text");
  const [promptsText, setPromptsText] = useState("");

  // Image selection state
  const [selectedImages, setSelectedImages] = useState([]); // Array of {id, path, thumbnailUrl, name}
  const [dragActive, setDragActive] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef(null);

  // Gallery modal state
  const [galleryOpen, setGalleryOpen] = useState(false);
  const [galleryBatches, setGalleryBatches] = useState([]);
  const [loadingGallery, setLoadingGallery] = useState(false);
  const [selectedBatch, setSelectedBatch] = useState(null);
  const [batchImages, setBatchImages] = useState([]);
  const [loadingBatchImages, setLoadingBatchImages] = useState(false);
  const [gallerySelectedImages, setGallerySelectedImages] = useState(new Set());

  // Ollama status and toggle - with localStorage persistence
  const [ollamaRunning, setOllamaRunning] = useState(false);
  const [videoGenerationEnabled, setVideoGenerationEnabled] = useState(() => {
    const saved = localStorage.getItem('videoGenerationEnabled');
    return saved !== null ? saved === 'true' : false;
  });
  const [checkingOllamaStatus, setCheckingOllamaStatus] = useState(false);
  const [togglingOllama, setTogglingOllama] = useState(false);

  // Preset selections
  const [qualityPreset, setQualityPreset] = useState("standard");
  const [durationPreset, setDurationPreset] = useState("short");
  const [motionPreset, setMotionPreset] = useState("normal");
  const [model, setModel] = useState("cogvideox-2b"); // Default to CogVideoX for better quality
  const [aspectRatio, setAspectRatio] = useState("16:9");
  const [videoSize, setVideoSize] = useState("large");
  const [lowVramMode, setLowVramMode] = useState(() => {
    const saved = localStorage.getItem('lowVramMode');
    // Default to TRUE for 16GB GPUs to prevent CUDA memory errors
    return saved !== null ? saved === 'true' : true;
  });

  // Advanced settings
  const [advancedParams, setAdvancedParams] = useState({
    num_inference_steps: null, // null means "use quality preset", explicit number means "override"
    guidance_scale: 6.0, // CogVideoX default
    generate_frames_only: false,
    frames_per_batch: 1,
    combine_frames: false,
  });

  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [activeBatchId, setActiveBatchId] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [batches, setBatches] = useState([]);
  const pollingRef = useRef(null);
  const ollamaStatusRef = useRef(null);

  // Get duration presets based on selected model
  const durationPresets = useMemo(() => {
    return isCogVideoXModel(model) ? COGVIDEOX_DURATION_PRESETS : SVD_DURATION_PRESETS;
  }, [model]);

  // Calculate video dimensions from aspect ratio and size
  const videoDimensions = useMemo(() => {
    const ratioConfig = ASPECT_RATIO_PRESETS[aspectRatio] || ASPECT_RATIO_PRESETS["16:9"];
    const sizeConfig = VIDEO_SIZE_PRESETS[videoSize] || VIDEO_SIZE_PRESETS.large;
    const baseSize = sizeConfig.baseSize;
    const ratio = ratioConfig.ratio;

    let width, height;
    if (ratio >= 1) {
      // Landscape or square
      width = baseSize;
      height = Math.round(baseSize / ratio);
    } else {
      // Portrait
      height = baseSize;
      width = Math.round(baseSize * ratio);
    }

    // Ensure dimensions are multiples of 8 (required by diffusion models)
    width = Math.round(width / 8) * 8;
    height = Math.round(height / 8) * 8;

    return { width, height };
  }, [aspectRatio, videoSize]);

  // Compute final params from presets
  const computedParams = useMemo(() => {
    const quality = QUALITY_PRESETS[qualityPreset] || QUALITY_PRESETS.standard;
    const currentDurationPresets = isCogVideoXModel(model) ? COGVIDEOX_DURATION_PRESETS : SVD_DURATION_PRESETS;
    const baseDuration = currentDurationPresets[durationPreset] || currentDurationPresets.short;
    const motion = MOTION_PRESETS[motionPreset] || MOTION_PRESETS.normal;
    const modelConfig = MODEL_OPTIONS[model] || {};

    // Start with defaults derived from UI selections
    let effectiveModel = model;
    let effectiveDurationFrames = baseDuration.duration_frames;
    let effectiveFps = baseDuration.fps;
    let width = videoDimensions.width;
    let height = videoDimensions.height;

    // Steps: user's quality preset takes precedence unless explicitly overridden in advanced
    // Priority: advancedParams.num_inference_steps (if explicitly set) > quality preset > model default
    let effectiveSteps;
    if (advancedParams.num_inference_steps !== null && advancedParams.num_inference_steps !== undefined) {
      // User explicitly set steps in advanced settings
      effectiveSteps = advancedParams.num_inference_steps;
    } else if (quality.num_inference_steps) {
      // Use quality preset's steps (this is what user selected in dropdown)
      effectiveSteps = quality.num_inference_steps;
    } else {
      // Fall back to model default only if quality preset doesn't specify
      effectiveSteps = modelConfig.defaultSteps || 25;
    }

    // Low VRAM safe preset for CogVideoX on 16GB GPUs
    // Very aggressive settings based on successful test: 8 frames, 15 steps, 480x320
    if (lowVramMode && isCogVideoXModel(model)) {
      // Force to 2B (lighter model)
      effectiveModel = "cogvideox-2b";

      // Aggressively clamp frames - tested working with 8 frames
      if (effectiveDurationFrames > 12) {
        effectiveDurationFrames = 12;
      }

      // Aggressive resolution reduction based on successful 480x320 test
      // Max 480px on longest side to ensure memory fits
      const maxSafeSide = 480;
      const longestSide = Math.max(width, height);
      if (longestSide > maxSafeSide) {
        const scale = maxSafeSide / longestSide;
        width = Math.round((width * scale) / 8) * 8;
        height = Math.round((height * scale) / 8) * 8;
      }
      // Ensure minimum dimensions are met (CogVideoX needs at least 256x256)
      if (width < 256) width = 256;
      if (height < 256) height = 256;
      // Ensure dimensions are multiples of 8
      width = Math.round(width / 8) * 8;
      height = Math.round(height / 8) * 8;

      // Aggressive step reduction - tested working with 15 steps
      if (effectiveSteps > 15) {
        effectiveSteps = 15;
      }
    }

    // Build final params - don't spread quality since it has SVD-specific width/height
    // that shouldn't override our calculated videoDimensions for CogVideoX
    return {
      model: effectiveModel,
      duration_frames: effectiveDurationFrames,
      fps: effectiveFps,
      motion_strength: motion.motion_strength,
      // Use calculated (and possibly clamped) dimensions from videoDimensions
      width,
      height,
      // Steps from quality preset (or advanced override)
      num_inference_steps: effectiveSteps,
      // Advanced params (but don't override steps if we computed it above)
      guidance_scale: advancedParams.guidance_scale,
      generate_frames_only: advancedParams.generate_frames_only,
      // For Low VRAM mode, use frames_per_batch=1 to minimize memory usage
      frames_per_batch: lowVramMode && isCogVideoXModel(model) ? 1 : advancedParams.frames_per_batch,
      combine_frames: advancedParams.combine_frames,
    };
  }, [qualityPreset, durationPreset, motionPreset, model, advancedParams, videoDimensions, lowVramMode]);

  const parsedPrompts = useMemo(() => {
    return promptsText
      .split("\n")
      .map((p) => p.trim())
      .filter(Boolean);
  }, [promptsText]);

  const stopPolling = () => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  };

  const startPollingStatus = (batchId) => {
    stopPolling();
    pollingRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/batch-video/status/${batchId}`);
        if (res.ok) {
          const data = await res.json();
          if (data.success) {
            setBatchStatus(data.data);
            if (
              data.data.status === "completed" ||
              data.data.status === "error" ||
              data.data.status === "cancelled"
            ) {
              stopPolling();
              await fetchBatches();
            }
          }
        }
      } catch (e) {
        // ignore polling errors
      }
    }, 2000);
  };

  // Check Ollama status - only updates ollamaRunning, NOT videoGenerationEnabled
  // videoGenerationEnabled is controlled by user interaction only
  const checkOllamaStatus = useCallback(async (updateToggle = false) => {
    setCheckingOllamaStatus(true);
    try {
      const res = await fetch(`${API_BASE}/gpu/status`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          const isRunning = data.data.ollama_running || false;
          const isAvailable = data.data.available || false;
          setOllamaRunning(isRunning);
          // Only update toggle state if explicitly requested (e.g., on initial load)
          // Otherwise, respect user's manual toggle state
          if (updateToggle) {
            const shouldEnable = !isRunning && isAvailable;
            setVideoGenerationEnabled(shouldEnable);
            localStorage.setItem('videoGenerationEnabled', shouldEnable.toString());
          }
        }
      }
    } catch (e) {
      console.error("Failed to check Ollama status:", e);
    } finally {
      setCheckingOllamaStatus(false);
    }
  }, []);

  // Toggle Video Generation (which controls Ollama)
  const handleOllamaToggle = useCallback(async (event) => {
    const shouldEnableVideoGen = event.target.checked;
    setTogglingOllama(true);
    
    // Immediately update local state and persist to localStorage
    setVideoGenerationEnabled(shouldEnableVideoGen);
    localStorage.setItem('videoGenerationEnabled', shouldEnableVideoGen.toString());
    
    try {
      if (shouldEnableVideoGen) {
        // User wants to enable video generation - stop Ollama first
        // Check if video generation is currently active
        const statusRes = await fetch(`${API_BASE}/gpu/status`);
        if (statusRes.ok) {
          const statusData = await statusRes.json();
          if (statusData.success && !statusData.data.available) {
            setError("Cannot enable video generation - another operation is currently using the GPU. Please wait for it to complete.");
            setTogglingOllama(false);
            // Reset toggle
            setVideoGenerationEnabled(false);
            localStorage.setItem('videoGenerationEnabled', 'false');
            return;
          }
        }
        
        // Stop Ollama to enable video generation
        const res = await fetch(`${API_BASE}/gpu/ollama/stop`, {
          method: "POST",
        });
        
        if (!res.ok) {
          const errorData = await res.json().catch(() => ({}));
          throw new Error(errorData.error || "Failed to stop Ollama");
        }
        
        const data = await res.json();
        if (!data.success) {
          throw new Error(data.error || "Failed to stop Ollama");
        }
        
        setSuccess("Ollama stopped successfully. Video generation is now enabled.");
      } else {
        // User wants to disable video generation - start Ollama
        // Check if video generation is currently active
        const statusRes = await fetch(`${API_BASE}/gpu/status`);
        if (statusRes.ok) {
          const statusData = await statusRes.json();
          if (statusData.success && !statusData.data.available) {
            setError("Cannot start Ollama - video generation is currently active. Please wait for it to complete.");
            setTogglingOllama(false);
            // Reset toggle
            setVideoGenerationEnabled(true);
            localStorage.setItem('videoGenerationEnabled', 'true');
            return;
          }
        }
        
        // Start Ollama
        const res = await fetch(`${API_BASE}/gpu/ollama/start`, {
          method: "POST",
        });
        
        if (!res.ok) {
          const errorData = await res.json().catch(() => ({}));
          throw new Error(errorData.error || "Failed to start Ollama");
        }
        
        const data = await res.json();
        if (!data.success) {
          throw new Error(data.error || "Failed to start Ollama");
        }
        
        setSuccess("Ollama started successfully. Video generation is now disabled.");
      }
      
      // Refresh Ollama status after a short delay (but don't update toggle state)
      setTimeout(() => {
        checkOllamaStatus(false);
      }, 1000);
      
    } catch (e) {
      setError(`Failed to ${shouldEnableVideoGen ? 'stop' : 'start'} Ollama: ${e.message}`);
      // Reset toggle on error
      setVideoGenerationEnabled(!shouldEnableVideoGen);
      localStorage.setItem('videoGenerationEnabled', (!shouldEnableVideoGen).toString());
    } finally {
      setTogglingOllama(false);
    }
  }, [checkOllamaStatus]);

  useEffect(() => {
    fetchBatches();
    // On initial load, update toggle state based on server status
    checkOllamaStatus(true);
    
    // Poll Ollama status every 5 seconds (but don't update toggle state)
    ollamaStatusRef.current = setInterval(() => {
      checkOllamaStatus(false);
    }, 5000);

    return () => {
      stopPolling();
      if (ollamaStatusRef.current) {
        clearInterval(ollamaStatusRef.current);
      }
    };
  }, [checkOllamaStatus]);

  const fetchBatches = async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/list`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          setBatches(data.data.batches || []);
        }
      }
    } catch (e) {
      // ignore
    }
  };

  // File upload handling
  const handleFileUpload = useCallback(async (files) => {
    if (!files || files.length === 0) return;

    setIsUploading(true);
    try {
      const formData = new FormData();
      files.forEach(file => {
        formData.append('files', file);
      });

      const response = await fetch(`${API_BASE}/batch-image/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || `Upload failed: HTTP ${response.status}`);
      }

      const data = await response.json();

      if (data.success && data.data.batch_id) {
        // Fetch the uploaded images from the new batch
        const statusRes = await fetch(`${API_BASE}/batch-image/status/${data.data.batch_id}?include_results=true`);
        if (statusRes.ok) {
          const statusData = await statusRes.json();
          if (statusData.success && statusData.data.results) {
            const newImages = statusData.data.results
              .filter(r => r.success && r.image_path)
              .map(r => {
                const getFilename = (path) => {
                  if (!path) return null;
                  const parts = path.replace(/\\/g, '/').split('/');
                  return parts[parts.length - 1];
                };
                const imageFilename = getFilename(r.image_path);
                return {
                  id: `${data.data.batch_id}_${imageFilename}`,
                  path: r.image_path,
                  thumbnailUrl: r.thumbnail_path
                    ? `${API_BASE}/batch-image/image/${data.data.batch_id}/${encodeURIComponent(getFilename(r.thumbnail_path))}?thumbnail=true`
                    : `${API_BASE}/batch-image/image/${data.data.batch_id}/${encodeURIComponent(imageFilename)}`,
                  name: imageFilename,
                  batchId: data.data.batch_id,
                };
              });
            setSelectedImages(prev => [...prev, ...newImages]);
          }
        }
        setSuccess(`Uploaded ${files.length} image(s) successfully`);
      }
    } catch (err) {
      setError(`Failed to upload files: ${err.message}`);
    } finally {
      setIsUploading(false);
    }
  }, []);

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

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileUpload(Array.from(e.dataTransfer.files));
    }
  }, [handleFileUpload]);

  const removeSelectedImage = useCallback((imageId) => {
    setSelectedImages(prev => prev.filter(img => img.id !== imageId));
  }, []);

  // Gallery functions
  const fetchGalleryBatches = useCallback(async () => {
    setLoadingGallery(true);
    try {
      const res = await fetch(`${API_BASE}/batch-image/list`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          setGalleryBatches(data.data.batches || []);
        }
      }
    } catch (e) {
      console.error("Failed to load gallery batches:", e);
    } finally {
      setLoadingGallery(false);
    }
  }, []);

  const fetchBatchImages = useCallback(async (batchId) => {
    setLoadingBatchImages(true);
    setBatchImages([]);
    try {
      const res = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);
      if (res.ok) {
        const data = await res.json();
        if (data.success && data.data.results) {
          const images = data.data.results
            .filter(r => r.success && r.image_path)
            .map(r => {
              const getFilename = (path) => {
                if (!path) return null;
                const parts = path.replace(/\\/g, '/').split('/');
                return parts[parts.length - 1];
              };
              const imageFilename = getFilename(r.image_path);
              const thumbnailFilename = r.thumbnail_path ? getFilename(r.thumbnail_path) : null;
              return {
                id: `${batchId}_${imageFilename}`,
                path: r.image_path,
                thumbnailUrl: thumbnailFilename
                  ? `${API_BASE}/batch-image/image/${batchId}/${encodeURIComponent(thumbnailFilename)}?thumbnail=true`
                  : `${API_BASE}/batch-image/image/${batchId}/${encodeURIComponent(imageFilename)}`,
                fullUrl: `${API_BASE}/batch-image/image/${batchId}/${encodeURIComponent(imageFilename)}`,
                name: imageFilename,
                batchId: batchId,
              };
            });
          setBatchImages(images);
        }
      }
    } catch (e) {
      console.error("Failed to load batch images:", e);
    } finally {
      setLoadingBatchImages(false);
    }
  }, []);

  const openGallery = useCallback(() => {
    setGalleryOpen(true);
    setSelectedBatch(null);
    setBatchImages([]);
    setGallerySelectedImages(new Set());
    fetchGalleryBatches();
  }, [fetchGalleryBatches]);

  const handleBatchClick = useCallback((batch) => {
    setSelectedBatch(batch);
    fetchBatchImages(batch.batch_id);
  }, [fetchBatchImages]);

  const toggleGalleryImageSelection = useCallback((imageId) => {
    setGallerySelectedImages(prev => {
      const newSet = new Set(prev);
      if (newSet.has(imageId)) {
        newSet.delete(imageId);
      } else {
        newSet.add(imageId);
      }
      return newSet;
    });
  }, []);

  const confirmGallerySelection = useCallback(() => {
    const newImages = batchImages.filter(img => gallerySelectedImages.has(img.id));
    // Avoid duplicates
    setSelectedImages(prev => {
      const existingIds = new Set(prev.map(img => img.id));
      const uniqueNew = newImages.filter(img => !existingIds.has(img.id));
      return [...prev, ...uniqueNew];
    });
    setGalleryOpen(false);
  }, [batchImages, gallerySelectedImages]);

  const handleGenerate = async () => {
    setError("");
    setSuccess("");
    setBatchStatus(null);

    // Check if video generation is enabled
    if (!videoGenerationEnabled) {
      setError("Video generation is disabled. Please turn off Ollama first using the toggle above.");
      return;
    }

    // Double-check that Ollama is actually stopped before generation
    try {
      const statusRes = await fetch(`${API_BASE}/gpu/status`);
      if (statusRes.ok) {
        const statusData = await statusRes.json();
        if (statusData.success && statusData.data.ollama_running) {
          setError("Ollama is still running. Please wait for it to stop, or manually stop it using the toggle above.");
          return;
        }
        if (statusData.success && !statusData.data.available) {
          setError("GPU is currently in use by another operation. Please wait for it to complete.");
          return;
        }
      }
    } catch (e) {
      console.warn("Failed to check GPU status before generation:", e);
      // Continue anyway, backend will handle the check
    }

    if (inputMode === "text" && parsedPrompts.length === 0) {
      setError("Please enter at least one prompt.");
      return;
    }
    if (inputMode === "image" && selectedImages.length === 0) {
      setError("Please select or upload at least one image.");
      return;
    }

    setIsGenerating(true);
    try {
      const imagePaths = selectedImages.map(img => img.path);
      const body =
        inputMode === "text"
          ? { prompts: parsedPrompts, ...computedParams }
          : { image_paths: imagePaths, ...computedParams };

      const url =
        inputMode === "text"
          ? `${API_BASE}/batch-video/generate/text`
          : `${API_BASE}/batch-video/generate/image`;

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        setError(errorData.error || `Failed to start generation: HTTP ${res.status}`);
        return;
      }

      const data = await res.json();
      if (!data.success) {
        setError(data.error || "Failed to start generation");
        return;
      }

      const batchId = data.data.batch_id;
      setActiveBatchId(batchId);
      setSuccess(`Generation started! This may take 1-2 minutes...`);
      startPollingStatus(batchId);
      await fetchBatches();
    } catch (e) {
      setError(`Generation failed: ${e.message}`);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleDownloadBatch = async (batchId) => {
    window.open(`${API_BASE}/batch-video/download/${batchId}`, "_blank");
  };

  const handleCombineFrames = async (batchId, itemId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/combine-frames/${batchId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fps: computedParams.fps || 7, item_id: itemId }),
      });
      if (res.ok) {
        await fetchBatches();
        if (activeBatchId === batchId) {
          startPollingStatus(batchId);
        }
      }
    } catch (e) {
      // ignore
    }
  };

  const handleDeleteBatch = async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/delete/${batchId}`, {
        method: "DELETE",
      });
      if (res.ok) {
        await fetchBatches();
        if (activeBatchId === batchId) {
          setBatchStatus(null);
          stopPolling();
        }
      }
    } catch (e) {
      // ignore
    }
  };

  const handleDeleteVideo = async (batchId, videoName) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/video/${batchId}/${encodePathSegments(videoName)}`, {
        method: "DELETE",
      });
      if (res.ok) {
        if (activeBatchId === batchId) {
          startPollingStatus(batchId);
        }
        await fetchBatches();
      }
    } catch (e) {
      // ignore
    }
  };

  const handleRenameVideo = async (batchId, videoName) => {
    const newName = window.prompt("Enter new video filename (include extension)", videoName);
    if (!newName) return;
    try {
      const res = await fetch(`${API_BASE}/batch-video/video/${batchId}/${encodePathSegments(videoName)}/rename`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: newName }),
      });
      if (res.ok) {
        if (activeBatchId === batchId) {
          startPollingStatus(batchId);
        }
        await fetchBatches();
      }
    } catch (e) {
      // ignore
    }
  };

  const currentResults = useMemo(() => {
    if (!batchStatus || !batchStatus.results) return [];
    return batchStatus.results;
  }, [batchStatus]);

  // Determine if controls should be disabled
  // Controls are disabled when Ollama is running OR when video generation is not enabled
  const controlsDisabled = ollamaRunning || !videoGenerationEnabled || isGenerating;

  return (
    <PageLayout title="Video Generation" variant="standard">

      {/* Error/Success Messages */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError('')}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 3 }} onClose={() => setSuccess('')}>
          {success}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* Settings Section - Left Side */}
        <Grid item xs={12} lg={6}>
          <Card sx={{ 
            height: 'fit-content',
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  mb: 3,
                  color: 'text.primary'
                }}
              >
                Generation Settings
              </Typography>

              {/* Ollama Status & Low VRAM Mode */}
              <Box sx={{ 
                mb: 3, 
                p: 2, 
                bgcolor: ollamaRunning ? 'warning.50' : 'info.50', 
                borderRadius: 2, 
                border: '1px solid', 
                borderColor: ollamaRunning ? 'warning.200' : 'info.200' 
              }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, color: ollamaRunning ? 'warning.main' : 'info.main' }}>
                    Ollama Status
                  </Typography>
                  {checkingOllamaStatus && <CircularProgress size={16} />}
                </Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                  <Chip 
                    label={ollamaRunning ? "Running" : "Stopped"} 
                    color={ollamaRunning ? "warning" : "default"}
                    size="small"
                  />
                  <Typography variant="caption" color="text.secondary">
                    {ollamaRunning 
                      ? "Video generation disabled while Ollama is active" 
                      : "Video generation available"}
                  </Typography>
                </Box>
                <FormControlLabel
                  control={
                    <Switch
                      checked={videoGenerationEnabled}
                      disabled={checkingOllamaStatus || togglingOllama || isGenerating}
                      onChange={handleOllamaToggle}
                      color="primary"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">
                        Enable Video Generation (stops Ollama)
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {togglingOllama 
                          ? (videoGenerationEnabled ? "Starting Ollama..." : "Stopping Ollama...")
                          : ollamaRunning 
                            ? "Click to stop Ollama and enable video generation" 
                            : "Click to start Ollama (will disable video generation)"}
                      </Typography>
                    </Box>
                  }
                />

                {/* Low VRAM safe preset for CogVideoX */}
                <FormControlLabel
                  control={
                    <Switch
                      checked={lowVramMode}
                      onChange={(e) => {
                        const newValue = e.target.checked;
                        setLowVramMode(newValue);
                        localStorage.setItem('lowVramMode', newValue.toString());
                      }}
                      color="primary"
                      size="small"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">
                        Low VRAM Safe Preset (CogVideoX)
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Recommended for 16GB GPUs: forces CogVideoX 2B, max 12 frames, reduced resolution (480px max), max 15 steps, and single-frame batching to minimize memory usage.
                      </Typography>
                    </Box>
                  }
                  sx={{ mt: 1 }}
                />
              </Box>

              {/* Main Generation Form */}
              <Box sx={{ opacity: controlsDisabled ? 0.5 : 1, pointerEvents: controlsDisabled ? 'none' : 'auto' }}>
        <Stack spacing={3}>
          {/* Input Mode Toggle */}
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">Create Video</Typography>
            <ToggleButtonGroup
              value={inputMode}
              exclusive
              onChange={(e, v) => v && setInputMode(v)}
              size="small"
            >
              <ToggleButton value="text">
                <Tooltip title="Text-to-Video: Describe what you want">
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <VideoIcon fontSize="small" />
                    <Typography variant="caption">Text</Typography>
                  </Box>
                </Tooltip>
              </ToggleButton>
              <ToggleButton value="image">
                <Tooltip title="Image-to-Video: Animate an existing image">
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <ImageIcon fontSize="small" />
                    <Typography variant="caption">Image</Typography>
                  </Box>
                </Tooltip>
              </ToggleButton>
            </ToggleButtonGroup>
          </Stack>

          {/* Prompt/Image Input */}
          {inputMode === "text" ? (
            <TextField
              label="What do you want to see? (one prompt per line)"
              multiline
              minRows={3}
              maxRows={6}
              value={promptsText}
              onChange={(e) => setPromptsText(e.target.value)}
              placeholder="A majestic eagle soaring over mountains at sunset&#10;A playful cat chasing butterflies in a garden"
              fullWidth
              variant="outlined"
            />
          ) : (
            <Box>
              {/* Image Upload Area */}
              <Box
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                sx={{
                  border: dragActive ? '2px dashed' : '2px dashed',
                  borderColor: dragActive ? 'primary.main' : 'grey.300',
                  borderRadius: 2,
                  p: 3,
                  textAlign: 'center',
                  bgcolor: dragActive ? 'action.hover' : 'transparent',
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  '&:hover': {
                    borderColor: 'primary.light',
                    bgcolor: 'action.hover',
                  },
                }}
                onClick={() => fileInputRef.current?.click()}
              >
                {isUploading ? (
                  <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                    <CircularProgress size={40} />
                    <Typography variant="body2" color="text.secondary">
                      Uploading...
                    </Typography>
                  </Box>
                ) : (
                  <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                    <UploadIcon sx={{ fontSize: 48, color: 'grey.400' }} />
                    <Typography variant="body1" color="text.secondary">
                      Drag & drop images here, or click to upload
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      Supports JPG, PNG, GIF, WebP
                    </Typography>
                  </Box>
                )}
              </Box>

              {/* Hidden file input */}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept="image/*"
                style={{ display: 'none' }}
                onChange={(e) => {
                  if (e.target.files && e.target.files.length > 0) {
                    handleFileUpload(Array.from(e.target.files));
                    e.target.value = '';
                  }
                }}
              />

              {/* Gallery Selection Button */}
              <Box sx={{ mt: 2, display: 'flex', justifyContent: 'center' }}>
                <Button
                  variant="outlined"
                  startIcon={<GalleryIcon />}
                  onClick={openGallery}
                  sx={{ textTransform: 'none' }}
                >
                  Select from Image Gallery
                </Button>
              </Box>

              {/* Selected Images Preview */}
              {selectedImages.length > 0 && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>
                    Selected Images ({selectedImages.length})
                  </Typography>
                  <Grid container spacing={1}>
                    {selectedImages.map((img) => (
                      <Grid item key={img.id}>
                        <Box
                          sx={{
                            position: 'relative',
                            width: 80,
                            height: 80,
                            borderRadius: 1,
                            overflow: 'hidden',
                            border: '1px solid',
                            borderColor: 'grey.300',
                          }}
                        >
                          <Box
                            component="img"
                            src={img.thumbnailUrl}
                            alt={img.name}
                            sx={{
                              width: '100%',
                              height: '100%',
                              objectFit: 'cover',
                            }}
                            onError={(e) => {
                              e.target.onerror = null;
                              e.target.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect fill="%23f0f0f0" width="80" height="80"/><text x="40" y="45" text-anchor="middle" fill="%23999" font-size="10">Error</text></svg>';
                            }}
                          />
                          <IconButton
                            size="small"
                            onClick={() => removeSelectedImage(img.id)}
                            sx={{
                              position: 'absolute',
                              top: 2,
                              right: 2,
                              bgcolor: 'rgba(0,0,0,0.6)',
                              color: 'white',
                              p: 0.25,
                              '&:hover': {
                                bgcolor: 'rgba(0,0,0,0.8)',
                              },
                            }}
                          >
                            <CloseIcon sx={{ fontSize: 14 }} />
                          </IconButton>
                        </Box>
                      </Grid>
                    ))}
                    {/* Add more button */}
                    <Grid item>
                      <Box
                        onClick={() => fileInputRef.current?.click()}
                        sx={{
                          width: 80,
                          height: 80,
                          borderRadius: 1,
                          border: '2px dashed',
                          borderColor: 'grey.300',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          cursor: 'pointer',
                          transition: 'all 0.2s ease',
                          '&:hover': {
                            borderColor: 'primary.main',
                            bgcolor: 'action.hover',
                          },
                        }}
                      >
                        <AddIcon color="action" />
                      </Box>
                    </Grid>
                  </Grid>
                </Box>
              )}
            </Box>
          )}

          <Divider sx={{ my: 3 }} />

          {/* Video Settings Section */}
          <Box sx={{ mb: 3 }}>
            <Typography 
              variant="subtitle1" 
              sx={{ 
                display: "flex", 
                alignItems: "center", 
                gap: 1,
                mb: 2.5,
                fontWeight: 600
              }}
            >
              <SettingsIcon fontSize="small" /> Video Settings
            </Typography>

            {/* Primary Settings Row */}
            <Grid container spacing={2} sx={{ mb: 2 }}>
              {/* Model Selection */}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>Model</InputLabel>
                  <Select
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    label="Model"
                  >
                    {Object.entries(MODEL_OPTIONS).map(([key, opt]) => (
                      <MenuItem key={key} value={key}>
                        <Box>
                          <Typography variant="body2">{opt.label}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {opt.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>

              {/* Quality Preset */}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <SpeedIcon fontSize="small" /> Quality
                    </Box>
                  </InputLabel>
                  <Select
                    value={qualityPreset}
                    onChange={(e) => setQualityPreset(e.target.value)}
                    label="Quality"
                  >
                    {Object.entries(QUALITY_PRESETS).map(([key, preset]) => (
                      <MenuItem key={key} value={key}>
                        <Box>
                          <Typography variant="body2">{preset.label}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {preset.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>

              {/* Duration Preset */}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <TimerIcon fontSize="small" /> Duration
                    </Box>
                  </InputLabel>
                  <Select
                    value={durationPreset}
                    onChange={(e) => setDurationPreset(e.target.value)}
                    label="Duration"
                  >
                    {Object.entries(durationPresets).map(([key, preset]) => (
                      <MenuItem key={key} value={key}>
                        <Box>
                          <Typography variant="body2">{preset.label}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {preset.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>
            </Grid>

            {/* Video Dimensions Row */}
            <Grid container spacing={2} sx={{ mb: 2 }}>
              {/* Aspect Ratio */}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>Aspect Ratio</InputLabel>
                  <Select
                    value={aspectRatio}
                    onChange={(e) => setAspectRatio(e.target.value)}
                    label="Aspect Ratio"
                  >
                    {Object.entries(ASPECT_RATIO_PRESETS).map(([key, preset]) => (
                      <MenuItem key={key} value={key}>
                        <Box>
                          <Typography variant="body2">{preset.label}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {preset.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>

              {/* Video Size */}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>Video Size</InputLabel>
                  <Select
                    value={videoSize}
                    onChange={(e) => setVideoSize(e.target.value)}
                    label="Video Size"
                  >
                    {Object.entries(VIDEO_SIZE_PRESETS).map(([key, preset]) => (
                      <MenuItem key={key} value={key}>
                        <Box>
                          <Typography variant="body2">{preset.label}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {preset.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>

              {/* Motion Preset - only for SVD models */}
              {!isCogVideoXModel(model) && (
                <Grid item xs={12} sm={6} md={4}>
                  <FormControl fullWidth size="small">
                    <InputLabel>
                      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                        <MotionIcon fontSize="small" /> Motion
                      </Box>
                    </InputLabel>
                    <Select
                      value={motionPreset}
                      onChange={(e) => setMotionPreset(e.target.value)}
                      label="Motion"
                    >
                      {Object.entries(MOTION_PRESETS).map(([key, preset]) => (
                        <MenuItem key={key} value={key}>
                          <Box>
                            <Typography variant="body2">{preset.label}</Typography>
                            <Typography variant="caption" color="text.secondary">
                              {preset.description}
                            </Typography>
                          </Box>
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                </Grid>
              )}
            </Grid>

            {/* Advanced Parameters Row */}
            <Box sx={{ mt: 2.5, mb: 2 }}>
              <Typography variant="caption" color="text.secondary" sx={{ mb: 1.5, display: "block", fontWeight: 500 }}>
                Advanced Parameters
              </Typography>
              <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
                <TextField
                  size="small"
                  label="Guidance Scale"
                  type="number"
                  inputProps={{ step: 0.5, min: 1, max: 20 }}
                  value={advancedParams.guidance_scale}
                  onChange={(e) =>
                    setAdvancedParams({
                      ...advancedParams,
                      guidance_scale: Number(e.target.value),
                    })
                  }
                  helperText="Higher = more prompt adherence"
                  sx={{
                    width: { xs: '100%', sm: '280px' },
                    '& .MuiFormHelperText-root': {
                      mt: 0.5,
                    },
                  }}
                />
              </Box>
              {/* Low VRAM Mode Active Warning */}
              {lowVramMode && isCogVideoXModel(model) && (
                <Alert 
                  severity="info" 
                  sx={{ 
                    mt: 1.5,
                    '& .MuiAlert-message': {
                      py: 0.5,
                    },
                  }}
                >
                  Low VRAM mode is active: Using CogVideoX 2B, max {computedParams.duration_frames} frames, max {computedParams.num_inference_steps} steps, and reduced resolution to minimize memory usage.
                </Alert>
              )}
            </Box>
          </Box>

          {/* Preview of computed settings */}
          <Box sx={{ mb: 2 }}>
            <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block", fontWeight: 500 }}>
              Computed Settings
            </Typography>
            <Box sx={{ 
              display: "flex", 
              gap: 1, 
              flexWrap: "wrap", 
              alignItems: "center",
              p: 1.5,
              borderRadius: 1,
              bgcolor: 'action.hover',
            }}>
              {isCogVideoXModel(model) ? (
                <Chip
                  size="small"
                  color="primary"
                  label="CogVideoX"
                  sx={{ fontWeight: 600 }}
                />
              ) : (
                <Chip
                  size="small"
                  color="default"
                  label="SVD"
                  sx={{ fontWeight: 500 }}
                />
              )}
              <Chip
                size="small"
                variant="outlined"
                label={`${computedParams.num_inference_steps} steps`}
              />
              <Chip
                size="small"
                variant="outlined"
                label={`${computedParams.duration_frames} frames`}
              />
              <Chip
                size="small"
                variant="outlined"
                label={`${computedParams.fps} FPS`}
              />
              <Chip
                size="small"
                variant="outlined"
                label={`~${(computedParams.duration_frames / computedParams.fps).toFixed(1)}s video`}
              />
              <Chip
                size="small"
                variant="outlined"
                label={`${computedParams.width}x${computedParams.height}`}
              />
              {!isCogVideoXModel(model) && (
                <Chip
                  size="small"
                  variant="outlined"
                  label={`Motion: ${computedParams.motion_strength}x`}
                />
              )}
            </Box>
          </Box>

          {/* Model-specific warnings */}
          {MODEL_OPTIONS[model]?.requiresImage && inputMode === "text" && (
            <Alert 
              severity="warning" 
              sx={{ 
                mb: 2,
                '& .MuiAlert-message': {
                  py: 0.5,
                },
              }}
            >
              {MODEL_OPTIONS[model]?.label} requires an image input. Switch to Image mode or select a different model.
            </Alert>
          )}

          <Divider />

          {/* Generate Button */}
          <Button
            variant="contained"
            size="large"
            startIcon={isGenerating ? null : <PlayIcon />}
            onClick={handleGenerate}
            disabled={controlsDisabled || isGenerating || (inputMode === "text" ? parsedPrompts.length === 0 : selectedImages.length === 0)}
            sx={{ py: 1.5 }}
            fullWidth
          >
            {isGenerating ? "Generating..." : "Generate Video"}
          </Button>

          {isGenerating && <LinearProgress />}
            </Stack>
          </Box>
          </CardContent>
        </Card>
        </Grid>

        {/* Status Section - Right Side */}
        <Grid item xs={12} lg={6}>
          {/* Active batch status */}
          {batchStatus ? (
            <Card sx={{ 
              mb: 3,
              boxShadow: 2,
              borderRadius: 2
            }}>
              <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                <Typography 
                  variant="h6" 
                  sx={{ 
                    fontWeight: 600,
                    mb: 2,
                    color: 'text.primary'
                  }}
                >
                  Current Progress
                </Typography>

                <Box sx={{ mb: 2 }}>
                  <Typography variant="body2" color="text.secondary">
                    Batch ID: {batchStatus.batch_id}
                  </Typography>
                  <Chip
                    label={batchStatus.status.toUpperCase()}
                    color={batchStatus.status === 'running' ? 'primary' :
                           batchStatus.status === 'completed' ? 'success' : 
                           batchStatus.status === 'error' ? 'error' : 'default'}
                    size="small"
                    sx={{ mt: 1 }}
                  />
                </Box>

                <Box sx={{ mb: 2 }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                    <Typography variant="body2">
                      Progress: {batchStatus.completed_videos || 0}/{batchStatus.total_videos || 0}
                    </Typography>
                    <Typography variant="body2">
                      {Math.round(((batchStatus.completed_videos || 0) / (batchStatus.total_videos || 1)) * 100)}%
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={((batchStatus.completed_videos || 0) / (batchStatus.total_videos || 1)) * 100}
                  />
                </Box>

                {batchStatus.status === 'completed' && (
                  <Button
                    startIcon={<DownloadIcon />}
                    variant="contained"
                    fullWidth
                    onClick={() => handleDownloadBatch(batchStatus.batch_id)}
                    sx={{ mb: 2 }}
                  >
                    Download All Videos
                  </Button>
                )}

                <Divider sx={{ my: 2 }} />

                <Grid container spacing={2}>
                  {currentResults.map((res) => (
                    <Grid item xs={12} sm={6} key={res.item_id}>
                      <Card variant="outlined">
                        <CardContent sx={{ pb: 1 }}>
                          {res.thumbnail_path ? (
                            <Box
                              component="img"
                              src={`${API_BASE}/batch-video/video/${batchStatus.batch_id}/${encodePathSegments(
                                PathFromUrl(res.thumbnail_path)
                              )}`}
                              alt="thumb"
                              sx={{
                                width: "100%",
                                height: 140,
                                objectFit: "cover",
                                borderRadius: 1,
                                mb: 1,
                              }}
                            />
                          ) : (
                            <Box
                              sx={{
                                width: "100%",
                                height: 140,
                                bgcolor: "grey.100",
                                borderRadius: 1,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                mb: 1,
                              }}
                            >
                              <VideoIcon color="action" sx={{ fontSize: 40 }} />
                            </Box>
                          )}
                          <Stack direction="row" spacing={0.5} alignItems="center">
                            <Chip
                              label={res.success ? "Ready" : "Error"}
                              color={res.success ? "success" : "error"}
                              size="small"
                            />
                            {res.frame_paths?.length > 0 && (
                              <Chip label={`${res.frame_paths.length}f`} size="small" variant="outlined" />
                            )}
                          </Stack>
                          {res.error && (
                            <Typography variant="caption" color="error" display="block" sx={{ mt: 0.5 }}>
                              {res.error}
                            </Typography>
                          )}
                        </CardContent>
                        <CardActions sx={{ pt: 0 }}>
                          {res.video_path && (
                            <Button
                              size="small"
                              variant="contained"
                              onClick={() =>
                                window.open(
                                  `${API_BASE}/batch-video/video/${batchStatus.batch_id}/${encodePathSegments(
                                    PathFromUrl(res.video_path)
                                  )}`,
                                  "_blank"
                                )
                              }
                            >
                              Play
                            </Button>
                          )}
                          {res.video_path && (
                            <>
                              <IconButton
                                size="small"
                                onClick={() => handleRenameVideo(batchStatus.batch_id, PathFromUrl(res.video_path))}
                              >
                                <RenameIcon fontSize="small" />
                              </IconButton>
                              <IconButton
                                size="small"
                                onClick={() => handleDeleteVideo(batchStatus.batch_id, PathFromUrl(res.video_path))}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </>
                          )}
                          {!res.video_path && res.frame_paths?.length > 0 && (
                            <Button
                              size="small"
                              onClick={() => handleCombineFrames(batchStatus.batch_id, res.item_id)}
                            >
                              Combine Frames
                            </Button>
                          )}
                        </CardActions>
                      </Card>
                    </Grid>
                  ))}
                </Grid>
              </CardContent>
            </Card>
          ) : (
            <Card sx={{ 
              mb: 3,
              boxShadow: 2,
              borderRadius: 2
            }}>
              <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                <Typography 
                  variant="h6" 
                  sx={{ 
                    fontWeight: 600,
                    mb: 2,
                    color: 'text.primary'
                  }}
                >
                  Generation Status
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>
                  No active generation. Start a video generation above to see progress here.
                </Typography>
              </CardContent>
            </Card>
          )}

          {/* Batch History */}
          <Card sx={{ 
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  History
                </Typography>
                <IconButton size="small" onClick={fetchBatches}>
                  <RefreshIcon />
                </IconButton>
              </Stack>
              <Grid container spacing={2}>
                {batches.map((b) => (
                  <Grid item xs={12} sm={6} key={b.batch_id}>
                    <Card variant="outlined">
                      <CardContent sx={{ pb: 1 }}>
                        <Typography variant="subtitle2" noWrap>
                          {b.display_name || b.batch_id}
                        </Typography>
                        <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
                          <Chip
                            label={b.status}
                            size="small"
                            color={
                              b.status === "completed"
                                ? "success"
                                : b.status === "error"
                                ? "error"
                                : "default"
                            }
                          />
                          <Chip
                            label={`${b.completed_videos ?? 0}/${b.total_videos ?? 0}`}
                            size="small"
                            variant="outlined"
                          />
                        </Stack>
                      </CardContent>
                      <CardActions sx={{ pt: 0 }}>
                        <Button
                          size="small"
                          onClick={() => {
                            setActiveBatchId(b.batch_id);
                            startPollingStatus(b.batch_id);
                          }}
                        >
                          View
                        </Button>
                        <IconButton size="small" onClick={() => handleDownloadBatch(b.batch_id)}>
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                        <IconButton size="small" onClick={() => handleDeleteBatch(b.batch_id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </CardActions>
                    </Card>
                  </Grid>
                ))}
                {batches.length === 0 && (
                  <Grid item xs={12}>
                    <Typography variant="body2" color="text.secondary" sx={{ textAlign: "center", py: 3 }}>
                      No videos generated yet. Create your first video above!
                    </Typography>
                  </Grid>
                )}
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      </Grid>


      {/* Gallery Selection Dialog */}
      <Dialog
        open={galleryOpen}
        onClose={() => setGalleryOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          <Stack direction="row" justifyContent="space-between" alignItems="center">
            <Typography variant="h6">
              {selectedBatch ? (
                <>
                  <IconButton size="small" onClick={() => setSelectedBatch(null)} sx={{ mr: 1 }}>
                    <ExpandLessIcon sx={{ transform: 'rotate(-90deg)' }} />
                  </IconButton>
                  {selectedBatch.display_name || selectedBatch.batch_id}
                </>
              ) : (
                'Select Images from Gallery'
              )}
            </Typography>
            <IconButton onClick={() => setGalleryOpen(false)}>
              <CloseIcon />
            </IconButton>
          </Stack>
        </DialogTitle>
        <DialogContent dividers sx={{ minHeight: 400 }}>
          {loadingGallery || loadingBatchImages ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 300 }}>
              <CircularProgress />
            </Box>
          ) : selectedBatch ? (
            // Show images from selected batch
            batchImages.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 4 }}>
                <Typography color="text.secondary">No images in this batch</Typography>
              </Box>
            ) : (
              <Grid container spacing={1}>
                {batchImages.map((img) => (
                  <Grid item xs={6} sm={4} md={3} key={img.id}>
                    <Box
                      onClick={() => toggleGalleryImageSelection(img.id)}
                      sx={{
                        position: 'relative',
                        paddingTop: '100%',
                        borderRadius: 1,
                        overflow: 'hidden',
                        cursor: 'pointer',
                        border: gallerySelectedImages.has(img.id) ? '3px solid' : '1px solid',
                        borderColor: gallerySelectedImages.has(img.id) ? 'primary.main' : 'grey.300',
                        transition: 'all 0.2s ease',
                        '&:hover': {
                          borderColor: 'primary.light',
                        },
                      }}
                    >
                      <Box
                        component="img"
                        src={img.thumbnailUrl}
                        alt={img.name}
                        sx={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          height: '100%',
                          objectFit: 'cover',
                        }}
                      />
                      {gallerySelectedImages.has(img.id) && (
                        <Box
                          sx={{
                            position: 'absolute',
                            top: 4,
                            right: 4,
                            bgcolor: 'primary.main',
                            borderRadius: '50%',
                            p: 0.25,
                          }}
                        >
                          <CheckCircleIcon sx={{ color: 'white', fontSize: 20 }} />
                        </Box>
                      )}
                    </Box>
                  </Grid>
                ))}
              </Grid>
            )
          ) : (
            // Show batches
            galleryBatches.length === 0 ? (
              <Box sx={{ textAlign: 'center', py: 4 }}>
                <Typography color="text.secondary">No image batches found</Typography>
                <Typography variant="caption" color="text.secondary">
                  Generate or upload some images first
                </Typography>
              </Box>
            ) : (
              <Grid container spacing={2}>
                {galleryBatches.map((batch) => (
                  <Grid item xs={12} sm={6} md={4} key={batch.batch_id}>
                    <Card
                      variant="outlined"
                      sx={{
                        cursor: 'pointer',
                        transition: 'all 0.2s ease',
                        '&:hover': {
                          borderColor: 'primary.main',
                          boxShadow: 1,
                        },
                      }}
                      onClick={() => handleBatchClick(batch)}
                    >
                      <CardContent>
                        <Typography variant="subtitle2" noWrap>
                          {batch.display_name || batch.batch_id}
                        </Typography>
                        <Stack direction="row" spacing={0.5} sx={{ mt: 1 }}>
                          <Chip
                            label={batch.status}
                            size="small"
                            color={batch.status === 'completed' ? 'success' : 'default'}
                          />
                          <Chip
                            label={`${batch.completed_images ?? batch.total_images ?? 0} images`}
                            size="small"
                            variant="outlined"
                          />
                        </Stack>
                      </CardContent>
                    </Card>
                  </Grid>
                ))}
              </Grid>
            )
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setGalleryOpen(false)}>Cancel</Button>
          {selectedBatch && gallerySelectedImages.size > 0 && (
            <Button
              variant="contained"
              onClick={confirmGallerySelection}
              startIcon={<CheckCircleIcon />}
            >
              Select {gallerySelectedImages.size} Image{gallerySelectedImages.size > 1 ? 's' : ''}
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
};

// Helper to handle local/absolute paths encoded in responses
function PathFromUrl(path) {
  if (!path) return "";
  try {
    const url = new URL(path, window.location.origin);
    return url.pathname.replace(/^\/+/, "");
  } catch {
    return String(path).replace(/^\/+/, "");
  }
}

function encodePathSegments(path) {
  if (!path) return "";
  return path
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export default VideoGeneratorPage;
