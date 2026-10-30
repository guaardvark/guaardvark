// frontend/src/pages/BatchImageGeneratorPage.jsx
// Batch Image Generator - Mass image generation with progress tracking
// Integrates with unified progress system and real-time updates

import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  TextField,
  Grid,
  Chip,
  LinearProgress,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Slider,
  Switch,
  FormControlLabel,
  Paper,
  ImageList,
  ImageListItem,
  ImageListItemBar,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  useTheme,
  useMediaQuery,
  Stack,
  Tooltip,
} from '@mui/material';
import {
  ExpandMore,
  Upload,
  PlayArrow,
  Download,
  GetApp,
  Visibility,
  Cancel,
  Settings as SettingsIcon,
  Refresh as RefreshIcon,
  Image as ImageIcon,
  CloudDownload,
} from '@mui/icons-material';

import { useImageProcesses } from '../contexts/UnifiedProgressContext';
import { useSearchParams } from 'react-router-dom';
import PageLayout from '../components/layout/PageLayout';
import CharacterPicker from '../components/filmcrew/CharacterPicker';
import ImageLightbox from '../components/images/ImageLightbox';
import BatchHistoryCard from '../components/images/BatchHistoryCard';
import BatchQueuePanel from '../components/images/BatchQueuePanel';
import useImageBatchQueue, { POLLABLE_STATUSES, TERMINAL_BATCH_STATUSES } from '../hooks/useImageBatchQueue';
import DeferredPromptField from '../components/images/DeferredPromptField';
import PromptTokenCounter from '../components/images/PromptTokenCounter';
import GpuGateBanner from '../components/common/GpuGateBanner';
import useJobsGate from '../hooks/useJobsGate';
import { formatUiError } from '../utils/uiError';
import { handleResponse } from '../api/apiClient';
import { ActionButton, Hint, SettingChip } from '../components/settings/ui';
import {
  QWEN_IMAGE_21_GUIDANCE_MAX,
  MOSS_IMAGE_GUIDANCE_MAX,
  FLUX3_KLEIN_GUIDANCE,
  ZIMAGE_GUIDANCE,
  ZIMAGE_PRESET_STEPS,
  ZIMAGE_STEPS,
  MAX_QUANTITY,
  IMAGE_MAX_SEED,
  parseImageSeed,
  buildFinalPrompts,
  clampQuantity,
  ignoresNegativeAndAnatomy,
  imageModelOption,
  isZimageModel,
  modelFamily,
  modelInstallNeed,
  promptStyleFor,
  qualityPresetsForModel,
  resolveQualityPreset,
  stripCopyCounter,
} from '../utils/batchImageSettings';
import lazyWithReload from '../utils/lazyWithReload';
import {
  batchToResume,
  createGalleryRefresher,
  imageProcessEvents,
  overlayLiveCounts,
} from '../utils/batchImageQueueWatch';
import { uiLocale, useUiTranslation } from '../i18n';

// Recent Batches cards drawn at first and per Show more.
const HISTORY_PAGE = 24;

const ImageModelsModal = lazyWithReload(() => import('../components/modals/ImageModelsModal'));

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const encodeFilename = (filename) => {
  if (!filename) return '';
  return filename.split('/').map(part => encodeURIComponent(part)).join('/');
};

const getFilenameFromPath = (path) => {
  if (!path) return null;
  return path.replace(/\\/g, '/').split('/').pop();
};

const mapBatchResultsToImages = (batchStatus) => {
  if (!batchStatus?.results) return [];
  return batchStatus.results
    .filter(r => r.success && r.image_path)
    .map(r => ({
      id: r.prompt_id,
      path: r.image_path,
      thumbnail: r.thumbnail_path,
      imageFilename: getFilenameFromPath(r.image_path),
      thumbnailFilename: getFilenameFromPath(r.thumbnail_path),
      // original_prompt is what the person typed; rewritten_prompt is the chat
      // model's version when one replaced it before rendering (details only).
      prompt: r.metadata?.original_prompt || '',
      rewrittenPrompt: r.metadata?.rewritten_prompt || '',
      metadata: r.metadata,
      batchId: batchStatus.batch_id,
    }));
};

const INPUT_MODES = ['single', 'bulk', 'csv', 'blueprint'];
// Recent Batches reloads at most this often while a batch is landing images.
const GALLERY_REFRESH_MS = 1500;

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

// Pull a renderable message out of an API error envelope or a fetch failure.
const errorMessageFrom = (payload, fallback) => formatUiError(payload) || fallback;

const BatchImageGeneratorPage = ({ embedded = false }) => {
  const { t, language } = useUiTranslation();
  const theme = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const isXs = useMediaQuery(theme.breakpoints.down('sm'));
  const isSm = useMediaQuery(theme.breakpoints.between('sm', 'md'));

  // Calculate responsive columns for ImageList
  const imageListCols = isXs ? 2 : isSm ? 3 : 4;

  // State management
  const [inputMode, setInputMode] = useState('single'); // 'single' (default, whole text as one prompt), 'bulk', 'csv', or 'blueprint'
  const [batchItems, setBatchItems] = useState(''); // Bulk textarea input like FileGenerationPage
  const [lookAndFeel, setLookAndFeel] = useState(''); // Style/aesthetic to apply to all prompts
  const [negativePrompt, setNegativePrompt] = useState('');
  const [csvFile, setCsvFile] = useState(null);
  const [blueprintFile, setBlueprintFile] = useState(null);
  const [quantity, setQuantity] = useState(1); // Number of images to generate
  const [activeBatch, setActiveBatch] = useState(null);
  const [batchHistory, setBatchHistory] = useState([]);
  const [clearedBatchIds, setClearedBatchIds] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem('clearedImageBatchIds') || '[]');
    } catch (e) {
      return [];
    }
  });
  const [generatedImages, setGeneratedImages] = useState([]);
  const [lightboxImage, setLightboxImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  // Typed seed; blank gives every image its own seed.
  const [seedText, setSeedText] = useState('');
  const [imageModelsModalOpen, setImageModelsModalOpen] = useState(false);
  // Set by an Install button: Manage models opens on that row and starts it.
  const [installModelId, setInstallModelId] = useState(null);
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  // Where the offline generator renders ("cuda", "mps", "cpu"); from /batch-image/status.
  // On a CUDA card the worker renders one picture at a time whatever Max Workers
  // says (batch_image_generator: one pipeline on one card), so the page shows that.
  const [generatorDevice, setGeneratorDevice] = useState(null);
  // Why Recent Batches could not be loaded; shown instead of "No image batches found".
  const [historyError, setHistoryError] = useState(null);
  const { gpuBusy, blockReason, blockDetail } = useJobsGate({ submitMode: 'queue' });

  // Director intelligence (new) — expand high-level concept via media_director (same as MV)
  const [directorEnabled, setDirectorEnabled] = useState(false);
  const [directorGuidance, setDirectorGuidance] = useState("");
  const [isExpanding, setIsExpanding] = useState(false);

  // Content type + quality enhancement state
  const [selectedPreset, setSelectedPreset] = useState('auto'); // 'auto' = auto-detect
  const [autoEnhance, setAutoEnhance] = useState(true);
  const [enhanceAnatomy, setEnhanceAnatomy] = useState(true);
  const [enhanceFaces, setEnhanceFaces] = useState(true);
  const [enhanceHands, setEnhanceHands] = useState(true);
  // Character casting: selected subject ids whose LoRA + trigger get applied.
  const [castSubjectIds, setCastSubjectIds] = useState([]);

  // Pre-cast a character when arriving from the Cast Library "Generate" button
  // (/batch-images?character=<id>).
  useEffect(() => {
    const cid = searchParams.get('character');
    if (cid) setCastSubjectIds([parseInt(cid, 10)]);
  }, [searchParams]);

  // ?mode=bulk (or single, csv, blueprint) opens that input mode.
  const modeParam = searchParams.get('mode');
  useEffect(() => {
    if (INPUT_MODES.includes(modeParam)) setInputMode(modeParam);
  }, [modeParam]);

  // Generation parameters
  const [params, setParams] = useState({
    model: 'auto',
    style: 'realistic',
    quality_preset: 'standard',
    // Modern family defaults (zimage / auto) — HF Turbo recipe 9/0, not SD-era 512/20/7.5
    width: 1024,
    height: 1024,
    steps: 9,
    steps_explicit: false,
    guidance: 0.0,
    max_workers: 2,
    preserve_order: true,
    generate_thumbnails: true,
    save_metadata: true
  });

  // Refs
  const fileInputRef = useRef(null);
  const blueprintFileInputRef = useRef(null);
  const pollingRef = useRef(null);
  // Tracks which batch polling is for; survives interval teardown so in-flight fetches
  // still apply (pollingRef is cleared on cleanup and must not gate result application).
  const pollingBatchIdRef = useRef(null);

  // Image-generation progress only: other jobs' events do not re-render the page.
  const imageProcesses = useImageProcesses();

  // Style options
  const styleOptions = [
    { value: 'realistic', label: t('imagesBatch.styles.realistic') },
    { value: 'artistic', label: t('imagesBatch.styles.artistic') },
    { value: 'cartoon', label: t('imagesBatch.styles.cartoon') },
    { value: 'sketch', label: t('imagesBatch.styles.sketch') },
    { value: 'infographic', label: t('imagesBatch.styles.infographic') },
    { value: 'technical', label: t('imagesBatch.styles.technical') }
  ];

  // Model options — fetched from the backend (/batch-image/models) so the list
  // never drifts from the canonical catalog. "Auto" renders with the Stills model
  // chosen in Settings > Generation; with none chosen it routes per prompt.
  const AUTO_MODEL_OPTION = {
    value: 'auto',
    label: t('imagesBatch.models.autoLabel'),
    description: t('imagesBatch.models.autoDescription'),
  };
  // Only the API's rows are state; the options, Auto included, are built at
  // render so their labels and notes follow the interface language.
  const [fetchedModelRows, setFetchedModelRows] = useState([]);
  const modelOptions = useMemo(
    () => [AUTO_MODEL_OPTION, ...fetchedModelRows.map(imageModelOption)],
    [fetchedModelRows, language],
  );
  // What Auto renders with today and how that model reads a prompt ('natural'
  // or 'tags'), from /batch-image/models: the same selection the batch uses.
  const [autoPromptStyle, setAutoPromptStyle] = useState(null);
  const [autoModel, setAutoModel] = useState(null);
  // User LoRAs from Manage Image Models: rows from the same models call, and the
  // ids switched on for this run. Sent as adapters; the backend resolves files.
  const [userAdapters, setUserAdapters] = useState([]);
  const [selectedAdapters, setSelectedAdapters] = useState([]);
  // User text encoders (Manage Image Models) and the one picked per slot for
  // this run, {slot: id}; an unpicked slot uses the shipped encoder.
  const [userEncoders, setUserEncoders] = useState([]);
  const [selectedEncoders, setSelectedEncoders] = useState({});
  // Models the backend filtered out (gated, unreachable). Listed read-only with the
  // reason — picking one used to mean a failed run or, worse, silent SD 1.5 output.
  const [unavailableModels, setUnavailableModels] = useState([]);

  // Z-Image guard: guidance MUST be 0 (the model is CFG-free) and steps must follow
  // its own recipe. Without this, switching in from another family left that family's
  // settings applied - e.g. Realistic Vision "High" (30 steps / guidance 8.0), which
  // the Z-Image preset list cannot correct because it has no 'high' entry, so
  // qualityPresets.find() returns undefined and re-applying silently no-ops.
  // Keyed on model + preset only, so the steps slider stays usable afterwards.
  useEffect(() => {
    if (!isZimageModel(params.model)) return;
    setParams(prev => {
      if (!isZimageModel(prev.model)) return prev;
      const wantedSteps = ZIMAGE_PRESET_STEPS[prev.quality_preset] ?? ZIMAGE_STEPS;
      if (prev.guidance === ZIMAGE_GUIDANCE && prev.steps === wantedSteps) return prev;
      return { ...prev, guidance: ZIMAGE_GUIDANCE, steps: wantedSteps, steps_explicit: false };
    });
  }, [params.model, params.quality_preset]);

  const loadImageModels = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/models`);
      const data = await handleResponse(response, { quiet: true });
      if (data.success && data.data?.models) {
        setFetchedModelRows(data.data.models);
        if (data.data.auto_prompt_style) setAutoPromptStyle(data.data.auto_prompt_style);
        setAutoModel(data.data.auto_model || null);
        setUnavailableModels(data.data.unavailable_models || []);
        setUserAdapters((data.data.adapters || []).filter((a) => a.is_downloaded));
        setUserEncoders((data.data.encoders || []).filter((e) => e.is_downloaded));
      }
    } catch (e) {
      debugLog('Failed to load image models', e);
      // Only "Auto" is left to pick from; say why instead of a near-empty list.
      setError(t('imagesBatch.errors.modelsLoadFailed', { error: formatUiError(e) || t('imagesBatch.errors.requestFailed') }));
    }
  }, []);

  useEffect(() => {
    loadImageModels();
  }, [loadImageModels]);

  // LoRAs that stack on the selected model's family. "auto" resolves to the
  // stills default on the backend, which is the Z-Image family.
  const family = modelFamily(params.model);
  const applicableAdapters = useMemo(() => {
    const fam = family === 'auto' ? 'zimage' : family.startsWith('krea') ? 'krea2' : family.replace('-turbo', '');
    return userAdapters.filter((a) => (a.applies_to || []).includes(fam));
  }, [userAdapters, family]);
  // Installed text encoders for the selected model's family, grouped by slot.
  const encoderSlots = useMemo(() => {
    const bySlot = {};
    for (const e of userEncoders) {
      if (!(e.applies_to || []).includes(family)) continue;
      if (!bySlot[e.slot]) bySlot[e.slot] = { slot: e.slot, label: e.slot_label, encoders: [] };
      bySlot[e.slot].encoders.push(e);
    }
    return Object.values(bySlot);
  }, [userEncoders, family]);
  const pickedEncoderIds = encoderSlots
    .map((group) => selectedEncoders[group.slot])
    .filter((id) => id && encoderSlots.some((group) => group.encoders.some((e) => e.id === id)));

  // Quality presets — family-aware (see utils/batchImageSettings for the tables).
  const isFlux = family === 'flux';
  const isQwenImage21 = family === 'qwen-image-21';
  const isMossImage = family === 'moss-image';
  const isFlux3Klein = family === 'flux3-klein';
  // Stills rendered by a ComfyUI graph: one picture at a time, 1K native, and a
  // guidance scale that is not SD-era CFG.
  const comfyStills = isFlux || isQwenImage21 || isMossImage || isFlux3Klein;
  const isZimage = isZimageModel(params.model);
  const unusedNegAnat = ignoresNegativeAndAnatomy(params.model);
  // A prose model (Z-Image, and Auto while it resolves to one) reads tag
  // suffixes as scene content, so the backend never applies the content type
  // to it (offline_image_generator.enhance_prompt_for_quality, natural branch).
  const prosePrompts = promptStyleFor(params.model, modelOptions, autoPromptStyle) === 'natural';
  const modelLabel = modelOptions.find(m => m.value === params.model)?.label || params.model;
  const autoModelLabel = autoModel
    ? (modelOptions.find(m => m.value === autoModel)?.name || autoModel)
    : null;
  const isModernDit = family === 'auto' || family === 'zimage' || family.startsWith('krea');
  const qualityPresets = qualityPresetsForModel(params.model);
  // A selected character renders with its base model's steps and guidance (the
  // backend ignores the page's values), so those controls are locked while one is
  // picked. Every mode but CSV sends the cast.
  const castLocked = castSubjectIds.length > 0 && inputMode !== 'csv';
  const castLockedNote = t('imagesBatch.cast.lockedNote');
  // The worker renders one picture at a time on a CUDA card (one pipeline, one
  // card), so Max Workers only applies elsewhere; the chip shows the real count.
  const oneAtATime = generatorDevice === 'cuda' || comfyStills;
  const effectiveWorkers = oneAtATime ? 1 : params.max_workers;
  const oneAtATimeNote = comfyStills && generatorDevice !== 'cuda'
    ? t('imagesBatch.workers.oneAtATimeModel', { model: isFlux ? 'FLUX' : isMossImage ? 'MOSS-Image' : isFlux3Klein ? 'FLUX 3 Klein' : 'Qwen-Image 2.1' })
    : t('imagesBatch.workers.oneAtATimeGpu');

  // Generating never downloads a model. A picked model that is not installed
  // holds Start until Install finishes. A character renders with its own base
  // model and blueprints use none, so neither waits on the picker.
  const installNeed = (inputMode === 'blueprint' || castLocked)
    ? null
    : modelInstallNeed(modelOptions, params.model);
  const installBlocksStart = !!installNeed?.blocks;
  // Manage models; given a model id, it opens on that row and starts Install.
  const openModels = (installId = null) => {
    setInstallModelId(installId);
    setImageModelsModalOpen(true);
  };
  const installName = installNeed?.option.name;
  const installSizeGb = installNeed?.option.sizeGb;
  const installButton = installNeed && (
    <Button
      color="inherit"
      size="small"
      startIcon={<CloudDownload />}
      onClick={() => openModels(installNeed.option.value)}
      sx={{ textTransform: 'none', whiteSpace: 'nowrap' }}
    >
      {t('imagesBatch.install.button')}
    </Button>
  );

  // Dimension presets — base + model-family 2K / Flux~2MP packs (filtered below)
  const dimensionPresetsBase = [
    // SD 1.5 / Standard presets
    { label: t('imagesBatch.dimensions.square', { width: 512, height: 512 }), width: 512, height: 512, pack: 'legacy' },
    { label: t('imagesBatch.dimensions.portrait', { width: 512, height: 768 }), width: 512, height: 768, pack: 'legacy' },
    { label: t('imagesBatch.dimensions.landscape', { width: 768, height: 512 }), width: 768, height: 512, pack: 'legacy' },
    { label: t('imagesBatch.dimensions.largeSquare', { width: 768, height: 768 }), width: 768, height: 768, pack: 'legacy' },
    { label: t('imagesBatch.dimensions.hdPortrait', { width: 512, height: 1024 }), width: 512, height: 1024, pack: 'legacy' },
    { label: t('imagesBatch.dimensions.hdLandscape', { width: 1024, height: 512 }), width: 1024, height: 512, pack: 'legacy' },
    // 1K / SDXL-class (default daily driver for modern models)
    { label: t('imagesBatch.dimensions.oneKSquare', { width: 1024, height: 1024 }), width: 1024, height: 1024, pack: '1k' },
    { label: t('imagesBatch.dimensions.oneKPortrait', { width: 832, height: 1216 }), width: 832, height: 1216, pack: '1k' },
    { label: t('imagesBatch.dimensions.oneKLandscape', { width: 1216, height: 832 }), width: 1216, height: 832, pack: '1k' },
    { label: t('imagesBatch.dimensions.oneKWide', { width: 1344, height: 768 }), width: 1344, height: 768, pack: '1k' },
    { label: t('imagesBatch.dimensions.oneKTall', { width: 768, height: 1344 }), width: 768, height: 1344, pack: '1k' },
    // Z-Image / Krea 2K pack (area ≤ ~2048²; long side up to 2688)
    { label: t('imagesBatch.dimensions.twoKSquare', { width: 2048, height: 2048 }), width: 2048, height: 2048, pack: '2k' },
    { label: t('imagesBatch.dimensions.twoKLandscapeWide', { width: 2688, height: 1472 }), width: 2688, height: 1472, pack: '2k' },
    { label: t('imagesBatch.dimensions.twoKUltrawide', { width: 2688, height: 1152 }), width: 2688, height: 1152, pack: '2k' },
    { label: t('imagesBatch.dimensions.twoKPortraitTall', { width: 1472, height: 2688 }), width: 1472, height: 2688, pack: '2k' },
    { label: t('imagesBatch.dimensions.twoKLandscapeClassic', { width: 2496, height: 1664 }), width: 2496, height: 1664, pack: '2k' },
    { label: t('imagesBatch.dimensions.twoKPortraitClassic', { width: 1664, height: 2496 }), width: 1664, height: 2496, pack: '2k' },
    // FLUX.1-dev max (~2.0 MP design range — NOT 2048²)
    { label: t('imagesBatch.dimensions.fluxSquare', { width: 1408, height: 1408 }), width: 1408, height: 1408, pack: 'flux2mp' },
    { label: t('imagesBatch.dimensions.fluxWide', { width: 1920, height: 1088 }), width: 1920, height: 1088, pack: 'flux2mp' },
    { label: t('imagesBatch.dimensions.fluxTall', { width: 1088, height: 1920 }), width: 1088, height: 1920, pack: 'flux2mp' },
    { label: t('imagesBatch.dimensions.fluxLandscape', { width: 1728, height: 1152 }), width: 1728, height: 1152, pack: 'flux2mp' },
    { label: t('imagesBatch.dimensions.fluxPortrait', { width: 1152, height: 1728 }), width: 1152, height: 1728, pack: 'flux2mp' },
    // Qwen-Image 2.1 2K pack: the official ComfyUI template notes native 2K
    // (2048x2048) output and sides in multiples of 32; no side above 2048.
    { label: t('imagesBatch.dimensions.qwenSquare', { width: 2048, height: 2048 }), width: 2048, height: 2048, pack: 'qwen2k' },
    { label: t('imagesBatch.dimensions.qwenWide', { width: 2048, height: 1152 }), width: 2048, height: 1152, pack: 'qwen2k' },
    { label: t('imagesBatch.dimensions.qwenTall', { width: 1152, height: 2048 }), width: 1152, height: 2048, pack: 'qwen2k' },
    { label: t('imagesBatch.dimensions.qwenLandscape', { width: 2048, height: 1376 }), width: 2048, height: 1376, pack: 'qwen2k' },
    { label: t('imagesBatch.dimensions.qwenPortrait', { width: 1376, height: 2048 }), width: 1376, height: 2048, pack: 'qwen2k' },
    // MOSS-Image 2 MP pack: the model's user guide recommends 1 to 2 MP and
    // sides in multiples of 16 (media_model_registry, moss-image family row).
    { label: t('imagesBatch.dimensions.mossSquare', { width: 1408, height: 1408 }), width: 1408, height: 1408, pack: 'moss2mp' },
    { label: t('imagesBatch.dimensions.mossWide', { width: 1920, height: 1088 }), width: 1920, height: 1088, pack: 'moss2mp' },
    { label: t('imagesBatch.dimensions.mossTall', { width: 1088, height: 1920 }), width: 1088, height: 1920, pack: 'moss2mp' },
    { label: t('imagesBatch.dimensions.mossLandscape', { width: 1728, height: 1152 }), width: 1728, height: 1152, pack: 'moss2mp' },
    { label: t('imagesBatch.dimensions.mossPortrait', { width: 1152, height: 1728 }), width: 1152, height: 1728, pack: 'moss2mp' },
  ];

  const dimensionPresetsForModel = (modelValue) => {
    const fam = modelFamily(modelValue);
    const flux = fam === 'flux';
    const qwen = fam === 'qwen-image-21';
    const moss = fam === 'moss-image';
    const modernDit = fam === 'auto' || fam === 'zimage' || fam.startsWith('krea');
    const legacySd = fam === 'sd';
    return dimensionPresetsBase.filter((p) => {
      // Draft 512/768 sizes available for Turbo/Krea as well as classic SD / auto
      if (p.pack === 'legacy') return legacySd || modernDit;
      if (p.pack === '1k') return !legacySd;
      if (p.pack === '2k') return modernDit && !flux;
      if (p.pack === 'flux2mp') return flux;
      if (p.pack === 'qwen2k') return qwen;
      if (p.pack === 'moss2mp') return moss;
      return true;
    });
  };

  const dimensionPresets = useMemo(
    () => dimensionPresetsForModel(params.model),
    // params.model only — forModel is pure over modelValue (language: the labels)
    [params.model, language],
  );

  // Content detection runs server-side at render time (auto_enhance /
  // content_preset are sent with the batch). The page no longer calls
  // /analyze-prompt on every keystroke: the result was never rendered.

  // NEW: Director expand (uses /batch-image/expand-concept; populates batchItems with plan shots for review/launch)
  const handleDirectorExpand = async () => {
    let idea = '';
    if (inputMode === 'single') {
      idea = (batchItems || lookAndFeel || '').trim();
    } else {
      idea = (batchItems || lookAndFeel || '').split('\n').find(l => l.trim()) || lookAndFeel;
    }
    if (!idea) {
      setError(t('imagesBatch.director.needIdea'));
      return;
    }
    setIsExpanding(true);
    setError('');
    try {
      const resp = await fetch(`${API_BASE}/batch-image/expand-concept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          idea,
          n: Math.max(1, Math.min(quantity || 4, 12)),
          look_and_feel: lookAndFeel,
          user_treatment: '',
          director_guidance: directorGuidance || undefined,
        })
      });
      let j = null;
      try {
        j = await resp.json();
      } catch (e) {
        j = null;
      }
      if (!resp.ok) {
        throw new Error(errorMessageFrom(j, `HTTP ${resp.status}`));
      }
      const plan = j?.data?.plan || j?.plan;
      const shots = (plan && plan.shots) || [];
      if (shots.length) {
        const lines = shots.map(s => (s.prompt || '').trim()).filter(Boolean);
        setBatchItems(lines.join('\n'));
        setInputMode('bulk'); // Director produces multiple -> switch to bulk
        setSuccess(t('imagesBatch.director.expanded', { count: lines.length }));
      } else {
        setError(t('imagesBatch.director.noShots'));
      }
    } catch (e) {
      setError(t('imagesBatch.director.failed', { error: e.message || e }));
    } finally {
      setIsExpanding(false);
    }
  };

  const checkServiceStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status`);

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        setError(t('imagesBatch.service.statusFailedHttp', { status: response.status }));
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError(t('imagesBatch.service.statusNotJson'));
        return;
      }

      const data = await response.json();

      if (!data.success) {
        setError(t('imagesBatch.service.unavailable'));
      }
      const device = data.data?.image_generator?.device;
      if (typeof device === 'string') setGeneratorDevice(device);
    } catch (err) {
      setError(t('imagesBatch.service.statusFailed'));
    }
  }, []);

  // Handle preset selection. Content presets shape the prompt (suffix + negatives)
  // and nothing else — picking one no longer rewrites steps/guidance/size, which the
  // model's own recipe owns. See the note on content_presets in offline_image_generator.
  const handlePresetChange = useCallback((presetName) => {
    setSelectedPreset(presetName);
  }, []);

  const loadBatchHistory = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/list`);

      if (!response.ok) {
        console.error(`Failed to load batch history: HTTP ${response.status}`);
        setHistoryError(t('imagesBatch.errors.backendHttp', { status: response.status }));
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        console.error('Response is not JSON:', contentType);
        setHistoryError(t('imagesBatch.errors.noBatchData'));
        return;
      }

      const data = await response.json();
      setHistoryError(null);

      if (data.success) {
        let storedCleared = [];
        try {
          storedCleared = JSON.parse(localStorage.getItem('clearedImageBatchIds') || '[]');
        } catch (e) {
          storedCleared = [];
        }
        const rawBatches = data.data.batches || [];
        // Infographic batches share the queue but are listed on their own page.
        const filtered = rawBatches.filter(
          (b) => !storedCleared.includes(b.batch_id) && (b.kind || 'image') === 'image',
        );
        setBatchHistory(filtered);
      }
    } catch (err) {
      console.error('Failed to load batch history:', err);
      setHistoryError(formatUiError(err) || t('imagesBatch.errors.requestFailed'));
    }
  }, []);

  // Follows every image batch, not only the one on screen: a batch finishing behind
  // the open one, or queued from chat, still reaches Recent Batches, and ImagesPage
  // (listening for images-updated) re-reads the library once a batch finishes.
  const galleryRefresherRef = useRef(null);
  if (!galleryRefresherRef.current) {
    galleryRefresherRef.current = createGalleryRefresher({
      reload: loadBatchHistory,
      notifyLibrary: () => window.dispatchEvent(new Event('images-updated')),
      delayMs: GALLERY_REFRESH_MS,
    });
  }
  useEffect(() => () => galleryRefresherRef.current.cancel(), []);

  // Live queue panel (mirrors Video Gen): stacked batches drain one at a time.
  // `activeBatch` is never cleared once a batch finishes, so it keeps the slow
  // poll alive (a batch queued from elsewhere still shows up) and the fast one
  // only while it runs.
  const activeBatchRunning = !!activeBatch && POLLABLE_STATUSES.has(activeBatch.status);
  const { queue, fetchQueue, clearCompleted: handleClearCompletedQueue } = useImageBatchQueue({
    onRows: (rows) => galleryRefresherRef.current.observeQueue(rows),
    onStalled: (err) => setError(t('imagesBatch.errors.queueStalled', { error: formatUiError(err) || t('imagesBatch.errors.requestFailed') })),
    keepWatching: activeBatchRunning,
    watchIdle: !!activeBatch,
  });

  const handleClearBatchList = useCallback(() => {
    const allIds = batchHistory.map((b) => b.batch_id);
    const updatedCleared = Array.from(new Set([...clearedBatchIds, ...allIds]));
    setClearedBatchIds(updatedCleared);
    try {
      localStorage.setItem('clearedImageBatchIds', JSON.stringify(updatedCleared));
    } catch (e) {
      console.error('Failed to save cleared batches to localStorage:', e);
    }
    setBatchHistory([]);
    setSuccess(t('imagesBatch.history.cleared'));
  }, [batchHistory, clearedBatchIds]);

  const hideBatch = useCallback((batchId) => {
    const updatedCleared = Array.from(new Set([...clearedBatchIds, batchId]));
    setClearedBatchIds(updatedCleared);
    try {
      localStorage.setItem('clearedImageBatchIds', JSON.stringify(updatedCleared));
    } catch (e) {
      console.error('Failed to save cleared batches to localStorage:', e);
    }
    setBatchHistory((prev) => prev.filter((b) => b.batch_id !== batchId));
  }, [clearedBatchIds]);

  const formatImageDate = useCallback((dStr) => {
    if (!dStr) return '';
    try {
      const d = new Date(dStr);
      if (isNaN(d.getTime())) return dStr;
      return d.toLocaleDateString(uiLocale(), { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return dStr;
    }
  }, []);

  // Load service status and history on mount (the queue hook loads itself).
  useEffect(() => {
    checkServiceStatus();
    loadBatchHistory();
  }, [loadBatchHistory]);

  const loadBatchById = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

      if (!response.ok) {
        setError(t('imagesBatch.errors.loadBatchFailedHttp', { status: response.status }));
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError(t('imagesBatch.errors.batchNotJson'));
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchStatus = data.data;
        setActiveBatch(batchStatus);
        // Replace, don't merge: a batch with no successful images must not keep
        // the previous batch's thumbnails on screen under its own header.
        setGeneratedImages(mapBatchResultsToImages(batchStatus));

        // Polling for a still-running batch is started by the activeBatch effect.
      }
    } catch (err) {
      setError(t('imagesBatch.errors.loadBatchFailed', { error: err.message }));
    }
  }, []);

  const stopPolling = useCallback(() => {
    pollingBatchIdRef.current = null;
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  const startPolling = useCallback((batchId) => {
    stopPolling();
    pollingBatchIdRef.current = batchId;

    pollingRef.current = setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

        if (!response.ok) {
          console.error('Polling error: HTTP', response.status);
          if (response.status === 404) {
            stopPolling();
            setError(t('imagesBatch.errors.batchNotFound'));
          }
          return;
        }

        const data = await response.json();

        if (data.success) {
          const batchStatus = data.data;

          // Ignore stale responses after cancel/complete or batch switch
          if (pollingBatchIdRef.current !== batchId || batchStatus.batch_id !== batchId) {
            return;
          }

          setActiveBatch(batchStatus);
          setGeneratedImages(mapBatchResultsToImages(batchStatus));

          // The queue panel has its own interval (2.5s while anything runs);
          // refresh it here only on the terminal transition so the two pollers
          // don't double the request rate for the life of the batch.
          if (TERMINAL_BATCH_STATUSES.has(batchStatus.status)) {
            stopPolling();
            setSuccess(t('imagesBatch.messages.batchFinished', { status: batchStatus.status }));
            loadBatchHistory();
            fetchQueue();
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
        // Don't stop polling on network errors, just log them
      }
    }, 2000);
  }, [stopPolling, fetchQueue, loadBatchHistory]);

  // Load specific batch if batch_id is in URL params (from ContentLibraryPage)
  useEffect(() => {
    const batchId = searchParams.get('batch');
    if (batchId) {
      loadBatchById(batchId);
    }
  }, [searchParams]);

  // Start/stop polling only when batch id or terminal status changes — not on every
  // progress tick (socket + poll both update activeBatch and used to restart polling,
  // clearing pollingRef and discarding in-flight status responses).
  const activeBatchId = activeBatch?.batch_id;
  const activeBatchStatus = activeBatch?.status;
  useEffect(() => {
    if (activeBatchId && POLLABLE_STATUSES.has(activeBatchStatus)) {
      startPolling(activeBatchId);
    } else {
      stopPolling();
    }

    return () => {
      stopPolling();
    };
  }, [activeBatchId, activeBatchStatus, startPolling, stopPolling]);

  // Keep generatedImages in sync with activeBatch.results (single source of truth for live batch).
  // This prevents stale/mismatched state during rapid polling updates.
  useEffect(() => {
    if (activeBatch && activeBatch.results && activeBatch.results.length > 0) {
      const synced = mapBatchResultsToImages(activeBatch);
      const syncedKey = synced.map((img) => img.id).join(',');
      const currentKey = generatedImages.map((img) => img.id).join(',');
      if (syncedKey !== currentKey) {
        setGeneratedImages(synced);
      }
    }
  }, [activeBatch]);

  // Monitor progress system for batch updates
  const completionHandledRef = useRef(null);
  useEffect(() => {
    const batchProcesses = imageProcesses.filter((process) => process.additional_data?.batch_id);

    if (batchProcesses.length > 0 && activeBatch) {
      const batchProcess = batchProcesses.find(
        p => p.additional_data.batch_id === activeBatch.batch_id
      );

      if (batchProcess) {
        // Update active batch with progress info from SocketIO
        setActiveBatch(prev => prev ? {
          ...prev,
          status: TERMINAL_BATCH_STATUSES.has(prev.status) ? prev.status : 'running',
          completed_images: batchProcess.additional_data.completed || prev.completed_images,
          progress_percentage: batchProcess.progress || prev.progress_percentage
        } : null);

        // On completion/error/cancel, do an immediate final poll to get all results
        if (['complete', 'end', 'error', 'cancelled'].includes(batchProcess.status) &&
            completionHandledRef.current !== activeBatch.batch_id) {
          completionHandledRef.current = activeBatch.batch_id;
          loadBatchById(activeBatch.batch_id);
          loadBatchHistory();
        }
      }
    }
  }, [imageProcesses, activeBatch?.batch_id, loadBatchById]);

  // Every image batch's progress events, not only the open batch's: a finish
  // refreshes Recent Batches and the library, and a batch the queue does not list
  // yet (started from chat or MCP while this page idled) gets the queue re-read.
  const finishedBatchesRef = useRef(new Set());
  const queueReadForRef = useRef(new Set());
  useEffect(() => {
    const refresher = galleryRefresherRef.current;
    const { finished, unknown } = imageProcessEvents(
      imageProcesses, finishedBatchesRef.current, refresher.knownBatchIds(),
    );
    finished.forEach((id) => finishedBatchesRef.current.add(id));
    const unread = unknown.filter((id) => !queueReadForRef.current.has(id));
    unread.forEach((id) => queueReadForRef.current.add(id));
    if (finished.length) refresher.schedule({ library: true });
    if (finished.length || unread.length) fetchQueue();
  }, [imageProcesses, fetchQueue]);

  // With no batch open, show the running one live (after a tab switch remounts the
  // page, or when one starts from elsewhere). A ?batch= link picks its own.
  const resumeRequestedRef = useRef(null);
  useEffect(() => {
    if (activeBatch || searchParams.get('batch')) return;
    const batchId = batchToResume(queue);
    if (!batchId || resumeRequestedRef.current === batchId) return;
    resumeRequestedRef.current = batchId;
    loadBatchById(batchId);
  }, [queue, activeBatch, searchParams, loadBatchById]);

  const liveBatchHistory = useMemo(() => overlayLiveCounts(batchHistory, queue), [batchHistory, queue]);
  const promptFlushRef = useRef(null);
  // Recent Batches draws a page of cards at a time; every card loads a preview.
  const [historyShown, setHistoryShown] = useState(HISTORY_PAGE);

  // The exact prompt list that Start Generation submits (single or bulk mode).
  const finalPrompts = useMemo(
    () => buildFinalPrompts({ inputMode, batchItems, lookAndFeel, quantity }),
    [inputMode, batchItems, lookAndFeel, quantity],
  );
  // Distinct prompts before the quantity multiplier — what the counters show.
  const uniquePromptCount = useMemo(
    () => buildFinalPrompts({ inputMode, batchItems, lookAndFeel: '', quantity: 1 }).length,
    [inputMode, batchItems],
  );

  // Handle quality preset changes
  const handleQualityPresetChange = (presetValue) => {
    const preset = qualityPresets.find(p => p.value === presetValue);
    if (preset) {
      setParams(prev => {
        const next = {
          ...prev,
          quality_preset: presetValue,
          steps: preset.steps,
          steps_explicit: false,
          guidance: preset.guidance,
        };
        // Z-Image High 2K = official sampling at 2K (the real quality lever for
        // Turbo). Only the EXPLICIT 'high-2k' preset touches the canvas, and
        // leaving it restores 1K — otherwise a "Fast" draft kept running at 2048²,
        // which is the 16GB OOM the preset was split out to avoid.
        const zimageFamily = isZimageModel(prev.model) || modelFamily(prev.model) === 'auto';
        if (zimageFamily && presetValue === 'high-2k') {
          next.width = 2048;
          next.height = 2048;
        } else if (prev.quality_preset === 'high-2k' && prev.width === 2048 && prev.height === 2048) {
          next.width = 1024;
          next.height = 1024;
        }
        return next;
      });
    }
  };

  // Handle model changes
  const handleModelChange = (modelValue) => {
    setParams(prev => {
      const fam = modelFamily(modelValue);
      let newParams = { ...prev, model: modelValue };

      // Adjust dimensions based on model capabilities.
      // Modern high-res models (SDXL, Z-Image, FLUX, Krea) and 'auto' → 1024.
      // SD1.5-class photoreal finetunes (realistic-vision, epic-realism) are 512-native.
      if (fam === 'sd') {
        newParams.width = 512;
        newParams.height = 512;
      } else {
        newParams.width = 1024;
        newParams.height = 1024;
      }

      // Every family has its own preset list; a value carried over from another
      // family (e.g. 'flux-quality' after leaving FLUX, or 'fast' arriving at
      // Krea Raw) is not in the new list, so the Select showed nothing and the
      // "Quality" chip described settings not in use. Apply the family default.
      const presetValue = resolveQualityPreset(modelValue, prev.quality_preset);
      const preset = qualityPresetsForModel(modelValue).find((p) => p.value === presetValue);
      newParams.quality_preset = presetValue;
      newParams.steps_explicit = false;
      if (preset) {
        newParams.steps = preset.steps;
        newParams.guidance = preset.guidance;
      }
      if (fam === 'zimage') {
        // Z-Image is CFG-free: guidance MUST be 0, official recipe is 9 steps.
        newParams.steps = ZIMAGE_PRESET_STEPS[presetValue] ?? ZIMAGE_STEPS;
        newParams.guidance = ZIMAGE_GUIDANCE;
      }
      if (fam === 'flux3-klein') {
        newParams.guidance = FLUX3_KLEIN_GUIDANCE;
      }
      if (fam === 'flux' || fam === 'qwen-image-21' || fam === 'moss-image' || fam === 'flux3-klein') {
        newParams.max_workers = 1; // VRAM safety — heavy Comfy graph
      }
      // The explicit High 2K preset is the only thing that puts the canvas at 2048².
      if (presetValue === 'high-2k') {
        newParams.width = 2048;
        newParams.height = 2048;
      }

      return newParams;
    });
  };

  // Browsers report CSVs as text/csv, application/vnd.ms-excel, or nothing at
  // all depending on the OS's file associations, so the extension is the
  // reliable signal (the backend re-validates by extension too).
  const isCsvFile = (file) =>
    !!file && (file.type === 'text/csv' || (file.name || '').toLowerCase().endsWith('.csv'));

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    if (isCsvFile(file)) {
      setCsvFile(file);
      setError('');
    } else {
      setError(t('imagesBatch.csv.invalidFile'));
      setCsvFile(null);
    }
  };

  const handleBlueprintFileUpload = (event) => {
    const file = event.target.files[0];
    if (isCsvFile(file)) {
      setBlueprintFile(file);
      setError('');
    } else {
      setError(t('imagesBatch.blueprint.invalidFile'));
      setBlueprintFile(null);
    }
  };

  const downloadTemplate = async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/template`);

      if (!response.ok) {
        setError(t('imagesBatch.csv.templateFailedHttp', { status: response.status }));
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('text/csv') && !contentType.includes('application/octet-stream')) {
        setError(t('imagesBatch.csv.templateNotCsv'));
        return;
      }

      const blob = await response.blob();

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'batch_generation_template.csv';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError(t('imagesBatch.csv.templateFailed', { error: err.message }));
    }
  };

  const startGeneration = async () => {
    // The prompt box hands its text over after a pause; take what is typed now.
    const typed = promptFlushRef.current ? promptFlushRef.current() : batchItems;
    setLoading(true);
    setError('');
    setGeneratedImages([]);
    setSuccess('');

    try {
      let response;

      if (inputMode === 'blueprint') {
        if (!blueprintFile) {
          setError(t('imagesBatch.blueprint.noFile'));
          setLoading(false);
          return;
        }

        const formData = new FormData();
        formData.append('file', blueprintFile);

        response = await fetch(`${API_BASE}/batch-image/generate/blueprints`, {
          method: 'POST',
          body: formData
        });
      } else if (inputMode === 'csv' && csvFile) {
        // CSV upload
        const formData = new FormData();
        formData.append('file', csvFile);

        // Add parameters
        Object.entries(params).forEach(([key, value]) => {
          formData.append(key, value.toString());
        });

        response = await fetch(`${API_BASE}/batch-image/generate/csv`, {
          method: 'POST',
          body: formData
        });
      } else if (inputMode === 'csv' && !csvFile) {
        setError(t('imagesBatch.csv.noFile'));
        setLoading(false);
        return;
      } else {
        // Single (whole text = one prompt) or bulk (one per line). The same
        // builder feeds the Preview dialog, so what is shown is what is sent.
        // No " (i+1)" numbering: the backend keys images by position
        // (prompt_id=prompt_N) and the text encoder read the counter as content.
        const promptsToGenerate = typed === batchItems
          ? finalPrompts
          : buildFinalPrompts({ inputMode, batchItems: typed, lookAndFeel, quantity });
        if (promptsToGenerate.length === 0) {
          setError(inputMode === 'single' ? t('imagesBatch.errors.noPrompt') : t('imagesBatch.errors.noPrompts'));
          setLoading(false);
          return;
        }

        debugLog('Batch image prompts prepared', { promptCount: promptsToGenerate.length, quantity });

        const uiConfig = {
          inputMode,
          batchItems: typed,
          lookAndFeel,
          negativePrompt,
          quantity,
          params,
          castSubjectIds,
          selectedPreset,
          autoEnhance,
          enhanceAnatomy,
          enhanceFaces,
          enhanceHands,
          directorEnabled,
          directorGuidance,
          seedText,
          selectedAdapters,
          selectedEncoders,
          // The encoders this request sends (picks for another family are not),
          // so a Regenerate from the Media page asks for the same ones.
          textEncoders: pickedEncoderIds,
        };

        const typedSeed = parseImageSeed(seedText);
        response = await fetch(`${API_BASE}/batch-image/generate/prompts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompts: promptsToGenerate,
            ...params,
            ...(typedSeed != null ? { seed: typedSeed } : {}),
            // Cast characters: backend resolves these to LoRA paths + trigger.
            subject_ids: castSubjectIds,
            // User LoRAs switched on below the model select.
            adapters: selectedAdapters
              .filter((id) => applicableAdapters.some((a) => a.id === id))
              .map((id) => ({ id })),
            // Text encoders picked under the model select; none = shipped.
            ...(pickedEncoderIds.length ? { text_encoders: pickedEncoderIds } : {}),
            // Quality enhancement parameters
            content_preset: selectedPreset === 'auto' ? null : selectedPreset,
            auto_enhance: autoEnhance,
            negative_prompt: negativePrompt,
            enhance_anatomy: enhanceAnatomy,
            enhance_faces: enhanceFaces,
            enhance_hands: enhanceHands,
            // Director (shared intelligent pipeline with MusicVideo / chat)
            director_mode: !!directorEnabled,
            director_guidance: directorGuidance || undefined,
            ui_config: uiConfig,
          })
        });
      }

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        let errorMessage = t('imagesBatch.errors.startFailedHttp', { status: response.status, statusText: response.statusText });
        try {
          errorMessage = errorMessageFrom(await response.json(), errorMessage);
        } catch (e) {
          // Non-JSON body: keep the HTTP status text.
        }
        setError(errorMessage);
        setLoading(false);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        const text = await response.text();
        setError(t('imagesBatch.errors.startNotJson', { response: text.substring(0, 200) }));
        setLoading(false);
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchId = data.data?.batch_id;
        if (!batchId) {
          setError(t('imagesBatch.errors.noBatchId'));
          setLoading(false);
          return;
        }

        const initialStatus = data.data?.status || 'queued';
        setActiveBatch({
          batch_id: batchId,
          status: initialStatus,
          total_images: data.data?.prompt_count || data.data?.total_images || 0,
          completed_images: 0,
          failed_images: 0,
          progress_percentage: 0,
        });
        setSuccess(
          inputMode === 'blueprint'
            ? (data.data?.message || t('imagesBatch.messages.blueprintQueued'))
            : t('imagesBatch.messages.queued'),
        );
        // Free the form so the next job can be composed immediately (like Video Gen).
        if (inputMode === 'single' || inputMode === 'bulk') {
          setBatchItems('');
        }
        fetchQueue();
        loadBatchHistory();
      } else {
        setError(errorMessageFrom(data, t('imagesBatch.errors.startFailedPlain')));
      }
    } catch (err) {
      console.error('Generation error:', err);
      setError(t('imagesBatch.errors.startFailed', { error: err.message || String(err) }));
    } finally {
      setLoading(false);
    }
  };

  // POST /cancel for one batch; returns true on success and surfaces the
  // server's message otherwise. Shared by the Cancel button and the queue rows.
  const requestCancel = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/cancel/${batchId}`, { method: 'POST' });
      let data = null;
      try {
        data = await response.json();
      } catch (e) {
        data = null;
      }
      if (!response.ok || !data?.success) {
        setError(errorMessageFrom(data, t('imagesBatch.errors.cancelFailedHttp', { status: response.status })));
        return false;
      }
      return true;
    } catch (err) {
      setError(t('imagesBatch.errors.cancelFailed', { error: err.message }));
      return false;
    }
  }, []);

  const cancelGeneration = async () => {
    if (!activeBatch) return;
    const ok = await requestCancel(activeBatch.batch_id);
    if (!ok) return;
    setSuccess(t('imagesBatch.messages.cancelled'));
    stopPolling();
    setActiveBatch(prev => prev ? { ...prev, status: 'cancelled' } : null);
    fetchQueue();
  };

  // Stable identity: this is handed to every batch history card, which is memoised.
  // Only state setters and module constants are referenced, so [] deps are sound.
  // With `documentId` (the Media page's Adjust & Retry on one image), the
  // batch's settings load and that image's prompt and seed replace the batch's.
  const handleAdjustRetry = useCallback(async (batchId, documentId = null) => {
    try {
      const res = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);
      if (!res.ok) { setError(t('imagesBatch.retry.loadFailedHttp', { status: res.status })); return; }
      const data = await res.json();
      const statusObj = data?.data || data;
      const rd = statusObj?.retry_data;
      const name = statusObj?.display_name || batchId.slice(0, 8);

      if (!rd) { setError(t('imagesBatch.retry.noSettings')); return; }

      const cfg = rd.params?.ui_config;
      if (cfg) {
        if (cfg.inputMode) setInputMode(cfg.inputMode);
        if (typeof cfg.batchItems === 'string') setBatchItems(cfg.batchItems);
        if (typeof cfg.lookAndFeel === 'string') setLookAndFeel(cfg.lookAndFeel);
        if (typeof cfg.negativePrompt === 'string') setNegativePrompt(cfg.negativePrompt);
        if (typeof cfg.quantity === 'number') setQuantity(cfg.quantity);
        if (cfg.params && typeof cfg.params === 'object') {
          setParams((prev) => ({ ...prev, ...cfg.params }));
        }
        if (Array.isArray(cfg.castSubjectIds)) setCastSubjectIds(cfg.castSubjectIds);
        if (cfg.selectedPreset) setSelectedPreset(cfg.selectedPreset);
        if (typeof cfg.autoEnhance === 'boolean') setAutoEnhance(cfg.autoEnhance);
        if (typeof cfg.enhanceAnatomy === 'boolean') setEnhanceAnatomy(cfg.enhanceAnatomy);
        if (typeof cfg.enhanceFaces === 'boolean') setEnhanceFaces(cfg.enhanceFaces);
        if (typeof cfg.enhanceHands === 'boolean') setEnhanceHands(cfg.enhanceHands);
        if (typeof cfg.directorEnabled === 'boolean') setDirectorEnabled(cfg.directorEnabled);
        if (typeof cfg.directorGuidance === 'string') setDirectorGuidance(cfg.directorGuidance);
        setSeedText(typeof cfg.seedText === 'string' ? cfg.seedText : '');
        if (Array.isArray(cfg.selectedAdapters)) setSelectedAdapters(cfg.selectedAdapters);
        setSelectedEncoders(
          cfg.selectedEncoders && typeof cfg.selectedEncoders === 'object' ? cfg.selectedEncoders : {},
        );
        setSuccess(t('imagesBatch.retry.loadedSettings', { name }));
      } else if (Array.isArray(rd.prompts)) {
        // rd.prompts is the EXPANDED list - one entry per generated image. A batch of
        // 1 prompt x quantity 7 comes back as 7 identical lines, so pasting it straight
        // back multiplied the prompt on every retry (reported 2026-08-05).
        //
        // If every unique prompt repeats the same number of times, that count IS the
        // original quantity: N copies at qty 1 generates exactly the same images as
        // 1 copy at qty N, so collapsing is lossless rather than a guess. Uneven
        // counts (A x3, B x2) can't be expressed with one quantity, so those keep the
        // literal expanded list.
        // Strip the legacy " (i+1)" counter FIRST — with it attached every copy
        // looked unique, so the collapse below never fired and the suffixed list
        // was pasted straight back in to be suffixed again.
        const rehydrated = rd.prompts.map(stripCopyCounter);
        const promptCounts = new Map();
        rehydrated.forEach((pr) => promptCounts.set(pr, (promptCounts.get(pr) || 0) + 1));
        const uniquePrompts = [...promptCounts.keys()];
        const repeats = [...promptCounts.values()];
        const evenlyRepeated = repeats.length > 0 && repeats.every((r) => r === repeats[0]);

        if (evenlyRepeated && repeats[0] > 1) {
          setBatchItems(uniquePrompts.join('\n'));
          setQuantity(repeats[0]);
        } else {
          setBatchItems(rehydrated.join('\n'));
        }
        // Single prompt belongs in the single-prompt field, not the bulk textarea.
        setInputMode(uniquePrompts.length > 1 ? 'bulk' : 'single');
        if (rd.params) {
          const p = rd.params;
          setParams((prev) => ({
            ...prev,
            model: p.model || prev.model,
            style: p.style || prev.style,
            width: p.width || prev.width,
            height: p.height || prev.height,
            steps: p.steps || prev.steps,
            steps_explicit: false,
            guidance: p.guidance !== undefined ? p.guidance : prev.guidance,
          }));
        }
        setSuccess(
          uniquePrompts.length === rd.prompts.length
            ? t('imagesBatch.retry.loadedPrompts', { name })
            : t('imagesBatch.retry.loadedPromptsQty', { name, count: uniquePrompts.length, quantity: repeats[0] })
        );
      }
      if (documentId) {
        const docRes = await fetch(`${API_BASE}/files/document/${documentId}`);
        const docData = await docRes.json().catch(() => ({}));
        const meta = (docData?.data || docData)?.metadata || {};
        if (docRes.ok && meta.original_prompt) {
          // The filed prompt carries the batch's Look & Feel, which the form
          // keeps in its own field and appends again on Generate.
          const look = (rd.params?.ui_config?.lookAndFeel || '').trim();
          const typed = look && meta.original_prompt.endsWith(`, ${look}`)
            ? meta.original_prompt.slice(0, -(look.length + 2))
            : meta.original_prompt;
          setInputMode('single');
          setBatchItems(typed);
          setQuantity(1);
          setSeedText(meta.seed_used != null ? String(meta.seed_used) : '');
          setSuccess(t('imagesBatch.retry.loadedImage', { name }));
        }
      }
      setError('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      setError(t('imagesBatch.retry.loadFailed', { error: e.message }));
    }
  }, []);

  // Adjust & Retry from the Media page: ?retry=<batch>[&doc=<image>], loaded
  // once, then dropped from the address so a reload does not load it again.
  useEffect(() => {
    const retry = searchParams.get('retry');
    if (!retry) return;
    handleAdjustRetry(retry, searchParams.get('doc'));
    const next = new URLSearchParams(searchParams);
    next.delete('retry');
    next.delete('doc');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, handleAdjustRetry]);

  const downloadResults = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/download/${batchId}`);

      if (!response.ok) {
        setError(t('imagesBatch.download.failedHttp', { status: response.status }));
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('application/zip') && !contentType.includes('application/octet-stream')) {
        setError(t('imagesBatch.download.notZip'));
        return;
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `batch_${batchId}_results.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError(t('imagesBatch.download.failed', { error: err.message }));
    }
  }, []);

  const buildBatchImageUrl = useCallback((image, batchId) => {
    if (!batchId || !image?.imageFilename) return '';
    return `${API_BASE}/batch-image/image/${batchId}/${encodeFilename(image.imageFilename)}`;
  }, []);

  const openLightboxAt = useCallback((images, index) => {
    const img = images[index];
    if (!img) return;
    const batchId = img.batchId || activeBatch?.batch_id;
    const url = buildBatchImageUrl(img, batchId);
    if (!url) return;
    setLightboxImage({
      url,
      name: img.prompt || img.imageFilename || '',
      rewrittenPrompt: img.rewrittenPrompt || '',
      seed: img.metadata?.seed_used ?? null,
      fileIndex: index,
    });
  }, [activeBatch?.batch_id, buildBatchImageUrl]);

  const openImageViewer = useCallback((image) => {
    const idx = generatedImages.findIndex(img => img.id === image.id);
    openLightboxAt(generatedImages, idx >= 0 ? idx : 0);
  }, [generatedImages, openLightboxAt]);

  const openHistoryBatchGallery = useCallback(async (batch, startIndex = 0) => {
    if (!batch?.batch_id) return;
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batch.batch_id}?include_results=true`);
      if (!response.ok) {
        setError(t('imagesBatch.errors.loadBatchFailedHttp', { status: response.status }));
        return;
      }
      const data = await response.json();
      if (!data.success) return;

      const batchStatus = data.data;
      const images = mapBatchResultsToImages(batchStatus);
      setActiveBatch(batchStatus);
      setGeneratedImages(images);

      const idx = Math.max(0, Math.min(startIndex, images.length - 1));
      if (images.length > 0) {
        openLightboxAt(images, idx);
      }
    } catch (err) {
      setError(t('imagesBatch.errors.loadBatchFailed', { error: err.message }));
    }
  }, [openLightboxAt]);

  const closeLightbox = useCallback(() => setLightboxImage(null), []);

  const handleLightboxPrev = useCallback(() => {
    if (!lightboxImage || lightboxImage.fileIndex <= 0) return;
    openLightboxAt(generatedImages, lightboxImage.fileIndex - 1);
  }, [lightboxImage, generatedImages, openLightboxAt]);

  const handleLightboxNext = useCallback(() => {
    if (!lightboxImage || lightboxImage.fileIndex >= generatedImages.length - 1) return;
    openLightboxAt(generatedImages, lightboxImage.fileIndex + 1);
  }, [lightboxImage, generatedImages, openLightboxAt]);

  const handleLightboxDownload = useCallback(() => {
    if (!lightboxImage) return;
    const img = generatedImages[lightboxImage.fileIndex];
    if (!img) return;
    const batchId = img.batchId || activeBatch?.batch_id;
    const link = document.createElement('a');
    link.href = buildBatchImageUrl(img, batchId);
    link.download = img.imageFilename || 'image.png';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightboxImage, generatedImages, activeBatch?.batch_id, buildBatchImageUrl]);

  return (
    <PageLayout
      title={embedded ? undefined : t('imagesBatch.page.title')}
      variant={embedded ? "fullscreen" : "standard"}
      noPadding={embedded}
      actions={
        batchHistory.length > 0 ? (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={handleClearBatchList}
            sx={{ textTransform: 'none', borderRadius: 1 }}
          >
            {t('imagesBatch.history.clear')}
          </Button>
        ) : null
      }
    >

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
        {/* Input Section */}
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
                {t('imagesBatch.page.settingsTitle')}
              </Typography>

              {/* Content Preset Selector. Only tag-style models take it; a prose
                  model gets a one-line note instead of a control that does nothing. */}
              {prosePrompts ? (
                <Box sx={{ mb: 3 }}>
                  <Hint>
                    {family === 'auto'
                      ? (autoModelLabel
                        ? t('imagesBatch.contentType.unusedAutoModel', { model: autoModelLabel })
                        : t('imagesBatch.contentType.unusedAuto'))
                      : t('imagesBatch.contentType.unusedModel', { model: modelLabel })}
                    {' '}{t('imagesBatch.contentType.describeHint')}
                  </Hint>
                </Box>
              ) : (
              <Box sx={{ mb: 3, p: 2, bgcolor: 'primary.50', borderRadius: 2, border: '1px solid', borderColor: 'primary.200' }}>
                <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                  {t('imagesBatch.contentType.title')}
                </Typography>
                <FormControl fullWidth size="small">
                  <Select
                    value={selectedPreset}
                    onChange={(e) => handlePresetChange(e.target.value)}
                    sx={{ bgcolor: 'background.paper' }}
                  >
                    <MenuItem value="auto">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.auto')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.autoHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_portrait">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.portrait')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.portraitHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_full_body">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.fullBody')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.fullBodyHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_working">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.working')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.workingHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="product_photo">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.product')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.productHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="landscape">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.landscape')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.landscapeHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="infographic_preset">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.infographic')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.infographicHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="general">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{t('imagesBatch.contentType.general')}</Typography>
                        <Typography variant="caption" color="text.secondary">
                          {t('imagesBatch.contentType.generalHelp')}
                        </Typography>
                      </Box>
                    </MenuItem>
                  </Select>
                </FormControl>
              </Box>
              )}

              {/* Input Mode Selection */}
              <Box sx={{ mb: 3 }}>
                <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                  {t('imagesBatch.input.title')}
                </Typography>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button
                    variant={inputMode === 'single' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('single')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'single' ? 600 : 400
                    }}
                  >
                    {t('imagesBatch.input.single')}
                  </Button>
                  <Button
                    variant={inputMode === 'bulk' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('bulk')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'bulk' ? 600 : 400
                    }}
                  >
                    {t('imagesBatch.input.bulk')}
                  </Button>
                  <Button
                    variant={inputMode === 'csv' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('csv')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'csv' ? 600 : 400
                    }}
                  >
                    {t('imagesBatch.input.csv')}
                  </Button>
                  <Button
                    variant={inputMode === 'blueprint' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('blueprint')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'blueprint' ? 600 : 400
                    }}
                  >
                    {t('imagesBatch.input.blueprint')}
                  </Button>
                </Box>
              </Box>

              {/* Cast (optional) - for single prompt and bulk */}
              {(inputMode === 'single' || inputMode === 'bulk') && (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    {t('imagesBatch.cast.title')}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    {t('imagesBatch.cast.help')}
                  </Typography>
                  <CharacterPicker
                    value={castSubjectIds}
                    onChange={setCastSubjectIds}
                    onlyTrained
                  />
                </Box>
              )}

              {/* Single Prompt Input (default) */}
              {inputMode === 'single' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    {t('imagesBatch.input.single')}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    {t('imagesBatch.single.help')}
                  </Typography>
                  <DeferredPromptField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder={t('imagesBatch.single.placeholder')}
                    value={batchItems}
                    onCommit={setBatchItems}
                    flushRef={promptFlushRef}
                    variant="outlined"
                    sx={{
                      mb: 2,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1,
                        fontFamily: 'monospace',
                        fontSize: '0.9rem'
                      }
                    }}
                  />
                  <PromptTokenCounter prompts={finalPrompts} model={params.model} sx={{ mt: -1.5, mb: 1 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {t('imagesBatch.single.note')}
                  </Typography>
                </Box>
              )}

              {/* Bulk Input */}
              {inputMode === 'bulk' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    {t('imagesBatch.bulk.title')}
                  </Typography>
                  <DeferredPromptField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder={t('imagesBatch.bulk.placeholder')}
                    value={batchItems}
                    onCommit={setBatchItems}
                    flushRef={promptFlushRef}
                    variant="outlined"
                    sx={{
                      mb: 2,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1,
                        fontFamily: 'monospace',
                        fontSize: '0.9rem'
                      }
                    }}
                  />
                  <PromptTokenCounter prompts={finalPrompts} model={params.model} sx={{ mt: -1.5, mb: 1 }} />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      {t('imagesBatch.bulk.ready', { count: uniquePromptCount })}
                    </Typography>
                    <Button
                      variant="text"
                      size="small"
                      onClick={() => {
                        setBatchItems(
                          'A majestic mountain landscape at sunset\n' +
                          'A cat sitting on a windowsill\n' +
                          'Abstract geometric patterns in blue\n' +
                          'Portrait of a wise old wizard\n' +
                          'A futuristic city skyline'
                        );
                        setLookAndFeel('photorealistic, professional photography, sharp focus, natural lighting');
                      }}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.75rem',
                        minWidth: 'auto',
                        px: 1,
                        py: 0.25
                      }}
                    >
                      {t('imagesBatch.bulk.loadExamples')}
                    </Button>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {t('imagesBatch.bulk.tip')}
                  </Typography>
                </Box>
              )}

              {/* Look & Feel (shared for single + bulk) */}
              {(inputMode === 'single' || inputMode === 'bulk') && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    {t('imagesBatch.look.title')}
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={3}
                    placeholder={t('imagesBatch.look.placeholder')}
                    value={lookAndFeel}
                    onChange={(e) => setLookAndFeel(e.target.value)}
                    variant="outlined"
                    sx={{
                      mb: 1,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1
                      }
                    }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <Typography variant="caption" color="text.secondary">
                      {inputMode === 'single' ? t('imagesBatch.look.single') : t('imagesBatch.look.bulk', { count: uniquePromptCount })}
                    </Typography>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={() => setShowPromptPreview(true)}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.75rem',
                        minWidth: 'auto',
                        px: 1.5,
                        py: 0.5
                      }}
                    >
                      {t('imagesBatch.look.preview')}
                    </Button>
                  </Box>
                  <TextField
                    fullWidth
                    multiline
                    minRows={2}
                    maxRows={4}
                    label={t('imagesBatch.negative.label')}
                    placeholder="blurry, distorted hands, extra fingers"
                    value={negativePrompt}
                    disabled={unusedNegAnat}
                    onChange={(e) => setNegativePrompt(e.target.value)}
                    helperText={
                      unusedNegAnat
                        ? t('imagesBatch.negative.unused')
                        : undefined
                    }
                    sx={{ mt: 2 }}
                  />
                  <Box sx={{ mt: 1.5, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                    {/* The backend adds the anatomy phrasing only on the tag path
                        (offline_image_generator.enhance_prompt_for_quality); a prose
                        model, and Auto while it resolves to one, skips it. */}
                    <SettingChip
                      label={t('imagesBatch.anatomy.label')}
                      on={enhanceAnatomy}
                      disabled={prosePrompts}
                      tooltip={
                        prosePrompts
                          ? t('imagesBatch.anatomy.unusedTooltip')
                          : t('imagesBatch.anatomy.tooltip')
                      }
                      onToggle={setEnhanceAnatomy}
                    />
                    {(prosePrompts || unusedNegAnat) && (
                      <Hint>
                        {prosePrompts && unusedNegAnat
                          ? t('imagesBatch.anatomy.unusedBoth')
                          : prosePrompts
                            ? t('imagesBatch.anatomy.unused')
                            : t('imagesBatch.negative.unused')}
                      </Hint>
                    )}
                  </Box>
                </Box>
              )}

              {/* CSV Upload */}
              {inputMode === 'csv' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    {t('imagesBatch.csv.title')}
                  </Typography>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleFileUpload}
                    ref={fileInputRef}
                    style={{ display: 'none' }}
                  />

                  <Button
                    startIcon={<Upload />}
                    onClick={() => fileInputRef.current?.click()}
                    variant="outlined"
                    fullWidth
                    sx={{
                      mb: 2,
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5
                    }}
                  >
                    {t('imagesBatch.csv.upload')}
                  </Button>

                  {csvFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      {t('imagesBatch.csv.selected', { name: csvFile.name })}
                    </Alert>
                  )}

                  <Button
                    startIcon={<GetApp />}
                    onClick={downloadTemplate}
                    variant="text"
                    size="small"
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1
                    }}
                  >
                    {t('imagesBatch.csv.template')}
                  </Button>
                </Box>
              )}

              {/* Offline Blueprint Upload */}
              {inputMode === 'blueprint' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    {t('imagesBatch.blueprint.title')}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    {t('imagesBatch.blueprint.help')}
                  </Typography>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleBlueprintFileUpload}
                    ref={blueprintFileInputRef}
                    style={{ display: 'none' }}
                  />

                  <Button
                    startIcon={<Upload />}
                    onClick={() => blueprintFileInputRef.current?.click()}
                    variant="outlined"
                    fullWidth
                    sx={{
                      mb: 2,
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5
                    }}
                  >
                    {t('imagesBatch.blueprint.upload')}
                  </Button>

                  {blueprintFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      {t('imagesBatch.csv.selected', { name: blueprintFile.name })}
                    </Alert>
                  )}

                  <Typography variant="caption" color="text.secondary">
                    {t('imagesBatch.blueprint.format')}
                  </Typography>
                </Box>
              )}

              {inputMode !== 'blueprint' && (
                <>

                  {/* Current Settings Display */}
                  <Box sx={{ mt: 3, p: 2, backgroundColor: 'background.paper', borderRadius: 1, border: '1px solid', borderColor: 'divider' }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1, mb: 1 }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 500 }}>{t('imagesBatch.current.title')}</Typography>
                      <ActionButton onClick={() => openModels()}>{t('imagesBatch.current.manageModels')}</ActionButton>
                    </Box>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                      <Chip
                        label={installBlocksStart ? t('imagesBatch.current.modelNotInstalled', { model: modelLabel }) : t('imagesBatch.current.model', { model: modelLabel })}
                        size="small"
                        variant="outlined"
                        color={installBlocksStart ? 'warning' : 'default'}
                      />
                      <Chip
                        label={t('imagesBatch.current.style', { style: params.style })}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={t('imagesBatch.current.quality', { quality: params.quality_preset })}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={t('imagesBatch.current.size', { width: params.width, height: params.height })}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={castLocked ? t('imagesBatch.current.stepsCast') : t('imagesBatch.current.steps', { steps: params.steps })}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={oneAtATime ? t('imagesBatch.current.oneAtATime') : t('imagesBatch.current.workers', { count: effectiveWorkers })}
                        size="small"
                        variant="outlined"
                        title={oneAtATime ? oneAtATimeNote : undefined}
                      />
                    </Box>
                  </Box>

                  {/* Generation Settings */}
                  <Accordion sx={{ mt: 3, borderRadius: 1 }}>
                    <AccordionSummary
                      expandIcon={<ExpandMore />}
                      sx={{
                        borderRadius: 1,
                        '&.Mui-expanded': {
                          borderRadius: '8px 8px 0 0'
                        }
                      }}
                    >
                      <Typography sx={{ fontWeight: 500 }}>{t('imagesBatch.advanced.title')}</Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ pt: 2 }}>
                      <Grid container spacing={2}>
                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>{t('imagesBatch.advanced.model')}</InputLabel>
                            <Select
                              value={params.model}
                              onChange={(e) => handleModelChange(e.target.value)}
                            >
                              {modelOptions.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  <Box>
                                    <Typography variant="body2">{option.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                          {installNeed && (
                            <Alert severity={installNeed.blocks ? 'warning' : 'info'} action={installButton} sx={{ mt: 1, borderRadius: 1 }}>
                              {installNeed.blocks
                                ? installSizeGb
                                  ? t('imagesBatch.install.notInstalledSized', { model: installName, size: installSizeGb })
                                  : t('imagesBatch.install.notInstalled', { model: installName })
                                : installSizeGb
                                  ? t('imagesBatch.install.noModelSized', { model: installName, size: installSizeGb })
                                  : t('imagesBatch.install.noModel', { model: installName })}
                            </Alert>
                          )}
                          {applicableAdapters.length > 0 && (
                            <Box sx={{ mt: 1 }}>
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                                {t('imagesBatch.advanced.yourLoras')}
                              </Typography>
                              <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
                                {applicableAdapters.map((a) => {
                                  const on = selectedAdapters.includes(a.id);
                                  return (
                                    <Chip
                                      key={a.id}
                                      label={a.name}
                                      size="small"
                                      color={on ? 'primary' : 'default'}
                                      variant={on ? 'filled' : 'outlined'}
                                      onClick={() =>
                                        setSelectedAdapters((prev) =>
                                          on ? prev.filter((id) => id !== a.id) : [...prev, a.id],
                                        )
                                      }
                                    />
                                  );
                                })}
                              </Stack>
                            </Box>
                          )}
                          {encoderSlots.length > 0 && (
                            <Box sx={{ mt: 1.5 }}>
                              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                                {encoderSlots.map((group) => (
                                  <FormControl key={group.slot} size="small" sx={{ minWidth: 220 }}>
                                    <InputLabel>{t('imagesBatch.advanced.textEncoderSlot', { slot: group.label })}</InputLabel>
                                    <Select
                                      value={
                                        group.encoders.some((e) => e.id === selectedEncoders[group.slot])
                                          ? selectedEncoders[group.slot]
                                          : ''
                                      }
                                      label={t('imagesBatch.advanced.textEncoderSlot', { slot: group.label })}
                                      onChange={(e) =>
                                        setSelectedEncoders((prev) => ({ ...prev, [group.slot]: e.target.value }))
                                      }
                                    >
                                      <MenuItem value="">{t('imagesBatch.advanced.shippedEncoder')}</MenuItem>
                                      {group.encoders.map((e) => (
                                        <MenuItem key={e.id} value={e.id}>
                                          {e.name}
                                        </MenuItem>
                                      ))}
                                    </Select>
                                  </FormControl>
                                ))}
                              </Stack>
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                                {t('imagesBatch.advanced.encodersHint')}
                              </Typography>
                            </Box>
                          )}
                          {unavailableModels.length > 0 && (
                            <Box sx={{ mt: 1 }}>
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                                {t('imagesBatch.advanced.unavailable')}
                              </Typography>
                              {unavailableModels.map((m) => (
                                <Tooltip key={m.id} title={m.reason || ''}>
                                  <Typography
                                    variant="caption"
                                    color="text.disabled"
                                    sx={{ display: 'block', lineHeight: 1.4 }}
                                  >
                                    • {m.label} — {m.reason}
                                  </Typography>
                                </Tooltip>
                              ))}
                            </Box>
                          )}
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>{t('imagesBatch.advanced.qualityPreset')}</InputLabel>
                            <Select
                              value={params.quality_preset}
                              onChange={(e) => handleQualityPresetChange(e.target.value)}
                              disabled={castLocked}
                            >
                              {qualityPresets.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  <Box>
                                    <Typography variant="body2">{option.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                            {castLocked && (
                              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                                {castLockedNote}
                              </Typography>
                            )}
                          </FormControl>
                        </Grid>

                        <Grid item xs={12}>
                          {/* auto_enhance picks the prompt-enhancement rung at render time
                              (stills_policy.resolve_enhance_mode): a chat-model prose rewrite
                              for a natural-language model, tag stuffing for a tag-style one.
                              It never touches steps, guidance or size — those come from the
                              model's recipe above. */}
                          <FormControlLabel
                            control={
                              <Switch
                                checked={autoEnhance}
                                onChange={(e) => setAutoEnhance(e.target.checked)}
                                size="small"
                              />
                            }
                            label={
                              <Box>
                                <Typography variant="body2">{t('imagesBatch.enhance.label')}</Typography>
                                <Typography variant="caption" color="text.secondary">
                                  {!autoEnhance
                                    ? t('imagesBatch.enhance.off')
                                    : prosePrompts
                                      ? (params.style === 'realistic'
                                        ? t('imagesBatch.enhance.prose')
                                        : t('imagesBatch.enhance.proseStyle', { style: params.style }))
                                      : unusedNegAnat
                                        ? t('imagesBatch.enhance.tagsNoNegative')
                                        : t('imagesBatch.enhance.tags')}
                                </Typography>
                              </Box>
                            }
                          />
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>{t('imagesBatch.advanced.style')}</InputLabel>
                            <Select
                              value={params.style}
                              onChange={(e) => setParams({ ...params, style: e.target.value })}
                            >
                              {styleOptions.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  {option.label}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <TextField
                            fullWidth
                            label={t('imagesBatch.advanced.quantity')}
                            type="number"
                            value={quantity}
                            onChange={(e) => setQuantity(clampQuantity(e.target.value))}
                            inputProps={{ min: 1, max: MAX_QUANTITY }}
                          />
                          {/* /batch-image/expand-concept: the chat model turns the idea above
                              into one prompt per shot and fills the prompt box with them. */}
                          <Tooltip
                            arrow
                            title={t('imagesBatch.director.expandTooltip')}
                          >
                            <span>
                              <Button
                                variant="outlined"
                                size="small"
                                onClick={handleDirectorExpand}
                                disabled={isExpanding || !(batchItems || lookAndFeel).trim()}
                              >
                                {isExpanding ? t('imagesBatch.director.expanding') : t('imagesBatch.director.expand')}
                              </Button>
                            </span>
                          </Tooltip>
                          {/* director_mode: stills_policy picks the chat-model rewrite for every
                              prompt, whatever the model's default enhancement would be. */}
                          <Tooltip
                            arrow
                            title={t('imagesBatch.director.rewriteTooltip')}
                          >
                            <FormControlLabel
                              control={<Switch size="small" checked={directorEnabled} onChange={(e) => setDirectorEnabled(e.target.checked)} />}
                              label={t('imagesBatch.director.rewrite')}
                            />
                          </Tooltip>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>{t('imagesBatch.dimensions.title')}</InputLabel>
                            <Select
                              value={`${params.width}x${params.height}`}
                              onChange={(e) => {
                                const [width, height] = e.target.value.split('x').map(Number);
                                setParams({ ...params, width, height });
                              }}
                              renderValue={(v) => {
                                const match = dimensionPresets.find(
                                  (p) => `${p.width}x${p.height}` === v,
                                );
                                return match ? match.label : v;
                              }}
                            >
                              {!dimensionPresets.some(
                                (p) => p.width === params.width && p.height === params.height,
                              ) && (
                                <MenuItem value={`${params.width}x${params.height}`}>
                                  {t('imagesBatch.dimensions.custom', { width: params.width, height: params.height })}
                                </MenuItem>
                              )}
                              {dimensionPresets.map(preset => (
                                <MenuItem key={`${preset.width}x${preset.height}`} value={`${preset.width}x${preset.height}`}>
                                  {preset.label}
                                </MenuItem>
                              ))}
                            </Select>
                            {(comfyStills || isModernDit)
                              && (params.width * params.height > 1024 * 1024) && (
                              <Typography variant="caption" color="warning.main" sx={{ mt: 0.5, display: 'block' }}>
                                {t('imagesBatch.dimensions.highResWarning')}
                              </Typography>
                            )}
                          </FormControl>
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            {castLocked
                              ? t('imagesBatch.steps.cast')
                              : isFlux
                                ? t('imagesBatch.steps.flux', { steps: params.steps })
                                : t('imagesBatch.steps.value', { steps: params.steps })}
                          </Typography>
                          <Slider
                            value={params.steps}
                            onChange={(e, value) => setParams({ ...params, steps: value, steps_explicit: true })}
                            disabled={castLocked}
                            min={1}
                            max={100}
                            step={1}
                            marks={[
                              { value: 8, label: '8' },
                              { value: 20, label: '20' },
                              { value: 28, label: '28' },
                              { value: 50, label: '50' },
                              { value: 100, label: '100' },
                            ]}
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            {castLocked
                              ? t('imagesBatch.guidance.cast')
                              : isZimage
                              ? t('imagesBatch.guidance.zimage')
                              : isFlux
                                ? t('imagesBatch.guidance.flux', { guidance: params.guidance })
                                : t('imagesBatch.guidance.scale', { guidance: params.guidance })}
                          </Typography>
                          {/* Z-Image is CFG-free: there is NO valid non-zero guidance. Any other value
                              produces washed-out, over-cooked output, so the control is disabled rather
                              than left free to drift off the only correct setting. `auto` is left
                              editable: the router may pick SDXL, which does use CFG. */}
                          <Slider
                            value={isZimage ? ZIMAGE_GUIDANCE : isFlux3Klein ? FLUX3_KLEIN_GUIDANCE : params.guidance}
                            onChange={(e, value) => setParams({ ...params, guidance: value })}
                            min={0}
                            max={isFlux ? 6 : isQwenImage21 ? QWEN_IMAGE_21_GUIDANCE_MAX : isMossImage ? MOSS_IMAGE_GUIDANCE_MAX : 20}
                            step={0.5}
                            marks
                            disabled={isZimage || isFlux3Klein || castLocked}
                          />
                          {castLocked && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              {castLockedNote}
                            </Typography>
                          )}
                          {isZimage && !castLocked && (
                            <Typography variant="caption" color="text.secondary">
                              {t('imagesBatch.guidance.zimageHelp')}
                            </Typography>
                          )}
                          {isQwenImage21 && !castLocked && (
                            <Typography variant="caption" color="text.secondary">
                              {t('imagesBatch.guidance.qwenHelp')}
                            </Typography>
                          )}
                          {isFlux3Klein && !castLocked && (
                            <Typography variant="caption" color="text.secondary">
                              {t('imagesBatch.guidance.flux3KleinHelp')}
                            </Typography>
                          )}
                          {isMossImage && !castLocked && (
                            <Typography variant="caption" color="text.secondary">
                              {t('imagesBatch.guidance.mossHelp')}
                            </Typography>
                          )}
                          {family === 'auto' && (
                            <Typography variant="caption" color="text.secondary">
                              {t('imagesBatch.guidance.autoHelp')}
                            </Typography>
                          )}
                        </Grid>

                        <Grid item xs={12}>
                          <TextField
                            size="small"
                            label={t('imagesBatch.seed.label')}
                            placeholder={t('imagesBatch.seed.placeholder')}
                            value={seedText}
                            inputProps={{ inputMode: 'numeric' }}
                            onChange={(e) => setSeedText(e.target.value.replace(/\D/g, '').slice(0, String(IMAGE_MAX_SEED).length))}
                            sx={{ width: 180 }}
                          />
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                            {parseImageSeed(seedText) != null
                              ? t('imagesBatch.seed.fixed', { seed: parseImageSeed(seedText) })
                              : seedText.trim()
                                ? t('imagesBatch.seed.tooLarge', { max: IMAGE_MAX_SEED })
                                : t('imagesBatch.seed.blank')}
                          </Typography>
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            {t('imagesBatch.workers.max', { count: effectiveWorkers })}
                          </Typography>
                          <Slider
                            value={effectiveWorkers}
                            onChange={(e, value) => setParams({ ...params, max_workers: value })}
                            min={1}
                            max={4}
                            step={1}
                            marks
                            disabled={oneAtATime}
                          />
                          {oneAtATime && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              {oneAtATimeNote}
                            </Typography>
                          )}
                        </Grid>

                        <Grid item xs={12}>
                          <FormControlLabel
                            control={
                              <Switch
                                checked={params.generate_thumbnails}
                                onChange={(e) => setParams({ ...params, generate_thumbnails: e.target.checked })}
                              />
                            }
                            label={t('imagesBatch.advanced.thumbnails')}
                          />
                        </Grid>
                      </Grid>
                    </AccordionDetails>
                  </Accordion>
                </>
              )}

              {/* Action Buttons — queue mode: never blocked by an in-flight batch */}
              <Box sx={{ mt: 3, display: 'flex', flexDirection: 'column', gap: 1 }}>
                <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} blockDetail={blockDetail} queueMode />
                {installNeed && (
                  <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
                    <Hint>
                      {installNeed.blocks
                        ? t('imagesBatch.install.startOff', { model: installName })
                        : t('imagesBatch.install.noModelShort')}
                    </Hint>
                    {installButton}
                  </Box>
                )}
                <Box sx={{ display: 'flex', gap: 2 }}>
                  <Button
                    variant="contained"
                    onClick={startGeneration}
                    disabled={loading || installBlocksStart}
                    startIcon={<PlayArrow />}
                    fullWidth
                    size="large"
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5,
                      fontWeight: 600
                    }}
                  >
                    {loading
                      ? t('imagesBatch.actions.queuing')
                      : (queue.some((q) => POLLABLE_STATUSES.has(q.status))
                        ? t('imagesBatch.actions.addToQueue')
                        : t('imagesBatch.actions.start'))}
                  </Button>

                  {activeBatch && POLLABLE_STATUSES.has(activeBatch.status) && (
                    <Button
                      variant="outlined"
                      onClick={cancelGeneration}
                      startIcon={<Cancel />}
                      color="error"
                      size="large"
                      sx={{
                        textTransform: 'none',
                        borderRadius: 1,
                        py: 1.5,
                        fontWeight: 600
                      }}
                    >
                      {t('imagesBatch.actions.cancel')}
                    </Button>
                  )}
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Grid >

        {/* Progress and Results Section */}
        < Grid item xs={12} lg={6} >
          {/* Batch Queue panel — live view of stacked jobs (mirrors Video Gen) */}
          <BatchQueuePanel
            queue={queue}
            onClearCompleted={handleClearCompletedQueue}
            onCancel={async (batchId) => {
              const ok = await requestCancel(batchId);
              fetchQueue();
              if (ok && activeBatch?.batch_id === batchId) {
                setActiveBatch((prev) => (prev ? { ...prev, status: 'cancelled' } : prev));
              }
            }}
          />

          {/* Active Batch Progress */}
          {
            activeBatch && (
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
                    {t('imagesBatch.progress.title')}
                  </Typography>

                  <Box sx={{ mb: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      {t('imagesBatch.progress.batchId', { id: activeBatch.batch_id })}
                    </Typography>
                    <Chip
                      label={activeBatch.status.toUpperCase()}
                      color={activeBatch.status === 'running' ? 'primary' :
                        activeBatch.status === 'completed' ? 'success' : 'default'}
                      size="small"
                      sx={{ mt: 1 }}
                    />
                  </Box>

                  <Box sx={{ mb: 2 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                      <Typography variant="body2">
                        {t('imagesBatch.progress.count', { done: activeBatch.completed_images || 0, total: activeBatch.total_images || 0 })}
                      </Typography>
                      <Typography variant="body2">
                        {activeBatch.progress_percentage || 0}%
                      </Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={activeBatch.progress_percentage || 0}
                    />
                  </Box>

                  <Box sx={{ display: 'flex', gap: 1.5, mt: 1, flexWrap: 'wrap' }}>
                    {activeBatch.status === 'completed' && (
                      <Button
                        startIcon={<Download />}
                        onClick={() => downloadResults(activeBatch.batch_id)}
                        variant="outlined"
                        size="small"
                      >
                        {t('imagesBatch.progress.download')}
                      </Button>
                    )}
                    {['completed', 'error', 'cancelled'].includes(activeBatch.status) && (
                      <Button
                        startIcon={<SettingsIcon />}
                        onClick={() => handleAdjustRetry(activeBatch.batch_id)}
                        variant="outlined"
                        size="small"
                      >
                        {t('imagesBatch.progress.adjustRetry')}
                      </Button>
                    )}
                  </Box>
                </CardContent>
              </Card>
            )
          }

          {/* Generated Images Gallery */}
          {
            (generatedImages.length > 0 || (activeBatch && activeBatch.results && activeBatch.results.some(r => r.success && r.image_path))) && (
              <Card sx={{
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
                    {t('imagesBatch.gallery.title', { count: generatedImages.length || (activeBatch?.results?.filter(r => r.success && r.image_path).length || 0) })}
                  </Typography>

                  <ImageList
                    cols={imageListCols}
                    gap={8}
                    sx={{
                      '& .MuiImageListItem-root': {
                        borderRadius: 1,
                        overflow: 'hidden'
                      }
                    }}
                  >
                    {generatedImages.map((image) => {
                      // Prefer embedded batchId (from the result that created this image) to avoid
                      // race conditions between setActiveBatch and setGeneratedImages during live polling.
                      // Falls back to activeBatch for older data.
                      const batchIdForUrl = image.batchId || (activeBatch && activeBatch.batch_id);
                      // Always try thumbnail first if we have a filename for it.
                      // Otherwise use the main image name (serving logic will fallback if needed).
                      let thumbnailUrl = '';
                      if (batchIdForUrl) {
                        const thumbName = image.thumbnailFilename || image.imageFilename;
                        if (thumbName) {
                          thumbnailUrl = `${API_BASE}/batch-image/image/${batchIdForUrl}/${encodeFilename(thumbName)}?thumbnail=true`;
                        }
                      }

                      return (
                        <ImageListItem key={image.id}>
                          <img
                            src={thumbnailUrl}
                            alt={image.prompt || t('imagesBatch.gallery.generatedImage')}
                            loading="lazy"
                            decoding="async"
                            style={{ cursor: 'pointer' }}
                            onClick={() => openImageViewer(image)}
                            onError={(e) => {
                              console.error('Failed to load image thumbnail:', image.id, thumbnailUrl);
                              const batchIdForUrl = image.batchId || (activeBatch && activeBatch.batch_id);
                              // Robust fallback: always try the full original image (no ?thumbnail) before hiding.
                              // This ensures we show *something* even if dedicated thumbnail is missing or 404s.
                              if (image.imageFilename && batchIdForUrl && !e.target.dataset.fallbackAttempted) {
                                e.target.src = `${API_BASE}/batch-image/image/${batchIdForUrl}/${encodeFilename(image.imageFilename)}`;
                                e.target.dataset.fallbackAttempted = 'true';
                              } else if (!e.target.dataset.hidden) {
                                // Only hide as last resort
                                e.target.style.display = 'none';
                                e.target.dataset.hidden = 'true';
                              }
                            }}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                openImageViewer(image);
                              }
                            }}
                          />
                          <ImageListItemBar
                            title={image.prompt ? image.prompt.substring(0, 30) + '...' : t('imagesBatch.gallery.noPrompt')}
                            subtitle={[
                              image.metadata?.seed_used != null ? t('imagesBatch.gallery.seed', { seed: image.metadata.seed_used }) : null,
                              image.rewrittenPrompt ? t('imagesBatch.gallery.rewritten') : null,
                            ].filter(Boolean).join(' · ') || undefined}
                            actionIcon={
                              <IconButton
                                sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                                onClick={() => openImageViewer(image)}
                                aria-label={t('imagesBatch.gallery.viewFull', { name: image.prompt || t('imagesBatch.gallery.generatedImage') })}
                              >
                                <Visibility />
                              </IconButton>
                            }
                          />
                        </ImageListItem>
                      );
                    })}
                  </ImageList>
                </CardContent>
              </Card>
            )
          }
          {/* Batch History — Stacked Thumbnail Gallery */}
          <Card sx={{
            mt: 3,
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
                <Typography variant="h6" sx={{ fontWeight: 600 }}>
                  {t('imagesBatch.history.title')}
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  {batchHistory.length > 0 && (
                    <Button
                      size="small"
                      variant="outlined"
                      color="inherit"
                      onClick={handleClearBatchList}
                      sx={{ textTransform: 'none', borderRadius: 1 }}
                    >
                      {t('imagesBatch.history.clear')}
                    </Button>
                  )}
                  <IconButton size="small" onClick={loadBatchHistory} title={t('imagesBatch.history.refresh')}>
                    <RefreshIcon />
                  </IconButton>
                </Stack>
              </Stack>

              <Box sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}>
                <Grid container spacing={2}>
                  {liveBatchHistory.slice(0, historyShown).map((batch) => (
                    <Grid item xs={6} sm={4} md={3} key={batch.batch_id}>
                      <BatchHistoryCard
                        batch={batch}
                        dateStr={formatImageDate(batch.created_at || batch.start_time || batch.end_time)}
                        onOpen={openHistoryBatchGallery}
                        onLoad={loadBatchById}
                        onHide={hideBatch}
                        onDownload={downloadResults}
                        onAdjustRetry={handleAdjustRetry}
                      />
                    </Grid>
                  ))}
                </Grid>
                {liveBatchHistory.length > historyShown && (
                  <Box sx={{ textAlign: 'center', mt: 1.5 }}>
                    <Button size="small" onClick={() => setHistoryShown((n) => n + HISTORY_PAGE)}>
                      {t('imagesBatch.history.showMore', { count: Math.min(HISTORY_PAGE, liveBatchHistory.length - historyShown) })}
                    </Button>
                  </Box>
                )}

                {historyError && (
                  <Alert
                    severity="error"
                    sx={{ mb: 1 }}
                    action={<Button color="inherit" size="small" onClick={loadBatchHistory}>{t('imagesBatch.history.retry')}</Button>}
                  >
                    {t('imagesBatch.history.loadFailed', { reason: historyError })}
                  </Alert>
                )}
                {batchHistory.length === 0 && !historyError && (
                  <Box sx={{ textAlign: 'center', py: 4 }}>
                    <ImageIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
                    <Typography variant="body2" color="text.secondary">
                      {t('imagesBatch.history.empty')}
                    </Typography>
                  </Box>
                )}
              </Box>
            </CardContent>
          </Card>
        </Grid >
      </Grid >

      {/* Full-screen batch image gallery (prev/next keyboard + arrows) */}
      {lightboxImage && (
        <ImageLightbox
          imageUrl={lightboxImage.url}
          imageName={lightboxImage.name}
          caption={lightboxImage.rewrittenPrompt ? t('imagesBatch.lightbox.sentToModel', { prompt: lightboxImage.rewrittenPrompt }) : ''}
          seed={lightboxImage.seed}
          onUseSeed={(s) => {
            setSeedText(String(s));
            setSuccess(t('imagesBatch.lightbox.seedUsed', { seed: s }));
          }}
          onClose={closeLightbox}
          onPrev={handleLightboxPrev}
          onNext={handleLightboxNext}
          onDownload={handleLightboxDownload}
          hasPrev={lightboxImage.fileIndex > 0}
          hasNext={lightboxImage.fileIndex < generatedImages.length - 1}
        />
      )}

      {/* Prompt Preview Dialog */}
      <Dialog
        open={showPromptPreview}
        onClose={() => setShowPromptPreview(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {quantity > 1
            ? t('imagesBatch.preview.titleQuantity', { count: uniquePromptCount, quantity })
            : t('imagesBatch.preview.title', { count: uniquePromptCount })}
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t('imagesBatch.preview.intro')}
          </Typography>
          <Box sx={{ maxHeight: 400, overflow: 'auto' }}>
            {/* Same list startGeneration submits (Look & Feel appended, quantity applied). */}
            {finalPrompts.map((prompt, index) => (
              <Paper
                key={index}
                variant="outlined"
                sx={{
                  p: 2,
                  mb: 1,
                  backgroundColor: 'background.default',
                  border: '1px solid',
                  borderColor: 'divider'
                }}
              >
                <Typography variant="body2" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                  <strong>{index + 1}.</strong> {prompt}
                </Typography>
              </Paper>
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowPromptPreview(false)}>{t('imagesBatch.preview.close')}</Button>
        </DialogActions>
      </Dialog>
      <React.Suspense fallback={null}>
        <ImageModelsModal
          open={imageModelsModalOpen}
          installModelId={installModelId}
          onClose={() => {
            setImageModelsModalOpen(false);
            setInstallModelId(null);
            loadImageModels();
          }}
          showMessage={(msg, severity) => {
            if (severity === "error") setError(msg);
            else setSuccess(msg);
          }}
        />
      </React.Suspense>
    </PageLayout>
  );
};

export default BatchImageGeneratorPage;