// frontend/src/components/images/InfographicGenerator.jsx
// Infographic generator. Two modes:
//   - Structured: guided fields (Title / Scene / Footer / Hashtags / Style / Aspect)
//   - Freeform:   single textarea sent to the model as written
//
// Generate queues a batch (/api/infographic/batch) on the image batch queue
// and returns at once, so the next one can be composed while it renders. The
// form stays filled: changing one word and sending it again is the usual loop.
// Results, Recent Infographics and Adjust & Retry read the batch back through
// /api/batch-image. The page also opens pre-filled from the Media page:
//   ?retry=<batch id>                  the batch's form as it was sent
//   ?retry=<batch id>&doc=<document>   that one image: its spec and seed
//   ?doc=<document>                    a single infographic made before batches

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Box,
  Paper,
  Typography,
  TextField,
  Button,
  MenuItem,
  Alert,
  CircularProgress,
  LinearProgress,
  Stack,
  ToggleButtonGroup,
  ToggleButton,
  Chip,
  Grid,
} from '@mui/material';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import {
  getInfographicStatus,
  queueInfographicBatch,
  INFOGRAPHIC_STYLES,
  INFOGRAPHIC_ASPECTS,
} from '../../api/infographicService';
import BatchQueuePanel from './BatchQueuePanel';
import BatchHistoryCard from './BatchHistoryCard';
import ImageLightbox from './ImageLightbox';
import useImageBatchQueue, { POLLABLE_STATUSES } from '../../hooks/useImageBatchQueue';
import { IMAGE_MAX_SEED, clampQuantity, parseImageSeed } from '../../utils/batchImageSettings';
import { uiLocale, useUiTranslation, Trans } from '../../i18n';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';
// Recent Infographics a person hid stay hidden in this browser.
const HIDDEN_KEY = 'clearedInfographicBatchIds';
const RESULTS_POLL_MS = 2500;

const ASPECT_RATIOS = { '16:9': 16 / 9, '1:1': 1, '9:16': 9 / 16, '4:5': 4 / 5, '3:2': 3 / 2 };

/** The offered aspect closest to a picture's size (for records that only kept the size). */
const aspectFromSize = (width, height) => {
  if (!width || !height) return '16:9';
  const ratio = width / height;
  return Object.entries(ASPECT_RATIOS)
    .reduce((best, [key, r]) => (Math.abs(r - ratio) < Math.abs(ASPECT_RATIOS[best] - ratio) ? key : best), '16:9');
};

const fileName = (path) => (path || '').replace(/\\/g, '/').split('/').pop();

const imageUrl = (batchId, path, thumbnail = false) =>
  `${API_BASE}/batch-image/image/${batchId}/${encodeURIComponent(fileName(path))}${thumbnail ? '?thumbnail=true' : ''}`;

const resultsOf = (batch) =>
  (batch?.results || [])
    .filter((r) => r.success && r.image_path)
    .map((r) => ({ id: r.prompt_id, path: r.image_path, seed: r.metadata?.seed_used, metadata: r.metadata || {} }));

const readHidden = () => {
  try {
    return JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]');
  } catch (e) {
    return [];
  }
};

/** Save a same-origin file without leaving the page. */
const saveFile = (url, name) => {
  const a = document.createElement('a');
  a.href = url;
  a.download = name || '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

const fetchJson = async (url) => {
  const res = await fetch(url);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.success === false) throw new Error(data.error || data.message || `HTTP ${res.status}`);
  return data.data ?? data;
};

const InfographicGenerator = () => {
  const { t } = useUiTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [mode, setMode] = useState('structured'); // structured | freeform

  // Structured fields
  const [title, setTitle] = useState('');
  const [scene, setScene] = useState('');
  const [footer, setFooter] = useState('');
  const [hashtags, setHashtags] = useState('');
  const [callouts, setCallouts] = useState('');
  const [style, setStyle] = useState('editorial');
  const [aspect, setAspect] = useState('16:9');
  const [model, setModel] = useState('');

  // Freeform
  const [rawPrompt, setRawPrompt] = useState('');

  // Batch
  const [quantity, setQuantity] = useState(1);
  const [seedText, setSeedText] = useState('');

  // Run state
  const [queuing, setQueuing] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [status, setStatus] = useState(null); // {ready, default_model, models:[...]}
  // The batch shown under Results: the last one queued here, or one opened from Recent.
  const [currentBatchId, setCurrentBatchId] = useState(null);
  const [currentBatch, setCurrentBatch] = useState(null);
  const [history, setHistory] = useState([]);
  const [lightboxIndex, setLightboxIndex] = useState(null);
  // A model the form was loaded with, kept until the model list has arrived.
  const pendingModelRef = useRef(null);
  const installedIdsRef = useRef([]);

  const refreshStatus = useCallback(async () => {
    try {
      const next = await getInfographicStatus();
      setStatus(next);
      // Keep a choice that is still installed; otherwise follow the default.
      setModel((prev) => {
        const installed = (next.models || []).filter((m) => m.installed).map((m) => m.id);
        const wanted = pendingModelRef.current || prev;
        pendingModelRef.current = null;
        return installed.includes(wanted) ? wanted : (next.default_model || installed[0] || '');
      });
    } catch (e) {
      setStatus({ ready: false, error: e.message });
    }
  }, []);

  const installedModels = (status?.models || []).filter((m) => m.installed);
  const chosenModel = installedModels.find((m) => m.id === model);
  installedIdsRef.current = installedModels.map((m) => m.id);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);

  const loadHistory = useCallback(async () => {
    try {
      const data = await fetchJson(`${API_BASE}/batch-image/list`);
      const hidden = readHidden();
      setHistory((data.batches || []).filter((b) => b.kind === 'infographic' && !hidden.includes(b.batch_id)));
    } catch (e) {
      // History is a convenience; the queue and results still work without it.
    }
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  const currentRunning = !!currentBatch && POLLABLE_STATUSES.has(currentBatch.status);
  const { queue, fetchQueue, clearCompleted } = useImageBatchQueue({ keepWatching: currentRunning });

  // Follow the batch under Results until it finishes, so images appear as they land.
  useEffect(() => {
    if (!currentBatchId) return undefined;
    let alive = true;
    let timer = null;
    const poll = async () => {
      try {
        const batch = await fetchJson(`${API_BASE}/batch-image/status/${currentBatchId}?include_results=true`);
        if (!alive) return;
        setCurrentBatch(batch);
        if (POLLABLE_STATUSES.has(batch.status)) {
          timer = setTimeout(poll, RESULTS_POLL_MS);
        } else {
          loadHistory();
          window.dispatchEvent(new Event('images-updated'));
        }
      } catch (e) {
        if (alive) timer = setTimeout(poll, RESULTS_POLL_MS * 2);
      }
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [currentBatchId, loadHistory]);

  const formConfig = () => ({
    mode, title, scene, footer, hashtags, callouts, rawPrompt, style, aspect, model, quantity, seedText,
  });

  // Only setters and refs, so these and the loaders below keep one identity.
  const applyModel = useCallback((id) => {
    if (!id) return;
    if (installedIdsRef.current.includes(id)) setModel(id);
    else pendingModelRef.current = id;
  }, []);

  /** Fill the form from a filed spec (InfographicSpec.record on the backend). */
  const applySpec = useCallback((spec = {}) => {
    if (spec.raw_prompt) {
      setMode('freeform');
      setRawPrompt(spec.raw_prompt);
    } else {
      setMode('structured');
      setTitle(spec.title || '');
      setScene(spec.scene || '');
      setFooter(spec.footer || '');
      setHashtags(Array.isArray(spec.hashtags) ? spec.hashtags.map((h) => `#${h}`).join(' ') : (spec.hashtags || ''));
      setCallouts(Array.isArray(spec.callouts) ? spec.callouts.join('\n') : (spec.callouts || ''));
    }
    if (spec.style) setStyle(spec.style);
    if (spec.aspect) setAspect(spec.aspect);
  }, []);

  // Adjust & Retry: the batch's form as it was sent, or one image of it
  // (its spec and seed, quantity 1) when `documentId` names one.
  const handleAdjustRetry = useCallback(async (batchId, documentId = null) => {
    setError('');
    try {
      const batch = await fetchJson(`${API_BASE}/batch-image/status/${batchId}`);
      const rd = batch.retry_data;
      if (!rd) {
        setError(t('images.infographic.retryNoSettings'));
        return;
      }
      const name = batch.display_name || batchId;
      if (documentId) {
        const doc = await fetchJson(`${API_BASE}/files/document/${documentId}`);
        const meta = doc.metadata || {};
        applySpec(meta.infographic || (rd.specs || [])[0]);
        applyModel(rd.params?.model);
        setQuantity(1);
        setSeedText(meta.seed_used != null ? String(meta.seed_used) : '');
        setNotice(t('images.infographic.retryLoadedImage', { name }));
      } else {
        // The filed spec first, then the form as it was typed on top of it,
        // so a snapshot missing a field still loads the spec's value.
        applySpec((rd.specs || [])[0]);
        applyModel(rd.params?.model);
        setQuantity(clampQuantity(rd.params?.quantity));
        setSeedText(rd.params?.seed != null ? String(rd.params.seed) : '');
        const cfg = rd.params?.ui_config;
        if (cfg && typeof cfg === 'object') {
          if (cfg.mode) setMode(cfg.mode === 'freeform' ? 'freeform' : 'structured');
          const fields = [
            ['title', setTitle], ['scene', setScene], ['footer', setFooter], ['hashtags', setHashtags],
            ['callouts', setCallouts], ['rawPrompt', setRawPrompt], ['seedText', setSeedText],
          ];
          fields.forEach(([key, set]) => { if (typeof cfg[key] === 'string') set(cfg[key]); });
          if (cfg.style) setStyle(cfg.style);
          if (cfg.aspect) setAspect(cfg.aspect);
          applyModel(cfg.model);
          if (cfg.quantity != null) setQuantity(clampQuantity(cfg.quantity));
        }
        setNotice(t('images.infographic.retryLoaded', { name }));
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (e) {
      setError(t('images.infographic.retryFailed', { error: e.message }));
    }
  }, [t, applySpec, applyModel]);

  // A single infographic from before batches: its record has the spec (or only
  // the prompt it was sent with), the model id, the seed and the size.
  const loadFromDocument = useCallback(async (documentId) => {
    setError('');
    try {
      const doc = await fetchJson(`${API_BASE}/files/document/${documentId}`);
      const meta = doc.metadata || {};
      if (meta.infographic) {
        applySpec(meta.infographic);
      } else {
        applySpec({ raw_prompt: meta.prompt || '', aspect: aspectFromSize(meta.width, meta.height) });
      }
      applyModel(meta.model);
      setQuantity(1);
      setSeedText(meta.seed != null ? String(meta.seed) : '');
      setNotice(t('images.infographic.retryLoaded', { name: doc.filename || documentId }));
    } catch (e) {
      setError(t('images.infographic.retryFailed', { error: e.message }));
    }
  }, [t, applySpec, applyModel]);

  // Opened from the Media page: load once, then drop the parameters so a
  // reload or a later visit does not load them again.
  useEffect(() => {
    const retry = searchParams.get('retry');
    const doc = searchParams.get('doc');
    if (!retry && !doc) return;
    if (retry) handleAdjustRetry(retry, doc);
    else loadFromDocument(doc);
    const next = new URLSearchParams(searchParams);
    next.delete('retry');
    next.delete('doc');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, handleAdjustRetry, loadFromDocument]);

  const canGenerate = (() => {
    if (queuing || !model) return false;
    if (mode === 'freeform') return rawPrompt.trim().length > 0;
    return scene.trim().length > 0;
  })();

  const handleGenerate = async () => {
    setError('');
    setNotice('');
    setQueuing(true);
    try {
      const fields = mode === 'freeform'
        ? { raw_prompt: rawPrompt, style, aspect, model }
        : { title, scene, footer, hashtags, callouts, style, aspect, model };
      const seed = parseImageSeed(seedText);
      const data = await queueInfographicBatch({
        ...fields,
        quantity,
        ...(seed != null ? { seed } : {}),
        ui_config: formConfig(),
      });
      setNotice(t('images.infographic.queued', { count: data.total_images }));
      setCurrentBatch(null);
      setCurrentBatchId(data.batch_id);
      fetchQueue();
    } catch (e) {
      setError(e.message || t('images.infographic.failed'));
    } finally {
      setQueuing(false);
    }
  };

  const handleCancel = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${API_BASE}/batch-image/cancel/${batchId}`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.success) setError(data.error || data.message || `HTTP ${res.status}`);
    } catch (e) {
      setError(e.message);
    }
    fetchQueue();
  }, [fetchQueue]);

  const openBatch = useCallback((batch) => {
    setCurrentBatch(null);
    setCurrentBatchId(batch.batch_id);
  }, []);

  const loadBatch = useCallback((batchId) => {
    setCurrentBatch(null);
    setCurrentBatchId(batchId);
  }, []);

  const hideBatch = useCallback((batchId) => {
    try {
      localStorage.setItem(HIDDEN_KEY, JSON.stringify(Array.from(new Set([...readHidden(), batchId]))));
    } catch (e) {
      // Storage refused: it still leaves this view.
    }
    setHistory((prev) => prev.filter((b) => b.batch_id !== batchId));
  }, []);

  const downloadBatch = useCallback((batchId) => {
    saveFile(`${API_BASE}/batch-image/download/${batchId}`, `batch_${batchId}_results.zip`);
  }, []);

  const formatDate = useCallback((value) => {
    if (!value) return '';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return `${d.toLocaleDateString(uiLocale(), { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit' })}`;
  }, []);

  const results = resultsOf(currentBatch);
  const lightboxItem = lightboxIndex != null ? results[lightboxIndex] : null;
  const queueBusy = queue.some((q) => POLLABLE_STATUSES.has(q.status));
  const typedSeed = parseImageSeed(seedText);

  // Status banner: no model installed means nothing can render, so say how
  // to get one before the user fills in the form.
  const renderStatusBanner = () => {
    if (!status || installedModels.length) return null;
    return (
      <Alert severity="warning" sx={{ mb: 2 }}>
        <Trans i18nKey="images.infographic.noModel" components={{ bold: <strong /> }} />
        <Button size="small" onClick={refreshStatus} sx={{ ml: 1 }}>{t('images.infographic.refresh')}</Button>
      </Alert>
    );
  };

  const renderResults = () => {
    if (!currentBatchId) {
      return (
        <Typography variant="body2" sx={{ color: 'text.secondary', textAlign: 'center', py: 6 }}>
          {t('images.infographic.previewEmpty')}
        </Typography>
      );
    }
    const total = currentBatch?.total_images || 0;
    const done = (currentBatch?.completed_images || 0) + (currentBatch?.failed_images || 0);
    const running = currentBatch && POLLABLE_STATUSES.has(currentBatch.status);
    return (
      <Stack spacing={1.5}>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 0 }} noWrap title={currentBatch?.display_name || currentBatchId}>
            {currentBatch?.display_name || currentBatchId}
          </Typography>
          {currentBatch && (
            <Chip size="small" variant="outlined" label={t('images.infographic.progress', { done, total })} />
          )}
          {currentBatch && !running && currentBatch.retry_data && (
            <Button size="small" onClick={() => handleAdjustRetry(currentBatchId)}>
              {t('images.batchCard.adjustRetry')}
            </Button>
          )}
        </Stack>
        {(running || !currentBatch) && (
          <Box>
            <LinearProgress
              variant={total ? 'determinate' : 'indeterminate'}
              value={total ? Math.round((done / total) * 100) : 0}
              sx={{ height: 4, borderRadius: 2 }}
            />
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {currentBatch?.status === 'queued'
                ? t('images.infographic.waiting')
                : t('images.infographic.generatingHint', { model: chosenModel?.name || model })}
            </Typography>
          </Box>
        )}
        {currentBatch?.error && !running && (
          <Alert severity="error" sx={{ py: 0 }}>{currentBatch.error}</Alert>
        )}
        {results.length > 0 && (
          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 1 }}>
            {results.map((r, idx) => (
              <Box
                key={r.id}
                component="button"
                type="button"
                onClick={() => setLightboxIndex(idx)}
                sx={{
                  p: 0, border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden',
                  cursor: 'pointer', bgcolor: 'grey.900', position: 'relative',
                }}
              >
                <img
                  src={imageUrl(currentBatchId, r.path, true)}
                  alt={t('images.infographic.resultAlt')}
                  loading="lazy"
                  style={{ width: '100%', display: 'block' }}
                />
                {r.seed != null && (
                  <Chip
                    size="small"
                    label={t('images.infographic.seed', { seed: r.seed })}
                    sx={{ position: 'absolute', bottom: 4, left: 4, height: 18, fontSize: '0.65rem', bgcolor: 'rgba(0,0,0,0.7)', color: 'white' }}
                  />
                )}
              </Box>
            ))}
          </Box>
        )}
      </Stack>
    );
  };

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, p: 2, maxWidth: 1600 }}>
      <Box>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {t('images.infographic.title')}
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          {t('images.infographic.intro')}
        </Typography>
      </Box>

      {renderStatusBanner()}

      <ToggleButtonGroup
        value={mode}
        exclusive
        onChange={(_e, v) => v && setMode(v)}
        size="small"
        sx={{ alignSelf: 'flex-start' }}
      >
        <ToggleButton value="structured">{t('images.infographic.structured')}</ToggleButton>
        <ToggleButton value="freeform">{t('images.infographic.freeform')}</ToggleButton>
      </ToggleButtonGroup>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 2, alignItems: 'start' }}>
        {/* LEFT — form */}
        <Paper variant="outlined" sx={{ p: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>
          {mode === 'structured' ? (
            <>
              <TextField
                label={t('images.infographic.titleLabel')}
                placeholder={t('images.infographic.titlePlaceholder')}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                size="small"
                fullWidth
                inputProps={{ maxLength: 90 }}
              />
              <TextField
                label={t('images.infographic.sceneLabel')}
                placeholder={t('images.infographic.scenePlaceholder')}
                value={scene}
                onChange={(e) => setScene(e.target.value)}
                multiline
                minRows={4}
                size="small"
                fullWidth
              />
              <TextField
                label={t('images.infographic.calloutsLabel')}
                placeholder={t('images.infographic.calloutsPlaceholder')}
                value={callouts}
                onChange={(e) => setCallouts(e.target.value)}
                multiline
                minRows={3}
                size="small"
                fullWidth
                helperText={t('images.infographic.calloutsHelp')}
              />
              <TextField
                label={t('images.infographic.footerLabel')}
                placeholder={t('images.infographic.footerPlaceholder')}
                value={footer}
                onChange={(e) => setFooter(e.target.value)}
                size="small"
                fullWidth
                inputProps={{ maxLength: 140 }}
              />
              <TextField
                label={t('images.infographic.hashtagsLabel')}
                placeholder={t('images.infographic.hashtagsPlaceholder')}
                value={hashtags}
                onChange={(e) => setHashtags(e.target.value)}
                size="small"
                fullWidth
                helperText={t('images.infographic.hashtagsHelp')}
              />
            </>
          ) : (
            <TextField
              label={t('images.infographic.freeformLabel')}
              placeholder={t('images.infographic.freeformPlaceholder')}
              value={rawPrompt}
              onChange={(e) => setRawPrompt(e.target.value)}
              multiline
              minRows={10}
              size="small"
              fullWidth
            />
          )}

          <TextField
            select
            label={t('images.infographic.model')}
            value={chosenModel ? model : ''}
            onChange={(e) => setModel(e.target.value)}
            size="small"
            fullWidth
            disabled={!installedModels.length}
            helperText={chosenModel?.license_note || ' '}
          >
            {installedModels.map((m) => (
              <MenuItem key={m.id} value={m.id}>{m.name}</MenuItem>
            ))}
          </TextField>

          <Stack direction="row" spacing={2}>
            <TextField
              select
              label={t('images.infographic.style')}
              value={style}
              onChange={(e) => setStyle(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
            >
              {INFOGRAPHIC_STYLES.map((s) => (
                <MenuItem key={s.value} value={s.value}>{s.label}</MenuItem>
              ))}
            </TextField>
            <TextField
              select
              label={t('images.infographic.aspect')}
              value={aspect}
              onChange={(e) => setAspect(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
            >
              {INFOGRAPHIC_ASPECTS.map((a) => (
                <MenuItem key={a.value} value={a.value}>{a.label}</MenuItem>
              ))}
            </TextField>
          </Stack>

          <Stack direction="row" spacing={2} alignItems="flex-start">
            <TextField
              label={t('images.infographic.quantity')}
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(clampQuantity(e.target.value))}
              inputProps={{ min: 1, max: 100 }}
              size="small"
              sx={{ width: 120 }}
            />
            <Box sx={{ flex: 1 }}>
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
                {typedSeed != null
                  ? t('imagesBatch.seed.fixed', { seed: typedSeed })
                  : seedText.trim()
                    ? t('imagesBatch.seed.tooLarge', { max: IMAGE_MAX_SEED })
                    : t('images.infographic.seedBlank')}
              </Typography>
            </Box>
          </Stack>

          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button
              variant="contained"
              startIcon={queuing ? <CircularProgress size={16} color="inherit" /> : <AutoAwesomeIcon />}
              onClick={handleGenerate}
              disabled={!canGenerate}
              size="medium"
            >
              {queuing
                ? t('images.infographic.queuing')
                : queueBusy ? t('images.infographic.addToQueue') : t('images.shared.generate')}
            </Button>
            {notice && !error && (
              <Alert severity="success" sx={{ flex: 1, py: 0 }} onClose={() => setNotice('')}>{notice}</Alert>
            )}
            {error && (
              <Alert severity="error" sx={{ flex: 1, py: 0 }} onClose={() => setError('')}>{error}</Alert>
            )}
          </Box>
        </Paper>

        {/* RIGHT — queue and results */}
        <Box>
          <BatchQueuePanel queue={queue} onCancel={handleCancel} onClearCompleted={clearCompleted} />
          <Paper variant="outlined" sx={{ p: 2, minHeight: 240, bgcolor: 'rgba(0,0,0,0.02)' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
              {t('images.infographic.results')}
            </Typography>
            {renderResults()}
          </Paper>
        </Box>
      </Box>

      {history.length > 0 && (
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
            {t('images.infographic.recent')}
          </Typography>
          <Grid container spacing={2}>
            {history.map((batch) => (
              <Grid item xs={6} sm={4} md={3} lg={2} key={batch.batch_id}>
                <BatchHistoryCard
                  batch={batch}
                  dateStr={formatDate(batch.start_time)}
                  onOpen={openBatch}
                  onLoad={loadBatch}
                  onHide={hideBatch}
                  onDownload={downloadBatch}
                  onAdjustRetry={handleAdjustRetry}
                />
              </Grid>
            ))}
          </Grid>
        </Box>
      )}

      {lightboxItem && (
        <ImageLightbox
          imageUrl={imageUrl(currentBatchId, lightboxItem.path)}
          imageName={currentBatch?.display_name || fileName(lightboxItem.path)}
          caption={lightboxItem.metadata?.infographic?.title || ''}
          seed={lightboxItem.seed ?? null}
          onUseSeed={(seed) => { setSeedText(String(seed)); setQuantity(1); setLightboxIndex(null); }}
          onClose={() => setLightboxIndex(null)}
          onPrev={() => setLightboxIndex((i) => Math.max(0, i - 1))}
          onNext={() => setLightboxIndex((i) => Math.min(results.length - 1, i + 1))}
          onDownload={() => saveFile(imageUrl(currentBatchId, lightboxItem.path), fileName(lightboxItem.path))}
          hasPrev={lightboxIndex > 0}
          hasNext={lightboxIndex < results.length - 1}
        />
      )}
    </Box>
  );
};

export default InfographicGenerator;
