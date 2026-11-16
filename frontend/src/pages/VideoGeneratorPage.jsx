// frontend/src/pages/VideoGeneratorPage.jsx
// Standalone Video Generation page with preset-based UI

import React, { useEffect, useMemo, useState, useRef, useCallback } from "react";
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
  Alert,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  CircularProgress,
} from "@mui/material";
import PageLayout from "../components/layout/PageLayout";
import { CardWorkspace } from "../components/board";
import {
  ConfirmActionDialog,
} from "../components/settings/ui";
import useJobsGate from "../hooks/useJobsGate";
import useBatchVideo from "../hooks/useBatchVideo";
import useStableCallbacks from "../hooks/useStableCallbacks";
import VideoPromptCard from "../components/videogen/cards/VideoPromptCard";
import VideoSettingsCard from "../components/videogen/cards/VideoSettingsCard";
import VideoQueueCard from "../components/videogen/cards/VideoQueueCard";
import VideoLibraryCard from "../components/videogen/cards/VideoLibraryCard";
import { API_BASE, stripGlyph, batchStatusLabel, PathFromUrl, encodePathSegments } from "../components/videogen/videoGenPage";
import {
  QUALITY_PRESETS,
  durationPresetsFor,
  aspectRatiosFor,
  resolveAspectRatio,
  MOTION_PRESETS,
  OUTPUT_QUALITY_TIERS,
  KEYFRAME_MODEL_OPTIONS,
  DEFAULT_KEYFRAME_MODEL,
  MODEL_DEFAULT_GUIDANCE,
  ASPECT_RATIO_PRESETS,
  VIDEO_SIZE_PRESETS,
  MODEL_OPTIONS,
  GENERATION_TYPES,
  DEFAULT_T2V_MODEL,
  DEFAULT_I2V_MODEL,
  familyType,
  isCogVideoXModel,
  canvasDimensions,
  defaultSizeFor,
  parseSeed,
  isWanModel,
  isLtxModel,
  isMinimaxModel,
  snapDimensions,
  fitAreaToRatio,
} from "../constants/videoGeneratorPresets";
import { refusalText } from "../utils/renderFailure";
import { applicableAdapters as applicableAdapterModels } from "../utils/videoAdapters";
import { stepsFromProfile, stepsTileNote } from "../utils/videoSteps";
import { promptControlsState } from "../utils/videoPromptControls";
import { joinPreviews,  promptsToPreview } from "../utils/videoPreviewPrompts";
import { applyClipRename, applyClipRenameToPlayer, clipFileName } from "../utils/videoRename";
import {
  boardProblems,
  emptyBoard,
  previewBoard,
  serializeBoard,
} from "../components/videogen/referenceBoardModel";
import {
  Download as DownloadIcon,
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

import axios from "axios";
import { useSearchParams } from "react-router-dom";
import { uploadDroppedMedia } from "../components/videoeditor/useExternalDrop";
import { formatUiError } from "../utils/uiError";
import { handleResponse } from "../api/apiClient";
import TextInputDialog from "../components/common/TextInputDialog";
import lazyWithReload from "../utils/lazyWithReload";
import PageStatus, { StatusChip } from "../components/layout/PageStatus";
import { useUiTranslation,  Trans } from "../i18n";

// Lazy import for VideoModelsModal
const VideoModelsModal = lazyWithReload(() => import("../components/modals/VideoModelsModal"));

// Ready-made arrangements of the page's cards (175 columns). The first is the default.
const VIDEO_GEN_LAYOUTS = [
  {
    id: "columns",
    labelKey: "layout.board.layouts.columns",
    layout: {
      prompt: { x: 0, y: 0, w: 60, h: 86 },
      settings: { x: 60, y: 0, w: 55, h: 86 },
      queue: { x: 115, y: 0, w: 60, h: 46 },
      results: { x: 115, y: 46, w: 60, h: 40 },
    },
  },
  {
    // The library across the full width, for browsing what has been made.
    id: "library",
    labelKey: "layout.board.layouts.library",
    layout: {
      prompt: { x: 0, y: 0, w: 60, h: 50 },
      settings: { x: 60, y: 0, w: 60, h: 50 },
      queue: { x: 120, y: 0, w: 55, h: 50 },
      results: { x: 0, y: 50, w: 175, h: 60 },
    },
  },
  {
    // Prompt and settings side by side at full height; queue and results in a side column.
    id: "compose",
    labelKey: "layout.board.layouts.compose",
    layout: {
      prompt: { x: 0, y: 0, w: 65, h: 99 },
      settings: { x: 65, y: 0, w: 60, h: 99 },
      queue: { x: 125, y: 0, w: 50, h: 45 },
      results: { x: 125, y: 45, w: 50, h: 54 },
    },
  },
];

const VideoGeneratorPage = ({ embedded = false }) => {
  const { t, language } = useUiTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
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
  // null until the person picks a size: the model's default for the mode applies.
  const [pickedVideoSize, setVideoSize] = useState(null);
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
  // Where the backend talks to ComfyUI (/gpu/comfyui/status); the ComfyUI
  // button opens that port on the host this page was opened on.
  const [comfyUiUrl, setComfyUiUrl] = useState(null);

  // Prompt preview state (calls /enhance-preview)
  const [previewEnhanced, setPreviewEnhanced] = useState("");
  const [previewVerbatim, setPreviewVerbatim] = useState(false);
  const [previewEnhanceOff, setPreviewEnhanceOff] = useState(false);
  // How many clips the previewed batch sends, and how many were previewed.
  const [previewCounts, setPreviewCounts] = useState({ shown: 0, total: 0, storyboard: false });
  const [previewLoading, setPreviewLoading] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  // Settings > Verbatim prompts, as the backend reports it: the render then
  // skips the enhancer and the Director, so the controls that feed them say so.
  const [verbatimPrompts, setVerbatimPrompts] = useState(false);
  useEffect(() => {
    let alive = true;
    fetch(`${API_BASE}/settings/verbatim_prompts`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d?.data) setVerbatimPrompts(Boolean(d.data.enabled)); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Batch-wide prompt modifiers (mirror BatchImageGen's "Look & Feel" pattern)
  const [lookAndFeel, setLookAndFeel] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [lowVramMode, setLowVramMode] = useState(() => {
    const saved = localStorage.getItem('lowVramMode');
    // Off unless the person turns it on. On a 16 GB card Wan 2.2 14B renders
    // 416x736 and 832x480 at 49 frames and 25 steps without it (measured
    // 2026-10-20); on, it cut every Wan clip to 480 px and 33 frames.
    return saved !== null ? saved === 'true' : false;
  });

  // Advanced settings
  const [advancedParams, setAdvancedParams] = useState({
    num_inference_steps: null, // null means "use quality preset", explicit number means "override"
    guidance_scale: 6.0, // CogVideoX default
    generate_frames_only: false,
    frames_per_batch: 1,
    combine_frames: false,
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
    // Typed seed; blank gives every clip a new random seed.
    seed: "",
  });
  // End frame for models that declare l2v / flf2v (image mode): {ref, name,
  // thumbnailUrl}, ref being an uploaded file's path or a library document id.
  const [endFrame, setEndFrame] = useState(null);
  // Reference board for models that declare ref2v (references mode).
  const [refBoard, setRefBoard] = useState(emptyBoard);
  const promptInputRef = useRef(null);
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
  // Why the Cast Library could not be read; shown instead of "train a character".
  const [castError, setCastError] = useState(null);
  const [castReload, setCastReload] = useState(0);
  useEffect(() => {
    let alive = true;
    setCastError(null);
    fetch("/api/cast-library")
      .then((r) => handleResponse(r, { quiet: true }))
      .then((d) => {
        if (!alive) return;
        const trained = (d.subjects || []).filter(
          (s) => s.kind === "character" && s.lora_path
        );
        setCastSubjects(trained);
      })
      .catch((e) => {
        if (alive) setCastError(formatUiError(e) || t("videoGenerator.errors.requestFailed"));
      });
    return () => { alive = false; };
  }, [castReload]);

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
        if (gpu?.data?.comfyui_url) setComfyUiUrl(gpu.data.comfyui_url);
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
  const [renameDialog, setRenameDialog] = useState(null); // { batchId, videoPath, current }
  const videoPlayerBoxRef = useRef(null);
  const [isPlayerFullscreen, setIsPlayerFullscreen] = useState(false);

  const navigateVideoPlayer = useCallback((delta) => {
    setVideoPlayer(prev => {
      if (!prev) return prev;
      const idx = prev.currentIndex + delta;
      if (idx < 0 || idx >= prev.results.length) return prev;
      const r = prev.results[idx];
      const url = `${API_BASE}/batch-video/video/${prev.batchId}/${encodePathSegments(PathFromUrl(r.video_path))}`;
      return { ...prev, url, currentIndex: idx, title: r.video_path?.split("/").pop() || t("videoGenerator.player.fallbackTitle", { number: idx + 1 }) };
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
  // Active accelerator ({vendor, memoryGb}) for an honest "where will this
  // run" chip — issue #43 Tier 1. null until known; worded when shown.
  const [accel, setAccel] = useState(null);
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
      const data = await handleResponse(res, { quiet: true });
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
      // Offline / API down — fall back to the (already-culled) MODEL_OPTIONS,
      // and say so: without the API nobody knows which of them are installed.
      setError(t("videoGenerator.errors.modelsCheckFailed", { reason: formatUiError(e) || t("videoGenerator.errors.requestFailed") }));
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
          setAccel({ vendor: "apple", memoryGb: gpu.unified_memory_gb || null });
        } else if (gpu.vendor === "nvidia") {
          setAccel({ vendor: "nvidia", memoryGb: gpu.vram_mb ? (gpu.vram_mb / 1024).toFixed(0) : null });
        } else if (gpu.vendor === "amd") {
          setAccel({ vendor: "amd" });
        } else {
          setAccel({ vendor: "cpu" });
        }
      } catch (e) {
        // hardware.json not written yet / API down — just don't show the chip.
      }
    })();
  }, []);

  const accelLabel = !accel
    ? null
    : accel.vendor === "apple"
      ? (accel.memoryGb ? t("videoGenerator.tiles.accelApple", { memory: accel.memoryGb }) : "Apple Silicon · MPS")
      : accel.vendor === "nvidia"
        ? (accel.memoryGb ? t("videoGenerator.tiles.accelNvidia", { memory: accel.memoryGb }) : "NVIDIA · CUDA")
        : accel.vendor === "amd"
          ? "AMD GPU"
          : t("videoGenerator.tiles.accelCpu");

  // Models that take a reference board; the References input is offered
  // whenever the registry lists one, installed or not (Generate routes an
  // uninstalled one to Install).
  const refModelIds = useMemo(
    () => Object.keys(modelMeta).filter((id) => modelMeta[id]?.capabilities?.modes?.includes("ref2v")),
    [modelMeta],
  );
  useEffect(() => {
    if (inputMode === "references" && Object.keys(modelMeta).length > 0 && refModelIds.length === 0) {
      setInputMode("text");
    }
  }, [inputMode, modelMeta, refModelIds]);

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
        if (inputMode === "references") return (caps.modes || []).includes("ref2v");
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
  }, [inputMode, apiModelIds, modelMeta, language]);

  // Auto-select a compatible model when input mode changes or the current id
  // is a user model that does not support this mode.
  useEffect(() => {
    const meta = modelMeta[model];
    const opt = MODEL_OPTIONS[model];
    const isCompatible = inputMode === "references"
      ? (meta?.capabilities?.modes || []).includes("ref2v")
      : inputMode === "image"
        ? (meta?.capabilities?.supports_i2v ?? opt?.supportsI2V)
        : (meta?.capabilities?.supports_t2v ?? opt?.supportsT2V);
    if (!isCompatible) {
      const fallback = availableModels[0]?.[0]
        || (inputMode === "image" ? DEFAULT_I2V_MODEL : DEFAULT_T2V_MODEL);
      if (fallback && fallback !== model) setModel(fallback);
    }
  }, [inputMode, model, modelMeta, availableModels]);

  // Duration presets follow the selected model's native fps.
  const durationPresets = useMemo(() => durationPresetsFor(model, modelMeta[model]), [model, modelMeta, language]);

  // A model that declares aspectRatios offers only those. Switching to one that
  // cannot render the current selection snaps it rather than leaving a ratio on
  // screen that the model will warp — 1:1 on Wan 5B being the case that bit.
  const allowedAspectRatios = useMemo(() => aspectRatiosFor(model, modelMeta[model]), [model, modelMeta, language]);
  useEffect(() => {
    setAspectRatio((current) => resolveAspectRatio(model, current, modelMeta[model]));
  }, [model, modelMeta]);

  // Calculate video dimensions from aspect ratio and size
  // The capability record the registry declares for the selected model
  // (modes, audio, cfg, step floor, speed profiles, style embeddings).
  const modelCaps = useMemo(() => modelMeta[model]?.capabilities || null, [model, modelMeta]);
  // Prompt styles the model offers; a style its registry entry withholds is
  // not listed, and a selection it withholds falls back to the first offered.
  const offeredStyles = modelCaps?.prompt_styles || null;
  useEffect(() => {
    if (offeredStyles && !offeredStyles.includes(promptStyle)) {
      setPromptStyle(offeredStyles[0] || "none");
    }
  }, [offeredStyles, promptStyle]);
  // Null when the model declares no speed profiles (the registry sends {}),
  // so the control is hidden; otherwise always one of the declared names.
  const activeSpeedProfile = useMemo(() => {
    const profiles = modelCaps?.speed_profiles || {};
    const names = Object.keys(profiles);
    if (!names.length) return null;
    if (profiles[advancedParams.speed_profile]) return advancedParams.speed_profile;
    return profiles.standard ? "standard" : names[0];
  }, [modelCaps, advancedParams.speed_profile]);
  const activeProfileSpec = useMemo(
    () => (activeSpeedProfile ? modelCaps?.speed_profiles?.[activeSpeedProfile] || null : null),
    [modelCaps, activeSpeedProfile],
  );
  // A step count typed in Advanced wins over presets, floors and profiles.
  const stepsTyped = advancedParams.num_inference_steps !== null && advancedParams.num_inference_steps !== undefined;
  // When the model's speed profile fixes the steps, the Steps chips cannot
  // change the render and say so instead of labelling the tile.
  const profileSteps = useMemo(
    () => stepsFromProfile({ profileSpec: activeProfileSpec, stepsTyped }),
    [activeProfileSpec, stepsTyped, language],
  );

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
      if (!res.ok || !data?.path) throw new Error(data?.error || t("videoGenerator.errors.uploadHttp", { status: res.status }));
      setAudioGuide({ path: data.path, name: file.name });
      setVoiceClips((prev) => [{ id: data.id || file.name, filename: file.name, path: data.path }, ...prev]);
    } catch (err) {
      setError(t("videoGenerator.errors.audioUploadFailed", { reason: err.message }));
    } finally {
      setIsUploadingAudioGuide(false);
    }
  };

  // A model change keeps the end frame and audio guide when the new model
  // takes them too, so switching precision builds does not drop a pick.
  useEffect(() => {
    const caps = modelMeta[model]?.capabilities;
    if (caps) {
      const modes = caps.modes || [];
      if (!modes.includes("l2v") && !modes.includes("flf2v")) setEndFrame(null);
      if (!caps.audio_in) setAudioGuide(null);
    }
    // Keyed on the model only: a metadata refresh must not clear a pick.
  }, [model]);

  useEffect(() => {
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

  // A text prompt rendered through a keyframe still is an image-to-video clip.
  const videoSize = pickedVideoSize
    ?? defaultSizeFor(model, inputMode === "text" && (cinematicKeyframe || selectedSubjectIds.length > 0) ? "image" : inputMode);

  const videoDimensions = useMemo(() => {
    // Resolve through the model: the snap effect settles a render later, and
    // a restored config can name a ratio this model never supported.
    const effectiveAspectRatio = resolveAspectRatio(model, aspectRatio);
    // A model with a declared canvas (CogVideoX 1.5 I2V) renders at its fixed
    // short side whatever the Size chips say; the aspect sets the long side.
    const canvas = MODEL_OPTIONS[model]?.canvas;
    if (canvas) {
      const ratioConfig = ASPECT_RATIO_PRESETS[effectiveAspectRatio] || ASPECT_RATIO_PRESETS["16:9"];
      return canvasDimensions(canvas, ratioConfig.ratio, MODEL_OPTIONS[model].dimensionAlignment);
    }
    // CogVideoX 1.0 is trained on 720x480 (3:2). Aspect-ratio math at 16:9 lands
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

  const applicableAdapters = useMemo(
    () => applicableAdapterModels(adapterModels, model, modelCaps?.speed_profiles),
    [adapterModels, model, modelCaps],
  );
  const applicableEncoders = useMemo(
    () => (encoderModels || []).filter((m) => (m.applies_to || []).includes(model)),
    [encoderModels, model],
  );

  // Low VRAM clamps frames, resolution and steps on CogVideoX and Wan only,
  // and never shrinks a model below the canvas it declares.
  const lowVramClamps = (m, meta) =>
    lowVramMode && ((isCogVideoXModel(m, meta) && !MODEL_OPTIONS[m]?.canvas) || isWanModel(m, meta));

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
    const profileSpec = activeProfileSpec;
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
    // A model with a declared canvas is never shrunk below it.
    if (lowVramMode && isCogVideoXModel(model, modelMeta[model]) && !modelConfig.canvas) {
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
      seed: parseSeed(advancedParams.seed),
      generate_frames_only: advancedParams.generate_frames_only,
      frames_per_batch: lowVramClamps(model, modelMeta[model]) ? 1 : advancedParams.frames_per_batch,
      combine_frames: advancedParams.combine_frames,
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
  }, [qualityPreset, durationPreset, motionPreset, model, advancedParams, videoDimensions, lowVramMode, qualityTier, promptStyle, enhancePrompt, directorMode, cinematicKeyframe, directorGuidance, fetaEnabled, fetaWeight, selectedSubjectIds, keyframeModel, postUpscale, highConsistencyMode, modelMeta, modelCaps, activeSpeedProfile, activeProfileSpec, stepsTyped, applicableAdapters, applicableEncoders]);

  // Low VRAM clamps frames, resolution and steps on CogVideoX and Wan only
  // (computedParams above); on other models the switch leaves the steps alone.
  const lowVramClampApplies = lowVramClamps(model, modelMeta[model]);

  const qualityChipOptions = useMemo(() => {
    if (isLtxModel(model, modelMeta[model])) return [];
    if (lowVramClamps(model, modelMeta[model])) return [];
    const min = MODEL_OPTIONS[model]?.minSteps ?? modelCaps?.min_steps ?? 0;
    const seen = new Set();
    const out = [];
    Object.entries(QUALITY_PRESETS).forEach(([key, preset]) => {
      const steps = Math.max(preset.num_inference_steps, min);
      if (seen.has(steps)) return;
      seen.add(steps);
      out.push({ value: key, label: stripGlyph(preset.label), tooltip: t("videoGenerator.clip.stepsTooltip", { count: steps }) });
    });
    return out;
  }, [model, lowVramMode, modelMeta, modelCaps, t, language]);

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
    handleApproveClip,
    handleRerenderClip,
    handleClearCompletedQueue,
  } = useBatchVideo({ setError, setSuccess, computedParams });

  // Preview what Add to queue will send: the same enhance_video_prompt path
  // as the render, once per clip. The route takes one prompt per call and
  // the enhancer is a pure function, so a batch is previewed line by line.
  const fetchPromptPreview = async () => {
    const { prompts, total } = promptsToPreview({ inputMode, promptsText, lookAndFeel, storyboardMode });
    if (prompts.length === 0) {
      setPreviewEnhanced("");
      return;
    }
    setPreviewLoading(true);
    try {
      const previewOne = async (prompt) => {
        const res = await fetch(`${API_BASE}/batch-video/enhance-preview`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt,
            prompt_style: promptStyle,
            enhance_prompt: enhancePrompt,
            fidelity_mode: fidelityMode,
            model,
            width: (computedParams && computedParams.width) || videoDimensions.width,
            height: (computedParams && computedParams.height) || videoDimensions.height,
            motion_strength: computedParams?.motion_strength,
            ...(inputMode === "references"
              ? {
                  references: previewBoard(refBoard),
                  duration_frames: computedParams?.duration_frames,
                  fps: computedParams?.fps,
                }
              : {}),
          }),
        });
        const data = await res.json();
        if (!data.success || !data.data) throw new Error(data.error || `HTTP ${res.status}`);
        return data.data;
      };
      const results = await Promise.all(prompts.map(previewOne));
      setPreviewEnhanced(joinPreviews(results.map((r) => r.enhanced_prompt || "")));
      const verbatim = results.some((r) => Boolean(r.verbatim));
      setPreviewVerbatim(verbatim);
      setVerbatimPrompts(verbatim);
      setPreviewEnhanceOff(results.some((r) => r.enhance_prompt === false));
      setPreviewCounts({ shown: results.length, total, storyboard: storyboardMode && inputMode === "text" });
      setShowPreview(true);
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

  // Which prompt controls the render reads right now (Motion chips, Prompt style).
  const promptControls = useMemo(
    () => promptControlsState({ enhancePrompt, verbatimPrompts, inputMode, fidelityMode, directorMode, storyboardMode }),
    [enhancePrompt, verbatimPrompts, inputMode, fidelityMode, directorMode, storyboardMode, language],
  );

  // End-frame modes the selected model declares (image mode): flf2v with a
  // start image, l2v without one.
  const endFrameModes = useMemo(
    () => (inputMode === "image" ? (modelCaps?.modes || []).filter((m) => m === "l2v" || m === "flf2v") : []),
    [inputMode, modelCaps],
  );
  const lastFrameOnly =
    inputMode === "image" && selectedImages.length === 0 && !!endFrame?.ref && endFrameModes.includes("l2v");
  // The board follows the reference model's ref_limits, also while the model
  // list is still switching over to it.
  const refLimits = (modelCaps?.modes || []).includes("ref2v")
    ? modelCaps.ref_limits
    : modelMeta[refModelIds[0]]?.capabilities?.ref_limits;
  // A card with a measured board budget shares it between the render, the
  // pictures and the clips (tier_defaults.ref_token_budget).
  const refFit = useMemo(() => ({
    budget: modelMeta[model]?.tier_defaults?.ref_token_budget,
    renderFrames: computedParams?.duration_frames,
    width: computedParams?.width,
    height: computedParams?.height,
    fps: computedParams?.fps || 24,
  }), [modelMeta, model, computedParams]);
  const refProblems = useMemo(() => boardProblems(refBoard, refLimits, refFit), [refBoard, refLimits, refFit, language]);

  // Put a reference tag at the prompt's cursor (or its end).
  const insertPromptTag = useCallback((tag) => {
    const el = promptInputRef.current;
    setPromptsText((prev) => {
      const start = el?.selectionStart ?? prev.length;
      const end = el?.selectionEnd ?? prev.length;
      const before = prev.slice(0, start);
      const pad = before && !/\s$/.test(before) ? " " : "";
      return `${before}${pad}${tag} ${prev.slice(end)}`;
    });
    requestAnimationFrame(() => el?.focus());
  }, []);

  // File upload handling. Start and end images go to Files > Images through
  // /api/files/upload, the path the References board uses, and are sent as
  // library document ids, which the generate routes resolve the same way as
  // a Files pick. (The old /batch-image/upload route put every upload in
  // Image Gen's Recent Batches as an "Uploaded Images" batch.)
  const uploadImageDocuments = useCallback(async (files) => {
    // Frames are render inputs, not documents to search: no vision index pass.
    const docs = await uploadDroppedMedia(files, { http: axios, autoIndex: false });
    const images = docs.filter((d) => d.kind === "image");
    if (images.length === 0) throw new Error(t("videoGenerator.errors.imagesOnly"));
    return images;
  }, []);

  const handleEndFrameUpload = useCallback(async (file) => {
    if (!file) return;
    setIsUploadingEndFrame(true);
    try {
      const [doc] = await uploadImageDocuments([file]);
      setEndFrame({
        ref: String(doc.id),
        name: doc.filename || file.name,
        thumbnailUrl: doc.thumbnail_url || `${API_BASE}/files/thumbnail?document_id=${doc.id}`,
      });
    } catch (err) {
      setError(t("videoGenerator.errors.endFrameUploadFailed", { reason: err.response?.data?.error?.message || err.message }));
    } finally {
      setIsUploadingEndFrame(false);
    }
  }, [uploadImageDocuments]);

  const handleFileUpload = useCallback(async (files) => {
    if (!files || files.length === 0) return;

    setIsUploading(true);
    try {
      const docs = await uploadImageDocuments(files);
      const newImages = docs.map((doc) => ({
        id: `doc_${doc.id}`,
        path: String(doc.id),
        thumbnailUrl: doc.thumbnail_url || `${API_BASE}/files/thumbnail?document_id=${doc.id}`,
        name: doc.filename,
        documentId: doc.id,
      }));
      setSelectedImages((prev) => [...prev, ...newImages]);
      setSuccess(t("videoGenerator.messages.uploaded", { count: newImages.length }));
    } catch (err) {
      setError(t("videoGenerator.errors.uploadFailed", { reason: err.response?.data?.error?.message || err.message }));
    } finally {
      setIsUploading(false);
    }
  }, [uploadImageDocuments]);

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
      setError(t("videoGenerator.errors.needPrompt"));
      return;
    }
    if (inputMode === "image" && selectedImages.length === 0 && !lastFrameOnly) {
      setError(endFrameModes.includes("l2v")
        ? t("videoGenerator.errors.needStartOrEnd")
        : t("videoGenerator.errors.needImage"));
      return;
    }
    if ((lastFrameOnly || inputMode === "references") && !promptsText.trim()) {
      setError(inputMode === "references"
        ? t("videoGenerator.errors.describeReferences")
        : t("videoGenerator.errors.describeEndFrame"));
      return;
    }
    if (inputMode === "references" && refProblems.length > 0) {
      setError(refProblems[0]);
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
        selectedImages, endFrame, refBoard,
      };

      const guidePayload =
        audioGuide?.path && modelCaps?.audio_in && inputMode !== "references"
          ? { guides: (inputMode === "text" ? finalPrompts : lastFrameOnly ? [motionPrompt] : imagePaths).map(() => [
              { kind: "audio", path: audioGuide.path, frame_idx: 0 },
            ]) }
          : {};
      const promptWithLook = lf && motionPrompt ? `${motionPrompt}, ${lf}` : motionPrompt;
      const body =
        inputMode === "references"
          ? {
              prompt: promptWithLook,
              references: serializeBoard(refBoard),
              ...computedParams,
              // Identity comes from the references, not a keyframe or a rewrite.
              director_mode: false,
              cinematic_keyframe: false,
              fidelity_mode: fidelityMode,
              high_consistency: highConsistencyMode,
              ...negativePayload,
              ui_config: uiConfig,
            }
          : lastFrameOnly
          ? {
              prompts: [promptWithLook],
              last_frame_paths: [endFrame.ref],
              ...guidePayload,
              ...computedParams,
              fidelity_mode: fidelityMode,
              high_consistency: highConsistencyMode,
              ...negativePayload,
              ui_config: uiConfig,
            }
          : inputMode === "text"
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
              ...(endFrame?.ref && endFrameModes.includes("flf2v")
                ? { last_frame_paths: imagePaths.map(() => endFrame.ref) }
                : {}),
              ...guidePayload,
              prompt: promptWithLook,
              ...computedParams,
              fidelity_mode: fidelityMode,
              high_consistency: highConsistencyMode,
              ...negativePayload,
              ui_config: uiConfig,
            };

      const url =
        inputMode === "references"
          ? `${API_BASE}/batch-video/generate/references`
          : inputMode === "text" || lastFrameOnly
          ? `${API_BASE}/batch-video/generate/text`
          : `${API_BASE}/batch-video/generate/image`;

      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        setError(refusalText(errorData) || t("videoGenerator.errors.queueFailedHttp", { status: res.status }));
        return;
      }

      const data = await res.json();
      if (!data.success) {
        setError(formatUiError(data.error || data.message) || t("videoGenerator.errors.queueFailedPlain"));
        return;
      }

      const batchId = data.data.batch_id;
      setActiveBatchId(batchId);
      const gpuMsg = data.data?.gpu?.message;
      setSuccess(
        gpuMsg
          ? t("videoGenerator.messages.queuedGpu", { message: gpuMsg })
          : t("videoGenerator.messages.queued")
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
      setError(t("videoGenerator.errors.queueFailed", { reason: e.message }));
    } finally {
      setIsGenerating(false);
    }
  };

  // Adjust & Retry: load a batch's saved control-panel config back into the form so
  // the user can tweak one thing and re-generate (instead of remembering all the
  // settings). New batches store an exact `ui_config` snapshot → restored verbatim;
  // older batches fall back to the core fields from the persisted params.
  // With `clipId` (the Media page's Adjust & Retry on one clip), that clip's
  // prompt or start image and its seed replace the batch's.
  const handleAdjustRetry = async (batchId, clipId = null) => {
    try {
      const res = await fetch(`${API_BASE}/batch-video/status/${batchId}`);
      if (!res.ok) { setError(t("videoGenerator.errors.settingsHttp", { status: res.status })); return; }
      const data = await res.json();
      const rd = data?.data?.retry_data || data?.retry_data;
      const name = data?.data?.metadata?.display_name || batchId.slice(0, 8);
      if (!rd) { setError(t("videoGenerator.errors.noSettings")); return; }

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
        if (cfg.endFrame && (cfg.endFrame.ref || cfg.endFrame.path)) {
          setEndFrame({ ...cfg.endFrame, ref: cfg.endFrame.ref || cfg.endFrame.path });
        }
        if (cfg.refBoard && typeof cfg.refBoard === "object") {
          setRefBoard({ images: [], videos: [], audios: [], ...cfg.refBoard });
        }

        if (Array.isArray(cfg.selectedImages) && cfg.selectedImages.length > 0) {
          setSelectedImages(cfg.selectedImages);
        } else if (rd.image_paths || rd.params?.image_paths) {
          restoreImagesFromPaths(rd.image_paths || rd.params?.image_paths);
        }

        setSuccess(t("videoGenerator.messages.loadedSettings", { name }));
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
          face_restore: !!p.face_restore,
          lora_name: p.lora_name || "",
          lora_strength: p.lora_strength ?? prev.lora_strength,
          frames_per_batch: p.frames_per_batch ?? prev.frames_per_batch,
          combine_frames: !!p.combine_frames,
          seed: p.seed != null ? String(p.seed) : "",
        }));
        setSuccess(t("videoGenerator.messages.loadedCoreSettings", { name }));
      }
      const clipIndex = clipId ? (rd.item_ids || []).indexOf(clipId) : -1;
      if (clipIndex >= 0) {
        if (rd.mode === "image") {
          const path = (rd.image_paths || rd.params?.image_paths || [])[clipIndex];
          if (path) {
            setInputMode("image");
            restoreImagesFromPaths([path]);
          }
        } else if (Array.isArray(rd.prompts) && rd.prompts[clipIndex] != null) {
          // The filed prompt carries Look & Feel, which the form keeps in its own field.
          const look = (rd.params?.ui_config?.lookAndFeel || "").trim();
          const prompt = rd.prompts[clipIndex];
          setInputMode("text");
          setStoryboardMode(false);
          setPromptsText(look && prompt.endsWith(`, ${look}`) ? prompt.slice(0, -(look.length + 2)) : prompt);
        }
        const clip = (data?.data?.results || data?.results || []).find((r) => r.item_id === clipId);
        if (clip?.metadata?.seed != null) {
          setAdvancedParams((prev) => ({ ...prev, seed: String(clip.metadata.seed) }));
        }
        setSuccess(t("videoGenerator.messages.loadedClip", { name }));
      }
      setError("");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(t("videoGenerator.errors.settingsFailed", { reason: e.message }));
    }
  };

  // Adjust & Retry from the Media page: ?retry=<batch>[&clip=<clip>], loaded
  // once, then dropped from the address so a reload does not load it again.
  useEffect(() => {
    const retry = searchParams.get("retry");
    if (!retry) return;
    handleAdjustRetry(retry, searchParams.get("clip"));
    const next = new URLSearchParams(searchParams);
    next.delete("retry");
    next.delete("clip");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

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
      } else {
        const body = await res.json().catch(() => ({}));
        setError(t("videoGenerator.errors.deleteVideoFailed", { reason: refusalText(body) || res.statusText }));
      }
    } catch (e) {
      setError(t("videoGenerator.errors.deleteVideoFailed", { reason: e.message }));
    }
  };

  // The server answers with the clip's new paths; the page repoints its cards and
  // an open player from them, so Play serves the renamed file without a reload.
  const handleRenameVideo = (batchId, videoPath) => {
    setRenameDialog({ batchId, videoPath, current: clipFileName(videoPath) });
  };

  const submitRenameVideo = async (newName) => {
    const target = renameDialog;
    setRenameDialog(null);
    if (!target) return;
    const { batchId, videoPath, current } = target;
    const name = newName.trim();
    if (!name || name === current) return;
    try {
      const res = await fetch(`${API_BASE}/batch-video/video/${batchId}/${encodePathSegments(videoPath)}/rename`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_name: name }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.success) {
        setError(t("videoGenerator.errors.renameFailed", { reason: refusalText(body) || res.statusText }));
        return;
      }
      const renamed = body.data;
      setBatchStatus((prev) => applyClipRename(prev, batchId, renamed));
      setVideoPlayer((prev) => applyClipRenameToPlayer(prev, batchId, renamed, (path) =>
        `${API_BASE}/batch-video/video/${batchId}/${encodePathSegments(PathFromUrl(path))}`,
      ));
      setSuccess(t("videoGenerator.messages.renamed", { name: clipFileName(renamed.video_path) }));
      fetchBatches();
    } catch (e) {
      setError(t("videoGenerator.errors.renameFailed", { reason: e.message }));
    }
  };

  const currentResults = useMemo(() => {
    if (!batchStatus || !batchStatus.results) return [];
    return batchStatus.results;
  }, [batchStatus]);

  const controlsDisabled = isGenerating;
  const canQueue =
    !isGenerating && (
      inputMode === "text" ? parsedPrompts.length > 0
        : inputMode === "references"
          ? promptsText.trim().length > 0 && refBoard.images.length + refBoard.videos.length > 0
          : selectedImages.length > 0 || (lastFrameOnly && promptsText.trim().length > 0)
    );
  const { gpuBusy, blockReason, blockDetail } = useJobsGate({ submitMode: "queue" });
  const castIdentityLocked = selectedSubjectIds.length > 0;
  const keyframeModelOptions = useMemo(() => {
    // With cast: family is auto from the character LoRA (backend ignores manual model).
    if (castIdentityLocked) {
      return Object.entries(KEYFRAME_MODEL_OPTIONS).filter(([key]) => key === "from-lora");
    }
    return Object.entries(KEYFRAME_MODEL_OPTIONS).filter(([key]) => key !== "from-lora");
  }, [castIdentityLocked]);

  // Shared by the result and batch buttons and their right-click menus.
  const openResultPlayer = (res, idx, videoUrl) => {
    const playable = currentResults.filter(r => r.video_path);
    const playIdx = playable.findIndex(r => r.item_id === res.item_id);
    setVideoPlayer({
      url: videoUrl,
      title: res.video_path?.split("/").pop() || t("videoGenerator.player.fallbackTitle", { number: idx + 1 }),
      batchId: batchStatus.batch_id,
      results: playable,
      currentIndex: playIdx >= 0 ? playIdx : 0,
    });
  };

  const confirmDeleteVideo = (batchId, res) => setConfirmAction({
    title: t("videoGenerator.confirm.deleteVideoTitle"),
    description: t("videoGenerator.confirm.deleteVideoText"),
    facts: [{ label: t("videoGenerator.confirm.file"), value: PathFromUrl(res.video_path) }],
    keeps: t("videoGenerator.confirm.deleteVideoKeeps"),
    confirmLabel: t("videoGenerator.confirm.deleteVideo"),
    run: () => handleDeleteVideo(batchId, PathFromUrl(res.video_path)),
  });

  const confirmCancelBatch = (b) => setConfirmAction({
    title: t("videoGenerator.confirm.cancelBatchTitle"),
    description: t("videoGenerator.confirm.cancelBatchText"),
    facts: [{ label: t("videoGenerator.confirm.batch"), value: b.display_name || b.batch_id.slice(0, 8) }],
    keeps: t("videoGenerator.confirm.cancelBatchKeeps"),
    confirmLabel: t("videoGenerator.confirm.cancelBatch"),
    run: () => handleCancelBatch(b.batch_id),
  });

  const confirmDeleteBatch = (b) => setConfirmAction({
    title: t("videoGenerator.confirm.deleteBatchTitle", {
      name: b.display_name || t("videoGenerator.library.batchName", { id: b.batch_id.slice(0, 8) }),
    }),
    description: t("videoGenerator.confirm.deleteBatchText"),
    facts: [{ label: t("videoGenerator.confirm.videos"), value: String(b.completed_videos ?? 0) }],
    keeps: t("videoGenerator.confirm.deleteBatchKeeps"),
    confirmLabel: t("videoGenerator.confirm.deleteBatch"),
    run: () => handleDeleteBatch(b.batch_id),
  });

  // Stable handlers for BatchLibraryGrid, which skips rendering unless they change.
  const openBatch = useCallback((b) => {
    setActiveBatchId(b.batch_id);
    startPollingStatus(b.batch_id);
  }, [setActiveBatchId, startPollingStatus]);
  const confirmDeleteBatchRef = useRef(confirmDeleteBatch);
  confirmDeleteBatchRef.current = confirmDeleteBatch;
  const requestDeleteBatch = useCallback((b) => confirmDeleteBatchRef.current(b), []);

  const resultMenuActions = (payload) => {
    if (!payload || !batchStatus) return [];
    const { res, idx, videoUrl } = payload;
    const batchId = batchStatus.batch_id;
    const held = res.success && res.review?.state === "needs_review";
    return [
      videoUrl && { label: t("videoGenerator.menu.play"), onClick: () => openResultPlayer(res, idx, videoUrl) },
      videoUrl && { label: t("videoGenerator.menu.openInNewTab"), onClick: () => window.open(videoUrl, "_blank") },
      res.metadata?.seed != null && {
        label: t("videoGenerator.menu.useSeed"),
        onClick: () => {
          setAdvancedParams((prev) => ({ ...prev, seed: String(res.metadata.seed) }));
          setSuccess(t("videoGenerator.messages.seedSet", { seed: res.metadata.seed }));
        },
      },
      held && { label: t("videoGenerator.menu.approveClip"), onClick: () => handleApproveClip(batchId, res.item_id), dividerBefore: true },
      held && { label: t("videoGenerator.menu.rerenderClip"), onClick: () => handleRerenderClip(batchId, res.item_id) },
      !res.video_path && res.frame_paths?.length > 0 && {
        label: t("videoGenerator.menu.combineFrames"),
        onClick: () => handleCombineFrames(batchId, res.item_id),
      },
      res.video_path && {
        label: t("videoGenerator.menu.rename"),
        onClick: () => handleRenameVideo(batchId, PathFromUrl(res.video_path)),
        dividerBefore: true,
      },
      res.video_path && {
        label: t("videoGenerator.menu.deleteVideo"),
        onClick: () => confirmDeleteVideo(batchId, res),
        color: "error.main",
      },
    ];
  };

  const batchMenuActions = (b) => {
    if (!b) return [];
    const running = b.status === "running" || b.status === "pending" || b.status === "processing";
    const finished = b.status === "completed" || b.status === "error" || b.status === "cancelled";
    return [
      {
        label: t("videoGenerator.menu.open"),
        onClick: () => {
          setActiveBatchId(b.batch_id);
          startPollingStatus(b.batch_id);
        },
      },
      b.status === "completed" && { label: t("videoGenerator.menu.downloadAll"), onClick: () => handleDownloadBatch(b.batch_id) },
      finished && { label: t("videoGenerator.menu.adjustRetry"), onClick: () => handleAdjustRetry(b.batch_id) },
      b.status === "error" && b.can_retry && { label: t("videoGenerator.menu.retry"), onClick: () => handleRetryBatch(b.batch_id) },
      running && { label: t("videoGenerator.menu.cancelBatch"), onClick: () => confirmCancelBatch(b), dividerBefore: true },
      {
        label: t("videoGenerator.menu.deleteBatch"),
        onClick: () => confirmDeleteBatch(b),
        color: "error.main",
        dividerBefore: !running,
      },
    ];
  };

  // The cards are memoized, so they are handed functions that keep their
  // identity; each call still runs this render's version.
  const actions = useStableCallbacks({
    applyPromptPreset,
    batchMenuActions,
    confirmCancelBatch,
    confirmDeleteVideo,
    fetchBatches,
    fetchPromptPreview,
    handleAdjustRetry,
    handleApproveClip,
    handleAudioGuideUpload,
    handleCancelBatch,
    handleClearCompletedQueue,
    handleCombineFrames,
    handleDeleteBatch,
    handleDownloadBatch,
    handleDragLeave,
    handleDragOver,
    handleDrop,
    handleEndFrameUpload,
    handleFileUpload,
    handleGenerate,
    handleRenameVideo,
    handleRerenderClip,
    handleRetryBatch,
    insertPromptTag,
    openBatch,
    openGallery,
    openResultPlayer,
    removeSelectedImage,
    requestDeleteBatch,
    resultMenuActions,
  });

  return (
    <PageLayout title={embedded ? undefined : t("videoGenerator.page.title")} variant={embedded ? "fullscreen" : "standard"} noPadding={embedded}>

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
              {t("videoGenerator.notReady.download", { name: modelNotReady.name })}
            </Button>
          }
        >
          <Trans
            i18nKey="videoGenerator.notReady.alert"
            values={{
              name: modelNotReady.name,
              missing: modelNotReady.missing?.length
                ? ` ${t("videoGenerator.notReady.missingFiles", { count: modelNotReady.missing.length })}`
                : "",
            }}
            components={{ bold: <strong /> }}
          />
        </Alert>
      )}

      {/* The page is five cards on the shared board: the current setup at a
          glance, what to make, how to make it, what is rendering, and what has
          been made. */}
      <Box sx={{ mt: 0.5 }}>
        <PageStatus>
        <StatusChip
          label={t("videoGenerator.tiles.model")}
          value={MODEL_OPTIONS[model]?.label || model}
          sub={modelMeta[model]?.is_ready === false ? t("videoGenerator.tiles.notInstalled") : accelLabel || t("videoGenerator.tiles.readinessUnknown")}
          tone={modelMeta[model]?.is_ready === false ? "error" : modelMeta[model]?.is_ready ? "ok" : "off"}
          onClick={() => setVideoModelsModalOpen(true)}
        />
        <StatusChip
          label={t("videoGenerator.tiles.frame")}
          value={`${computedParams.width}×${computedParams.height}`}
          sub={t("videoGenerator.tiles.frameSub", { frames: computedParams.duration_frames, fps: computedParams.fps })}
        />
        <StatusChip
          label={t("videoGenerator.tiles.steps")}
          value={String(computedParams.num_inference_steps)}
          sub={stepsTileNote({
            lowVramClamp: lowVramClampApplies,
            profile: profileSteps,
            presetLabel: qualityChipOptions.find((o) => o.value === qualityPreset)?.label,
          })}
        />
        <StatusChip
          label={t("videoGenerator.tiles.queue")}
          value={
            queue.filter((q) => ["queued", "running", "pending"].includes(q.status)).length
              ? t("videoGenerator.queue.active", { count: queue.filter((q) => ["queued", "running", "pending"].includes(q.status)).length })
              : t("videoGenerator.tiles.idle")
          }
          tone={gpuBusy ? "warn" : "ok"}
          sub={gpuBusy ? blockReason || t("videoGenerator.tiles.gpuBusy") : t("videoGenerator.tiles.readyToQueue")}
        />
        </PageStatus>
        <CardWorkspace
          boardKey="video-gen"
          version={2}
          presets={VIDEO_GEN_LAYOUTS.map((preset) => ({ ...preset, label: t(preset.labelKey) }))}
          cards={[
            {
              id: "prompt",
              title: t("layout.board.cards.prompt"),
              layout: VIDEO_GEN_LAYOUTS[0].layout.prompt,
              body: (
                <VideoPromptCard
                  applyPromptPreset={actions.applyPromptPreset}
                  canQueue={canQueue}
                  controlsDisabled={controlsDisabled}
                  dragActive={dragActive}
                  endFrame={endFrame}
                  endFrameModes={endFrameModes}
                  enhancePrompt={enhancePrompt}
                  fetchPromptPreview={actions.fetchPromptPreview}
                  fidelityMode={fidelityMode}
                  fileInputRef={fileInputRef}
                  handleDragLeave={actions.handleDragLeave}
                  handleDragOver={actions.handleDragOver}
                  handleDrop={actions.handleDrop}
                  handleEndFrameUpload={actions.handleEndFrameUpload}
                  handleFileUpload={actions.handleFileUpload}
                  handleGenerate={actions.handleGenerate}
                  highConsistencyMode={highConsistencyMode}
                  inputMode={inputMode}
                  insertPromptTag={actions.insertPromptTag}
                  isGenerating={isGenerating}
                  isUploading={isUploading}
                  isUploadingEndFrame={isUploadingEndFrame}
                  lookAndFeel={lookAndFeel}
                  lowVramClampApplies={lowVramClampApplies}
                  lowVramMode={lowVramMode}
                  model={model}
                  modelCaps={modelCaps}
                  modelMeta={modelMeta}
                  negativePrompt={negativePrompt}
                  openGallery={actions.openGallery}
                  parsedPrompts={parsedPrompts}
                  previewCounts={previewCounts}
                  previewEnhanceOff={previewEnhanceOff}
                  previewEnhanced={previewEnhanced}
                  previewLoading={previewLoading}
                  previewVerbatim={previewVerbatim}
                  promptGallery={promptGallery}
                  promptInputRef={promptInputRef}
                  promptPresets={promptPresets}
                  promptsText={promptsText}
                  refBoard={refBoard}
                  refFit={refFit}
                  refLimits={refLimits}
                  refModelIds={refModelIds}
                  refProblems={refProblems}
                  removeSelectedImage={actions.removeSelectedImage}
                  selectedImages={selectedImages}
                  selectedPromptPreset={selectedPromptPreset}
                  setEndFrame={setEndFrame}
                  setEnhancePrompt={setEnhancePrompt}
                  setError={setError}
                  setFidelityMode={setFidelityMode}
                  setHighConsistencyMode={setHighConsistencyMode}
                  setInputMode={setInputMode}
                  setLookAndFeel={setLookAndFeel}
                  setLowVramMode={setLowVramMode}
                  setNegativePrompt={setNegativePrompt}
                  setPreviewEnhanced={setPreviewEnhanced}
                  setPromptsText={setPromptsText}
                  setRefBoard={setRefBoard}
                  setShowPreview={setShowPreview}
                  showPreview={showPreview}
                  storyboardMode={storyboardMode}
                  storyboardShots={storyboardShots}
                  verbatimPrompts={verbatimPrompts}
                />
              ),
            },
            {
              id: "settings",
              title: t("layout.board.cards.settings"),
              layout: VIDEO_GEN_LAYOUTS[0].layout.settings,
              body: (
                <VideoSettingsCard
                  activeSpeedProfile={activeSpeedProfile}
                  advancedParams={advancedParams}
                  allowedAspectRatios={allowedAspectRatios}
                  anyModelReady={anyModelReady}
                  applicableAdapters={applicableAdapters}
                  applicableEncoders={applicableEncoders}
                  aspectRatio={aspectRatio}
                  audioGuide={audioGuide}
                  availableModels={availableModels}
                  blockDetail={blockDetail}
                  blockReason={blockReason}
                  canQueue={canQueue}
                  castError={castError}
                  castIdentityLocked={castIdentityLocked}
                  castSubjects={castSubjects}
                  cinematicKeyframe={cinematicKeyframe}
                  comfyUiUrl={comfyUiUrl}
                  computedParams={computedParams}
                  controlsDisabled={controlsDisabled}
                  directorGuidance={directorGuidance}
                  directorMode={directorMode}
                  durationPreset={durationPreset}
                  durationPresets={durationPresets}
                  faceRestoreAvailable={faceRestoreAvailable}
                  faceRestoreModelReady={faceRestoreModelReady}
                  faceRestoreNodeAvailable={faceRestoreNodeAvailable}
                  fetaEnabled={fetaEnabled}
                  fetaWeight={fetaWeight}
                  gpuBusy={gpuBusy}
                  handleAudioGuideUpload={actions.handleAudioGuideUpload}
                  handleGenerate={actions.handleGenerate}
                  highConsistencyMode={highConsistencyMode}
                  inputMode={inputMode}
                  isGenerating={isGenerating}
                  isUploadingAudioGuide={isUploadingAudioGuide}
                  keyframeModel={keyframeModel}
                  keyframeModelOptions={keyframeModelOptions}
                  model={model}
                  modelCaps={modelCaps}
                  modelMeta={modelMeta}
                  motionPreset={motionPreset}
                  offeredStyles={offeredStyles}
                  postUpscale={postUpscale}
                  profileSteps={profileSteps}
                  promptControls={promptControls}
                  promptStyle={promptStyle}
                  qualityChipOptions={qualityChipOptions}
                  qualityPreset={qualityPreset}
                  qualityTier={qualityTier}
                  selectedSubjectIds={selectedSubjectIds}
                  setAdvancedParams={setAdvancedParams}
                  setAspectRatio={setAspectRatio}
                  setAudioGuide={setAudioGuide}
                  setCastReload={setCastReload}
                  setCinematicKeyframe={setCinematicKeyframe}
                  setDirectorGuidance={setDirectorGuidance}
                  setDirectorMode={setDirectorMode}
                  setDurationPreset={setDurationPreset}
                  setFetaEnabled={setFetaEnabled}
                  setFetaWeight={setFetaWeight}
                  setHighlightModelId={setHighlightModelId}
                  setKeyframeModel={setKeyframeModel}
                  setModel={setModel}
                  setMotionPreset={setMotionPreset}
                  setPostUpscale={setPostUpscale}
                  setPromptStyle={setPromptStyle}
                  setQualityPreset={setQualityPreset}
                  setQualityTier={setQualityTier}
                  setSelectedSubjectIds={setSelectedSubjectIds}
                  setStoryboardMode={setStoryboardMode}
                  setStoryboardShots={setStoryboardShots}
                  setVideoModelsModalOpen={setVideoModelsModalOpen}
                  setVideoSize={setVideoSize}
                  storyboardMode={storyboardMode}
                  storyboardShots={storyboardShots}
                  videoSize={videoSize}
                  voiceClips={voiceClips}
                />
              ),
            },
            {
              id: "queue",
              title: t("layout.board.cards.queue"),
              layout: VIDEO_GEN_LAYOUTS[0].layout.queue,
              body: (
                <VideoQueueCard
                  batchStatus={batchStatus}
                  confirmCancelBatch={actions.confirmCancelBatch}
                  confirmDeleteVideo={actions.confirmDeleteVideo}
                  currentResults={currentResults}
                  handleAdjustRetry={actions.handleAdjustRetry}
                  handleApproveClip={actions.handleApproveClip}
                  handleCancelBatch={actions.handleCancelBatch}
                  handleClearCompletedQueue={actions.handleClearCompletedQueue}
                  handleCombineFrames={actions.handleCombineFrames}
                  handleDeleteBatch={actions.handleDeleteBatch}
                  handleDownloadBatch={actions.handleDownloadBatch}
                  handleRenameVideo={actions.handleRenameVideo}
                  handleRerenderClip={actions.handleRerenderClip}
                  handleRetryBatch={actions.handleRetryBatch}
                  openResultPlayer={actions.openResultPlayer}
                  queue={queue}
                  resultMenuActions={actions.resultMenuActions}
                  setConfirmAction={setConfirmAction}
                />
              ),
            },
            {
              id: "results",
              title: t("layout.board.cards.results"),
              layout: VIDEO_GEN_LAYOUTS[0].layout.results,
              body: (
                <VideoLibraryCard
                  batchMenuActions={actions.batchMenuActions}
                  batches={batches}
                  confirmCancelBatch={actions.confirmCancelBatch}
                  fetchBatches={actions.fetchBatches}
                  handleRetryBatch={actions.handleRetryBatch}
                  openBatch={actions.openBatch}
                  requestDeleteBatch={actions.requestDeleteBatch}
                />
              ),
            },
          ]}
        />
      </Box>


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
                t("videoGenerator.gallery.title")
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
                <Typography color="text.secondary">{t("videoGenerator.gallery.noImages")}</Typography>
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
                <Typography color="text.secondary">{t("videoGenerator.gallery.noBatches")}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {t("videoGenerator.gallery.noBatchesHint")}
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
                            label={batchStatusLabel(batch.status)}
                            size="small"
                            color={batch.status === 'completed' ? 'success' : 'default'}
                          />
                          <Chip
                            label={t("videoGenerator.gallery.imageCount", { count: batch.completed_images ?? batch.total_images ?? 0 })}
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
          <Button onClick={() => setGalleryOpen(false)}>{t("videoGenerator.gallery.cancel")}</Button>
          {selectedBatch && gallerySelectedImages.size > 0 && (
            <Button
              variant="contained"
              onClick={confirmGallerySelection}
              startIcon={<CheckCircleIcon />}
            >
              {t("videoGenerator.gallery.select", { count: gallerySelectedImages.size })}
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

      <TextInputDialog
        open={Boolean(renameDialog)}
        title={t("videoGenerator.rename.title")}
        description={t("videoGenerator.rename.description")}
        label={t("videoGenerator.rename.label")}
        initialValue={renameDialog?.current || ""}
        submitLabel={t("videoGenerator.rename.submit")}
        onSubmit={submitRenameVideo}
        onClose={() => setRenameDialog(null)}
      />

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
                    title={isPlayerFullscreen ? t("videoGenerator.player.exitFullscreen") : t("videoGenerator.player.fullscreen")}
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
                    {t("videoGenerator.player.prev")}
                  </Button>
                  <Button size="small" disabled={!hasNext} onClick={() => navigateVideoPlayer(1)} endIcon={<NextIcon />}>
                    {t("videoGenerator.player.next")}
                  </Button>
                </Stack>
                <Stack direction="row" spacing={1}>
                  <Button size="small" onClick={() => window.open(videoPlayer.url, "_blank")} startIcon={<OpenInNewIcon />}>
                    {t("videoGenerator.player.open")}
                  </Button>
                  <Button size="small" onClick={() => {
                    const a = document.createElement("a");
                    a.href = videoPlayer.url;
                    a.download = videoPlayer.title;
                    a.click();
                  }} startIcon={<DownloadIcon />}>
                    {t("videoGenerator.player.download")}
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

export default VideoGeneratorPage;
