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
  NavigateBefore as PrevIcon,
  NavigateNext as NextIcon,
  Pause as PauseIcon,
  VolumeUp as VolumeUpIcon,
  VolumeOff as VolumeOffIcon,
  Fullscreen as FullscreenIcon,
} from "@mui/icons-material";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const formatVideoDate = (isoStr) => {
  if (!isoStr) return null;
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    const diffDay = Math.floor(diffMs / 86400000);
    if (diffMin < 1) return "Just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: d.getFullYear() !== now.getFullYear() ? "numeric" : undefined });
  } catch { return null; }
};

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
  anime: { label: "Anime (Japanese)", description: "Japanese cel-shaded animation" },
  "3d_animation": { label: "3D Animation (Pixar-style)", description: "Polished CGI, expressive characters" },
  stop_motion: { label: "Stop-motion / Claymation", description: "Tactile clay, handcrafted feel" },
  hand_drawn: { label: "Hand-drawn 2D (Ghibli-style)", description: "Painterly watercolor backgrounds" },
  western_cartoon: { label: "Western Cartoon", description: "Bold outlines, flat shading, snappy motion" },
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
  hd: {
    label: "HD",
    description: "1280px (CPU offload, slower)",
    baseSize: 1280,
  },
  fullhd: {
    label: "Full HD",
    description: "1920px (CPU offload, much slower)",
    baseSize: 1920,
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
    dimensionAlignment: 16,
  },
  // CogVideoX models — 5b is the recommended one for quality.
  // 2b is materially weaker and only worth picking for quick draft passes.
  "cogvideox-2b": {
    label: "CogVideoX 2B (Fast/Draft)",
    description: "6s draft videos, lower quality (~12GB VRAM) — prefer 5B",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 30,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 16,
  },
  "cogvideox-5b": {
    label: "CogVideoX 5B (Recommended)",
    description: "6s videos, best quality (~16GB VRAM)",
    type: "cogvideox",
    maxFrames: 49,
    resolution: [720, 480],
    defaultSteps: 50,
    supportsT2V: true,
    supportsI2V: false,
    dimensionAlignment: 16,
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
    dimensionAlignment: 16,
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
    dimensionAlignment: 8,
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
    dimensionAlignment: 8,
  },
};

// Default model per input mode
const DEFAULT_T2V_MODEL = "wan22-14b";
const DEFAULT_I2V_MODEL = "cogvideox-5b-i2v";

// Helper to check model type
const isCogVideoXModel = (model) => MODEL_OPTIONS[model]?.type === "cogvideox";
const isWanModel = (model) => MODEL_OPTIONS[model]?.type === "wan";

// CogVideoX/Wan use 2x2 patch embedding on top of an 8x VAE → dims must be /16.
// SVD is a U-Net with no patches → /8 is enough. Off-by-one here turns into
// "tensor a (51) must match tensor b (50)" at scheduler.step. Don't ship without it.
const snapDimensions = (width, height, model) => {
  const align = MODEL_OPTIONS[model]?.dimensionAlignment ?? 16;
  return {
    width: Math.round(width / align) * align,
    height: Math.round(height / align) * align,
  };
};
const isSvdModel = (model) => MODEL_OPTIONS[model]?.type === "svd";

// Lazy import for VideoModelsModal
const VideoModelsModal = React.lazy(() => import("../components/modals/VideoModelsModal"));

const VideoGeneratorPage = ({ embedded = false }) => {
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

  // Batch-wide prompt modifiers (mirror BatchImageGen's "Look & Feel" pattern)
  const [lookAndFeel, setLookAndFeel] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
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
  const [queue, setQueue] = useState([]);
  const [videoPlayer, setVideoPlayer] = useState(null); // { url, title, batchId, results, currentIndex }
  const pollingRef = useRef(null);
  const queuePollingRef = useRef(null);

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
    // CogVideoX is trained on 720x480 (3:2). Aspect-ratio math at 16:9 lands
    // on 720x405 → snaps to 720x400, which is off-spec and produces distorted
    // output every time. Pin to the model's native frame and let the user
    // letterbox / crop in post if they need a different aspect.
    if (isCogVideoXModel(model)) {
      const [nativeW, nativeH] = MODEL_OPTIONS[model].resolution;
      return { width: nativeW, height: nativeH };
    }

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

    // Snap to the model's required alignment (16 for CogVideoX/Wan, 8 for SVD).
    ({ width, height } = snapDimensions(width, height, model));

    return { width, height };
  }, [aspectRatio, videoSize, model]);

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

    // CogVideoX is unusually step-sensitive — anything below ~50 produces visibly
    // smeared / underbaked output regardless of the rest of the params. Floor it
    // unless the user explicitly opts into fewer in advanced settings.
    if (isCogVideoXModel(model) && effectiveSteps < 50 &&
        (advancedParams.num_inference_steps === null || advancedParams.num_inference_steps === undefined)) {
      effectiveSteps = 50;
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
        width = width * scale;
        height = height * scale;
      }
      // Ensure minimum dimensions are met (CogVideoX needs at least 256x256)
      if (width < 256) width = 256;
      if (height < 256) height = 256;
      // Snap to the model's required alignment (always last, after every resize)
      ({ width, height } = snapDimensions(width, height, effectiveModel));

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
        width = width * scale;
        height = height * scale;
      }
      if (width < 256) width = 256;
      if (height < 256) height = 256;
      ({ width, height } = snapDimensions(width, height, effectiveModel));

      // Moderate step reduction
      if (effectiveSteps > 20) {
        effectiveSteps = 20;
      }
    }

    // High resolution mode — trade steps/frames for pixels.
    // At 1280+ the model needs breathing room, so we cap steps and frames
    // unless the user explicitly overrode them in advanced settings.
    const isHighRes = Math.max(width, height) >= 1280;
    if (isHighRes && !lowVramMode) {
      // Cap steps — more pixels per step means fewer steps needed for quality
      const userOverrodeSteps = advancedParams.num_inference_steps !== null && advancedParams.num_inference_steps !== undefined;
      if (!userOverrodeSteps && effectiveSteps > 30) {
        effectiveSteps = 30;
      }
      // Cap frames to keep VRAM in check on 16GB cards
      if (Math.max(width, height) >= 1920 && effectiveDurationFrames > 33) {
        effectiveDurationFrames = 33; // ~2s at 16fps — still looks great at 1080p
      } else if (effectiveDurationFrames > 49) {
        effectiveDurationFrames = 49; // ~3s at 16fps for 720p HD
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
    fetchQueue();
    // Continuous queue polling — cheap, gives the user live feedback as
    // batches drain and as they stack up new ones.
    queuePollingRef.current = setInterval(fetchQueue, 2000);
    return () => {
      stopPolling();
      if (queuePollingRef.current) clearInterval(queuePollingRef.current);
    };
  }, []);

  const fetchBatches = async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/list`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          const sorted = (data.data.batches || []).sort((a, b) => {
            const ta = a.start_time || a.end_time || "";
            const tb = b.start_time || b.end_time || "";
            return tb.localeCompare(ta); // newest first
          });
          setBatches(sorted);
        }
      }
    } catch (e) {
      // ignore
    }
  };

  const fetchQueue = async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/queue`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          setQueue(data.data.queue || []);
        }
      }
    } catch (e) {
      // ignore polling errors
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

      // Look & Feel concatenation — same pattern as BatchImageGen.
      // Each prompt gets the batch-wide style modifier appended.
      const lf = lookAndFeel.trim();
      const finalPrompts = lf
        ? parsedPrompts.map((p) => `${p}, ${lf}`)
        : parsedPrompts;

      // SVD ignores negative prompts (image-conditioned only). Hide it from the wire too.
      const isSvd = (computedParams.model || model || "").toLowerCase().includes("svd");
      const trimmedNeg = negativePrompt.trim();
      const negativePayload = !isSvd && trimmedNeg ? { negative_prompt: trimmedNeg } : {};

      const body =
        inputMode === "text"
          ? { prompts: finalPrompts, ...computedParams, ...negativePayload }
          : {
              image_paths: imagePaths,
              prompt: lf && motionPrompt ? `${motionPrompt}, ${lf}` : motionPrompt,
              ...computedParams,
              ...negativePayload,
            };

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
        setError(errorData.error || `Failed to queue batch: HTTP ${res.status}`);
        return;
      }

      const data = await res.json();
      if (!data.success) {
        setError(data.error || "Failed to queue batch");
        return;
      }

      const batchId = data.data.batch_id;
      setActiveBatchId(batchId);
      setSuccess(`Batch queued. The worker drains one batch at a time — keep stacking 'em.`);
      startPollingStatus(batchId);
      await fetchBatches();

      // Reset prompts so the user can immediately compose the next batch.
      // Keep Look & Feel + Negative Prompt — those usually carry across batches.
      if (inputMode === "text") {
        setPromptsText("");
      }
    } catch (e) {
      setError(`Failed to queue batch: ${e.message}`);
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

  const handleCancelBatch = async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/batch/${batchId}/cancel`, {
        method: "POST",
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
    <PageLayout title={embedded ? undefined : "Video Generation"} variant={embedded ? "fullscreen" : "standard"} noPadding={embedded}>

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

          {/* Batch-wide prompt modifiers — apply to every prompt in the batch */}
          <Stack spacing={2} sx={{ mt: 2 }}>
            <TextField
              label="Look & Feel (optional, applied to every prompt)"
              multiline
              minRows={2}
              maxRows={4}
              value={lookAndFeel}
              onChange={(e) => setLookAndFeel(e.target.value)}
              placeholder="moody cinematic, golden hour lighting, dramatic shadows, shallow depth of field"
              helperText={
                lookAndFeel.trim()
                  ? `Will be appended to ${parsedPrompts.length || 0} prompt${parsedPrompts.length === 1 ? "" : "s"} in this batch.`
                  : "Style modifier — same shape as BatchImageGen's Look & Feel field."
              }
              fullWidth
              variant="outlined"
              size="small"
            />

            {!(model || "").toLowerCase().includes("svd") ? (
              <TextField
                label="Negative Prompt (optional, applied to every prompt)"
                multiline
                minRows={2}
                maxRows={4}
                value={negativePrompt}
                onChange={(e) => setNegativePrompt(e.target.value)}
                placeholder="blurry, distorted hands, washed out colors, watermark, text overlay"
                helperText="What to avoid in every video. Quality defects work better than content restrictions."
                fullWidth
                variant="outlined"
                size="small"
              />
            ) : (
              <Tooltip
                title="SVD is image-conditioned only — it has no text-negative path, so a negative prompt would be ignored. Pick CogVideoX or Wan 2.2 to use this field."
                placement="right"
                arrow
              >
                <Box>
                  <Alert severity="info" variant="outlined" sx={{ py: 0.5 }}>
                    Negative Prompt isn't supported by SVD. Switch to CogVideoX or Wan 2.2 to use it.
                  </Alert>
                </Box>
              </Tooltip>
            )}
          </Stack>

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
            {isGenerating ? "Queueing..." : "Add to Queue"}
          </Button>

          {isGenerating && <LinearProgress />}
            </Stack>
          </Box>
          </CardContent>
        </Card>
        </Grid>

        {/* Status Section - Right Side */}
        <Grid item xs={12} lg={6}>
          {/* Batch Queue panel — live view of what's running and what's stacked behind it */}
          {queue.length > 0 && (
            <Card sx={{ mb: 3, boxShadow: 2, borderRadius: 2 }}>
              <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    Batch Queue
                  </Typography>
                  <Chip
                    label={`${queue.filter(q => q.status === 'queued' || q.status === 'running').length} active`}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                </Stack>
                <Stack spacing={1}>
                  {queue.map((q, idx) => {
                    const slotTag = `#${idx + 1}`;
                    const pct = q.total_videos > 0
                      ? Math.round(((q.completed_videos + q.failed_videos) / q.total_videos) * 100)
                      : 0;
                    const chipColor =
                      q.status === 'running' ? 'primary' :
                      q.status === 'queued' ? 'default' :
                      q.status === 'completed' ? 'success' :
                      q.status === 'cancelled' ? 'warning' :
                      q.status === 'error' ? 'error' : 'default';
                    const cancellable = q.status === 'queued' || q.status === 'running';
                    return (
                      <Box
                        key={q.batch_id}
                        sx={{
                          p: 1.5,
                          border: '1px solid',
                          borderColor: q.is_running ? 'primary.main' : 'divider',
                          borderRadius: 1,
                          bgcolor: q.is_running ? 'action.hover' : 'transparent',
                        }}
                      >
                        <Stack direction="row" alignItems="center" spacing={1.5}>
                          <Chip
                            label={slotTag}
                            size="small"
                            variant="outlined"
                            sx={{ minWidth: 44, fontFamily: 'monospace' }}
                          />
                          <Box sx={{ flex: 1, minWidth: 0 }}>
                            <Typography variant="body2" noWrap title={q.batch_id}>
                              {q.display_name || q.batch_id}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {q.completed_videos + q.failed_videos}/{q.total_videos} videos
                              {q.failed_videos > 0 ? ` (${q.failed_videos} failed)` : ''}
                            </Typography>
                          </Box>
                          <Chip
                            label={q.status.toUpperCase()}
                            size="small"
                            color={chipColor}
                          />
                          {cancellable && (
                            <Tooltip
                              title={q.status === 'running' ? 'Cancel — interrupts ComfyUI mid-frame' : 'Remove from queue'}
                              arrow
                            >
                              <IconButton
                                size="small"
                                onClick={() => handleCancelBatch(q.batch_id)}
                                aria-label="cancel batch"
                              >
                                <CloseIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </Stack>
                        {q.status === 'running' && (
                          <LinearProgress
                            variant="determinate"
                            value={pct}
                            sx={{ mt: 1, height: 4, borderRadius: 2 }}
                          />
                        )}
                        {q.error && (
                          <Typography variant="caption" color="error" sx={{ mt: 0.5, display: 'block' }}>
                            {q.error}
                          </Typography>
                        )}
                      </Box>
                    );
                  })}
                </Stack>
              </CardContent>
            </Card>
          )}

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
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
                    <Chip
                      label={batchStatus.status.toUpperCase()}
                      color={batchStatus.status === 'running' ? 'primary' :
                             batchStatus.status === 'completed' ? 'success' :
                             batchStatus.status === 'error' ? 'error' :
                             batchStatus.status === 'cancelled' ? 'warning' : 'default'}
                      size="small"
                    />
                    {(batchStatus.status === 'running' || batchStatus.status === 'pending') && (
                      <Button
                        size="small"
                        color="warning"
                        variant="outlined"
                        onClick={() => handleCancelBatch(batchStatus.batch_id)}
                      >
                        Cancel
                      </Button>
                    )}
                  </Stack>
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
                  {currentResults.map((res, idx) => {
                    const videoUrl = res.video_path
                      ? `${API_BASE}/batch-video/video/${batchStatus.batch_id}/${encodePathSegments(PathFromUrl(res.video_path))}`
                      : null;
                    const thumbUrl = res.thumbnail_path
                      ? `${API_BASE}/batch-video/video/${batchStatus.batch_id}/${encodePathSegments(PathFromUrl(res.thumbnail_path))}`
                      : null;
                    return (
                    <Grid item xs={12} sm={6} key={res.item_id}>
                      <Card variant="outlined">
                        <CardContent sx={{ pb: 1 }}>
                          <Box
                            sx={{
                              position: "relative",
                              width: "100%",
                              aspectRatio: "16/9",
                              borderRadius: 1,
                              overflow: "hidden",
                              mb: 1,
                              bgcolor: "grey.900",
                              cursor: videoUrl ? "pointer" : "default",
                            }}
                            onClick={() => {
                              if (!videoUrl) return;
                              const playable = currentResults.filter(r => r.video_path);
                              const playIdx = playable.findIndex(r => r.item_id === res.item_id);
                              setVideoPlayer({
                                url: videoUrl,
                                title: res.video_path?.split("/").pop() || `Video ${idx + 1}`,
                                batchId: batchStatus.batch_id,
                                results: playable,
                                currentIndex: playIdx >= 0 ? playIdx : 0,
                              });
                            }}
                          >
                            {thumbUrl ? (
                              <Box
                                component="img"
                                src={thumbUrl}
                                alt="thumb"
                                sx={{ width: "100%", height: "100%", objectFit: "cover" }}
                              />
                            ) : (
                              <Box sx={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
                                <VideoIcon color="action" sx={{ fontSize: 40 }} />
                              </Box>
                            )}
                            {videoUrl && (
                              <Box sx={{
                                position: "absolute", top: 0, left: 0, right: 0, bottom: 0,
                                display: "flex", alignItems: "center", justifyContent: "center",
                                bgcolor: "rgba(0,0,0,0.3)", opacity: 0, transition: "opacity 0.2s",
                                "&:hover": { opacity: 1 },
                              }}>
                                <PlayIcon sx={{ fontSize: 48, color: "white" }} />
                              </Box>
                            )}
                          </Box>
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
                          {videoUrl && (
                            <Button
                              size="small"
                              variant="contained"
                              startIcon={<PlayIcon />}
                              onClick={() => {
                                const playable = currentResults.filter(r => r.video_path);
                                const playIdx = playable.findIndex(r => r.item_id === res.item_id);
                                setVideoPlayer({
                                  url: videoUrl,
                                  title: res.video_path?.split("/").pop() || `Video ${idx + 1}`,
                                  batchId: batchStatus.batch_id,
                                  results: playable,
                                  currentIndex: playIdx >= 0 ? playIdx : 0,
                                });
                              }}
                            >
                              Play
                            </Button>
                          )}
                          {videoUrl && (
                            <Tooltip title="Open in new tab">
                              <IconButton size="small" onClick={() => window.open(videoUrl, "_blank")}>
                                <OpenInNewIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
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
                                <CloseIcon fontSize="small" />
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
                    );
                  })}
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

          {/* Batch History — Stacked Thumbnail Gallery */}
          <Card sx={{
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  Video Library
                </Typography>
                <IconButton size="small" onClick={fetchBatches}>
                  <RefreshIcon />
                </IconButton>
              </Stack>
              <Grid container spacing={2}>
                {batches.map((b) => {
                  const dateStr = formatVideoDate(b.start_time || b.end_time);
                  const videoCount = b.completed_videos ?? 0;
                  const totalCount = b.total_videos ?? 0;
                  return (
                  <Grid item xs={6} sm={4} md={3} key={b.batch_id}>
                    <Box
                      onClick={() => {
                        setActiveBatchId(b.batch_id);
                        startPollingStatus(b.batch_id);
                      }}
                      sx={{
                        cursor: 'pointer',
                        position: 'relative',
                        borderRadius: 2,
                        overflow: 'hidden',
                        transition: 'transform 0.2s, box-shadow 0.2s',
                        '&:hover': {
                          transform: 'translateY(-4px)',
                          boxShadow: 6,
                          '& .batch-overlay': { opacity: 1 },
                        },
                      }}
                    >
                      {/* Stacked thumbnail effect */}
                      <Box sx={{ position: 'relative', aspectRatio: '16/9' }}>
                        {/* Background layers for stack effect */}
                        {videoCount > 2 && (
                          <Box sx={{
                            position: 'absolute', top: -6, left: 6, right: -6, bottom: 6,
                            bgcolor: 'grey.800', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                          }} />
                        )}
                        {videoCount > 1 && (
                          <Box sx={{
                            position: 'absolute', top: -3, left: 3, right: -3, bottom: 3,
                            bgcolor: 'grey.850', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                          }} />
                        )}
                        {/* Main thumbnail */}
                        <Box sx={{
                          position: 'relative', width: '100%', height: '100%',
                          bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
                          border: 1, borderColor: 'grey.700',
                        }}>
                          {videoCount > 0 ? (
                            <Box
                              component="img"
                              src={`${API_BASE}/batch-video/preview/${b.batch_id}`}
                              alt="Preview"
                              sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => { e.target.style.display = 'none'; }}
                            />
                          ) : (
                            <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                              <VideoIcon sx={{ fontSize: 36, color: 'grey.600' }} />
                            </Box>
                          )}
                          {/* Hover overlay */}
                          <Box className="batch-overlay" sx={{
                            position: 'absolute', inset: 0,
                            bgcolor: 'rgba(0,0,0,0.5)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            opacity: 0, transition: 'opacity 0.2s',
                          }}>
                            <PlayIcon sx={{ fontSize: 40, color: 'white' }} />
                          </Box>
                          {/* Video count badge */}
                          <Chip
                            label={`${videoCount} video${videoCount !== 1 ? 's' : ''}`}
                            size="small"
                            sx={{
                              position: 'absolute', top: 6, right: 6,
                              height: 20, fontSize: '0.65rem',
                              bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
                              '& .MuiChip-label': { px: 0.75 },
                            }}
                          />
                          {/* Status indicator */}
                          {b.status !== 'completed' && (
                            <Chip
                              label={b.status}
                              size="small"
                              color={b.status === 'error' ? 'error' : b.status === 'cancelled' ? 'warning' : 'info'}
                              sx={{
                                position: 'absolute', bottom: 6, left: 6,
                                height: 18, fontSize: '0.6rem',
                              }}
                            />
                          )}
                        </Box>
                      </Box>
                      {/* Batch label */}
                      <Box sx={{ pt: 0.75, px: 0.5 }}>
                        <Typography variant="caption" noWrap sx={{ fontWeight: 500, display: 'block' }}>
                          {b.display_name || `Batch ${b.batch_id.slice(0, 8)}`}
                        </Typography>
                        <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.65rem' }}>
                          {dateStr}
                        </Typography>
                      </Box>
                    </Box>
                  </Grid>
                  );
                })}
              </Grid>
              {batches.length === 0 && (
                <Box sx={{ textAlign: 'center', py: 4 }}>
                  <VideoIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
                  <Typography variant="body2" color="text.secondary">
                    No videos generated yet
                  </Typography>
                </Box>
              )}
            </CardContent>
          </Card>

          {/* Legacy batch controls — keep for running/pending batches */}
          {batches.filter(b => b.status === "running" || b.status === "pending" || b.status === "processing").map((b) => (
            <Box key={`ctrl-${b.batch_id}`} sx={{ mt: 1 }}>
              <Button size="small" color="warning" variant="outlined" onClick={() => handleCancelBatch(b.batch_id)}>
                Cancel {b.display_name || b.batch_id.slice(0, 8)}
              </Button>
            </Box>
          ))}
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

      {/* Inline Video Player Dialog */}
      <Dialog
        open={!!videoPlayer}
        onClose={() => setVideoPlayer(null)}
        maxWidth="md"
        fullWidth
        PaperProps={{ sx: { bgcolor: "grey.900", borderRadius: 2 } }}
      >
        {videoPlayer && (() => {
          const { results, currentIndex, batchId } = videoPlayer;
          const hasPrev = currentIndex > 0;
          const hasNext = currentIndex < results.length - 1;
          const navigateTo = (idx) => {
            const r = results[idx];
            const url = `${API_BASE}/batch-video/video/${batchId}/${encodePathSegments(PathFromUrl(r.video_path))}`;
            setVideoPlayer(prev => ({ ...prev, url, currentIndex: idx, title: r.video_path?.split("/").pop() || `Video ${idx + 1}` }));
          };
          return (
            <>
              <DialogTitle sx={{ color: "grey.300", display: "flex", justifyContent: "space-between", alignItems: "center", pb: 1 }}>
                <Typography variant="subtitle2" noWrap sx={{ flex: 1, mr: 2, color: "grey.400" }}>
                  {videoPlayer.title}
                  <Typography component="span" variant="caption" sx={{ ml: 1, color: "grey.600" }}>
                    {currentIndex + 1} / {results.length}
                  </Typography>
                </Typography>
                <IconButton size="small" onClick={() => setVideoPlayer(null)} sx={{ color: "grey.400" }}>
                  <CloseIcon />
                </IconButton>
              </DialogTitle>
              <DialogContent sx={{ p: 0, position: "relative" }}>
                <Box sx={{ position: "relative", bgcolor: "black" }}>
                  <video
                    key={videoPlayer.url}
                    src={videoPlayer.url}
                    controls
                    autoPlay
                    loop
                    style={{ width: "100%", display: "block", maxHeight: "70vh" }}
                  />
                  {/* Prev/Next overlays */}
                  {hasPrev && (
                    <IconButton
                      onClick={() => navigateTo(currentIndex - 1)}
                      sx={{
                        position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)",
                        bgcolor: "rgba(0,0,0,0.5)", color: "white", "&:hover": { bgcolor: "rgba(0,0,0,0.7)" },
                      }}
                    >
                      <PrevIcon />
                    </IconButton>
                  )}
                  {hasNext && (
                    <IconButton
                      onClick={() => navigateTo(currentIndex + 1)}
                      sx={{
                        position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
                        bgcolor: "rgba(0,0,0,0.5)", color: "white", "&:hover": { bgcolor: "rgba(0,0,0,0.7)" },
                      }}
                    >
                      <NextIcon />
                    </IconButton>
                  )}
                </Box>
              </DialogContent>
              <DialogActions sx={{ justifyContent: "space-between", px: 2, py: 1 }}>
                <Stack direction="row" spacing={1}>
                  <Button size="small" disabled={!hasPrev} onClick={() => navigateTo(currentIndex - 1)} startIcon={<PrevIcon />}>
                    Prev
                  </Button>
                  <Button size="small" disabled={!hasNext} onClick={() => navigateTo(currentIndex + 1)} endIcon={<NextIcon />}>
                    Next
                  </Button>
                </Stack>
                <Stack direction="row" spacing={1}>
                  <Button size="small" onClick={() => window.open(videoPlayer.url, "_blank")} startIcon={<OpenInNewIcon />}>
                    Open
                  </Button>
                  <Button size="small" onClick={() => {
                    const a = document.createElement("a");
                    a.href = videoPlayer.url;
                    a.download = videoPlayer.title;
                    a.click();
                  }} startIcon={<DownloadIcon />}>
                    Download
                  </Button>
                </Stack>
              </DialogActions>
            </>
          );
        })()}
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
