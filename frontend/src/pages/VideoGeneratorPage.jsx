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
  OpenInNew as OpenInNewIcon,
  HighQuality as HighQualityIcon,
  AutoFixHigh as EnhanceIcon,
} from "@mui/icons-material";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Preset configurations for easy selection
const QUALITY_PRESETS = {
  fast: {
    label: "⚡ Fast",
    description: "Quick preview (10 steps)",
    num_inference_steps: 10,
    width: 720,
    height: 480,
  },
  standard: {
    label: "✨ Standard",
    description: "Good quality (30 steps)",
    num_inference_steps: 30,
    width: 720,
    height: 480,
  },
  high: {
    label: "🎬 High Quality",
    description: "Best details (40 steps)",
    num_inference_steps: 40,
    width: 720,
    height: 480,
  },
  maximum: {
    label: "🏆 Maximum",
    description: "Maximum quality (50 steps)",
    num_inference_steps: 50,
    width: 720,
    height: 480,
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

// Duration presets for Wan 2.2 models (81 frames max @ 16fps = ~5 seconds)
const WAN_DURATION_PRESETS = {
  short: {
    label: "Short",
    description: "~2 seconds",
    duration_frames: 33,
    fps: 16,
  },
  medium: {
    label: "Medium",
    description: "~3 seconds",
    duration_frames: 49,
    fps: 16,
  },
  long: {
    label: "Long",
    description: "~5 seconds",
    duration_frames: 81,
    fps: 16,
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

// Post-processing quality tiers (interpolation + upscaling)
const OUTPUT_QUALITY_TIERS = {
  draft: {
    label: "Draft",
    description: "Raw output, fastest",
    interpolation: 1,
    upscale: false,
  },
  standard: {
    label: "Standard",
    description: "2x FPS interpolation",
    interpolation: 2,
    upscale: false,
  },
  cinema: {
    label: "Cinema",
    description: "2x FPS + 2x upscale",
    interpolation: 2,
    upscale: true,
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

// Prompt enhancement style presets
const PROMPT_STYLES = {
  cinematic: { label: "Cinematic", description: "Film-quality lighting and motion" },
  realistic: { label: "Realistic", description: "Photorealistic detail" },
  artistic: { label: "Artistic", description: "Stylized and expressive" },
  anime: { label: "Anime", description: "Japanese animation style" },
  none: { label: "None", description: "No enhancement" },
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
  // Wan 2.2 models (state-of-the-art, recommended)
  "wan22-14b": {
    label: "Wan 2.2 14B (GGUF Q5)",
    description: "Best quality, 5s videos (~11GB VRAM)",
    type: "wan",
    maxFrames: 81,
    resolution: [832, 480],
    defaultSteps: 25,
    supportsT2V: true,
    supportsI2V: false,
  },
  // CogVideoX models
  "cogvideox-2b": {
    label: "CogVideoX 2B",
    description: "6s videos, fast (~12GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 30,
    supportsT2V: true,
    supportsI2V: false,
  },
  "cogvideox-5b": {
    label: "CogVideoX 5B",
    description: "6s videos, best quality (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    supportsT2V: true,
    supportsI2V: false,
  },
  "cogvideox-5b-i2v": {
    label: "CogVideoX 5B I2V",
    description: "Image-to-video, 6s (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    supportsT2V: false,
    supportsI2V: true,
  },
  // SVD models (legacy)
  svd: {
    label: "SVD (legacy)",
    description: "2s videos, 512x512",
    type: "svd",
    maxFrames: 14,
    resolution: [512, 512],
    defaultSteps: 25,
    supportsT2V: false,
    supportsI2V: true,
  },
  "svd-xt": {
    label: "SVD-XT (legacy)",
    description: "3.5s videos, 512x512",
    type: "svd",
    maxFrames: 25,
    resolution: [512, 512],
    defaultSteps: 25,
    supportsT2V: false,
    supportsI2V: true,
  },
};

// Default model per input mode
const DEFAULT_T2V_MODEL = "wan22-14b";
const DEFAULT_I2V_MODEL = "cogvideox-5b-i2v";

// Helper to check model type
const isCogVideoXModel = (model) => MODEL_OPTIONS[model]?.type === "cogvideox";
const isWanModel = (model) => MODEL_OPTIONS[model]?.type === "wan";
const isSvdModel = (model) => MODEL_OPTIONS[model]?.type === "svd";

// Lazy import for VideoModelsModal
const VideoModelsModal = React.lazy(() => import("../components/modals/VideoModelsModal"));

const VideoGeneratorPage = () => {
  const [inputMode, setInputMode] = useState("text");
  const [promptsText, setPromptsText] = useState("");
  const [videoModelsModalOpen, setVideoModelsModalOpen] = useState(false);

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

  // Preset selections
  const [qualityPreset, setQualityPreset] = useState("standard");
  const [durationPreset, setDurationPreset] = useState("short");
  const [motionPreset, setMotionPreset] = useState("normal");
  const [model, setModel] = useState(DEFAULT_T2V_MODEL);
  const [aspectRatio, setAspectRatio] = useState("16:9");
  const [videoSize, setVideoSize] = useState("large");
  const [qualityTier, setQualityTier] = useState("standard");
  const [promptStyle, setPromptStyle] = useState("cinematic");
  const [enhancePrompt, setEnhancePrompt] = useState(true);
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

  // CogVideoX-specific power features
  const [teaCacheEnabled, setTeaCacheEnabled] = useState(false);
  const [teaCacheThreshold, setTeaCacheThreshold] = useState(0.3);
  const [fetaEnabled, setFetaEnabled] = useState(false);
  const [fetaWeight, setFetaWeight] = useState(1.0);

  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [activeBatchId, setActiveBatchId] = useState(null);
  const [batchStatus, setBatchStatus] = useState(null);
  const [batches, setBatches] = useState([]);
  const pollingRef = useRef(null);

  // Filter models by current input mode
  const availableModels = useMemo(() => {
    return Object.entries(MODEL_OPTIONS).filter(([_, config]) =>
      inputMode === "image" ? config.supportsI2V : config.supportsT2V
    );
  }, [inputMode]);

  // Auto-select best model when input mode changes
  useEffect(() => {
    const currentConfig = MODEL_OPTIONS[model];
    const isCompatible = inputMode === "image"
      ? currentConfig?.supportsI2V
      : currentConfig?.supportsT2V;
    if (!isCompatible) {
      setModel(inputMode === "image" ? DEFAULT_I2V_MODEL : DEFAULT_T2V_MODEL);
    }
  }, [inputMode]);

  // Get duration presets based on selected model
  const durationPresets = useMemo(() => {
    if (isWanModel(model)) return WAN_DURATION_PRESETS;
    if (isCogVideoXModel(model)) return COGVIDEOX_DURATION_PRESETS;
    return SVD_DURATION_PRESETS;
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
    const currentDurationPresets = isWanModel(model) ? WAN_DURATION_PRESETS : isCogVideoXModel(model) ? COGVIDEOX_DURATION_PRESETS : SVD_DURATION_PRESETS;
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
      // Force to 2B for T2V models, but preserve I2V model identity
      if (model !== "cogvideox-5b-i2v") {
        effectiveModel = "cogvideox-2b";
      }

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

    // Low VRAM safe preset for Wan 2.2 on 16GB GPUs
    // GGUF Q5 is already memory-efficient; moderate clamping
    if (lowVramMode && isWanModel(model)) {
      // Clamp frames to short duration to reduce memory
      if (effectiveDurationFrames > 33) {
        effectiveDurationFrames = 33;
      }

      // Reduce resolution — max 480px on longest side
      const maxSafeSide = 480;
      const longestSide = Math.max(width, height);
      if (longestSide > maxSafeSide) {
        const scale = maxSafeSide / longestSide;
        width = Math.round((width * scale) / 8) * 8;
        height = Math.round((height * scale) / 8) * 8;
      }
      if (width < 256) width = 256;
      if (height < 256) height = 256;
      width = Math.round(width / 8) * 8;
      height = Math.round(height / 8) * 8;

      // Moderate step reduction
      if (effectiveSteps > 20) {
        effectiveSteps = 20;
      }
    }

    // Post-processing quality tier
    const tier = OUTPUT_QUALITY_TIERS[qualityTier] || OUTPUT_QUALITY_TIERS.standard;

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
      frames_per_batch: lowVramMode && (isCogVideoXModel(model) || isWanModel(model)) ? 1 : advancedParams.frames_per_batch,
      combine_frames: advancedParams.combine_frames,
      // Post-processing: interpolation and upscaling from quality tier
      interpolation_multiplier: tier.interpolation,
      upscale: tier.upscale,
      // Prompt enhancement
      prompt_style: promptStyle,
      enhance_prompt: enhancePrompt,
      // CogVideoX power features
      teacache_threshold: teaCacheEnabled && isCogVideoXModel(effectiveModel) ? teaCacheThreshold : null,
      feta_weight: fetaEnabled && isCogVideoXModel(effectiveModel) ? fetaWeight : null,
    };
  }, [qualityPreset, durationPreset, motionPreset, model, advancedParams, videoDimensions, lowVramMode, qualityTier, promptStyle, enhancePrompt, teaCacheEnabled, teaCacheThreshold, fetaEnabled, fetaWeight]);

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

  useEffect(() => {
    fetchBatches();
    return () => {
      stopPolling();
    };
  }, []);

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

    // Pre-flight GPU check — backend will also enforce this
    try {
      const statusRes = await fetch(`${API_BASE}/gpu/status`);
      if (statusRes.ok) {
        const statusData = await statusRes.json();
        if (statusData.success && !statusData.data.available) {
          setError("GPU is currently in use. Stop Ollama or other GPU services from the Plugins page first.");
          return;
        }
      }
    } catch (e) {
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
      const motionPrompt = promptsText.trim();
      const body =
        inputMode === "text"
          ? { prompts: parsedPrompts, ...computedParams }
          : { image_paths: imagePaths, prompt: motionPrompt || "", ...computedParams };

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

  const controlsDisabled = isGenerating;

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

              {/* Low VRAM Mode */}
              <Box sx={{
                mb: 3,
                p: 2,
                bgcolor: 'info.50',
                borderRadius: 2,
                border: '1px solid',
                borderColor: 'info.200'
              }}>
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
                        Low VRAM Safe Preset
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        Recommended for 16GB GPUs: reduces frames, resolution, and steps to minimize memory usage.
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
              {/* Motion/Action Direction for I2V */}
              <TextField
                label="Describe the motion or action (optional)"
                multiline
                minRows={2}
                maxRows={4}
                value={promptsText}
                onChange={(e) => setPromptsText(e.target.value)}
                placeholder="Make this character jump around happily, waving its arms&#10;Slow camera zoom in with gentle head turn and blinking"
                fullWidth
                variant="outlined"
                sx={{ mb: 2 }}
              />

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
                    {availableModels.map(([key, opt]) => (
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
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<SettingsIcon />}
                  onClick={() => setVideoModelsModalOpen(true)}
                  sx={{ mt: 1, textTransform: "none" }}
                >
                  Manage Video Models
                </Button>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<OpenInNewIcon />}
                  onClick={() => window.open('http://localhost:8188', '_blank')}
                  sx={{ mt: 1, ml: 1, textTransform: "none" }}
                >
                  Advanced Editor
                </Button>
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
              {/* Aspect Ratio — not applicable for SVD (fixed 512x512) */}
              {!isSvdModel(model) && (
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
              )}

              {/* Video Size — not applicable for SVD (fixed 512x512) */}
              {!isSvdModel(model) && (
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
              )}

              {/* Motion Preset - only for SVD models */}
              {isSvdModel(model) && (
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

              {/* Output Quality Tier (post-processing) */}
              <Grid item xs={12} sm={6} md={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <HighQualityIcon fontSize="small" /> Output Quality
                    </Box>
                  </InputLabel>
                  <Select
                    value={qualityTier}
                    onChange={(e) => setQualityTier(e.target.value)}
                    label="Output Quality"
                  >
                    {Object.entries(OUTPUT_QUALITY_TIERS).map(([key, tier]) => (
                      <MenuItem key={key} value={key}>
                        <Box>
                          <Typography variant="body2">{tier.label}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {tier.description}
                          </Typography>
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>
            </Grid>

            {/* Advanced Parameters Row — hidden for SVD (no text prompt controls) */}
            {!isSvdModel(model) && (
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
                <FormControl size="small" sx={{ width: { xs: '100%', sm: '280px' } }}>
                  <InputLabel>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      <EnhanceIcon fontSize="small" /> Prompt Style
                    </Box>
                  </InputLabel>
                  <Select
                    value={promptStyle}
                    onChange={(e) => setPromptStyle(e.target.value)}
                    label="Prompt Style"
                  >
                    {Object.entries(PROMPT_STYLES).map(([key, preset]) => (
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
                <FormControlLabel
                  control={
                    <Switch
                      checked={enhancePrompt}
                      onChange={(e) => setEnhancePrompt(e.target.checked)}
                      color="primary"
                      size="small"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">Enhance Prompt</Typography>
                      <Typography variant="caption" color="text.secondary">
                        Add quality descriptors automatically
                      </Typography>
                    </Box>
                  }
                  sx={{ ml: 0 }}
                />
              </Box>
              {/* CogVideoX Power Features */}
              {isCogVideoXModel(model) && (
              <Box sx={{ display: "flex", alignItems: "flex-start", gap: 2, flexWrap: "wrap", mt: 2 }}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={teaCacheEnabled}
                      onChange={(e) => setTeaCacheEnabled(e.target.checked)}
                      color="primary"
                      size="small"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">Speed Boost (TeaCache)</Typography>
                      <Typography variant="caption" color="text.secondary">
                        ~1.5x faster generation
                      </Typography>
                    </Box>
                  }
                  sx={{ ml: 0 }}
                />
                {teaCacheEnabled && (
                  <TextField
                    size="small"
                    label="Cache Threshold"
                    type="number"
                    inputProps={{ step: 0.1, min: 0.1, max: 1.0 }}
                    value={teaCacheThreshold}
                    onChange={(e) => setTeaCacheThreshold(Number(e.target.value))}
                    helperText="Higher = faster, lower quality"
                    sx={{ width: 160 }}
                  />
                )}
                <FormControlLabel
                  control={
                    <Switch
                      checked={fetaEnabled}
                      onChange={(e) => setFetaEnabled(e.target.checked)}
                      color="primary"
                      size="small"
                    />
                  }
                  label={
                    <Box>
                      <Typography variant="body2">Enhance-A-Video</Typography>
                      <Typography variant="caption" color="text.secondary">
                        Improved temporal coherence
                      </Typography>
                    </Box>
                  }
                  sx={{ ml: 0 }}
                />
                {fetaEnabled && (
                  <TextField
                    size="small"
                    label="Enhancement Weight"
                    type="number"
                    inputProps={{ step: 0.1, min: 0.1, max: 3.0 }}
                    value={fetaWeight}
                    onChange={(e) => setFetaWeight(Number(e.target.value))}
                    helperText="Higher = stronger effect"
                    sx={{ width: 160 }}
                  />
                )}
              </Box>
              )}
            </Box>
            )}
            {/* Low VRAM Mode Active Warning */}
            {lowVramMode && (isCogVideoXModel(model) || isWanModel(model)) && (
              <Alert
                severity="info"
                sx={{
                  mt: 1.5,
                  mb: 2,
                  '& .MuiAlert-message': {
                    py: 0.5,
                  },
                }}
              >
                {isCogVideoXModel(model) && model === "cogvideox-5b-i2v"
                  ? `Low VRAM mode is active: Max ${computedParams.duration_frames} frames, max ${computedParams.num_inference_steps} steps, and reduced resolution (model preserved for I2V).`
                  : `Low VRAM mode is active: Max ${computedParams.duration_frames} frames, max ${computedParams.num_inference_steps} steps, and reduced resolution to minimize memory usage.`
                }
              </Alert>
            )}
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
              {isWanModel(model) ? (
                <Chip
                  size="small"
                  color="secondary"
                  label="Wan 2.2"
                  sx={{ fontWeight: 600 }}
                />
              ) : isCogVideoXModel(model) ? (
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
              {isSvdModel(model) && (
                <Chip
                  size="small"
                  variant="outlined"
                  label={`Motion: ${computedParams.motion_strength}x`}
                />
              )}
              {computedParams.interpolation_multiplier > 1 && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="info"
                  label={`${computedParams.interpolation_multiplier}x FPS`}
                />
              )}
              {computedParams.upscale && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="secondary"
                  label="2x Upscale"
                />
              )}
              {computedParams.enhance_prompt && computedParams.prompt_style !== "none" && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="warning"
                  label={`${PROMPT_STYLES[computedParams.prompt_style]?.label || computedParams.prompt_style} style`}
                />
              )}
              {computedParams.teacache_threshold && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="success"
                  label={`TeaCache ${computedParams.teacache_threshold}`}
                />
              )}
              {computedParams.feta_weight && (
                <Chip
                  size="small"
                  variant="outlined"
                  color="success"
                  label={`FETA ${computedParams.feta_weight}`}
                />
              )}
            </Box>
          </Box>

          {/* Model-mode mismatch is now prevented by filtering — no warning needed */}

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

      {/* Video Models Modal */}
      <React.Suspense fallback={null}>
        <VideoModelsModal
          open={videoModelsModalOpen}
          onClose={() => setVideoModelsModalOpen(false)}
          showMessage={(msg, severity) => {
            if (severity === "error") setError(msg);
            else setSuccess(msg);
          }}
        />
      </React.Suspense>
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
