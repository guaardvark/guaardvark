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
  Close as CloseIcon,
  Settings as SettingsIcon,
  Refresh as RefreshIcon,
  Image as ImageIcon,
  CloudDownload,
} from '@mui/icons-material';

import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';
import { useSearchParams } from 'react-router-dom';
import PageLayout from '../components/layout/PageLayout';
import CharacterPicker from '../components/filmcrew/CharacterPicker';
import ImageLightbox from '../components/images/ImageLightbox';
import BatchHistoryCard from '../components/images/BatchHistoryCard';
import GpuGateBanner from '../components/common/GpuGateBanner';
import useJobsGate from '../hooks/useJobsGate';
import { formatUiError } from '../utils/uiError';
import { ActionButton, Hint, SettingChip } from '../components/settings/ui';
import {
  QWEN_IMAGE_21_GUIDANCE_MAX,
  ZIMAGE_GUIDANCE,
  ZIMAGE_PRESET_STEPS,
  ZIMAGE_STEPS,
  MAX_QUANTITY,
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
const POLLABLE_STATUSES = new Set(['queued', 'pending', 'running']);
const TERMINAL_BATCH_STATUSES = new Set(['completed', 'error', 'cancelled']);
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
  const theme = useTheme();
  const [searchParams] = useSearchParams();
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
  const [imageModelsModalOpen, setImageModelsModalOpen] = useState(false);
  // Set by an Install button: Manage models opens on that row and starts it.
  const [installModelId, setInstallModelId] = useState(null);
  const [showPromptPreview, setShowPromptPreview] = useState(false);
  // Where the offline generator renders ("cuda", "mps", "cpu"); from /batch-image/status.
  // On a CUDA card the worker renders one picture at a time whatever Max Workers
  // says (batch_image_generator: one pipeline on one card), so the page shows that.
  const [generatorDevice, setGeneratorDevice] = useState(null);
  // Live queue panel (mirrors Video Gen) — stacked batches drain one-at-a-time
  const [queue, setQueue] = useState([]);
  const queueSignatureRef = useRef('');
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
  // (/images?character=<id>).
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

  // Progress system integration
  const { activeProcesses } = useUnifiedProgress();

  // Style options
  const styleOptions = [
    { value: 'realistic', label: 'Realistic' },
    { value: 'artistic', label: 'Artistic' },
    { value: 'cartoon', label: 'Cartoon' },
    { value: 'sketch', label: 'Sketch' },
    { value: 'infographic', label: 'Infographic' },
    { value: 'technical', label: 'Technical' }
  ];

  // Model options — fetched from the backend (/batch-image/models) so the list
  // never drifts from the canonical catalog. "Auto" renders with the Stills model
  // chosen in Settings > Generation; with none chosen it routes per prompt.
  const AUTO_MODEL_OPTION = {
    value: 'auto',
    label: 'Auto — best per prompt ⭐',
    description: 'Uses the Stills model chosen in Settings > Generation; with none chosen, the router picks the best downloaded model for each prompt',
  };
  const [modelOptions, setModelOptions] = useState([AUTO_MODEL_OPTION]);
  // What Auto renders with today and how that model reads a prompt ('natural'
  // or 'tags'), from /batch-image/models: the same selection the batch uses.
  const [autoPromptStyle, setAutoPromptStyle] = useState(null);
  const [autoModel, setAutoModel] = useState(null);
  // User LoRAs from Manage Image Models: rows from the same models call, and the
  // ids switched on for this run. Sent as adapters; the backend resolves files.
  const [userAdapters, setUserAdapters] = useState([]);
  const [selectedAdapters, setSelectedAdapters] = useState([]);
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
      const data = await response.json();
      if (data.success && data.data?.models) {
        const fetched = data.data.models.map(imageModelOption);
        setModelOptions([AUTO_MODEL_OPTION, ...fetched]);
        if (data.data.auto_prompt_style) setAutoPromptStyle(data.data.auto_prompt_style);
        setAutoModel(data.data.auto_model || null);
        setUnavailableModels(data.data.unavailable_models || []);
        setUserAdapters((data.data.adapters || []).filter((a) => a.is_downloaded));
      }
    } catch (e) {
      debugLog('Failed to load image models', e);
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

  // Quality presets — family-aware (see utils/batchImageSettings for the tables).
  const isFlux = family === 'flux';
  const isQwenImage21 = family === 'qwen-image-21';
  // Stills rendered by a ComfyUI graph: one picture at a time, 1K native, and a
  // guidance scale that is not SD-era CFG.
  const comfyStills = isFlux || isQwenImage21;
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
  const castLockedNote = 'Set by the selected character\u2019s base model while a character is picked.';
  // The worker renders one picture at a time on a CUDA card (one pipeline, one
  // card), so Max Workers only applies elsewhere; the chip shows the real count.
  const oneAtATime = generatorDevice === 'cuda' || comfyStills;
  const effectiveWorkers = oneAtATime ? 1 : params.max_workers;
  const oneAtATimeNote = comfyStills && generatorDevice !== 'cuda'
    ? `${isFlux ? 'FLUX' : 'Qwen-Image 2.1'} renders one picture at a time.`
    : 'One picture at a time on the graphics card.';

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
  const installSize = installNeed?.option.sizeGb ? ` (about ${installNeed.option.sizeGb} GB)` : '';
  const installButton = installNeed && (
    <Button
      color="inherit"
      size="small"
      startIcon={<CloudDownload />}
      onClick={() => openModels(installNeed.option.value)}
      sx={{ textTransform: 'none', whiteSpace: 'nowrap' }}
    >
      Install
    </Button>
  );

  // Dimension presets — base + model-family 2K / Flux~2MP packs (filtered below)
  const dimensionPresetsBase = [
    // SD 1.5 / Standard presets
    { label: 'Square (512x512)', width: 512, height: 512, pack: 'legacy' },
    { label: 'Portrait (512x768)', width: 512, height: 768, pack: 'legacy' },
    { label: 'Landscape (768x512)', width: 768, height: 512, pack: 'legacy' },
    { label: 'Large Square (768x768)', width: 768, height: 768, pack: 'legacy' },
    { label: 'HD Portrait (512x1024)', width: 512, height: 1024, pack: 'legacy' },
    { label: 'HD Landscape (1024x512)', width: 1024, height: 512, pack: 'legacy' },
    // 1K / SDXL-class (default daily driver for modern models)
    { label: '1K Square (1024x1024)', width: 1024, height: 1024, pack: '1k' },
    { label: '1K Portrait (832x1216)', width: 832, height: 1216, pack: '1k' },
    { label: '1K Landscape (1216x832)', width: 1216, height: 832, pack: '1k' },
    { label: '1K Wide 16:9 (1344x768)', width: 1344, height: 768, pack: '1k' },
    { label: '1K Tall 9:16 (768x1344)', width: 768, height: 1344, pack: '1k' },
    // Z-Image / Krea 2K pack (area ≤ ~2048²; long side up to 2688)
    { label: '2K Square (2048x2048)', width: 2048, height: 2048, pack: '2k' },
    { label: '2K Landscape 16:9 (2688x1472)', width: 2688, height: 1472, pack: '2k' },
    { label: '2K Ultrawide 21:9 (2688x1152)', width: 2688, height: 1152, pack: '2k' },
    { label: '2K Portrait 9:16 (1472x2688)', width: 1472, height: 2688, pack: '2k' },
    { label: '2K Landscape 3:2 (2496x1664)', width: 2496, height: 1664, pack: '2k' },
    { label: '2K Portrait 2:3 (1664x2496)', width: 1664, height: 2496, pack: '2k' },
    // FLUX.1-dev max (~2.0 MP design range — NOT 2048²)
    { label: 'Flux max square (1408x1408)', width: 1408, height: 1408, pack: 'flux2mp' },
    { label: 'Flux max 16:9 (1920x1088)', width: 1920, height: 1088, pack: 'flux2mp' },
    { label: 'Flux max 9:16 (1088x1920)', width: 1088, height: 1920, pack: 'flux2mp' },
    { label: 'Flux max 3:2 (1728x1152)', width: 1728, height: 1152, pack: 'flux2mp' },
    { label: 'Flux max 2:3 (1152x1728)', width: 1152, height: 1728, pack: 'flux2mp' },
    // Qwen-Image 2.1 2K pack: the official ComfyUI template notes native 2K
    // (2048x2048) output and sides in multiples of 32; no side above 2048.
    { label: 'Qwen 2K Square (2048x2048)', width: 2048, height: 2048, pack: 'qwen2k' },
    { label: 'Qwen 2K 16:9 (2048x1152)', width: 2048, height: 1152, pack: 'qwen2k' },
    { label: 'Qwen 2K 9:16 (1152x2048)', width: 1152, height: 2048, pack: 'qwen2k' },
    { label: 'Qwen 2K 3:2 (2048x1376)', width: 2048, height: 1376, pack: 'qwen2k' },
    { label: 'Qwen 2K 2:3 (1376x2048)', width: 1376, height: 2048, pack: 'qwen2k' },
  ];

  const dimensionPresetsForModel = (modelValue) => {
    const fam = modelFamily(modelValue);
    const flux = fam === 'flux';
    const qwen = fam === 'qwen-image-21';
    const modernDit = fam === 'auto' || fam === 'zimage' || fam.startsWith('krea');
    const legacySd = fam === 'sd';
    return dimensionPresetsBase.filter((p) => {
      // Draft 512/768 sizes available for Turbo/Krea as well as classic SD / auto
      if (p.pack === 'legacy') return legacySd || modernDit;
      if (p.pack === '1k') return !legacySd;
      if (p.pack === '2k') return modernDit && !flux;
      if (p.pack === 'flux2mp') return flux;
      if (p.pack === 'qwen2k') return qwen;
      return true;
    });
  };

  const dimensionPresets = useMemo(
    () => dimensionPresetsForModel(params.model),
    // params.model only — forModel is pure over modelValue
    [params.model],
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
      setError('Enter a high-level concept or look & feel first');
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
        setSuccess(`Your idea became ${lines.length} shots. Read them over, edit any line, then Start.`);
      } else {
        setError('The chat model gave no shots for this idea. Try a fuller description, or check that a chat model is running under Settings.');
      }
    } catch (e) {
      setError(
        'Could not turn the idea into shots. Check that a chat model is installed and running '
        + `under Settings, then try again. (${e.message || e})`,
      );
    } finally {
      setIsExpanding(false);
    }
  };

  const checkServiceStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status`);

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        setError(`Service status check failed: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Service status response is not JSON');
        return;
      }

      const data = await response.json();

      if (!data.success) {
        setError('Batch image generation service is not available');
      }
      const device = data.data?.image_generator?.device;
      if (typeof device === 'string') setGeneratorDevice(device);
    } catch (err) {
      setError('Failed to check service status');
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
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        console.error('Response is not JSON:', contentType);
        return;
      }

      const data = await response.json();

      if (data.success) {
        let storedCleared = [];
        try {
          storedCleared = JSON.parse(localStorage.getItem('clearedImageBatchIds') || '[]');
        } catch (e) {
          storedCleared = [];
        }
        const rawBatches = data.data.batches || [];
        const filtered = rawBatches.filter((b) => !storedCleared.includes(b.batch_id));
        setBatchHistory(filtered);
      }
    } catch (err) {
      console.error('Failed to load batch history:', err);
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

  const fetchQueue = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/queue`);
      if (!response.ok) return;
      const data = await response.json();
      const rows = data?.data?.queue ?? data?.queue ?? [];
      // Frontend-only dismissal: hide terminal rows the user cleared. Only
      // terminal statuses are filtered so a live batch can never be masked.
      let clearedQueue = [];
      try {
        clearedQueue = JSON.parse(localStorage.getItem('clearedImageQueueIds') || '[]');
      } catch (e) {
        clearedQueue = [];
      }
      const visible = (Array.isArray(rows) ? rows : []).filter(
        (q) => !(TERMINAL_BATCH_STATUSES.has(q.status) && clearedQueue.includes(q.batch_id))
      );
      galleryRefresherRef.current.observeQueue(visible);
      // This polls every 2.5s. Replacing `queue` with a fresh array each tick re-renders
      // the whole page — including every batch thumbnail card — even when nothing moved,
      // so only commit when the queue actually changed.
      const signature = visible
        .map((q) => `${q.batch_id}:${q.status}:${q.completed_images ?? ''}/${q.total_images ?? ''}`)
        .join('|');
      if (signature === queueSignatureRef.current) return;
      queueSignatureRef.current = signature;
      setQueue(visible);
    } catch (err) {
      console.debug('Failed to load image batch queue:', err);
    }
  }, []);

  const handleClearCompletedQueue = useCallback(() => {
    const doneIds = queue.filter((q) => TERMINAL_BATCH_STATUSES.has(q.status)).map((q) => q.batch_id);
    if (doneIds.length === 0) return;
    // Drop the memoised signature so the next poll re-commits against the new baseline.
    queueSignatureRef.current = '';
    try {
      const stored = JSON.parse(localStorage.getItem('clearedImageQueueIds') || '[]');
      localStorage.setItem('clearedImageQueueIds', JSON.stringify(Array.from(new Set([...stored, ...doneIds]))));
    } catch (e) {
      console.error('Failed to save cleared queue items to localStorage:', e);
    }
    setQueue((prev) => prev.filter((q) => !TERMINAL_BATCH_STATUSES.has(q.status)));
  }, [queue]);

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
    setSuccess('Cleared batch history list.');
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
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return dStr;
    }
  }, []);

  // Load service status, history and queue on mount
  useEffect(() => {
    checkServiceStatus();
    loadBatchHistory();
    fetchQueue();
  }, [fetchQueue, loadBatchHistory]);

  // Refresh the queue panel. `activeBatch` is never cleared once a batch finishes, so
  // this used to hammer the server every 2.5s for the life of the page. Keep the same
  // reach — so a batch queued from elsewhere still shows up — but drop to a slow idle
  // cadence once nothing is running. Depend on scalars rather than `queue` itself so the
  // interval isn't torn down and rebuilt on every tick.
  const queueHasActive = queue.some((q) => POLLABLE_STATUSES.has(q.status));
  const activeBatchRunning = !!activeBatch && POLLABLE_STATUSES.has(activeBatch.status);
  const anythingRunning = queueHasActive || activeBatchRunning;
  const shouldPoll = anythingRunning || !!activeBatch || queue.length > 0;
  const pollIntervalMs = anythingRunning ? 2500 : 15000;
  useEffect(() => {
    if (!shouldPoll) return undefined;
    const id = setInterval(() => { fetchQueue(); }, pollIntervalMs);
    return () => clearInterval(id);
  }, [shouldPoll, pollIntervalMs, fetchQueue]);

  const loadBatchById = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

      if (!response.ok) {
        setError(`Failed to load batch: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Batch response is not JSON');
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
      setError(`Failed to load batch: ${err.message}`);
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
            setError('Batch not found');
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
            setSuccess(`Batch generation ${batchStatus.status}`);
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
    const batchProcesses = Array.from(activeProcesses.values()).filter(
      process => (process.processType === 'image_generation' || process.process_type === 'image_generation') &&
        process.additional_data?.batch_id
    );

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
  }, [activeProcesses, activeBatch?.batch_id, loadBatchById]);

  // Every image batch's progress events, not only the open batch's: a finish
  // refreshes Recent Batches and the library, and a batch the queue does not list
  // yet (started from chat or MCP while this page idled) gets the queue re-read.
  const finishedBatchesRef = useRef(new Set());
  const queueReadForRef = useRef(new Set());
  useEffect(() => {
    const refresher = galleryRefresherRef.current;
    const { finished, unknown } = imageProcessEvents(
      activeProcesses.values(), finishedBatchesRef.current, refresher.knownBatchIds(),
    );
    finished.forEach((id) => finishedBatchesRef.current.add(id));
    const unread = unknown.filter((id) => !queueReadForRef.current.has(id));
    unread.forEach((id) => queueReadForRef.current.add(id));
    if (finished.length) refresher.schedule({ library: true });
    if (finished.length || unread.length) fetchQueue();
  }, [activeProcesses, fetchQueue]);

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

  const handleBatchItemsChange = (event) => {
    setBatchItems(event.target.value);
  };

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
      if (fam === 'flux' || fam === 'qwen-image-21') {
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
      setError('Please select a valid CSV file');
      setCsvFile(null);
    }
  };

  const handleBlueprintFileUpload = (event) => {
    const file = event.target.files[0];
    if (isCsvFile(file)) {
      setBlueprintFile(file);
      setError('');
    } else {
      setError('Please select a valid CSV file for blueprints');
      setBlueprintFile(null);
    }
  };

  const downloadTemplate = async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/template`);

      if (!response.ok) {
        setError(`Failed to download template: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('text/csv') && !contentType.includes('application/octet-stream')) {
        setError('Template response is not a valid CSV file');
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
      setError('Failed to download template: ' + err.message);
    }
  };

  const startGeneration = async () => {
    setLoading(true);
    setError('');
    setGeneratedImages([]);
    setSuccess('');

    try {
      let response;

      if (inputMode === 'blueprint') {
        if (!blueprintFile) {
          setError('Please select a CSV file for blueprint generation');
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
        setError('Please select a CSV file for upload');
        setLoading(false);
        return;
      } else {
        // Single (whole text = one prompt) or bulk (one per line). The same
        // builder feeds the Preview dialog, so what is shown is what is sent.
        // No " (i+1)" numbering: the backend keys images by position
        // (prompt_id=prompt_N) and the text encoder read the counter as content.
        const promptsToGenerate = finalPrompts;
        if (promptsToGenerate.length === 0) {
          setError(inputMode === 'single' ? 'Please provide a prompt' : 'Please provide at least one prompt or topic');
          setLoading(false);
          return;
        }

        debugLog('Batch image prompts prepared', { promptCount: promptsToGenerate.length, quantity });

        const uiConfig = {
          inputMode,
          batchItems,
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
        };

        response = await fetch(`${API_BASE}/batch-image/generate/prompts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompts: promptsToGenerate,
            ...params,
            // Cast characters: backend resolves these to LoRA paths + trigger.
            subject_ids: castSubjectIds,
            // User LoRAs switched on below the model select.
            adapters: selectedAdapters
              .filter((id) => applicableAdapters.some((a) => a.id === id))
              .map((id) => ({ id })),
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
        let errorMessage = `Failed to start generation: HTTP ${response.status} ${response.statusText}`;
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
        setError(`Generation response is not JSON. Response: ${text.substring(0, 200)}`);
        setLoading(false);
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchId = data.data?.batch_id;
        if (!batchId) {
          setError('Batch generation started but no batch ID returned');
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
            ? (data.data?.message || 'Blueprint batch queued. Results appear as generation completes.')
            : 'Batch queued. Batches render one after another; you can queue more while this one runs.',
        );
        // Free the form so the next job can be composed immediately (like Video Gen).
        if (inputMode === 'single' || inputMode === 'bulk') {
          setBatchItems('');
        }
        fetchQueue();
        loadBatchHistory();
      } else {
        setError(errorMessageFrom(data, 'Failed to start generation'));
      }
    } catch (err) {
      console.error('Generation error:', err);
      setError('Failed to start generation: ' + (err.message || String(err)));
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
        setError(errorMessageFrom(data, `Failed to cancel generation: HTTP ${response.status}`));
        return false;
      }
      return true;
    } catch (err) {
      setError('Failed to cancel generation: ' + err.message);
      return false;
    }
  }, []);

  const cancelGeneration = async () => {
    if (!activeBatch) return;
    const ok = await requestCancel(activeBatch.batch_id);
    if (!ok) return;
    setSuccess('Batch generation cancelled');
    stopPolling();
    setActiveBatch(prev => prev ? { ...prev, status: 'cancelled' } : null);
    fetchQueue();
  };

  // Stable identity: this is handed to every batch history card, which is memoised.
  // Only state setters and module constants are referenced, so [] deps are sound.
  const handleAdjustRetry = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);
      if (!res.ok) { setError(`Couldn't load settings: HTTP ${res.status}`); return; }
      const data = await res.json();
      const statusObj = data?.data || data;
      const rd = statusObj?.retry_data;
      const name = statusObj?.display_name || batchId.slice(0, 8);

      if (!rd) { setError("This batch didn't store its settings."); return; }

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
        setSuccess(`Loaded "${name}" settings into control panel — adjust anything, then Start Generation.`);
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
            ? `Loaded "${name}" prompts into panel.`
            : `Loaded "${name}": ${uniquePrompts.length} prompt${uniquePrompts.length === 1 ? '' : 's'} x qty ${repeats[0]}.`
        );
      }
      setError('');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      setError(`Couldn't load settings: ${e.message}`);
    }
  }, []);

  const downloadResults = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/download/${batchId}`);

      if (!response.ok) {
        setError(`Failed to download results: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('application/zip') && !contentType.includes('application/octet-stream')) {
        setError('Download response is not a valid ZIP file');
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
      setError('Failed to download results: ' + err.message);
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
      caption: img.rewrittenPrompt ? `Sent to the model: ${img.rewrittenPrompt}` : '',
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
        setError(`Failed to load batch: HTTP ${response.status}`);
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
      setError(`Failed to load batch: ${err.message}`);
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
      title={embedded ? undefined : "Image Generator"}
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
            Clear Batch List
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
                Generation Settings
              </Typography>

              {/* Content Preset Selector. Only tag-style models take it; a prose
                  model gets a one-line note instead of a control that does nothing. */}
              {prosePrompts ? (
                <Box sx={{ mb: 3 }}>
                  <Hint>
                    {family === 'auto'
                      ? (autoModelLabel
                        ? `Content Type is not used: Auto renders with ${autoModelLabel}, which reads the prompt as plain sentences.`
                        : 'Content Type is not used: Auto picks a model that reads the prompt as plain sentences.')
                      : `Content Type is not used by ${modelLabel}: it reads the prompt as plain sentences.`}
                    {' '}Describe the scene in your own words. The content types only shape prompts for tag-style models.
                  </Hint>
                </Box>
              ) : (
              <Box sx={{ mb: 3, p: 2, bgcolor: 'primary.50', borderRadius: 2, border: '1px solid', borderColor: 'primary.200' }}>
                <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                  Content Type (for better quality)
                </Typography>
                <FormControl fullWidth size="small">
                  <Select
                    value={selectedPreset}
                    onChange={(e) => handlePresetChange(e.target.value)}
                    sx={{ bgcolor: 'background.paper' }}
                  >
                    <MenuItem value="auto">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Auto-detect (Recommended)</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Stuffs quality tags into the prompt from its content. Does not detect or change generation settings.
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_portrait">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Portrait</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Headshots, face close-ups, profile photos
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_full_body">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Full Body</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Standing, sitting, or posed full-body shots
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_working">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Working/Action</Typography>
                        <Typography variant="caption" color="text.secondary">
                          People doing activities, using tools, interacting with objects
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="product_photo">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Product Photo</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Clean product shots, commercial photography
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="landscape">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Landscape/Scenery</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Nature, cityscapes, outdoor scenes
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="infographic_preset">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Infographic/Diagram</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Flat design, icons, vector graphics, charts
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="general">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>General Purpose</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Default settings for any content
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
                  Input Method
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
                    Single Prompt
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
                    Bulk Input
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
                    CSV Upload
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
                    Offline Blueprints
                  </Button>
                </Box>
              </Box>

              {/* Cast (optional) - for single prompt and bulk */}
              {(inputMode === 'single' || inputMode === 'bulk') && (
                <Box sx={{ mb: 2 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    Cast (optional)
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    Pick a trained character — identity (trigger + class + vision marks) and LoRA are applied automatically.
                    Write scene/action only; do not re-describe the face or costume.
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
                    Single Prompt
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                    Paste full prompt text (paragraphs and line breaks preserved as one prompt).
                    Use "Number of Images per Prompt" below for multiples from this exact prompt.
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder="Paste your complete prompt here, including paragraphs if needed. The entire text will be used as a single prompt for image generation.&#10;&#10;Example: A detailed scene description with multiple sentences and line breaks all for one image concept..."
                    value={batchItems}
                    onChange={handleBatchItemsChange}
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
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Whole text = 1 prompt. Quantity duplicates it exactly for multiple images.
                  </Typography>
                </Box>
              )}

              {/* Bulk Input */}
              {inputMode === 'bulk' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    Image Topics/Prompts
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder="Enter image topics or prompts, one per line:&#10;&#10;A majestic mountain landscape at sunset&#10;A cat sitting on a windowsill&#10;Abstract geometric patterns in blue&#10;Portrait of a wise old wizard&#10;A futuristic city skyline&#10;..."
                    value={batchItems}
                    onChange={handleBatchItemsChange}
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
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      {uniquePromptCount} prompts ready for generation (bulk mode)
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
                      Load Examples
                    </Button>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Tip: Each line becomes a separate image prompt
                  </Typography>
                </Box>
              )}

              {/* Look & Feel (shared for single + bulk) */}
              {(inputMode === 'single' || inputMode === 'bulk') && (
                <Box sx={{ mt: 3 }}>
                  <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                    Look & Feel (Optional)
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={3}
                    placeholder="Describe the visual style to apply to the prompt(s):&#10;&#10;Examples:&#10;• In shades of blue, no text, darker colors&#10;• Minimalist black and white, clean lines&#10;• Professional infographic style, flat design&#10;• Photorealistic, dramatic lighting"
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
                      {inputMode === 'single' ? 'Style will be appended to your single prompt' : `This style will be applied to all ${uniquePromptCount} prompts above`}
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
                      Preview Prompts
                    </Button>
                  </Box>
                  <TextField
                    fullWidth
                    multiline
                    minRows={2}
                    maxRows={4}
                    label="Negative prompt"
                    placeholder="blurry, distorted hands, extra fingers"
                    value={negativePrompt}
                    disabled={unusedNegAnat}
                    onChange={(e) => setNegativePrompt(e.target.value)}
                    helperText={
                      unusedNegAnat
                        ? 'This model does not use a negative prompt.'
                        : undefined
                    }
                    sx={{ mt: 2 }}
                  />
                  <Box sx={{ mt: 1.5, display: 'flex', flexDirection: 'column', gap: 0.75 }}>
                    {/* The backend adds the anatomy phrasing only on the tag path
                        (offline_image_generator.enhance_prompt_for_quality); a prose
                        model, and Auto while it resolves to one, skips it. */}
                    <SettingChip
                      label="Enhance anatomy"
                      on={enhanceAnatomy}
                      disabled={prosePrompts}
                      tooltip={
                        prosePrompts
                          ? 'Not used here: this model reads the prompt as plain sentences, so anatomy tags would be drawn as scene content. Describe the pose in words instead.'
                          : 'Adds anatomy phrasing when the prompt is a person.'
                      }
                      onToggle={setEnhanceAnatomy}
                    />
                    {(prosePrompts || unusedNegAnat) && (
                      <Hint>
                        {prosePrompts && unusedNegAnat
                          ? 'This model does not use a negative prompt or anatomy enhancement.'
                          : prosePrompts
                            ? 'This model does not use anatomy enhancement.'
                            : 'This model does not use a negative prompt.'}
                      </Hint>
                    )}
                  </Box>
                </Box>
              )}

              {/* CSV Upload */}
              {inputMode === 'csv' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    CSV File Upload
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
                    Upload CSV File
                  </Button>

                  {csvFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      File selected: {csvFile.name}
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
                    Download CSV Template
                  </Button>
                </Box>
              )}

              {/* Offline Blueprint Upload */}
              {inputMode === 'blueprint' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    Offline Blueprint CSV Upload
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Upload a CSV file with 'city' and 'count' (or 'patents') columns to generate data blueprints (CPU-only). Large batches run in the background; poll for status until complete.
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
                    Upload Blueprint CSV
                  </Button>

                  {blueprintFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      File selected: {blueprintFile.name}
                    </Alert>
                  )}

                  <Typography variant="caption" color="text.secondary">
                    Accepted format: .csv (columns like city/name and count/patents/value)
                  </Typography>
                </Box>
              )}

              {inputMode !== 'blueprint' && (
                <>

                  {/* Current Settings Display */}
                  <Box sx={{ mt: 3, p: 2, backgroundColor: 'background.paper', borderRadius: 1, border: '1px solid', borderColor: 'divider' }}>
                    <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 1, mb: 1 }}>
                      <Typography variant="subtitle2" sx={{ fontWeight: 500 }}>Current Settings</Typography>
                      <ActionButton onClick={() => openModels()}>Manage models</ActionButton>
                    </Box>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                      <Chip
                        label={`Model: ${modelLabel}${installBlocksStart ? ' (not installed)' : ''}`}
                        size="small"
                        variant="outlined"
                        color={installBlocksStart ? 'warning' : 'default'}
                      />
                      <Chip
                        label={`Style: ${params.style}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Quality: ${params.quality_preset}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Size: ${params.width}x${params.height}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={castLocked ? 'Steps: from character' : `Steps: ${params.steps}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={oneAtATime ? 'One at a time' : `Workers: ${effectiveWorkers}`}
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
                      <Typography sx={{ fontWeight: 500 }}>Advanced Settings</Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ pt: 2 }}>
                      <Grid container spacing={2}>
                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Model</InputLabel>
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
                                ? `${installName} is not installed. Install downloads it${installSize}; Start stays off until it finishes.`
                                : `No image model is installed yet. Install ${installName}${installSize} for Auto to use.`}
                            </Alert>
                          )}
                          {applicableAdapters.length > 0 && (
                            <Box sx={{ mt: 1 }}>
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                                Your LoRAs (off until you turn one on):
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
                          {unavailableModels.length > 0 && (
                            <Box sx={{ mt: 1 }}>
                              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
                                Not available on this machine:
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
                            <InputLabel>Quality Preset</InputLabel>
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
                                <Typography variant="body2">Enhance prompts automatically</Typography>
                                <Typography variant="caption" color="text.secondary">
                                  {!autoEnhance
                                    ? 'Off — prompts are sent as written.'
                                    : prosePrompts
                                      ? ('Rewrites each prompt into a fuller description with the chat model before rendering. '
                                        + (params.style === 'realistic'
                                          ? 'If no chat model answers, the prompt goes as typed. '
                                          : `If no chat model answers, the prompt goes as typed plus one sentence asking for the ${params.style} look. `)
                                        + 'Steps and guidance are not changed.')
                                      : unusedNegAnat
                                        ? 'Adds the content type\u2019s quality and anatomy phrasing to each prompt. This model ignores negative prompts. Steps and guidance are not changed.'
                                        : 'Adds the content type\u2019s quality and anatomy phrasing (and negatives) to each prompt. Steps and guidance are not changed.'}
                                </Typography>
                              </Box>
                            }
                          />
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Style</InputLabel>
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
                            label="Number of Images per Prompt"
                            type="number"
                            value={quantity}
                            onChange={(e) => setQuantity(clampQuantity(e.target.value))}
                            inputProps={{ min: 1, max: MAX_QUANTITY }}
                          />
                          {/* /batch-image/expand-concept: the chat model turns the idea above
                              into one prompt per shot and fills the prompt box with them. */}
                          <Tooltip
                            arrow
                            title="Turns the idea above into several different shots, one prompt each, written by the chat model. They replace the text above so you can read and edit them before you start."
                          >
                            <span>
                              <Button
                                variant="outlined"
                                size="small"
                                onClick={handleDirectorExpand}
                                disabled={isExpanding || !(batchItems || lookAndFeel).trim()}
                              >
                                {isExpanding ? 'Writing shots…' : 'Expand into shots'}
                              </Button>
                            </span>
                          </Tooltip>
                          {/* director_mode: stills_policy picks the chat-model rewrite for every
                              prompt, whatever the model's default enhancement would be. */}
                          <Tooltip
                            arrow
                            title="Has the chat model rewrite every prompt into a fuller description before rendering, instead of the quick tag phrasing. Prose models get this rewrite anyway when Enhance prompts is on."
                          >
                            <FormControlLabel
                              control={<Switch size="small" checked={directorEnabled} onChange={(e) => setDirectorEnabled(e.target.checked)} />}
                              label="Rewrite prompts with the chat model"
                            />
                          </Tooltip>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Dimensions</InputLabel>
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
                                  Custom ({params.width}x{params.height})
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
                                High-res (&gt;1MP): more VRAM; may OOM on 16GB cards.
                              </Typography>
                            )}
                          </FormControl>
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            Steps: {castLocked ? 'set by the character' : params.steps}
                            {isFlux && !castLocked ? ' (FLUX quality ↑ with more steps)' : ''}
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
                              ? 'Guidance: set by the character'
                              : isZimage
                              ? 'Guidance Scale: 0 (CFG-free model)'
                              : isFlux
                                ? `FluxGuidance: ${params.guidance}`
                                : `Guidance Scale: ${params.guidance}`}
                          </Typography>
                          {/* Z-Image is CFG-free: there is NO valid non-zero guidance. Any other value
                              produces washed-out, over-cooked output, so the control is disabled rather
                              than left free to drift off the only correct setting. `auto` is left
                              editable: the router may pick SDXL, which does use CFG. */}
                          <Slider
                            value={isZimage ? ZIMAGE_GUIDANCE : params.guidance}
                            onChange={(e, value) => setParams({ ...params, guidance: value })}
                            min={0}
                            max={isFlux ? 6 : isQwenImage21 ? QWEN_IMAGE_21_GUIDANCE_MAX : 20}
                            step={0.5}
                            marks
                            disabled={isZimage || castLocked}
                          />
                          {castLocked && (
                            <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                              {castLockedNote}
                            </Typography>
                          )}
                          {isZimage && !castLocked && (
                            <Typography variant="caption" color="text.secondary">
                              Z-Image runs CFG-free — guidance is fixed at 0. Use Steps for quality.
                            </Typography>
                          )}
                          {isQwenImage21 && !castLocked && (
                            <Typography variant="caption" color="text.secondary">
                              Qwen-Image 2.1 runs at guidance 1 on the official path. Raise it only together with a negative prompt.
                            </Typography>
                          )}
                          {family === 'auto' && (
                            <Typography variant="caption" color="text.secondary">
                              Auto routes per prompt. The usual pick (Z-Image) is CFG-free — keep 0 unless you expect an SDXL route.
                            </Typography>
                          )}
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>
                            Max Workers: {effectiveWorkers}
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
                            label="Generate Thumbnails"
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
                        ? `Start is off: ${installName} is not installed.`
                        : 'No image model is installed yet.'}
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
                      ? 'Queuing…'
                      : (queue.some((q) => POLLABLE_STATUSES.has(q.status))
                        ? 'Add to Queue'
                        : 'Start Generation')}
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
                      Cancel
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
          {queue.length > 0 && (
            <Card sx={{ mb: 3, boxShadow: 2, borderRadius: 2 }}>
              <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
                  <Typography variant="h6" sx={{ fontWeight: 600 }}>
                    Batch Queue
                  </Typography>
                  <Stack direction="row" spacing={1} alignItems="center">
                    {queue.some((q) => TERMINAL_BATCH_STATUSES.has(q.status)) && (
                      <Button
                        size="small"
                        variant="outlined"
                        color="inherit"
                        onClick={handleClearCompletedQueue}
                        sx={{ textTransform: 'none', borderRadius: 1 }}
                      >
                        Clear Completed
                      </Button>
                    )}
                    <Chip
                      label={`${queue.filter((q) => POLLABLE_STATUSES.has(q.status)).length} active`}
                      size="small"
                      color="primary"
                      variant="outlined"
                    />
                  </Stack>
                </Stack>
                <Stack spacing={1}>
                  {queue.map((q, idx) => {
                    const slotTag = `#${idx + 1}`;
                    const total = q.total_images || 0;
                    const done = (q.completed_images || 0) + (q.failed_images || 0);
                    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
                    const chipColor =
                      q.status === 'running' ? 'primary'
                        : q.status === 'queued' || q.status === 'pending' ? 'default'
                          : q.status === 'completed' ? 'success'
                            : q.status === 'cancelled' ? 'warning'
                              : q.status === 'error' ? 'error' : 'default';
                    const cancellable = POLLABLE_STATUSES.has(q.status);
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
                            <Typography variant="body2" noWrap title={q.display_name || q.batch_id}>
                              {q.display_name || q.batch_id}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {done}/{total} images
                              {q.failed_images > 0 ? ` (${q.failed_images} failed)` : ''}
                            </Typography>
                          </Box>
                          <Chip label={(q.status || '').toUpperCase()} size="small" color={chipColor} />
                          {cancellable && (
                            <Tooltip
                              title={q.status === 'running' ? 'Cancel running batch' : 'Remove from queue'}
                              arrow
                            >
                              <IconButton
                                size="small"
                                onClick={async () => {
                                  const ok = await requestCancel(q.batch_id);
                                  fetchQueue();
                                  if (ok && activeBatch?.batch_id === q.batch_id) {
                                    setActiveBatch((prev) => (prev ? { ...prev, status: 'cancelled' } : prev));
                                  }
                                }}
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
                      </Box>
                    );
                  })}
                </Stack>
              </CardContent>
            </Card>
          )}

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
                    Current Progress
                  </Typography>

                  <Box sx={{ mb: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      Batch ID: {activeBatch.batch_id}
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
                        Progress: {activeBatch.completed_images || 0}/{activeBatch.total_images || 0}
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
                        Download Results
                      </Button>
                    )}
                    {['completed', 'error', 'cancelled'].includes(activeBatch.status) && (
                      <Button
                        startIcon={<SettingsIcon />}
                        onClick={() => handleAdjustRetry(activeBatch.batch_id)}
                        variant="outlined"
                        size="small"
                      >
                        Adjust &amp; Retry
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
                    Generated Images ({generatedImages.length || (activeBatch?.results?.filter(r => r.success && r.image_path).length || 0)})
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
                            alt={image.prompt || 'Generated image'}
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
                            title={image.prompt ? image.prompt.substring(0, 30) + '...' : 'No prompt'}
                            subtitle={image.rewrittenPrompt ? 'Rewritten before rendering; open to read it' : undefined}
                            actionIcon={
                              <IconButton
                                sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                                onClick={() => openImageViewer(image)}
                                aria-label={`View full image: ${image.prompt || 'Generated image'}`}
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
                  Recent Batches
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
                      Clear Batch List
                    </Button>
                  )}
                  <IconButton size="small" onClick={loadBatchHistory} title="Refresh batches">
                    <RefreshIcon />
                  </IconButton>
                </Stack>
              </Stack>

              <Box sx={{ maxHeight: 520, overflowY: 'auto', pr: 0.5 }}>
                <Grid container spacing={2}>
                  {liveBatchHistory.map((batch) => (
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

                {batchHistory.length === 0 && (
                  <Box sx={{ textAlign: 'center', py: 4 }}>
                    <ImageIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
                    <Typography variant="body2" color="text.secondary">
                      No image batches found
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
          caption={lightboxImage.caption}
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
          Preview Generated Prompts ({uniquePromptCount}{quantity > 1 ? ` × ${quantity}` : ''})
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            These are the final prompts that will be sent to the image generator:
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
          <Button onClick={() => setShowPromptPreview(false)}>Close</Button>
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