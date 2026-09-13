// frontend/src/pages/VideoGeneratorPage.jsx
// Standalone Video Generation page with preset-based UI

import React, { useEffect, useMemo, useState, useRef, useCallback } from "react";
import {
  Box,
  Typography,
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
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
  Collapse,
} from "@mui/material";
import PageLayout from "../components/layout/PageLayout";
import GpuGateBanner from "../components/common/GpuGateBanner";
import {
  SettingChip,
  ChoiceChips,
  ActionButton,
  SettingsPanel,
  Cluster,
  Line,
  Hint,
  ConfirmActionDialog,
  DashboardStrip,
  DashboardTile,
} from "../components/settings/ui";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import useJobsGate from "../hooks/useJobsGate";
import useBatchVideo from "../hooks/useBatchVideo";
import {
  QUALITY_PRESETS,
  durationPresetsFor,
  aspectRatiosFor,
  resolveAspectRatio,
  WAN5B_SAMPLER_PROFILES,
  MOTION_PRESETS,
  OUTPUT_QUALITY_TIERS,
  KEYFRAME_MODEL_OPTIONS,
  DEFAULT_KEYFRAME_MODEL,
  MODEL_DEFAULT_GUIDANCE,
  ASPECT_RATIO_PRESETS,
  PROMPT_STYLES,
  VIDEO_SIZE_PRESETS,
  MODEL_OPTIONS,
  GENERATION_TYPES,
  DEFAULT_T2V_MODEL,
  DEFAULT_I2V_MODEL,
  familyType,
  isCogVideoXModel,
  isWanModel,
  isLtxModel,
  isMinimaxModel,
  snapDimensions,
  fitAreaToRatio,
} from "../constants/videoGeneratorPresets";
import VideoGenEffectiveSettings from "../components/videogen/VideoGenEffectiveSettings";
import LiveLatentPreview from "../components/videogen/LiveLatentPreview";
import { videoGenStageLabel } from "../components/videogen/stageLabels";
import {
  PlayArrow as PlayIcon,
  Refresh as RefreshIcon,
  Download as DownloadIcon,
  VideoLibrary as VideoIcon,
  DriveFileRenameOutline as RenameIcon,
  ExpandLess as ExpandLessIcon,
  Settings as SettingsIcon,
  Close as CloseIcon,
  CheckCircle as CheckCircleIcon,
  OpenInNew as OpenInNewIcon,
  NavigateBefore as PrevIcon,
  NavigateNext as NextIcon,
  Fullscreen as FullscreenIcon,
  FullscreenExit as FullscreenExitIcon,
} from "@mui/icons-material";

import { formatUiError } from "../utils/uiError";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const stripGlyph = (label) => String(label || "").replace(/^[^\p{L}\p{N}]+/u, "").trim();

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
  const [fidelityMode, setFidelityMode] = useState(false); // "Exact text mode" / preserve fidelity — light enhancement only
  // Quality pipeline (v2.6.2 — ported from the music-video generator). Opt-in.
  const [directorMode, setDirectorMode] = useState(false);          // rewrite each prompt via the cinematic Director
  const [cinematicKeyframe, setCinematicKeyframe] = useState(false); // FLUX still -> Wan2.2 I2V per clip (slower, sharper)
  const [directorGuidance, setDirectorGuidance] = useState("");      // optional free-text steer for the Director
  const [storyboardMode, setStoryboardMode] = useState(false);       // one concept -> N director-written shots
  const [storyboardShots, setStoryboardShots] = useState(6);
  const [keyframeModel, setKeyframeModel] = useState(DEFAULT_KEYFRAME_MODEL);
  const [highConsistencyMode, setHighConsistencyMode] = useState(false);
  const [postUpscale, setPostUpscale] = useState(false); // independent 2x upscale (quality post-processing)
  const [faceRestoreNodeAvailable, setFaceRestoreNodeAvailable] = useState(null);
  const [faceRestoreModelReady, setFaceRestoreModelReady] = useState(null);
  const faceRestoreAvailable =
    faceRestoreNodeAvailable === true && faceRestoreModelReady === true;

  // Prompt preview state (calls /enhance-preview)
  const [previewEnhanced, setPreviewEnhanced] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

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
    freeu: false,
    face_restore: false,
    lora_name: "",
    lora_strength: 1.0,
    wan_sampler_profile: "official",
    // Capability-contract knobs; only sent when the model declares them.
    speed_profile: "standard",
    style_embedding: "",
    adapters: [],
    adapter_strength: 0.7,
    // User text encoder id ("" = the one the model ships with).
    text_encoder: "",
  });
  // End frame for models that declare first+last-frame generation (image mode).
  const [endFrame, setEndFrame] = useState(null); // {path, name}
  // Audio guide for models that declare audio_in: a voice clip or track the
  // model performs from the start of the clip. {path, name}
  const [audioGuide, setAudioGuide] = useState(null);
  const [voiceClips, setVoiceClips] = useState([]);
  const [isUploadingAudioGuide, setIsUploadingAudioGuide] = useState(false);
  // Prompt presets the backend ships for the selected model's family
  // (/api/batch-video/prompt-presets); empty for families without any.
  const [promptPresets, setPromptPresets] = useState([]);
  const [promptGallery, setPromptGallery] = useState(null);
  const [selectedPromptPreset, setSelectedPromptPreset] = useState("");
  const [isUploadingEndFrame, setIsUploadingEndFrame] = useState(false);
  // Which model the registry's per-VRAM-class defaults were last applied to.
  const seededTierFor = useRef(null);

  // Cast picker: trained character Subjects whose LoRA locks identity into a
  // cinematic keyframe via character_still_pipeline (Z-Image/SDXL/FLUX by train base).
  // Video models don't apply face LoRAs — identity rides in a freshly generated still.
  // Training images are never used as start frames (keyframes always on-demand).
  const [castSubjects, setCastSubjects] = useState([]);
  const [selectedSubjectIds, setSelectedSubjectIds] = useState([]);
  useEffect(() => {
    let alive = true;
    fetch("/api/cast-library")
      .then((r) => (r.ok ? r.json() : { subjects: [] }))
      .then((d) => {
        if (!alive) return;
        const trained = (d.subjects || []).filter(
          (s) => s.kind === "character" && s.lora_path
        );
        setCastSubjects(trained);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // CogVideoX temporal-coherence feature (quality, not speed)
  const [fetaEnabled, setFetaEnabled] = useState(false);
  const [fetaWeight, setFetaWeight] = useState(1.0);

  // Cast selection implies cinematic keyframe; still model is auto from LoRA family.
  useEffect(() => {
    if (selectedSubjectIds.length > 0) {
      setCinematicKeyframe(true);
      setKeyframeModel("from-lora");
    }
  }, [selectedSubjectIds.length]);

  // Apply one-click quality & consistency preset when the master switch is enabled.
  useEffect(() => {
    if (!highConsistencyMode) return;
    setDirectorMode(true);
    setEnhancePrompt(true);
    setFidelityMode(false);
    setQualityTier("cinema");
    setPostUpscale(true);
    setAdvancedParams((prev) => ({
      ...prev,
      face_restore: faceRestoreAvailable === true,
      freeu: isWanModel(model, modelMeta[model]),
    }));
    if (isCogVideoXModel(model, modelMeta[model])) {
      setFetaEnabled(true);
      setFetaWeight(1.0);
    }
    if (inputMode === "text") {
      setCinematicKeyframe(true);
    }
  }, [highConsistencyMode, model, inputMode, faceRestoreAvailable]);

  const refreshFaceRestoreStatus = useCallback(async () => {
    try {
      const [gpuRes, modelsRes] = await Promise.all([
        fetch(`${API_BASE}/gpu/comfyui/status`),
        fetch(`${API_BASE}/batch-video/models`),
      ]);
      let nodeOk = false;
      let modelOk = false;
      if (gpuRes.ok) {
        const gpu = await gpuRes.json();
        nodeOk = gpu?.data?.face_restore_node_available === true;
        setFaceRestoreNodeAvailable(nodeOk);
      }
      if (modelsRes.ok) {
        const models = await modelsRes.json();
        const codeformer = models?.data?.models?.find((m) => m.id === "codeformer");
        modelOk = !!codeformer?.is_ready;
        setFaceRestoreModelReady(modelOk);
      }
      if (!nodeOk || !modelOk) {
        setAdvancedParams((prev) => (prev.face_restore ? { ...prev, face_restore: false } : prev));
      }
    } catch {
      // ComfyUI down / API unavailable — leave toggle disabled until known.
    }
  }, []);

  useEffect(() => {
    refreshFaceRestoreStatus();
  }, [refreshFaceRestoreStatus]);

  // Sync guidance scale to model-family defaults when the model changes.
  useEffect(() => {
    const family = familyType(model, modelMeta[model]);
    // Per-model wins: some models' backend workflows default differently from
    // their family, and the UI should send what the backend would have chosen.
    const defaultCfg = MODEL_OPTIONS[model]?.defaultGuidance ?? MODEL_DEFAULT_GUIDANCE[family];
    if (defaultCfg != null) {
      setAdvancedParams((prev) => ({ ...prev, guidance_scale: defaultCfg }));
    }
  }, [model]);

  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [videoPlayer, setVideoPlayer] = useState(null); // { url, title, batchId, results, currentIndex }
  const videoPlayerBoxRef = useRef(null);
  const [isPlayerFullscreen, setIsPlayerFullscreen] = useState(false);

  const navigateVideoPlayer = useCallback((delta) => {
    setVideoPlayer(prev => {
      if (!prev) return prev;
      const idx = prev.currentIndex + delta;
      if (idx < 0 || idx >= prev.results.length) return prev;
      const r = prev.results[idx];
      const url = `${API_BASE}/batch-video/video/${prev.batchId}/${encodePathSegments(PathFromUrl(r.video_path))}`;
      return { ...prev, url, currentIndex: idx, title: r.video_path?.split("/").pop() || `Video ${idx + 1}` };
    });
  }, []);

  const togglePlayerFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      videoPlayerBoxRef.current?.requestFullscreen?.().catch(() => {});
    }
  }, []);

  // Arrow-key prev/next for the player. Capture phase so it wins over the
  // native <video> controls' seek behavior, and keeps working while the
  // player container is fullscreen (key events still reach document there).
  useEffect(() => {
    if (!videoPlayer) return undefined;
    const onKeyDown = (e) => {
      const tag = e.target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.target?.isContentEditable) return;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        e.stopPropagation();
        navigateVideoPlayer(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        e.stopPropagation();
        navigateVideoPlayer(1);
      } else if (e.key === "Escape" && document.fullscreenElement) {
        // Let the browser exit fullscreen without also closing the dialog.
        e.stopPropagation();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [videoPlayer, navigateVideoPlayer]);

  useEffect(() => {
    const onFsChange = () => setIsPlayerFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFsChange);
    return () => document.removeEventListener("fullscreenchange", onFsChange);
  }, []);

  // Authoritative set of selectable model ids from the backend registry. Null
  // until loaded (then we don't filter). Keeps the dropdown from ever drifting
  // from the backend cull — remove a model from VIDEO_MODEL_REGISTRY and it
  // disappears here automatically. Rich per-model metadata stays in MODEL_OPTIONS.
  const [apiModelIds, setApiModelIds] = useState(null);
  // null = not yet known; true/false once the model list loads. Drives the
  // first-run "no model installed" nudge below (issue #36 discoverability).
  const [anyModelReady, setAnyModelReady] = useState(null);
  // Active accelerator label (e.g. "Apple Silicon · MPS · 64GB unified") for an
  // honest "where will this run" chip — issue #43 Tier 1. null until known.
  const [accelLabel, setAccelLabel] = useState(null);
  // Per-model readiness from the backend registry, keyed by model id:
  // { is_ready, missing_files, name }. Drives the pre-flight that blocks a
  // silent fall-back to a lesser model when the *selected* model isn't fully
  // installed (the "I clicked Wan 2.2 and got CogVideoX without being told" bug).
  const [modelMeta, setModelMeta] = useState({});
  const [adapterModels, setAdapterModels] = useState([]);
  const [encoderModels, setEncoderModels] = useState([]);
  // When set, the selected model isn't installed — surface a blocking banner
  // ({ id, name, missing }) instead of generating with a fallback.
  const [modelNotReady, setModelNotReady] = useState(null);
  // Model id to scroll-to + pulse inside the Manage Video Models modal.
  const [highlightModelId, setHighlightModelId] = useState(null);
  const [confirmAction, setConfirmAction] = useState(null);
  const [confirmBusy, setConfirmBusy] = useState(false);

  // Pull the authoritative model list + readiness. Extracted so we can re-run it
  // after the user closes the install modal (a freshly-installed model should
  // clear the "not ready" banner without a page reload).
  const refreshModels = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/models`);
      const data = await res.json();
      if (data.success && data.data?.models) {
        const vids = data.data.models.filter(m => GENERATION_TYPES.has(m.type));
        const ids = new Set(vids.map(m => m.id));
        if (ids.size > 0) setApiModelIds(ids);
        setAnyModelReady(vids.some(m => m.is_ready));
        setAdapterModels((data.data.models || []).filter((m) => m.type === "lora" && m.is_ready));
        setEncoderModels((data.data.models || []).filter((m) => m.type === "encoder" && m.user && m.is_ready));
        const meta = {};
        vids.forEach(m => {
          meta[m.id] = {
            is_ready: m.is_ready,
            missing_files: m.missing_files || [],
            name: m.name,
            type: m.type,
            user: m.user,
            dimension_alignment: m.dimension_alignment,
            max_pixel_area: m.max_pixel_area,
            // Capability contract declared on the registry entry (modes, audio,
            // cfg, step floor, speed profiles, style embeddings, license) plus
            // the starting settings the backend picked for this card's VRAM class.
            capabilities: m.capabilities || null,
            tier_defaults: m.tier_defaults || null,
            license: m.license || null,
          };
        });
        setModelMeta(meta);
        // If a previously-flagged model is now ready, retract the banner.
        setModelNotReady(prev => (prev && meta[prev.id]?.is_ready ? null : prev));
      }
    } catch (e) {
      // Offline / API down — fall back to the (already-culled) MODEL_OPTIONS.
    }
  }, []);

  useEffect(() => {
    refreshModels();
  }, [refreshModels]);

  // Surface the accelerator the backend actually detected (NVIDIA/CUDA, Apple
  // Silicon/MPS, or CPU) so Mac users can see Metal is in play. Best-effort.
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/node/hardware-profile`);
        if (!res.ok) return;
        const hw = await res.json();
        const gpu = hw?.gpu || {};
        if (gpu.vendor === "apple") {
          const mem = gpu.unified_memory_gb ? ` · ${gpu.unified_memory_gb}GB unified` : "";
          setAccelLabel(`Apple Silicon · MPS${mem}`);
        } else if (gpu.vendor === "nvidia") {
          const vram = gpu.vram_mb ? ` · ${(gpu.vram_mb / 1024).toFixed(0)}GB VRAM` : "";
          setAccelLabel(`NVIDIA · CUDA${vram}`);
        } else if (gpu.vendor === "amd") {
          setAccelLabel("AMD GPU");
        } else {
          setAccelLabel("CPU only");
        }
      } catch (e) {
        // hardware.json not written yet / API down — just don't show the chip.
      }
    })();
  }, []);

  // Filter models by current input mode AND the backend allowlist.
  // Registry-only (user-added) ids are not in MODEL_OPTIONS; capabilities win.
  const availableModels = useMemo(() => {
    const metaIds = Object.keys(modelMeta);
    if (metaIds.length === 0) {
      return Object.entries(MODEL_OPTIONS).filter(([key, config]) => {
        const modeOk = inputMode === "image" ? config.supportsI2V : config.supportsT2V;
        const allowed = apiModelIds == null || apiModelIds.has(key);
        return modeOk && allowed;
      });
    }
    return metaIds
      .filter((id) => {
        const caps = modelMeta[id]?.capabilities || {};
        const opt = MODEL_OPTIONS[id];
        return inputMode === "image"
          ? (caps.supports_i2v ?? opt?.supportsI2V)
          : (caps.supports_t2v ?? opt?.supportsT2V);
      })
      .map((id) => [
        id,
        {
          ...(MODEL_OPTIONS[id] || {}),
          label: modelMeta[id]?.name || MODEL_OPTIONS[id]?.label || id,
          type: modelMeta[id]?.type || MODEL_OPTIONS[id]?.type,
        },
      ]);
  }, [inputMode, apiModelIds, modelMeta]);

  // Auto-select a compatible model when input mode changes or the current id
  // is a user model that does not support this mode.
  useEffect(() => {
    const meta = modelMeta[model];
    const opt = MODEL_OPTIONS[model];
    const isCompatible = inputMode === "image"
      ? (meta?.capabilities?.supports_i2v ?? opt?.supportsI2V)
      : (meta?.capabilities?.supports_t2v ?? opt?.supportsT2V);
    if (!isCompatible) {
      const fallback = availableModels[0]?.[0]
        || (inputMode === "image" ? DEFAULT_I2V_MODEL : DEFAULT_T2V_MODEL);
      if (fallback && fallback !== model) setModel(fallback);
    }
  }, [inputMode, model, modelMeta, availableModels]);

  // Duration presets follow the selected model's native fps.
  const durationPresets = useMemo(() => durationPresetsFor(model, modelMeta[model]), [model, modelMeta]);

  // A model that declares aspectRatios offers only those. Switching to one that
  // cannot render the current selection snaps it rather than leaving a ratio on
  // screen that the model will warp — 1:1 on Wan 5B being the case that bit.
  const allowedAspectRatios = useMemo(() => aspectRatiosFor(model, modelMeta[model]), [model, modelMeta]);
  useEffect(() => {
    setAspectRatio((current) => resolveAspectRatio(model, current, modelMeta[model]));
  }, [model, modelMeta]);

  // Calculate video dimensions from aspect ratio and size
  // The capability record the registry declares for the selected model
  // (modes, audio, cfg, step floor, speed profiles, style embeddings).
  const modelCaps = useMemo(() => modelMeta[model]?.capabilities || null, [model, modelMeta]);
  const activeSpeedProfile = useMemo(() => {
    const profiles = modelCaps?.speed_profiles;
    if (!profiles) return null;
    return profiles[advancedParams.speed_profile] ? advancedParams.speed_profile : "standard";
  }, [modelCaps, advancedParams.speed_profile]);

  // Seed the controls from the registry's per-VRAM-class starting settings
  // once, when the selected model's metadata arrives. Anything can be changed
  // afterwards; a later metadata refresh does not reseed.
  useEffect(() => {
    const tier = modelMeta[model]?.tier_defaults;
    if (!tier || seededTierFor.current === model) return;
    seededTierFor.current = model;
    if (tier.speed_profile) {
      setAdvancedParams((prev) => ({ ...prev, speed_profile: tier.speed_profile }));
    }
    if (tier.frames) {
      const presets = durationPresetsFor(model, modelMeta[model]);
      const key = Object.entries(presets).find(([, p]) => p.duration_frames === tier.frames)?.[0];
      if (key) setDurationPreset(key);
    }
  }, [model, modelMeta]);
  useEffect(() => {
    if (!modelCaps?.audio_in) return;
    let alive = true;
    fetch(`${API_BASE}/audio-foundry/voice-clips`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (alive) setVoiceClips(data?.clips || []); })
      .catch(() => { if (alive) setVoiceClips([]); });
    return () => { alive = false; };
  }, [modelCaps]);

  const handleAudioGuideUpload = async (file) => {
    if (!file) return;
    setIsUploadingAudioGuide(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("name", file.name);
      const res = await fetch(`${API_BASE}/audio-foundry/voice-clips/upload`, { method: "POST", body: formData });
      const data = await res.json();
      if (!res.ok || !data?.path) throw new Error(data?.error || `Upload failed: HTTP ${res.status}`);
      setAudioGuide({ path: data.path, name: file.name });
      setVoiceClips((prev) => [{ id: data.id || file.name, filename: file.name, path: data.path }, ...prev]);
    } catch (err) {
      setError(`Audio upload failed: ${err.message}`);
    } finally {
      setIsUploadingAudioGuide(false);
    }
  };

  useEffect(() => {
    setEndFrame(null);
    setAudioGuide(null);
    setSelectedPromptPreset("");
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/batch-video/prompt-presets?model=${encodeURIComponent(model)}`);
        const data = res.ok ? await res.json() : null;
        if (!alive) return;
        setPromptPresets(data?.data?.presets || []);
        setPromptGallery(data?.data?.gallery || null);
      } catch (e) {
        if (alive) {
          setPromptPresets([]);
          setPromptGallery(null);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [model]);

  const applyPromptPreset = (slug) => {
    setSelectedPromptPreset(slug);
    const preset = promptPresets.find((p) => p.slug === slug);
    if (!preset) return;
    setPromptsText(preset.prompt);
    if (preset.ratio && aspectRatiosFor(model)[preset.ratio]) setAspectRatio(preset.ratio);
    const presets = durationPresetsFor(model, modelMeta[model]);
    const key = Object.entries(presets).find(([, p]) => p.duration_frames === preset.frames)?.[0]
      || Object.entries(presets).sort((a, b) => Math.abs(a[1].duration_frames - preset.frames) - Math.abs(b[1].duration_frames - preset.frames))[0]?.[0];
    if (key) setDurationPreset(key);
    if (preset.style) setPromptStyle(preset.style);
  };

  const videoDimensions = useMemo(() => {
    // Resolve through the model: the snap effect settles a render later, and
    // a restored config can name a ratio this model never supported.
    const effectiveAspectRatio = resolveAspectRatio(model, aspectRatio);
    // CogVideoX is trained on 720x480 (3:2). Aspect-ratio math at 16:9 lands
    // on 720x405 → snaps to 720x400, which is off-spec and produces distorted
    // output every time. Pin to the model's native frame and let the user
    // letterbox / crop in post if they need a different aspect.
    if (isCogVideoXModel(model, modelMeta[model])) {
      const [nativeW, nativeH] = MODEL_OPTIONS[model].resolution;
      return { width: nativeW, height: nativeH };
    }
    // LTX-2.3 / 2.5: dims must be divisible by 32; 768×512 is the 16GB-safe pixel
    // budget. The old hard pin silently ignored the Aspect Ratio selector —
    // portrait/square batches came out landscape. Honor the aspect by
    // redistributing the SAME pixel area (constant VRAM/compute); Video Size
    // stays pinned to the budget and its dropdown is disabled for LTX.
    // MiniMax H3 uses the same fixed-budget treatment: its pixel area is what
    // costs VRAM, and the aspect ratio reshapes the frame inside it.
    if (isLtxModel(model, modelMeta[model]) || isMinimaxModel(model, modelMeta[model])) {
      const tier = modelMeta[model]?.tier_defaults;
      const [nativeW, nativeH] = tier?.width && tier?.height
        ? [tier.width, tier.height]
        : MODEL_OPTIONS[model].resolution;
      const ratioConfig = ASPECT_RATIO_PRESETS[effectiveAspectRatio] || ASPECT_RATIO_PRESETS["16:9"];
      return fitAreaToRatio(nativeW * nativeH, ratioConfig.ratio, model, modelMeta[model]);
    }

    const ratioConfig = ASPECT_RATIO_PRESETS[effectiveAspectRatio] || ASPECT_RATIO_PRESETS["16:9"];
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

    // Cap total pixel area at what the model can actually sustain. Full HD
    // Square (1920×1920 = 3.7 MPx) never finished on either Wan and read as
    // "the selector is broken" — scale down preserving the chosen aspect.
    const maxArea = modelMeta[model]?.max_pixel_area ?? MODEL_OPTIONS[model]?.maxPixelArea;
    if (maxArea && width * height > maxArea) {
      const scale = Math.sqrt(maxArea / (width * height));
      width *= scale;
      height *= scale;
    }

    // Snap to the model's required alignment (registry SSOT when available).
    ({ width, height } = snapDimensions(width, height, model, modelMeta[model]));

    return { width, height };
  }, [aspectRatio, videoSize, model, modelMeta]);

  // A speed profile's LoRAs are trained for that profile's steps, cfg and shift, and
  // Wan's pair is split per expert; offered here they would stack on both experts at
  // the base settings. The profile picker is the only way to use them.
  const applicableAdapters = useMemo(() => {
    const owned = new Set();
    Object.values(modelCaps?.speed_profiles || {}).forEach((spec) => {
      if (spec?.lora) owned.add(spec.lora);
      Object.values(spec?.loras || {}).forEach((id) => owned.add(id));
    });
    return (adapterModels || []).filter((m) => {
      const applies = m.applies_to || [];
      return (applies.length === 0 || applies.includes(model)) && !owned.has(m.id);
    });
  }, [adapterModels, model, modelCaps]);
  const applicableEncoders = useMemo(
    () => (encoderModels || []).filter((m) => (m.applies_to || []).includes(model)),
    [encoderModels, model],
  );

  // Compute final params from presets
  const computedParams = useMemo(() => {
    const quality = QUALITY_PRESETS[qualityPreset] || QUALITY_PRESETS.standard;
    const currentDurationPresets = durationPresetsFor(model, modelMeta[model]);
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

    // A model may declare the fewest steps at which it produces something worth
    // looking at. Below it the output is not "faster", it is wrong — smeared,
    // under-resolved, colour-bled — and a quality preset offering that is a
    // preset offering a broken render. CogVideoX needs 50; Wan needs 20.
    //
    // An explicit value in Advanced still wins: the floor exists to stop a preset
    // silently choosing a bad number, not to overrule someone who typed one.
    const profileSpec = activeSpeedProfile ? modelCaps?.speed_profiles?.[activeSpeedProfile] : null;
    const stepsTyped = !(advancedParams.num_inference_steps === null || advancedParams.num_inference_steps === undefined);
    const declaredMin = profileSpec?.min_steps ?? modelConfig.minSteps ?? modelCaps?.min_steps ?? (isCogVideoXModel(model, modelMeta[model]) ? 50 : null);
    if (declaredMin && effectiveSteps < declaredMin && !stepsTyped) {
      effectiveSteps = declaredMin;
    }
    // A speed profile (turbo LoRA) is trained for its own step count; the
    // quality preset's number is for the base sampler and is not used.
    if (profileSpec?.steps && !stepsTyped) {
      effectiveSteps = profileSpec.steps;
    }

    // LTX distilled is trained for 8 steps @ CFG=1 — quality presets that
    // push 30–50 steps waste time and can degrade distilled output.
    if (isLtxModel(model, modelMeta[model]) &&
        (advancedParams.num_inference_steps === null || advancedParams.num_inference_steps === undefined)) {
      effectiveSteps = modelConfig.defaultSteps || 8;
    }

    // Low VRAM safe preset for CogVideoX on 16GB GPUs
    // Very aggressive settings based on successful test: 8 frames, 15 steps, 480x320.
    // (cogvideox-2b was retired; cogvideox-5b stays and is tamed via the clamps below.)
    if (lowVramMode && isCogVideoXModel(model, modelMeta[model])) {
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
      ({ width, height } = snapDimensions(width, height, effectiveModel, modelMeta[effectiveModel]));

      // Low-VRAM may cut resolution and frames, never steps below the floor.
      const cogMin = modelConfig.minSteps || 50;
      if (effectiveSteps > cogMin) {
        effectiveSteps = cogMin;
      }
    }

    // Low VRAM safe preset for Wan 2.2 on 16GB GPUs
    // GGUF Q5 is already memory-efficient; moderate clamping
    if (lowVramMode && isWanModel(model, modelMeta[model])) {
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
      ({ width, height } = snapDimensions(width, height, effectiveModel, modelMeta[effectiveModel]));

      // Moderate step reduction
      if (effectiveSteps > 20) {
        effectiveSteps = 20;
      }
    }

    // High resolution mode — trade steps/frames for pixels.
    // Area-based so a square frame gets the same guard as widescreen with the
    // same pixel count (992×992 ≈ 1280×736). Caps steps and frames unless the
    // user explicitly overrode them in advanced settings.
    const pixelArea = width * height;
    const isHighRes = pixelArea >= 900_000; // ≈1280×720
    if (isHighRes && !lowVramMode) {
      // Cap steps — more pixels per step means fewer steps needed for quality
      const userOverrodeSteps = advancedParams.num_inference_steps !== null && advancedParams.num_inference_steps !== undefined;
      if (!userOverrodeSteps && effectiveSteps > 30) {
        effectiveSteps = 30;
      }
      // Cap frames to keep VRAM in check on 16GB cards
      if (pixelArea >= 2_000_000 && effectiveDurationFrames > 33) {
        effectiveDurationFrames = 33; // still looks great at 1080p
      } else if (effectiveDurationFrames > 49) {
        effectiveDurationFrames = 49; // 720p HD budget on 16GB
      }
    }

    // Post-processing quality tier
    const tier = OUTPUT_QUALITY_TIERS[qualityTier] || OUTPUT_QUALITY_TIERS.standard;
    const useKeyframePath = cinematicKeyframe || selectedSubjectIds.length > 0;
    // FETA improves temporal coherence on CogVideoX — auto-on for Cinema tier.
    const effectiveFeta =
      isCogVideoXModel(effectiveModel, modelMeta[effectiveModel]) &&
      (fetaEnabled || qualityTier === "cinema" || highConsistencyMode);

    // Build final params - don't spread quality since it has legacy width/height
    // fields that shouldn't override our calculated videoDimensions for CogVideoX
    return {
      model: effectiveModel,
      duration_frames: effectiveDurationFrames,
      fps: effectiveFps,
      motion_strength: motion.motion_strength,
      width,
      height,
      num_inference_steps: effectiveSteps,
      guidance_scale: advancedParams.guidance_scale,
      generate_frames_only: advancedParams.generate_frames_only,
      frames_per_batch: lowVramMode && (isCogVideoXModel(model, modelMeta[model]) || isWanModel(model, modelMeta[model])) ? 1 : advancedParams.frames_per_batch,
      combine_frames: advancedParams.combine_frames,
      freeu: advancedParams.freeu,
      face_restore: advancedParams.face_restore,
      lora_name: advancedParams.lora_name,
      lora_strength: advancedParams.lora_strength,
      adapters: (advancedParams.adapters || [])
        .filter((id) => applicableAdapters.some((a) => a.id === id))
        .map((id) => ({ id, strength: advancedParams.adapter_strength ?? 0.7 })),
      text_encoder: applicableEncoders.some((e) => e.id === advancedParams.text_encoder)
        ? advancedParams.text_encoder
        : undefined,
      wan_sampler_profile: MODEL_OPTIONS[effectiveModel]?.samplerProfiles
        ? advancedParams.wan_sampler_profile
        : undefined,
      speed_profile: activeSpeedProfile || undefined,
      style_embedding: modelCaps?.style_embeddings?.length && advancedParams.style_embedding
        ? advancedParams.style_embedding
        : undefined,
      // The backend raises a preset value to the model's floor but keeps one
      // a person typed; tell it which this is.
      steps_explicit: stepsTyped,
      subject_ids: selectedSubjectIds,
      interpolation_multiplier: tier.interpolation,
      upscale: tier.upscale || postUpscale,
      prompt_style: promptStyle,
      enhance_prompt: enhancePrompt,
      director_mode: directorMode,
      cinematic_keyframe: cinematicKeyframe || selectedSubjectIds.length > 0,
      director_guidance: directorGuidance.trim() || null,
      feta_weight: effectiveFeta ? fetaWeight : null,
      metadata: {
        ...(useKeyframePath
          ? {
              keyframe_model:
                selectedSubjectIds.length > 0 ? "from-lora" : keyframeModel,
            }
          : {}),
      },
    };
  }, [qualityPreset, durationPreset, motionPreset, model, advancedParams, videoDimensions, lowVramMode, qualityTier, promptStyle, enhancePrompt, directorMode, cinematicKeyframe, directorGuidance, fetaEnabled, fetaWeight, selectedSubjectIds, keyframeModel, postUpscale, highConsistencyMode, modelMeta, modelCaps, activeSpeedProfile, applicableAdapters, applicableEncoders]);

  const qualityChipOptions = useMemo(() => {
    if (isLtxModel(model, modelMeta[model])) return [];
    if (lowVramMode && (isWanModel(model, modelMeta[model]) || isCogVideoXModel(model, modelMeta[model]))) return [];
    const min = MODEL_OPTIONS[model]?.minSteps ?? modelCaps?.min_steps ?? 0;
    const seen = new Set();
    const out = [];
    Object.entries(QUALITY_PRESETS).forEach(([key, preset]) => {
      const steps = Math.max(preset.num_inference_steps, min);
      if (seen.has(steps)) return;
      seen.add(steps);
      out.push({ value: key, label: stripGlyph(preset.label), tooltip: `${steps} steps` });
    });
    return out;
  }, [model, lowVramMode, modelMeta, modelCaps]);

  useEffect(() => {
    if (qualityChipOptions.length === 0) return;
    if (!qualityChipOptions.some((o) => o.value === qualityPreset)) {
      setQualityPreset(qualityChipOptions[0].value);
    }
  }, [qualityChipOptions, qualityPreset]);

  const {
    activeBatchId,
    setActiveBatchId,
    batchStatus,
    setBatchStatus,
    batches,
    queue,
    fetchBatches,
    fetchQueue,
    startPollingStatus,
    handleDownloadBatch,
    handleCombineFrames,
    handleDeleteBatch,
    handleCancelBatch,
    handleRetryBatch,
    handleClearCompletedQueue,
  } = useBatchVideo({ setError, setSuccess, computedParams });

  // Fetch enhanced prompt preview from backend (re-uses the same enhance_video_prompt logic + fidelity_mode)
  const fetchPromptPreview = async () => {
    // Compute first prompt locally to avoid forward-reference issues with parsedPrompts const
    const firstLine = (promptsText || "").split("\n").map((p) => p.trim()).filter(Boolean)[0] || "";
    const basePrompt = inputMode === "text" ? (firstLine || promptsText.trim()) : promptsText.trim();
    if (!basePrompt) {
      setPreviewEnhanced("");
      return;
    }
    setPreviewLoading(true);
    try {
      const res = await fetch(`${API_BASE}/batch-video/enhance-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: basePrompt,
          prompt_style: promptStyle,
          enhance_prompt: enhancePrompt,
          fidelity_mode: fidelityMode,
          model,
          width: (computedParams && computedParams.width) || videoDimensions.width,
          height: (computedParams && computedParams.height) || videoDimensions.height,
        }),
      });
      const data = await res.json();
      if (data.success && data.data) {
        setPreviewEnhanced(data.data.enhanced_prompt || "");
        setShowPreview(true);
      } else {
        setPreviewEnhanced("");
      }
    } catch (e) {
      console.error("Prompt preview failed", e);
      setPreviewEnhanced("");
    } finally {
      setPreviewLoading(false);
    }
  };

  const parsedPrompts = useMemo(() => {
    return promptsText
      .split("\n")
      .map((p) => p.trim())
      .filter(Boolean);
  }, [promptsText]);

  // File upload handling
  // Upload one end frame through the same route as start images and keep its
  // server path; sent as last_frame_paths, one per start image.
  const handleEndFrameUpload = useCallback(async (file) => {
    if (!file) return;
    setIsUploadingEndFrame(true);
    try {
      const formData = new FormData();
      formData.append("files", file);
      const response = await fetch(`${API_BASE}/batch-image/upload`, { method: "POST", body: formData });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || `Upload failed: HTTP ${response.status}`);
      }
      const data = await response.json();
      if (data.success && data.data.batch_id) {
        const statusRes = await fetch(`${API_BASE}/batch-image/status/${data.data.batch_id}?include_results=true`);
        const statusData = statusRes.ok ? await statusRes.json() : null;
        const first = statusData?.data?.results?.find((r) => r.success && r.image_path);
        if (first) {
          setEndFrame({ path: first.image_path, name: file.name });
          return;
        }
      }
      throw new Error("The end frame did not upload");
    } catch (err) {
      setError(`End frame upload failed: ${err.message}`);
    } finally {
      setIsUploadingEndFrame(false);
    }
  }, []);

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

    // Pre-flight: never silently substitute a worse model. If the model the user
    // selected isn't fully installed, the backend falls back to whatever IS
    // installed (e.g. Wan 2.2 I2V → CogVideoX I2V) and hands back a low-quality
    // result the user never asked for — then blames the product. Stop here and
    // route them to install it. Fail-OPEN when readiness is unknown (API down /
    // offline) so we never block a legitimately-installed model.
    const effectiveModelId = computedParams.model || model;
    const selMeta = modelMeta[effectiveModelId];
    if (selMeta && selMeta.is_ready === false) {
      const niceName = selMeta.name || MODEL_OPTIONS[effectiveModelId]?.label || effectiveModelId;
      setModelNotReady({ id: effectiveModelId, name: niceName, missing: selMeta.missing_files || [] });
      setHighlightModelId(effectiveModelId);
      setVideoModelsModalOpen(true);
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

      const trimmedNeg = negativePrompt.trim();
      const negativePayload = trimmedNeg ? { negative_prompt: trimmedNeg } : {};

      // Storyboard mode (text only): the whole prompt box is ONE concept the Director
      // expands into N shots. The backend creates N items and writes the shots.
      const storyboardPayload =
        storyboardMode && inputMode === "text"
          ? { storyboard_concept: (promptsText || "").trim(), storyboard_shots: storyboardShots }
          : {};

      // Exact control-panel snapshot so "Adjust & Retry" can restore this batch verbatim.
      const uiConfig = {
        inputMode, promptsText, lookAndFeel, model,
        qualityPreset, durationPreset, motionPreset, aspectRatio, videoSize,
        promptStyle, cinematicKeyframe, fidelityMode, negativePrompt,
        storyboardMode, storyboardShots, lowVramMode, highConsistencyMode, advancedParams,
        selectedImages,
      };

      const guidePayload =
        audioGuide?.path && modelCaps?.audio_in
          ? { guides: (inputMode === "text" ? finalPrompts : imagePaths).map(() => [
              { kind: "audio", path: audioGuide.path, frame_idx: 0 },
            ]) }
          : {};
      const body =
        inputMode === "text"
          ? {
              prompts: finalPrompts,
              ...guidePayload,
              ...computedParams,
              fidelity_mode: fidelityMode,
              high_consistency: highConsistencyMode,
              ...storyboardPayload,
              ...negativePayload,
              ui_config: uiConfig,
            }
          : {
              image_paths: imagePaths,
              ...(endFrame?.path && modelCaps?.modes?.includes("flf2v")
                ? { last_frame_paths: imagePaths.map(() => endFrame.path) }
                : {}),
              ...guidePayload,
              prompt: lf && motionPrompt ? `${motionPrompt}, ${lf}` : motionPrompt,
              ...computedParams,
              fidelity_mode: fidelityMode,
              high_consistency: highConsistencyMode,
              ...negativePayload,
              ui_config: uiConfig,
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
        setError(formatUiError(errorData.error || errorData.message) || `Failed to queue batch: HTTP ${res.status}`);
        return;
      }

      const data = await res.json();
      if (!data.success) {
        setError(formatUiError(data.error || data.message) || "Failed to queue batch");
        return;
      }

      const batchId = data.data.batch_id;
      setActiveBatchId(batchId);
      const gpuMsg = data.data?.gpu?.message;
      setSuccess(
        gpuMsg
          ? `Batch queued. ${gpuMsg}`
          : "Batch queued. The worker drains one batch at a time — keep stacking 'em."
      );
      startPollingStatus(batchId);
      await fetchBatches();
      await fetchQueue();

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

  // Adjust & Retry: load a batch's saved control-panel config back into the form so
  // the user can tweak one thing and re-generate (instead of remembering all the
  // settings). New batches store an exact `ui_config` snapshot → restored verbatim;
  // older batches fall back to the core fields from the persisted params.
  const handleAdjustRetry = async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/status/${batchId}`);
      if (!res.ok) { setError(`Couldn't load settings: HTTP ${res.status}`); return; }
      const data = await res.json();
      const rd = data?.data?.retry_data || data?.retry_data;
      const name = data?.data?.metadata?.display_name || batchId.slice(0, 8);
      if (!rd) { setError("This batch didn't store its settings."); return; }

      const restoreImagesFromPaths = (paths) => {
        if (!Array.isArray(paths) || paths.length === 0) return;
        const restored = paths.map((p, idx) => {
          const parts = (p || "").replace(/\\/g, '/').split('/');
          const filename = parts[parts.length - 1] || `image_${idx}.png`;
          const match = (p || "").replace(/\\/g, '/').match(/\/image_batches\/([^/]+)\//);
          const imgBatchId = match ? match[1] : batchId;
          return {
            id: `${imgBatchId}_${filename}`,
            path: p,
            thumbnailUrl: `${API_BASE}/batch-image/image/${imgBatchId}/${encodeURIComponent(filename)}?thumbnail=true`,
            name: filename,
            batchId: imgBatchId,
          };
        });
        setSelectedImages(restored);
      };

      const cfg = rd.params?.ui_config;
      if (cfg) {
        // Exact round-trip — restore the panel verbatim from the saved snapshot.
        if (cfg.inputMode) setInputMode(cfg.inputMode);
        if (typeof cfg.promptsText === "string") setPromptsText(cfg.promptsText);
        if (typeof cfg.lookAndFeel === "string") setLookAndFeel(cfg.lookAndFeel);
        if (cfg.model) setModel(cfg.model);
        if (cfg.qualityPreset) setQualityPreset(cfg.qualityPreset);
        if (cfg.durationPreset) setDurationPreset(cfg.durationPreset);
        if (cfg.motionPreset) setMotionPreset(cfg.motionPreset);
        if (cfg.aspectRatio) setAspectRatio(cfg.aspectRatio);
        if (cfg.videoSize) setVideoSize(cfg.videoSize);
        if (cfg.promptStyle) setPromptStyle(cfg.promptStyle);
        if (typeof cfg.cinematicKeyframe === "boolean") setCinematicKeyframe(cfg.cinematicKeyframe);
        if (typeof cfg.fidelityMode === "boolean") setFidelityMode(cfg.fidelityMode);
        if (typeof cfg.negativePrompt === "string") setNegativePrompt(cfg.negativePrompt);
        if (typeof cfg.storyboardMode === "boolean") setStoryboardMode(cfg.storyboardMode);
        if (cfg.storyboardShots) setStoryboardShots(cfg.storyboardShots);
        if (typeof cfg.lowVramMode === "boolean") setLowVramMode(cfg.lowVramMode);
        if (cfg.advancedParams && typeof cfg.advancedParams === "object") setAdvancedParams(cfg.advancedParams);

        if (Array.isArray(cfg.selectedImages) && cfg.selectedImages.length > 0) {
          setSelectedImages(cfg.selectedImages);
        } else if (rd.image_paths || rd.params?.image_paths) {
          restoreImagesFromPaths(rd.image_paths || rd.params?.image_paths);
        }

        setSuccess(`Loaded "${name}" settings into the panel — adjust anything, then Generate.`);
      } else {
        // Older batch (no snapshot): restore the core from the stored params.
        const p = rd.params || {};
        if (rd.mode === "image") {
          setInputMode("image");
          if (rd.prompt) setPromptsText(rd.prompt);
          restoreImagesFromPaths(rd.image_paths || p.image_paths);
        } else if (Array.isArray(rd.prompts)) {
          setPromptsText(rd.prompts.join("\n"));
        }
        if (p.model) setModel(p.model);
        if (typeof p.negative_prompt === "string") setNegativePrompt(p.negative_prompt);
        if (p.prompt_style) setPromptStyle(p.prompt_style);
        if (typeof p.fidelity_mode === "boolean") setFidelityMode(p.fidelity_mode);
        setAdvancedParams((prev) => ({
          ...prev,
          // Stored steps are what the batch ran, not what the person typed: a preset
          // or speed profile chose them unless steps_explicit says otherwise. Loading
          // them as typed would bypass the step floor and the profile's own count.
          num_inference_steps: p.metadata?.steps_explicit ? (p.num_inference_steps ?? prev.num_inference_steps) : null,
          speed_profile: p.speed_profile || "standard",
          style_embedding: p.style_embedding || "",
          guidance_scale: p.guidance_scale ?? prev.guidance_scale,
          wan_sampler_profile: p.wan_sampler_profile ?? prev.wan_sampler_profile,
          freeu: !!p.freeu,
          face_restore: !!p.face_restore,
          lora_name: p.lora_name || "",
          lora_strength: p.lora_strength ?? prev.lora_strength,
          frames_per_batch: p.frames_per_batch ?? prev.frames_per_batch,
          combine_frames: !!p.combine_frames,
        }));
        setSuccess(`Loaded "${name}" core settings (older batch — double-check resolution/duration).`);
      }
      setError("");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(`Couldn't load settings: ${e.message}`);
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

  // Live per-step progress for the currently-rendering video. The batch bar only
  // moves when a whole clip finishes; THIS shows "denoising 12/50" inside the
  // active clip, fed by the ComfyUI ws progress bridge (process_type=video_render,
  // process_id=item_id). Single GPU = at most one active render, so we just take
  // the freshest non-terminal video_render process (preferring this batch's).
  const { getProcessesByType, activeProcesses, getPreviewUrl } = useUnifiedProgress();
  const activeStep = useMemo(() => {
    if (!batchStatus || batchStatus.status !== "running") return null;
    const live = (getProcessesByType("video_render") || []).filter((p) =>
      !["complete", "end", "error", "cancelled"].includes(p.status)
    );
    if (!live.length) return null;
    const mine = live.filter((p) => p.additional_data?.batch_id === batchStatus.batch_id);
    const pool = mine.length ? mine : live;
    return pool.reduce((a, b) => (b.timestamp > a.timestamp ? b : a));
  }, [batchStatus, getProcessesByType, activeProcesses]);

  const controlsDisabled = isGenerating;
  const canQueue =
    !isGenerating && (inputMode === "text" ? parsedPrompts.length > 0 : selectedImages.length > 0);
  const { gpuBusy, blockReason } = useJobsGate({ submitMode: "queue" });
  const castIdentityLocked = selectedSubjectIds.length > 0;
  const keyframeModelOptions = useMemo(() => {
    // With cast: family is auto from the character LoRA (backend ignores manual model).
    if (castIdentityLocked) {
      return Object.entries(KEYFRAME_MODEL_OPTIONS).filter(([key]) => key === "from-lora");
    }
    return Object.entries(KEYFRAME_MODEL_OPTIONS).filter(([key]) => key !== "from-lora");
  }, [castIdentityLocked]);

  return (
    <PageLayout title={embedded ? undefined : "Video Generation"} variant={embedded ? "fullscreen" : "standard"} noPadding={embedded}>

      {/* Error/Success Messages */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError('')}>
          {formatUiError(error)}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 3 }} onClose={() => setSuccess('')}>
          {formatUiError(success) || String(success)}
        </Alert>
      )}

      {/* Selected model not installed — block generation rather than silently
          downgrade to a worse model. The action button reopens the install
          modal and pulses the exact model to download. */}
      {modelNotReady && (
        <Alert
          severity="warning"
          sx={{ mb: 3 }}
          onClose={() => setModelNotReady(null)}
          action={
            <Button
              color="inherit"
              size="small"
              startIcon={<SettingsIcon />}
              onClick={() => { setHighlightModelId(modelNotReady.id); setVideoModelsModalOpen(true); }}
            >
              Download {modelNotReady.name}
            </Button>
          }
        >
          <strong>{modelNotReady.name} isn’t fully installed.</strong> Generating now would
          silently fall back to a lower-quality model — so it’s blocked. Install it first
          {modelNotReady.missing?.length
            ? ` (${modelNotReady.missing.length} file${modelNotReady.missing.length > 1 ? "s" : ""} missing)`
            : ""}, then generate again.
        </Alert>
      )}

      <DashboardStrip>
        <DashboardTile
          label="Model"
          value={MODEL_OPTIONS[model]?.label || model}
          sub={modelMeta[model]?.is_ready === false ? "not installed" : accelLabel || "readiness unknown"}
          tone={modelMeta[model]?.is_ready === false ? "error" : modelMeta[model]?.is_ready ? "ok" : "off"}
          onClick={() => setVideoModelsModalOpen(true)}
        />
        <DashboardTile
          label="Frame"
          value={`${computedParams.width}×${computedParams.height}`}
          sub={`${computedParams.duration_frames} frames · ${computedParams.fps} fps`}
        />
        <DashboardTile
          label="Steps"
          value={String(computedParams.num_inference_steps)}
          sub={lowVramMode ? "Low VRAM clamp" : qualityChipOptions.find((o) => o.value === qualityPreset)?.label || "preset"}
        />
        <DashboardTile
          label="Queue"
          value={
            queue.filter((q) => ["queued", "running", "pending"].includes(q.status)).length
              ? `${queue.filter((q) => ["queued", "running", "pending"].includes(q.status)).length} active`
              : "Idle"
          }
          tone={gpuBusy ? "warn" : "ok"}
          sub={gpuBusy ? blockReason || "GPU busy" : "ready to queue"}
        />
      </DashboardStrip>

      <Grid container spacing={3} sx={{ mt: 0.5 }}>
        {/* Compose */}
        <Grid item xs={12} lg={6}>
          <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5, opacity: controlsDisabled ? 0.5 : 1, pointerEvents: controlsDisabled ? "none" : "auto" }}>
            <SettingsPanel
              id="videogen-mode"
              title="Mode"
              description="How this job spends the GPU."
              actions={
                <ActionButton
                  kind={canQueue ? "primary" : "neutral"}
                  onClick={handleGenerate}
                  loading={isGenerating}
                  disabled={!canQueue || controlsDisabled}
                >
                  Add to queue
                </ActionButton>
              }
            >
              <Line>
                <SettingChip
                  label="Low VRAM"
                  on={lowVramMode}
                  note={lowVramMode ? "next job" : undefined}
                  tooltip="Reduces frames, resolution, and steps on 16GB cards. Default on."
                  onToggle={(next) => {
                    setLowVramMode(next);
                    localStorage.setItem("lowVramMode", next.toString());
                    if (next && highConsistencyMode) setHighConsistencyMode(false);
                  }}
                />
                <SettingChip
                  label="High consistency"
                  on={highConsistencyMode}
                  tooltip="Director, keyframe→I2V, cinema post, face restore when available. Turns Low VRAM off."
                  onToggle={(next) => {
                    setHighConsistencyMode(next);
                    if (next && lowVramMode) {
                      setLowVramMode(false);
                      localStorage.setItem("lowVramMode", "false");
                    }
                  }}
                />
              </Line>
            </SettingsPanel>

            <SettingsPanel id="videogen-prompt" title="Prompt">
              <Cluster label="Input">
                <ChoiceChips
                  ariaLabel="Input mode"
                  value={inputMode}
                  onChange={setInputMode}
                  options={[
                    { value: "text", label: "Text" },
                    { value: "image", label: "Image" },
                  ]}
                />
              </Cluster>
              {inputMode === "text" && promptPresets.length > 0 && (
                <Cluster label="Preset">
                  <Line nowrap>
                    <FormControl size="small" className="grow">
                      <InputLabel>Prompt preset</InputLabel>
                      <Select
                        value={selectedPromptPreset}
                        onChange={(e) => applyPromptPreset(e.target.value)}
                        label="Prompt preset"
                      >
                        <MenuItem value="">None</MenuItem>
                        {promptPresets.map((p) => (
                          <MenuItem key={p.slug} value={p.slug}>
                            {p.title}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                  </Line>
                  {promptGallery?.source?.url && (
                    <Hint>
                      Written in the model&apos;s structured prompt format. More in the{" "}
                      <a href={promptGallery.source.url} target="_blank" rel="noreferrer noopener">
                        community gallery
                      </a>
                      .
                    </Hint>
                  )}
                </Cluster>
              )}
              {inputMode === "text" ? (
                <TextField
                  label={storyboardMode ? "Storyboard concept" : "What do you want to see? (one prompt per line)"}
                  multiline
                  minRows={storyboardMode ? 2 : 3}
                  maxRows={storyboardMode ? 4 : 6}
                  value={promptsText}
                  onChange={(e) => setPromptsText(e.target.value)}
                  placeholder={
                    storyboardMode
                      ? "A lone astronaut discovers a bioluminescent forest on an alien moon"
                      : "A majestic eagle soaring over mountains at sunset"
                  }
                  helperText={
                    storyboardMode
                      ? `One concept only — becomes ${storyboardShots} connected shots.`
                      : parsedPrompts.length > 1
                        ? `${parsedPrompts.length} clips — Look & Feel applies to all.`
                        : undefined
                  }
                  fullWidth
                  size="small"
                />
              ) : (
                <Box>
                  <TextField
                    label="Describe the motion or action (optional)"
                    multiline
                    minRows={2}
                    maxRows={4}
                    value={promptsText}
                    onChange={(e) => setPromptsText(e.target.value)}
                    placeholder="Slow camera zoom in with a gentle head turn"
                    fullWidth
                    size="small"
                    sx={{ mb: 1.5 }}
                  />
                  <Box
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    onClick={() => fileInputRef.current?.click()}
                    sx={{
                      border: "2px dashed",
                      borderColor: dragActive ? "primary.main" : "divider",
                      borderRadius: "8px",
                      p: 2,
                      textAlign: "center",
                      bgcolor: dragActive ? "action.hover" : "transparent",
                      cursor: "pointer",
                    }}
                  >
                    {isUploading ? (
                      <CircularProgress size={28} />
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        Drop images here, or click to upload
                      </Typography>
                    )}
                  </Box>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept="image/*"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      if (e.target.files && e.target.files.length > 0) {
                        handleFileUpload(Array.from(e.target.files));
                        e.target.value = "";
                      }
                    }}
                  />
                  <Line sx={{ mt: 1 }}>
                    <ActionButton onClick={openGallery}>Select from gallery</ActionButton>
                  </Line>
                  {selectedImages.length > 0 && (
                    <Box sx={{ mt: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
                      {selectedImages.map((img) => (
                        <Box key={img.id} sx={{ position: "relative", width: 72, height: 72, borderRadius: "6px", overflow: "hidden" }}>
                          <Box component="img" src={img.thumbnailUrl} alt={img.name} sx={{ width: "100%", height: "100%", objectFit: "cover" }} />
                          <IconButton
                            size="small"
                            onClick={() => removeSelectedImage(img.id)}
                            sx={{ position: "absolute", top: 2, right: 2, bgcolor: "rgba(0,0,0,0.6)", color: "white", p: 0.25 }}
                          >
                            <CloseIcon sx={{ fontSize: 14 }} />
                          </IconButton>
                        </Box>
                      ))}
                    </Box>
                  )}
                </Box>
              )}
              <TextField
                label="Look & Feel"
                multiline
                minRows={2}
                maxRows={4}
                value={lookAndFeel}
                onChange={(e) => setLookAndFeel(e.target.value)}
                placeholder="moody cinematic, golden hour, shallow depth of field"
                helperText={lookAndFeel.trim() ? `Appended to ${parsedPrompts.length || 0} prompt(s).` : "Optional style applied to every prompt."}
                fullWidth
                size="small"
              />
              <TextField
                label="Negative prompt"
                multiline
                minRows={2}
                maxRows={4}
                value={negativePrompt}
                disabled={modelCaps?.cfg === false || isMinimaxModel(model, modelMeta[model])}
                onChange={(e) => setNegativePrompt(e.target.value)}
                placeholder="blurry, distorted hands, flicker"
                helperText={
                  modelCaps?.cfg === false || isMinimaxModel(model, modelMeta[model])
                    ? "This model samples without CFG — negative prompt is not used."
                    : undefined
                }
                fullWidth
                size="small"
              />
              <Line>
                <SettingChip
                  label="Exact text"
                  on={fidelityMode}
                  tooltip="Light enhancement only — keeps on-screen text and logos."
                  onToggle={(next) => {
                    setFidelityMode(next);
                    setPreviewEnhanced("");
                    setShowPreview(false);
                  }}
                />
                <SettingChip
                  label="Enhance prompt"
                  on={enhancePrompt}
                  tooltip="Adds quality and motion descriptors. Motion chips do nothing when this is off."
                  onToggle={setEnhancePrompt}
                />
                <ActionButton
                  onClick={fetchPromptPreview}
                  loading={previewLoading}
                  disabled={!((inputMode === "text" ? parsedPrompts.length : promptsText.trim()) > 0)}
                >
                  Preview enhanced
                </ActionButton>
              </Line>
              {showPreview && previewEnhanced && (
                <TextField
                  label="Enhanced prompt (what will be sent)"
                  value={previewEnhanced}
                  multiline
                  minRows={2}
                  fullWidth
                  size="small"
                  InputProps={{ readOnly: true }}
                />
              )}
            </SettingsPanel>

            <SettingsPanel
              id="videogen-pipeline"
              title="Pipeline"
              description="Shot writing and identity. High consistency turns these on."
            >
              <Line>
                <SettingChip
                  label="Director"
                  on={directorMode}
                  tooltip="Rewrites each prompt into a cinematic shot before generation."
                  onToggle={setDirectorMode}
                />
                <SettingChip
                  label="Keyframe → I2V"
                  on={cinematicKeyframe || selectedSubjectIds.length > 0}
                  disabled={selectedSubjectIds.length > 0}
                  tooltip={
                    selectedSubjectIds.length > 0
                      ? "On because a cast member is selected."
                      : "Still first, then image-to-video. Sharper than pure text-to-video."
                  }
                  onToggle={setCinematicKeyframe}
                />
                <SettingChip
                  label="Storyboard"
                  on={storyboardMode}
                  tooltip="One concept becomes N connected shots."
                  onToggle={setStoryboardMode}
                />
                {storyboardMode && (
                  <TextField
                    type="number"
                    label="Shots"
                    value={storyboardShots}
                    onChange={(e) => setStoryboardShots(Math.max(1, Math.min(50, parseInt(e.target.value, 10) || 1)))}
                    size="small"
                    sx={{ width: 96 }}
                    inputProps={{ min: 1, max: 50 }}
                  />
                )}
              </Line>
              {(directorMode || storyboardMode) && (
                <TextField
                  value={directorGuidance}
                  onChange={(e) => setDirectorGuidance(e.target.value)}
                  placeholder="Director guidance (handheld 35mm, slow push-ins…)"
                  size="small"
                  fullWidth
                />
              )}
              <Collapse in={cinematicKeyframe || selectedSubjectIds.length > 0}>
                {castIdentityLocked ? (
                  <Hint>Keyframe model is the character&apos;s training base. Training images are never start frames.</Hint>
                ) : (
                  <FormControl size="small" fullWidth sx={{ mt: 0.5 }}>
                    <InputLabel>Keyframe image model</InputLabel>
                    <Select
                      value={keyframeModel === "from-lora" ? DEFAULT_KEYFRAME_MODEL : keyframeModel}
                      onChange={(e) => setKeyframeModel(e.target.value)}
                      label="Keyframe image model"
                    >
                      {keyframeModelOptions.map(([key, cfg]) => (
                        <MenuItem key={key} value={key}>
                          {cfg.label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                )}
              </Collapse>
              <FormControl size="small" fullWidth>
                <InputLabel>Cast</InputLabel>
                <Select
                  multiple
                  value={selectedSubjectIds}
                  onChange={(e) => {
                    const v = e.target.value;
                    setSelectedSubjectIds(typeof v === "string" ? v.split(",").map(Number) : v);
                  }}
                  label="Cast"
                  renderValue={(sel) =>
                    castSubjects.filter((s) => sel.includes(s.id)).map((s) => s.name).join(", ") || "None"
                  }
                >
                  {castSubjects.map((s) => (
                    <MenuItem key={s.id} value={s.id}>
                      {s.name}{s.trigger_word ? ` (${s.trigger_word})` : ""}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Hint>
                {castSubjects.length === 0
                  ? "Train a character in Cast Library to lock identity into every keyframe."
                  : "LoRA + prompt invent each keyframe still, then animate."}
              </Hint>
            </SettingsPanel>

            <SettingsPanel id="videogen-clip" title="Clip">
              <Cluster label="Model">
                <Line nowrap>
                  <FormControl size="small" className="grow">
                    <InputLabel>Model</InputLabel>
                    <Select value={model} onChange={(e) => setModel(e.target.value)} label="Model">
                      {availableModels.map(([key, opt]) => (
                        <MenuItem key={key} value={key}>
                          {opt.label}
                        </MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <ActionButton onClick={() => setVideoModelsModalOpen(true)}>Manage models</ActionButton>
                  <ActionButton kind="link" onClick={() => window.open("http://localhost:8188", "_blank")}>
                    ComfyUI
                  </ActionButton>
                </Line>
                {applicableAdapters.length > 0 && (
                  <>
                    <Line wrap>
                      {applicableAdapters.map((a) => {
                        const on = (advancedParams.adapters || []).includes(a.id);
                        return (
                          <SettingChip
                            key={a.id}
                            label={a.name}
                            on={on}
                            onToggle={() => {
                              setAdvancedParams((prev) => {
                                const cur = prev.adapters || [];
                                return {
                                  ...prev,
                                  adapters: on ? cur.filter((id) => id !== a.id) : [...cur, a.id],
                                };
                              });
                            }}
                          />
                        );
                      })}
                    </Line>
                    <Hint>LoRAs you added in Manage models. Off until you turn one on. Strength 0.7.</Hint>
                  </>
                )}
                {applicableEncoders.length > 0 && (
                  <>
                    <Line>
                      <FormControl size="small" sx={{ minWidth: 260 }}>
                        <InputLabel>Text encoder</InputLabel>
                        <Select
                          value={applicableEncoders.some((e) => e.id === advancedParams.text_encoder) ? advancedParams.text_encoder : ""}
                          label="Text encoder"
                          onChange={(e) => setAdvancedParams((prev) => ({ ...prev, text_encoder: e.target.value }))}
                        >
                          <MenuItem value="">Shipped with the model</MenuItem>
                          {applicableEncoders.map((e) => (
                            <MenuItem key={e.id} value={e.id}>
                              {e.name}
                            </MenuItem>
                          ))}
                        </Select>
                      </FormControl>
                    </Line>
                    <Hint>Text encoders you added in Manage models. The shipped one runs unless you pick another.</Hint>
                  </>
                )}
                {modelMeta[model]?.license?.name && (
                  <Hint>
                    {modelMeta[model].license.attribution} · {modelMeta[model].license.name}. {modelMeta[model].license.note}
                  </Hint>
                )}
                {anyModelReady === false && (
                  <Hint>No video model is installed yet — open Manage models first.</Hint>
                )}
              </Cluster>
              {qualityChipOptions.length > 0 && (
                <Cluster label="Steps" note="raised to the model floor">
                  <ChoiceChips
                    ariaLabel="Quality steps"
                    value={qualityPreset}
                    onChange={setQualityPreset}
                    options={qualityChipOptions}
                  />
                </Cluster>
              )}
              <Cluster label="Duration">
                <ChoiceChips
                  ariaLabel="Duration"
                  value={durationPreset}
                  onChange={setDurationPreset}
                  options={Object.entries(durationPresets).map(([key, preset]) => ({
                    value: key,
                    label: stripGlyph(preset.label),
                    tooltip: preset.description,
                  }))}
                />
              </Cluster>
              <Cluster label="Motion" note={enhancePrompt ? "via prompt enhancer" : "no effect while Enhance is off"}>
                <ChoiceChips
                  ariaLabel="Motion"
                  value={motionPreset}
                  onChange={setMotionPreset}
                  disabled={!enhancePrompt}
                  options={Object.entries(MOTION_PRESETS).map(([key, preset]) => ({
                    value: key,
                    label: stripGlyph(preset.label),
                    tooltip: preset.description,
                  }))}
                />
              </Cluster>
              {isCogVideoXModel(model, modelMeta[model]) ? (
                <Hint>CogVideoX is trained at {computedParams.width}×{computedParams.height}. Other aspects distort.</Hint>
              ) : (
                <>
                  <Cluster label="Aspect">
                    <ChoiceChips
                      ariaLabel="Aspect ratio"
                      value={aspectRatio}
                      onChange={setAspectRatio}
                      options={Object.entries(allowedAspectRatios).map(([key, preset]) => ({
                        value: key,
                        label: preset.label,
                        tooltip: preset.description,
                      }))}
                    />
                  </Cluster>
                  <Cluster label="Size" note={`renders ${computedParams.width}×${computedParams.height}`}>
                    <ChoiceChips
                      ariaLabel="Video size"
                      value={videoSize}
                      onChange={setVideoSize}
                      disabled={isLtxModel(model, modelMeta[model]) || isMinimaxModel(model, modelMeta[model])}
                      options={Object.entries(VIDEO_SIZE_PRESETS).map(([key, preset]) => ({
                        value: key,
                        label: stripGlyph(preset.label),
                        tooltip: preset.description,
                      }))}
                    />
                  </Cluster>
                </>
              )}
              <Cluster label="Output">
                <ChoiceChips
                  ariaLabel="Output quality"
                  value={qualityTier}
                  onChange={setQualityTier}
                  options={Object.entries(OUTPUT_QUALITY_TIERS).map(([key, tier]) => ({
                    value: key,
                    label: tier.label,
                    tooltip: tier.description,
                  }))}
                />
              </Cluster>
            </SettingsPanel>

            <SettingsPanel id="videogen-finish" title="Finish">
              <Line>
                <SettingChip
                  label="Fix anatomy"
                  on={advancedParams.face_restore}
                  disabled={!faceRestoreAvailable}
                  tooltip={
                    faceRestoreAvailable
                      ? "CodeFormer face restore"
                      : !faceRestoreNodeAvailable
                        ? "ComfyUI node missing"
                        : "CodeFormer weights not installed"
                  }
                  onToggle={(next) => setAdvancedParams({ ...advancedParams, face_restore: next })}
                  onSettings={
                    faceRestoreNodeAvailable && faceRestoreModelReady === false
                      ? () => {
                          setHighlightModelId("codeformer");
                          setVideoModelsModalOpen(true);
                        }
                      : undefined
                  }
                />
                <SettingChip
                  label="2× upscale"
                  on={postUpscale || qualityTier === "cinema"}
                  disabled={qualityTier === "cinema"}
                  tooltip={qualityTier === "cinema" ? "On because Output is Cinema." : "Real-ESRGAN after generation."}
                  onToggle={setPostUpscale}
                />
                <SettingChip
                  label="PNG frames"
                  on={advancedParams.generate_frames_only}
                  tooltip="Also save a lossless PNG sequence beside the MP4."
                  onToggle={(next) => setAdvancedParams({ ...advancedParams, generate_frames_only: next })}
                />
                {isWanModel(model, modelMeta[model]) && (
                  <SettingChip
                    label="FreeU"
                    on={advancedParams.freeu}
                    tooltip="Fine-detail boost on Wan."
                    onToggle={(next) => setAdvancedParams({ ...advancedParams, freeu: next })}
                  />
                )}
                {isCogVideoXModel(model, modelMeta[model]) && (
                  <SettingChip
                    label="FETA"
                    on={fetaEnabled || qualityTier === "cinema" || highConsistencyMode}
                    disabled={qualityTier === "cinema" || highConsistencyMode}
                    tooltip="Temporal coherence between frames."
                    onToggle={setFetaEnabled}
                  />
                )}
              </Line>
              {isCogVideoXModel(model, modelMeta[model]) && (fetaEnabled || qualityTier === "cinema" || highConsistencyMode) && (
                <TextField
                  size="small"
                  label="FETA weight"
                  type="number"
                  inputProps={{ step: 0.1, min: 0.1, max: 3.0 }}
                  value={fetaWeight}
                  onChange={(e) => setFetaWeight(Number(e.target.value))}
                  sx={{ width: 140 }}
                />
              )}
              {MODEL_OPTIONS[model]?.samplerProfiles && (
                <FormControl size="small" fullWidth>
                  <InputLabel>Sampler profile</InputLabel>
                  <Select
                    value={advancedParams.wan_sampler_profile}
                    onChange={(e) => setAdvancedParams({ ...advancedParams, wan_sampler_profile: e.target.value })}
                    label="Sampler profile"
                  >
                    {MODEL_OPTIONS[model].samplerProfiles.map((key) => (
                      <MenuItem key={key} value={key}>
                        {WAN5B_SAMPLER_PROFILES[key]?.label || key}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}
              {modelCaps?.speed_profiles && (
                <FormControl size="small" fullWidth>
                  <InputLabel>Speed profile</InputLabel>
                  <Select
                    value={activeSpeedProfile || "standard"}
                    onChange={(e) => setAdvancedParams({ ...advancedParams, speed_profile: e.target.value })}
                    label="Speed profile"
                  >
                    {Object.entries(modelCaps.speed_profiles).map(([key, spec]) => (
                      <MenuItem key={key} value={key}>
                        {spec.label || key}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}
              {modelCaps?.style_embeddings?.length > 0 && (
                <FormControl size="small" fullWidth>
                  <InputLabel>Style embedding</InputLabel>
                  <Select
                    value={advancedParams.style_embedding || ""}
                    onChange={(e) => setAdvancedParams({ ...advancedParams, style_embedding: e.target.value })}
                    label="Style embedding"
                  >
                    <MenuItem value="">None</MenuItem>
                    {modelCaps.style_embeddings.map((emb) => (
                      <MenuItem key={emb.id} value={emb.id}>{emb.label}</MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}
              {inputMode === "image" && modelCaps?.modes?.includes("flf2v") && (
                <Line>
                  <ActionButton component="label" loading={isUploadingEndFrame}>
                    {endFrame ? `End frame: ${endFrame.name}` : "Add end frame"}
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      onChange={(e) => {
                        handleEndFrameUpload(e.target.files?.[0]);
                        e.target.value = "";
                      }}
                    />
                  </ActionButton>
                  {endFrame && <ActionButton onClick={() => setEndFrame(null)}>Remove</ActionButton>}
                </Line>
              )}
              {modelCaps?.audio_in && (
                <Cluster label="Audio guide">
                  <Line nowrap>
                    <FormControl size="small" className="grow">
                      <InputLabel>Audio to perform</InputLabel>
                      <Select
                        value={audioGuide?.path || ""}
                        onChange={(e) => {
                          const clip = voiceClips.find((c) => c.path === e.target.value);
                          setAudioGuide(clip ? { path: clip.path, name: clip.filename } : null);
                        }}
                        label="Audio to perform"
                      >
                        <MenuItem value="">None</MenuItem>
                        {voiceClips.map((c) => (
                          <MenuItem key={c.id || c.path} value={c.path}>{c.filename}</MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <ActionButton component="label" loading={isUploadingAudioGuide}>
                      Upload
                      <input
                        type="file"
                        accept="audio/*"
                        hidden
                        onChange={(e) => {
                          handleAudioGuideUpload(e.target.files?.[0]);
                          e.target.value = "";
                        }}
                      />
                    </ActionButton>
                  </Line>
                </Cluster>
              )}
              <Cluster label="Prompt tuning">
                <Line>
                  <TextField
                    size="small"
                    label="Guidance"
                    type="number"
                    inputProps={{ step: 0.5, min: 1, max: 20 }}
                    value={advancedParams.guidance_scale}
                    disabled={modelCaps?.cfg === false || isMinimaxModel(model, modelMeta[model])}
                    onChange={(e) => setAdvancedParams({ ...advancedParams, guidance_scale: Number(e.target.value) })}
                    sx={{ width: 120 }}
                  />
                  <FormControl size="small" sx={{ minWidth: 180 }}>
                    <InputLabel>Prompt style</InputLabel>
                    <Select value={promptStyle} onChange={(e) => setPromptStyle(e.target.value)} label="Prompt style">
                      {Object.entries(PROMPT_STYLES).map(([key, preset]) => (
                        <MenuItem key={key} value={key}>{preset.label}</MenuItem>
                      ))}
                    </Select>
                  </FormControl>
                  <TextField
                    label="Frames / batch"
                    type="number"
                    size="small"
                    inputProps={{ min: 1, max: 8 }}
                    value={advancedParams.frames_per_batch}
                    onChange={(e) => {
                      const v = Math.max(1, parseInt(e.target.value || "1", 10));
                      setAdvancedParams((prev) => ({ ...prev, frames_per_batch: v }));
                    }}
                    sx={{ width: 140 }}
                  />
                </Line>
              </Cluster>
              <VideoGenEffectiveSettings
                model={model}
                computedParams={computedParams}
                capabilities={modelCaps}
                cinematicKeyframe={cinematicKeyframe}
                selectedSubjectIds={selectedSubjectIds}
                keyframeModel={keyframeModel}
                directorMode={directorMode}
                faceRestore={advancedParams.face_restore}
                freeu={advancedParams.freeu}
              />
              <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} queueMode />
              <ActionButton
                kind={canQueue ? "primary" : "neutral"}
                onClick={handleGenerate}
                loading={isGenerating}
                disabled={!canQueue || controlsDisabled}
                sx={{ height: 36, alignSelf: "flex-start" }}
              >
                Add to queue
              </ActionButton>
            </SettingsPanel>
          </Box>
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
                  <Stack direction="row" spacing={1} alignItems="center">
                    {queue.some((q) => ["completed", "error", "cancelled"].includes(q.status)) && (
                      <Button
                        size="small"
                        variant="outlined"
                        color="inherit"
                        onClick={handleClearCompletedQueue}
                        sx={{ textTransform: "none", borderRadius: 1 }}
                      >
                        Clear Completed
                      </Button>
                    )}
                    <Chip
                      label={`${queue.filter(q => q.status === 'queued' || q.status === 'running' || q.status === 'pending').length} active`}
                      size="small"
                      color="primary"
                      variant="outlined"
                    />
                  </Stack>
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
                                onClick={() => setConfirmAction({
                                  title: "Cancel this batch?",
                                  description: "Stops the GPU job. Clips already written stay on disk.",
                                  facts: [{ label: "Batch", value: q.display_name || q.batch_id.slice(0, 8) }],
                                  keeps: "finished videos in this batch, the form, other batches",
                                  confirmLabel: "Cancel batch",
                                  run: () => handleCancelBatch(q.batch_id),
                                })}
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
                            {formatUiError(q.error)}
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
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1, flexWrap: "wrap" }}>
                    <Chip
                      label={batchStatus.status.toUpperCase()}
                      color={batchStatus.status === 'running' ? 'primary' :
                             batchStatus.status === 'completed' ? 'success' :
                             batchStatus.status === 'error' ? 'error' :
                             batchStatus.status === 'cancelled' ? 'warning' : 'default'}
                      size="small"
                    />
                    {batchStatus.stage && (
                      <Chip
                        label={videoGenStageLabel(batchStatus.stage)}
                        size="small"
                        variant="outlined"
                        color={batchStatus.stage === "gpu_wait" ? "warning" : "default"}
                      />
                    )}
                    {(batchStatus.status === 'running' || batchStatus.status === 'pending' || batchStatus.status === 'queued') && (
                      <Button
                        size="small"
                        color="warning"
                        variant="outlined"
                        onClick={() => setConfirmAction({
                          title: "Cancel this batch?",
                          description: "Stops the GPU job. Clips already written stay on disk.",
                          facts: [{ label: "Batch", value: batchStatus.display_name || batchStatus.batch_id.slice(0, 8) }],
                          keeps: "finished videos in this batch, the form, other batches",
                          confirmLabel: "Cancel batch",
                          run: () => handleCancelBatch(batchStatus.batch_id),
                        })}
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
                      {batchStatus.stage === "gpu_wait" ? " — waiting for GPU" : ""}
                    </Typography>
                    <Typography variant="body2">
                      {typeof batchStatus.progress_pct === "number"
                        ? Math.round(batchStatus.progress_pct)
                        : Math.round(((batchStatus.completed_videos || 0) / (batchStatus.total_videos || 1)) * 100)}%
                    </Typography>
                  </Box>
                  <LinearProgress
                    variant="determinate"
                    value={
                      typeof batchStatus.progress_pct === "number"
                        ? batchStatus.progress_pct
                        : ((batchStatus.completed_videos || 0) / (batchStatus.total_videos || 1)) * 100
                    }
                  />
                </Box>

                {/* Live current-step (per-clip) progress from the ComfyUI ws bridge */}
                {activeStep && (
                  <Box sx={{ mb: 2 }}>
                    <LiveLatentPreview src={getPreviewUrl(activeStep.job_id)} />
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'capitalize' }}>
                        {activeStep.message || 'Rendering…'}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {activeStep.progress || 0}%
                      </Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={activeStep.progress || 0}
                      color="secondary"
                      sx={{ height: 4, borderRadius: 2 }}
                    />
                  </Box>
                )}

                {(batchStatus.status === 'completed' || batchStatus.status === 'error' || batchStatus.status === 'cancelled') && (
                  <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
                    {batchStatus.status === 'completed' && (
                      <Button
                        startIcon={<DownloadIcon />}
                        variant="contained"
                        sx={{ flex: 2 }}
                        onClick={() => handleDownloadBatch(batchStatus.batch_id)}
                      >
                        Download All Videos
                      </Button>
                    )}
                    <Button
                      startIcon={<SettingsIcon />}
                      variant="outlined"
                      sx={{ flex: 1, whiteSpace: 'nowrap' }}
                      onClick={() => handleAdjustRetry(batchStatus.batch_id)}
                    >
                      Adjust &amp; Retry
                    </Button>
                    <Button
                      startIcon={<CloseIcon />}
                      variant="outlined"
                      color="error"
                      sx={{ flex: 1, whiteSpace: 'nowrap' }}
                      onClick={() => setConfirmAction({
                        title: `Delete "${batchStatus.display_name || batchStatus.batch_id.slice(0, 8)}"?`,
                        description: "Removes this batch and every video in it.",
                        facts: [{ label: "Videos", value: String(batchStatus.completed_videos ?? batchStatus.total_videos ?? "all") }],
                        keeps: "other batches, installed models, and the prompt in the form",
                        confirmLabel: "Delete batch",
                        run: () => handleDeleteBatch(batchStatus.batch_id),
                      })}
                    >
                      Delete
                    </Button>
                  </Box>
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
                    const liveThumb = (!thumbUrl && activeStep && res.item_id === activeStep.job_id)
                      ? getPreviewUrl(activeStep.job_id)
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
                            {thumbUrl || liveThumb ? (
                              <Box
                                component="img"
                                src={thumbUrl || liveThumb}
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
                          <Stack direction="row" spacing={0.5} alignItems="center" flexWrap="wrap">
                            <Chip
                              label={res.success ? "Ready" : "Error"}
                              color={res.success ? "success" : "error"}
                              size="small"
                            />
                            {res.frame_paths?.length > 0 && (
                              <Chip label={`${res.frame_paths.length}f`} size="small" variant="outlined" />
                            )}
                            {res.metadata?.quality?.vlm_review?.available &&
                              typeof res.metadata.quality.vlm_review.review?.quality_score === "number" && (
                              <Chip
                                label={`QA ${res.metadata.quality.vlm_review.review.quality_score}/10`}
                                size="small"
                                color={res.metadata.quality.vlm_review.review.quality_score >= 5 ? "success" : "warning"}
                                variant="outlined"
                              />
                            )}
                            {typeof res.metadata?.quality?.identity?.score === "number" && (
                              <Chip
                                label={`ID ${Math.round(res.metadata.quality.identity.score * 100)}%`}
                                size="small"
                                variant="outlined"
                              />
                            )}
                            {res.metadata?.quality?.flagged && (
                              <Chip
                                label="Review"
                                size="small"
                                color="warning"
                                title={(res.metadata.quality.flag_reasons || []).join(", ")}
                              />
                            )}
                          </Stack>
                          {res.error && (
                            <Typography variant="caption" color="error" display="block" sx={{ mt: 0.5 }}>
                              {formatUiError(res.error)}
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
                                onClick={() => setConfirmAction({
                                  title: "Delete this video?",
                                  description: "Removes the file. Other clips in the batch stay.",
                                  facts: [{ label: "File", value: PathFromUrl(res.video_path) }],
                                  keeps: "the rest of this batch, other batches, the form",
                                  confirmLabel: "Delete video",
                                  run: () => handleDeleteVideo(batchStatus.batch_id, PathFromUrl(res.video_path)),
                                })}
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

                {(batchStatus.status === 'error' || batchStatus.status === 'cancelled') && (
                  <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 2 }}>
                    {batchStatus.retry_data ? (
                      <Button
                        startIcon={<RefreshIcon />}
                        variant="contained"
                        color="primary"
                        onClick={() => handleRetryBatch(batchStatus.batch_id)}
                      >
                        Retry Batch
                      </Button>
                    ) : (
                      // No saved config (legacy batch) — honest disabled state, not placebo.
                      // Disabled buttons swallow hover events, so wrap in a span for the Tooltip.
                      <Tooltip title="Original prompts & settings weren't saved for this batch (created before retry support), so it can't be auto-retried. Recreate it from the settings above.">
                        <span>
                          <Button startIcon={<RefreshIcon />} variant="outlined" color="primary" disabled>
                            Retry Batch
                          </Button>
                        </span>
                      </Tooltip>
                    )}
                  </Box>
                )}
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
              <Box sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}>
              <Grid container spacing={2}>
                {batches.map((b) => {
                  const dateStr = formatVideoDate(b.start_time || b.end_time);
                  const videoCount = b.completed_videos ?? 0;
                  const rawName = b.display_name || `Batch ${b.batch_id.slice(0, 8)}`;
                  // Backend trims new names to ~40 chars; legacy batches may hold a
                  // full prompt. Cap defensively so a long name can't push the card down.
                  const label = rawName.length > 42 ? rawName.slice(0, 41).trimEnd() + '…' : rawName;
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
                          '& .batch-delete': { opacity: 1 },
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
                          {/* Delete batch — reveals on card hover (X per the no-trash-icon rule) */}
                          <Tooltip title="Delete batch">
                            <IconButton
                              size="small"
                              className="batch-delete"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConfirmAction({
                                  title: `Delete "${rawName}"?`,
                                  description: "Removes this batch and every video in it.",
                                  facts: [{ label: "Videos", value: String(videoCount) }],
                                  keeps: "other batches, installed models, and the prompt in the form",
                                  confirmLabel: "Delete batch",
                                  run: () => handleDeleteBatch(b.batch_id),
                                });
                              }}
                              sx={{
                                position: 'absolute', top: 4, left: 4,
                                width: 24, height: 24,
                                bgcolor: 'rgba(0,0,0,0.6)', color: 'white',
                                opacity: 0, transition: 'opacity 0.2s',
                                '&:hover': { bgcolor: 'error.main' },
                              }}
                            >
                              <CloseIcon sx={{ fontSize: 16 }} />
                            </IconButton>
                          </Tooltip>
                        </Box>
                      </Box>
                      {/* Batch label */}
                      <Box sx={{ pt: 0.75, px: 0.5 }}>
                        <Typography variant="caption" noWrap title={rawName} sx={{ fontWeight: 500, display: 'block' }}>
                          {label}
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
              </Box>
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
              <Button size="small" color="warning" variant="outlined" onClick={() => setConfirmAction({
                title: "Cancel this batch?",
                description: "Stops the GPU job. Clips already written stay on disk.",
                facts: [{ label: "Batch", value: b.display_name || b.batch_id.slice(0, 8) }],
                keeps: "finished videos in this batch, the form, other batches",
                confirmLabel: "Cancel batch",
                run: () => handleCancelBatch(b.batch_id),
              })}>
                Cancel {b.display_name || b.batch_id.slice(0, 8)}
              </Button>
            </Box>
          ))}
          {/* Retry for failed batches that have persisted original config */}
          {batches.filter(b => b.status === "error" && b.can_retry).map((b) => (
            <Box key={`retry-${b.batch_id}`} sx={{ mt: 1 }}>
              <Button
                size="small"
                color="primary"
                variant="contained"
                startIcon={<RefreshIcon />}
                onClick={() => handleRetryBatch(b.batch_id)}
              >
                Retry {b.display_name || b.batch_id.slice(0, 8)}
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
          onClose={() => {
            setVideoModelsModalOpen(false);
            setHighlightModelId(null);
            // Re-pull readiness: a just-finished install should clear the banner.
            refreshModels();
            refreshFaceRestoreStatus();
          }}
          highlightModelId={highlightModelId}
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
          const { results, currentIndex } = videoPlayer;
          const hasPrev = currentIndex > 0;
          const hasNext = currentIndex < results.length - 1;
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
                <Box
                  ref={videoPlayerBoxRef}
                  sx={{
                    position: "relative", bgcolor: "black",
                    // When this container is fullscreened the overlays stay visible;
                    // center the video and let it use the full viewport.
                    "&:fullscreen": { display: "flex", alignItems: "center", justifyContent: "center" },
                    "&:fullscreen video": { maxHeight: "100vh" },
                  }}
                >
                  <video
                    key={videoPlayer.url}
                    src={videoPlayer.url}
                    controls
                    autoPlay
                    loop
                    // Fullscreen goes through our button so the container (with the
                    // nav arrows) is what gets fullscreened, not the bare <video>.
                    controlsList="nofullscreen"
                    onDoubleClick={togglePlayerFullscreen}
                    style={{ width: "100%", display: "block", maxHeight: "70vh" }}
                  />
                  <IconButton
                    onClick={togglePlayerFullscreen}
                    title={isPlayerFullscreen ? "Exit full screen" : "Full screen"}
                    sx={{
                      position: "absolute", right: 8, top: 8,
                      bgcolor: "rgba(0,0,0,0.5)", color: "white", "&:hover": { bgcolor: "rgba(0,0,0,0.7)" },
                    }}
                  >
                    {isPlayerFullscreen ? <FullscreenExitIcon /> : <FullscreenIcon />}
                  </IconButton>
                  {/* Prev/Next overlays */}
                  {hasPrev && (
                    <IconButton
                      onClick={() => navigateVideoPlayer(-1)}
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
                      onClick={() => navigateVideoPlayer(1)}
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
                  <Button size="small" disabled={!hasPrev} onClick={() => navigateVideoPlayer(-1)} startIcon={<PrevIcon />}>
                    Prev
                  </Button>
                  <Button size="small" disabled={!hasNext} onClick={() => navigateVideoPlayer(1)} endIcon={<NextIcon />}>
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
      <ConfirmActionDialog
        open={!!confirmAction}
        title={confirmAction?.title || ""}
        description={confirmAction?.description}
        facts={confirmAction?.facts || []}
        keeps={confirmAction?.keeps}
        confirmLabel={confirmAction?.confirmLabel}
        busy={confirmBusy}
        onClose={() => !confirmBusy && setConfirmAction(null)}
        onConfirm={async () => {
          if (!confirmAction?.run) return;
          setConfirmBusy(true);
          try {
            await confirmAction.run();
            setConfirmAction(null);
          } finally {
            setConfirmBusy(false);
          }
        }}
      />
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
