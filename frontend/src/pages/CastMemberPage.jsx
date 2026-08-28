import React, { useState, useEffect, useRef, useCallback } from 'react';
import { formatUiError } from "../utils/uiError";
import { useParams, useNavigate } from 'react-router-dom';
import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';
import {
  Box, Tabs, Tab, Typography, Button, TextField, Card, CardMedia, CardContent,
  CardActions, Chip, CircularProgress, Alert, Dialog, DialogTitle, DialogContent,
  DialogActions, Grid, IconButton, Tooltip, Divider, Link, LinearProgress,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome';
import RefreshIcon from '@mui/icons-material/Refresh';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import CloseIcon from '@mui/icons-material/Close';
import {
  getCastSubject, getCastSubjectDetail, updateCastSubject, planCharacter, rebuildBibleFromRefs,
  generateSamples, cancelGenerateSamples, listSamples, regenerateSample, approveSamples,
  deleteSample, trainSubject, cancelTrainSubject,
} from '../api/productionService';
import { SubjectThumb } from '../components/filmcrew/CastLibraryView';
import DragDropImageUpload from '../components/filmcrew/DragDropImageUpload';

const POLL_MS = 5000;
const POLL_CAP = 180; // 15 min safety cap on a generate/train poll loop

const DEFAULT_TRAINING_SETTINGS = {
  resolution: 768,
  rank: 16,
  alpha: 16,
  learning_rate: 1e-4,
  steps: '',
  base_model_id: 'zimage-turbo',
};

const trainingSettingsFromSubject = (subject) => {
  const raw = subject?.training_settings_json || {};
  return {
    resolution: raw.resolution ?? DEFAULT_TRAINING_SETTINGS.resolution,
    rank: raw.rank ?? DEFAULT_TRAINING_SETTINGS.rank,
    alpha: raw.alpha ?? DEFAULT_TRAINING_SETTINGS.alpha,
    learning_rate: raw.learning_rate ?? DEFAULT_TRAINING_SETTINGS.learning_rate,
    steps: raw.steps ?? '',
    base_model_id:
      raw.base_model_id || subject?.base_model_id || DEFAULT_TRAINING_SETTINGS.base_model_id,
  };
};

// A sample is "in flight" while its image is still being produced.
const isPending = (s) => s.status === 'pending' || s.status === 'generating';

// Generate Character sheet only: promoted samples already live on Training Data.
const isOnGenerateSheet = (s) => !s.promoted_to_training;

const StatusChip = ({ status }) => {
  const color = status === 'done' ? 'success'
    : status === 'failed' ? 'error'
    : status === 'cancelled' ? 'default'
    : status === 'generating' ? 'warning' : 'default';
  return <Chip size="small" label={status} color={color} />;
};

/** Training-lifecycle chip for a generated sample (approved / training / trained). */
const sampleTrainStatus = (sample, subject) => {
  if (sample.promoted_to_training) return { label: 'trained', color: 'success' };
  if (!sample.approved || sample.status !== 'done') return null;
  if (subject?.training_status === 'training') return { label: 'training', color: 'warning' };
  return { label: 'approved', color: 'info' };
};

/** Training-lifecycle chip for a durable ref path on Training Data. */
const refTrainStatus = (path, subject) => {
  const last = subject?.last_trained_image_paths || [];
  if (path && last.includes(path)) return { label: 'trained', color: 'success' };
  if (subject?.training_status === 'training') return { label: 'training', color: 'warning' };
  return { label: 'ref', color: 'default' };
};

const TrainStatusChip = ({ status }) => {
  if (!status?.label) return null;
  return (
    <Chip
      size="small"
      label={status.label}
      color={status.color || 'default'}
      variant="filled"
      sx={{ height: 20, fontSize: '0.7rem', '& .MuiChip-label': { px: 0.75 } }}
    />
  );
};

const CastMemberPage = () => {
  const { subjectId } = useParams();
  const navigate = useNavigate();

  const [subject, setSubject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(0);

  // Overview edit form.
  const [form, setForm] = useState({
    name: '', description: '', trigger_word: '', voice_id: '', bible: '',
  });
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [rebuildingBible, setRebuildingBible] = useState(false);

  // Generate-character state.
  const [samples, setSamples] = useState([]);
  const [planning, setPlanning] = useState(false);
  const [busy, setBusy] = useState(false);        // generate/train dispatch in flight
  const [polling, setPolling] = useState(false);
  // True from Generate dispatch until samples settle / cancel — so Cancel is
  // available even while the worker is still in the LLM planning phase.
  const [generateActive, setGenerateActive] = useState(false);
  const pollCount = useRef(0);
  const [regenTarget, setRegenTarget] = useState(null); // sample being regenerated
  const [regenPrompt, setRegenPrompt] = useState('');
  const [lightboxIdx, setLightboxIdx] = useState(null); // open enlarged viewer at this samples[] index

  // Local state to surface training progress from unified jobs (so frontend "knows"
  // when GPU is crunching on long LoRA train, even if subject poll lags or health/celery 503s).
  const [trainingJob, setTrainingJob] = useState(null);
  const [trainingSettings, setTrainingSettings] = useState(DEFAULT_TRAINING_SETTINGS);
  const [savingTrainingSettings, setSavingTrainingSettings] = useState(false);
  const [trainingSettingsSaved, setTrainingSettingsSaved] = useState(false);

  const loadSubject = useCallback(async () => {
    // Prefer efficient single-subject (with samples when convenient).
    // Falls back gracefully.
    try {
      const detail = await getCastSubjectDetail(subjectId, { includeSamples: true });
      const s = detail?.subject || detail;
      setSubject(s);
      if (s) {
        setForm({
          name: s.name || '', description: s.description || '',
          trigger_word: s.trigger_word || '', voice_id: s.voice_id || '',
          bible: s.bible || '',
        });
        setTrainingSettings(trainingSettingsFromSubject(s));
      }
      if (detail?.samples) {
        setSamples(detail.samples);
      }
      return s;
    } catch (e) {
      // legacy fallback
      const s = await getCastSubject(subjectId);
      setSubject(s);
      if (s) {
        setForm({
          name: s.name || '', description: s.description || '',
          trigger_word: s.trigger_word || '', voice_id: s.voice_id || '',
          bible: s.bible || '',
        });
        setTrainingSettings(trainingSettingsFromSubject(s));
      }
      return s;
    }
  }, [subjectId]);

  const loadSamples = useCallback(async () => {
    const data = await listSamples(subjectId);
    setSamples(data.samples || []);
    return data.samples || [];
  }, [subjectId]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const s = await loadSubject();
        if (alive && !s) setError('Subject not found.');
        const rows = await loadSamples();
        // If a generation/regeneration is already in flight when we land on the
        // page (e.g. after a refresh), auto-resume polling so finished images
        // appear without a manual reload.
        if (alive && (rows || []).some(isPending)) {
          setGenerateActive(true);
        }
        if (alive && ((rows || []).some(isPending) || s?.training_status === 'training')) {
          pollCount.current = 0;
          setPolling(true);
        }
      } catch (e) {
        if (alive) setError('Failed to load this cast member.');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [loadSubject, loadSamples]);

  // Poll while a generate/regenerate/train job is running; auto-stop when the
  // work settles (no pending samples and not training) or the cap is hit.
  useEffect(() => {
    if (!polling) return undefined;
    const id = setInterval(async () => {
      pollCount.current += 1;
      try {
        const [s, rows] = await Promise.all([loadSubject(), loadSamples()]);
        const stillGenerating = (rows || []).some(isPending);
        const stillTraining = s?.training_status === 'training';
        if (!stillGenerating) setGenerateActive(false);
        if ((!stillGenerating && !stillTraining) || pollCount.current >= POLL_CAP) {
          setPolling(false);
        }
      } catch (e) {
        // transient — keep polling until the cap
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [polling, loadSubject, loadSamples]);

  const startPolling = () => { pollCount.current = 0; setPolling(true); };

  // React to unified progress jobs for *this* subject (key for batch queue of many cast trainings).
  // The backend now creates processes with additional_data.subject_id on dispatch.
  // This lets the page get live updates / terminal notifications without sole reliance on 5s poll,
  // enabling long-running day+ batch jobs to surface correctly.
  const { activeProcesses } = useUnifiedProgress();
  useEffect(() => {
    if (!subjectId) return;
    const procs = Array.from(activeProcesses.values());

    // Find any job for this subject (samples or training)
    const subjectMatch = procs.find((p) => {
      const ad = p.additional_data || p.metadata || p;
      return String(ad.subject_id || ad.sample_subject_id || '') === String(subjectId);
    });

    // Specifically for training job (from dispatch additional_data or kind)
    const trainingMatch = procs.find((p) => {
      const ad = p.additional_data || p.metadata || p;
      const isThisSubject = String(ad.subject_id || '') === String(subjectId);
      const isTrain = ad.operation === 'train_lora' || p.kind === 'training' || ad.kind === 'cast_training';
      return isThisSubject && isTrain;
    });

    if (trainingMatch) {
      const st = (trainingMatch.status || '').toLowerCase();
      if (['complete', 'end', 'error', 'cancelled', 'failed'].includes(st)) {
        setTrainingJob(null);
        loadSubject();
        loadSamples();
        setPolling(false);
      } else {
        setTrainingJob({
          id: trainingMatch.id || trainingMatch.job_id,
          progress: trainingMatch.progress,
          message: trainingMatch.message || trainingMatch.status,
        });
      }
    } else if (trainingJob) {
      // no longer in active processes
      setTrainingJob(null);
    }

    if (subjectMatch) {
      const st = (subjectMatch.status || '').toLowerCase();
      if (['complete', 'end', 'error', 'cancelled', 'failed'].includes(st)) {
        setGenerateActive(false);
        loadSubject();
        loadSamples();
        setPolling(false);
      }
    }
  }, [activeProcesses, subjectId, loadSubject, loadSamples]);

  // Safety net: if the subject status updates to not-training (e.g. from a manual
  // refresh or late poll), make sure we stop the sample/training poll spinner.
  useEffect(() => {
    if (subject && subject.training_status !== 'training') {
      const stillGen = samples.some(isPending);
      if (!stillGen) {
        setPolling(false);
      }
    }
  }, [subject, samples]);

  // Slow background poll for subject status (every 30s) so that even if the
  // main polling flag was turned off prematurely (e.g. due to race on complete
  // event before DB commit), we eventually see the final 'trained' status and
  // clear the spinner / footer state.
  useEffect(() => {
    if (!subjectId) return undefined;
    const id = setInterval(() => {
      loadSubject().then((s) => {
        if (s && s.training_status !== 'training') {
          const stillGen = samples.some(isPending);
          if (!stillGen) setPolling(false);
        }
      }).catch(() => {});
    }, 30000);
    return () => clearInterval(id);
  }, [subjectId, loadSubject, samples]);

  // Arrow-key / Escape navigation for the enlarged image viewer (sheet = non-promoted).
  const sheetLenForKeys = samples.filter(isOnGenerateSheet).length;
  useEffect(() => {
    if (lightboxIdx === null) return undefined;
    const onKey = (e) => {
      if (e.key === 'ArrowLeft') setLightboxIdx((i) => (i > 0 ? i - 1 : i));
      else if (e.key === 'ArrowRight') setLightboxIdx((i) => (i < sheetLenForKeys - 1 ? i + 1 : i));
      else if (e.key === 'Escape') setLightboxIdx(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lightboxIdx, sheetLenForKeys]);

  const handleSave = async () => {
    setSaving(true); setError(null);
    try {
      await updateCastSubject(subjectId, form);
      await loadSubject();
      setSavedNote(true);
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || 'Failed to save changes.');
    } finally {
      setSaving(false);
    }
  };

  const handleRebuildBible = async () => {
    const refs = subject?.ref_image_paths || [];
    if (!refs.length) {
      setError('Upload reference photos on Training Data first, then sync identity from photos.');
      return;
    }
    setRebuildingBible(true); setError(null);
    try {
      const data = await rebuildBibleFromRefs(subjectId, {
        refresh_captions: true,
        refresh_sample_prompts: true,
      });
      if (data.subject) setSubject(data.subject);
      if (data.bible != null) {
        setForm((prev) => ({ ...prev, bible: data.bible || '', trigger_word: data.trigger_word || prev.trigger_word }));
      }
      await loadSubject();
      setSavedNote(true);
    } catch (e) {
      setError(
        e.response?.data?.message
        || formatUiError(e.response?.data?.error)
        || 'Sync identity from photos failed (is Ollama up?).',
      );
    } finally {
      setRebuildingBible(false);
    }
  };

  const handlePlan = async () => {
    // Re-plan starts a FRESH sheet — it discards the current set, including any
    // approved keepers. Guard against accidental loss (the append flow exists
    // precisely so users don't have to wipe to add more).
    if (approvedCount > 0) {
      const ok = window.confirm(
        `Re-plan will discard the current sheet, including your ${approvedCount} approved ` +
        `sample${approvedCount > 1 ? 's' : ''}. To ADD more without losing these, use ` +
        `"Generate additional" instead.\n\nRe-plan from scratch anyway?`
      );
      if (!ok) return;
    }
    setPlanning(true); setError(null);
    try {
      const data = await planCharacter(subjectId);
      if (data.bible) setSubject((prev) => prev ? { ...prev, bible: data.bible, trigger_word: data.trigger_word || prev.trigger_word } : prev);
      // Reload full sample list so promoted Training Data keepers stay in state
      // (plan response only returns the new sheet rows).
      await loadSamples();
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || 'Planning failed (the LLM may be offline).');
    } finally {
      setPlanning(false);
    }
  };

  const handleGenerate = async () => {
    setBusy(true); setError(null);
    try {
      // Append a new batch onto the curated set (keeps approved keepers, stacks the
      // new shots above them). If we have a trained LoRA, use it so the new samples
      // stay consistent with the trained character (new costumes, angles, etc.).
      const options = { append: true, ...(subject.lora_path ? { use_trained_lora: true } : {}) };
      const res = await generateSamples(subjectId, options);
      setGenerateActive(true);
      await loadSamples();
      if (res?.job_id) {
        console.debug('[CastMemberPage] generate job', res.job_id);
      }
      startPolling();
    } catch (e) {
      setGenerateActive(false);
      setError(formatUiError(e.response?.data?.error) || 'Failed to start sample generation.');
    } finally {
      setBusy(false);
    }
  };

  const submitRegen = async () => {
    const sid = regenTarget.id;
    const body = regenPrompt.trim() ? { prompt_override: regenPrompt.trim() } : {};
    setRegenTarget(null); setRegenPrompt('');
    setError(null);
    try {
      const res = await regenerateSample(subjectId, sid, body);
      setGenerateActive(true);
      await loadSamples();
      if (res?.job_id) console.debug('[CastMemberPage] regen job', res.job_id);
      startPolling();
    } catch (e) {
      setGenerateActive(false);
      setError(formatUiError(e.response?.data?.error) || 'Failed to regenerate sample.');
    }
  };

  const toggleApprove = async (sample) => {
    try {
      await approveSamples(subjectId, [sample.id], !sample.approved);
      await loadSamples();
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || 'Failed to update approval.');
    }
  };

  const handleDeleteSample = async (sample) => {
    try {
      await deleteSample(subjectId, sample.id);
      // Keep the lightbox sane if the open image was the one removed (sheet only).
      setLightboxIdx((i) => {
        if (i === null) return i;
        const sheetLen = samples.filter(isOnGenerateSheet).length;
        return Math.max(0, Math.min(i, Math.max(0, sheetLen - 2)));
      });
      await loadSamples();
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || 'Failed to delete sample.');
    }
  };

  const approveAllDone = async () => {
    // Only the Generate Character sheet (skip already-promoted Training Data keepers).
    const ids = samples
      .filter((s) => s.status === 'done' && !s.promoted_to_training)
      .map((s) => s.id);
    if (!ids.length) return;
    try {
      await approveSamples(subjectId, ids, true);
      await loadSamples();
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || 'Failed to approve set.');
    }
  };

  const buildTrainingSettingsPayload = () => {
    const stepsRaw = trainingSettings.steps;
    const steps = stepsRaw === '' || stepsRaw == null ? null : Number(stepsRaw);
    return {
      resolution: Number(trainingSettings.resolution) || 768,
      rank: Number(trainingSettings.rank) || 16,
      alpha: Number(trainingSettings.alpha) || 16,
      learning_rate: Number(trainingSettings.learning_rate) || 1e-4,
      steps: Number.isFinite(steps) && steps > 0 ? steps : null,
      base_model_id: trainingSettings.base_model_id || subject?.base_model_id || 'zimage-turbo',
    };
  };

  const handleSaveTrainingSettings = async () => {
    setSavingTrainingSettings(true);
    setError(null);
    try {
      await updateCastSubject(subjectId, { training_settings: buildTrainingSettingsPayload() });
      await loadSubject();
      setTrainingSettingsSaved(true);
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || 'Failed to save training settings.');
    } finally {
      setSavingTrainingSettings(false);
    }
  };

  const handleTrain = async () => {
    setBusy(true); setError(null);
    try {
      const res = await trainSubject(subjectId, { training_settings: buildTrainingSettingsPayload() });
      await loadSubject();
      if (res?.job_id) {
        setTrainingJob({ id: res.job_id, operation: 'train_lora' });
        console.debug('[CastMemberPage] train job started', res.job_id);
      }
      startPolling();
    } catch (e) {
      const data = e.response?.data;
      if (data?.error === 'train_base_not_ready') {
        setError(data.message || data.train_status_note || 'Train base not ready for this model.');
      } else {
        setError(
          (typeof data?.error === 'string' ? data.error : null)
          || data?.message
          || (e.response?.status === 409 ? 'Already training.' : 'Failed to start training.'),
        );
      }
    } finally {
      setBusy(false);
    }
  };

  const handleCancelTrain = async () => {
    setBusy(true); setError(null);
    try {
      await cancelTrainSubject(subjectId);
      setTrainingJob(null);
      await loadSubject();
    } catch (e) {
      setError(e.response?.data?.reason || formatUiError(e.response?.data?.error) || 'Failed to cancel training.');
    } finally {
      setBusy(false);
    }
  };

  const handleCancelGenerate = async () => {
    setBusy(true); setError(null);
    try {
      await cancelGenerateSamples(subjectId);
      setGenerateActive(false);
      await loadSamples();
      setPolling(false);
    } catch (e) {
      const data = e.response?.data;
      if (data?.reason === 'not_generating') {
        setGenerateActive(false);
        await loadSamples();
        setPolling(false);
      } else {
        setError(data?.reason || data?.error || 'Failed to cancel generation.');
      }
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <Box sx={{ display: 'flex', justifyContent: 'center', p: 6 }}><CircularProgress /></Box>;
  }
  if (!subject) {
    return (
      <Box sx={{ p: 3 }}>
        <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/cast')}>Back to studio</Button>
        <Alert severity="error" sx={{ mt: 2 }}>{error || 'Subject not found.'}</Alert>
      </Box>
    );
  }

  // Generate Character sheet excludes samples already promoted into Training Data
  // after a successful train. Until promotion, approved gens show on BOTH tabs.
  const sheetSamples = samples.filter(isOnGenerateSheet);
  const approvedPending = sheetSamples.filter((s) => s.approved && s.status === 'done');
  const approvedCount = approvedPending.length;
  const doneCount = sheetSamples.filter((s) => s.status === 'done').length;
  const generatingCount = sheetSamples.filter((s) => s.status === 'generating').length;
  const failedCount = sheetSamples.filter((s) => s.status === 'failed').length;
  const total = sheetSamples.length;
  const active = generatingCount > 0 || (polling && sheetSamples.some(isPending));
  const refCount = (subject.ref_image_paths || []).length;
  // Trainable from EITHER uploaded/promoted reference images OR approved samples
  // still on the generate sheet — backend trains on the union of both.
  const trainable = refCount > 0 || approvedCount > 0;
  const training = subject.training_status === 'training';

  // For "images that have been used in training to date" + amend/catch-up vision.
  // IMPORTANT: This count is only set after a verified real trainer success
  // (sidecar + minimum LoRA file size checks in lora_trainer_tasks).
  const lastTrainedPaths = subject.last_trained_image_paths || [];
  const lastTrainedCount = lastTrainedPaths.length;
  // Pool = durable refs (uploads + promoted gens) + approved gens not yet promoted.
  // Do not double-count: promoted samples are already folded into ref_image_paths.
  const currentPoolSize = refCount + approvedCount;
  const hasPendingAmend = lastTrainedCount > 0 && currentPoolSize > lastTrainedCount;
  const trainingStatusLabel = hasPendingAmend && subject.training_status === 'trained'
    ? 'trained (pending amend)'
    : subject.training_status;

  // Approved generated samples still awaiting promotion — shown on Training Data
  // next to refs so both tabs reflect the live training pool.
  const pendingPromoteThumbs = approvedPending
    .filter((s) => s.image_url)
    .map((s) => ({
      key: `sample-${s.id}`,
      src: s.image_url,
      name: s.angle || `Sample ${s.index + 1}`,
      status: sampleTrainStatus(s, subject),
    }));

  return (
    <Box
      sx={{
        // AppLayout clips with overflow:hidden — this page must own scrolling
        // so tall tabs (Training Data → Train LoRA) remain reachable.
        p: { xs: 1, sm: 2 },
        height: '100%',
        minHeight: 0,
        overflowY: 'auto',
        boxSizing: 'border-box',
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
        <IconButton onClick={() => navigate('/cast')} aria-label="back to studio"><ArrowBackIcon /></IconButton>
        <Typography variant="h5" sx={{ fontWeight: 600 }}>{subject.name}</Typography>
        <Chip label={subject.kind} size="small" variant="outlined" />
        <Chip label={subject.training_status} size="small"
              color={
                subject.training_status === 'trained' ? 'success' :
                subject.training_status === 'failed' ? 'error' :
                training ? 'warning' : 'default'
              } />
        <Tooltip
          title={
            subject.train_status_note ||
            'LoRA train/inference base from Settings → Media models. Must match family at generate time.'
          }
        >
          <Chip
            label={subject.base_model_name || subject.base_model_id || 'base ?'}
            size="small"
            color={subject.train_ready === false ? 'warning' : 'primary'}
            variant="outlined"
          />
        </Tooltip>
        {polling && <CircularProgress size={18} sx={{ ml: 1 }} />}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Overview" />
        <Tab label="Training Data" />
        <Tab label="Generate Character" />
        <Tab label="Versions" />
      </Tabs>

      {/* ── Overview ───────────────────────────────────────────────────────── */}
      {tab === 0 && (
        <Grid container spacing={3}>
          <Grid item xs={12} sm={4} md={3}>
            <Box sx={{ borderRadius: 1, overflow: 'hidden' }}><SubjectThumb subject={subject} /></Box>
          </Grid>
          <Grid item xs={12} sm={8} md={9}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 560 }}>
              <TextField label="Name" value={form.name}
                         onChange={(e) => setForm({ ...form, name: e.target.value })} fullWidth />
              {subject.kind === 'character' && (
                <TextField label="Trigger word (LoRA token)" value={form.trigger_word}
                           onChange={(e) => setForm({ ...form, trigger_word: e.target.value })} fullWidth
                           helperText="Rare token the LoRA trains on; every prompt must include it. Blank → uses the name." />
              )}
              <TextField label="Voice ID (optional)" value={form.voice_id}
                         onChange={(e) => setForm({ ...form, voice_id: e.target.value })} fullWidth
                         helperText="Audio Foundry voice for narration. Leave blank to clear." />
              <TextField
                label="Description"
                value={form.description}
                multiline
                rows={3}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                fullWidth
                helperText="Optional brief for FilmCrew / script invent only. Not used for Cast generate when reference photos exist."
              />
              <Divider sx={{ my: 1 }} />
              <Typography variant="overline" color="text.secondary">Identity bible</Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                Appearance from your photos — vision describes what it sees (human, animal, costume, …)
                and keeps the consistent details. Used for base (no-LoRA) generates; with a trained LoRA,
                generate uses trigger + short marks only. Train does not rewrite this.
              </Typography>
              {(subject.ref_image_paths || []).length > 0 && !subject.bible_vision_grounded && (
                <Alert severity="warning" sx={{ mb: 1 }}>
                  {subject.bible_manual_override
                    ? <>Manual edit — use <strong>Sync identity from photos</strong> to re-ground from refs.</>
                    : <>Bible may not match your photos — use <strong>Sync identity from photos</strong> so Generate / captions stop inventing a different look.</>}
                </Alert>
              )}
              <TextField
                label="Identity bible"
                value={form.bible}
                onChange={(e) => setForm({ ...form, bible: e.target.value })}
                multiline
                rows={5}
                fullWidth
                helperText={
                  subject.bible_vision_grounded
                    ? 'Vision-grounded from reference photos'
                    : subject.bible_manual_override
                      ? 'Manual edit — Sync identity from photos to re-ground'
                      : 'Not yet synced from photos'
                }
              />
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
                <Button variant="contained" onClick={handleSave} disabled={saving || !form.name}>
                  {saving ? 'Saving…' : 'Save changes'}
                </Button>
                <Button
                  variant="outlined"
                  onClick={handleRebuildBible}
                  disabled={rebuildingBible || busy || !(subject.ref_image_paths || []).length}
                >
                  {rebuildingBible ? 'Scanning photos…' : 'Sync identity from photos'}
                </Button>
                {savedNote && <Typography variant="caption" color="success.main">Saved</Typography>}
              </Box>
            </Box>
          </Grid>
        </Grid>
      )}

      {/* ── Training Data ──────────────────────────────────────────────────── */}
      {tab === 1 && (
        <Box sx={{ maxWidth: 1100, mx: 'auto', pb: 6, width: '100%' }}>
          {/* Intro and status info stay at top */}
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Images for the *next* training run: uploaded references plus approved generated samples.
            After a successful train, generated keepers promote here permanently and leave the Generate Character sheet.
            Add new outfits / details then click Train to amend.
          </Typography>

          {/* Prominent display of last-trained count and amend status */}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1.5 }}>
            <Chip 
              size="small" 
              label={`Current pool: ${currentPoolSize} image${currentPoolSize === 1 ? '' : 's'}`} 
              variant="outlined" 
            />
            {lastTrainedCount > 0 && (
              <Chip 
                size="small" 
                label={`Last trained (real, verified): ${lastTrainedCount}`} 
                color="success" 
                variant="outlined" 
              />
            )}
            {hasPendingAmend && (
              <Chip size="small" label="Pending amend / catch-up" color="warning" />
            )}
            {lastTrainedCount === 0 && subject.training_status === 'trained' && (
              <Chip size="small" label="Trained (image list not recorded)" color="default" />
            )}
          </Box>
          <DragDropImageUpload
            subjectId={subject.id}
            existingPaths={subject.ref_image_paths || []}
            onUploaded={loadSubject}
            helperText="Uploads immediately to this cast member."
            extraItems={pendingPromoteThumbs}
            getPathStatus={(path) => refTrainStatus(path, subject)}
          />

          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle2" gutterBottom>Training hyperparameters</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            Train base must match generate base. Z-Image Turbo is the product default and
            trains + generates; SDXL Legacy is the older path. Steps blank = auto from image count.
          </Typography>
          <Grid container spacing={2} sx={{ mb: 2, maxWidth: 720 }}>
            <Grid item xs={12} sm={8}>
              <TextField
                select
                label="Train base"
                size="small"
                fullWidth
                value={trainingSettings.base_model_id || 'zimage-turbo'}
                onChange={(e) => {
                  setTrainingSettingsSaved(false);
                  setTrainingSettings({ ...trainingSettings, base_model_id: e.target.value });
                }}
                SelectProps={{ native: true }}
                helperText="Global default: Settings → Media models"
              >
                <option value="zimage-turbo">Z-Image Turbo (default — trains + generates)</option>
                <option value="flux-dev">FLUX.1 Dev (max quality — train soon)</option>
                <option value="sdxl-legacy">SDXL Legacy (legacy path)</option>
              </TextField>
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label="Resolution" type="number" size="small" fullWidth
                value={trainingSettings.resolution}
                onChange={(e) => { setTrainingSettingsSaved(false); setTrainingSettings({ ...trainingSettings, resolution: e.target.value }); }}
                helperText="Snapped to 64px (512–1024)" />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label="LoRA rank" type="number" size="small" fullWidth
                value={trainingSettings.rank}
                onChange={(e) => { setTrainingSettingsSaved(false); setTrainingSettings({ ...trainingSettings, rank: e.target.value }); }}
                helperText="4–64" />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label="LoRA alpha" type="number" size="small" fullWidth
                value={trainingSettings.alpha}
                onChange={(e) => { setTrainingSettingsSaved(false); setTrainingSettings({ ...trainingSettings, alpha: e.target.value }); }}
                helperText="Usually matches rank" />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label="Learning rate" type="number" size="small" fullWidth
                value={trainingSettings.learning_rate}
                onChange={(e) => { setTrainingSettingsSaved(false); setTrainingSettings({ ...trainingSettings, learning_rate: e.target.value }); }}
                inputProps={{ step: '0.00001' }} />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label="Steps (optional)" type="number" size="small" fullWidth
                value={trainingSettings.steps}
                onChange={(e) => { setTrainingSettingsSaved(false); setTrainingSettings({ ...trainingSettings, steps: e.target.value }); }}
                helperText="Blank = auto from image count" />
            </Grid>
            <Grid item xs={12}>
              <Button variant="outlined" size="small" onClick={handleSaveTrainingSettings}
                disabled={savingTrainingSettings || training}>
                {savingTrainingSettings ? 'Saving…' : 'Save training settings'}
              </Button>
              {trainingSettingsSaved && (
                <Typography variant="caption" color="success.main" sx={{ ml: 2 }}>Saved</Typography>
              )}
            </Grid>
          </Grid>

          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle2" gutterBottom>Train the LoRA</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {refCount > 0
              ? `${refCount} training-set image${refCount > 1 ? 's' : ''} (uploads + previously trained)${approvedCount > 0 ? ` + ${approvedCount} approved generated pending promotion` : ''}.`
              : approvedCount > 0
                ? `No uploads yet — will train on ${approvedCount} approved generated sample${approvedCount > 1 ? 's' : ''}; they promote here after train success.`
                : 'Drop in reference images above (or generate + approve some in the Generate Character tab) to enable training.'}
            {' '}Training incorporates the current set. Add new data (outfits, details) later and Train again to amend/evolve.
          </Typography>
          {subject.caption_coverage && subject.caption_coverage.images > 0 && (
            <Typography variant="caption" color={
              (subject.caption_coverage.bare_captions || 0) > (subject.caption_coverage.images / 2)
                ? 'warning.main' : 'text.secondary'
            } sx={{ display: 'block', mb: 1 }}>
              Captions: {subject.caption_coverage.rich_captions || 0} rich / {subject.caption_coverage.bare_captions || 0} bare
              of {subject.caption_coverage.images} images
              {(subject.caption_coverage.bare_captions || 0) > (subject.caption_coverage.images / 2)
                ? ' — train will VLM-caption bare uploads first'
                : ''}
            </Typography>
          )}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Button variant="contained" color="secondary" onClick={handleTrain}
                    disabled={busy || training || !trainable}>
              {training ? 'Training…' : hasPendingAmend ? 'Train LoRA (catch-up/amend)' : 'Train LoRA'}
            </Button>
            {(training || trainingJob) && (
              <Button variant="outlined" color="error" onClick={handleCancelTrain} disabled={busy}>
                Cancel training
              </Button>
            )}
          </Box>
          {subject.training_status && subject.training_status !== 'untrained' && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
              status: {trainingStatusLabel} {lastTrainedCount > 0 ? `(last real verified trained on ${lastTrainedCount} images)` : ''}
            </Typography>
          )}

          {/* Live training progress from unified job system. This lets the frontend
              know the GPU is actively training (see nvitop) even while the global
              celery health endpoint returns 503/busy (worker monopolized by the
              long-running LoRA task) and the subject DB status is only updated at the end.
              The job progress is pushed via sockets/unified context. */}
          {(training || trainingJob) && (
            <Box sx={{ mt: 2, maxWidth: 480 }}>
              <LinearProgress
                variant={trainingJob?.progress != null ? "determinate" : "indeterminate"}
                value={trainingJob?.progress || 0}
              />
              <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                {trainingJob?.message || 'LoRA training running — GPU active'}
                {trainingJob?.id && ` · job ${String(trainingJob.id).slice(0, 12)}...`}
                {' · (health/celery may report busy/503; this is expected for long GPU tasks)'}
              </Typography>
            </Box>
          )}

          {/* Enhanced error + recovery for real hardware (a 16 GB consumer GPU etc.)
              Matches user's "no simulations" requirement and the exact failure mode
              seen when the venv-torch cannot see CUDA despite a working GPU. */}
          {subject.training_status === 'failed' && subject.training_error && (
            <Alert severity="error" sx={{ mt: 1.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Training failed
              </Typography>
              <Typography
                variant="body2"
                component="pre"
                sx={{ 
                  whiteSpace: 'pre-wrap', 
                  fontFamily: 'monospace', 
                  fontSize: '0.8rem',
                  mt: 0.5,
                  mb: 0.5
                }}
              >
                {subject.training_error}
              </Typography>
              <Typography variant="caption">
                Fix the problem, then click “Train LoRA” again. New training data (additional outfits etc.) will be incorporated on the next run (amend).
              </Typography>
            </Alert>
          )}
        </Box>
      )}

      {/* ── Generate Character ─────────────────────────────────────────────── */}
      {tab === 2 && (
        <Box>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 2 }}>
            <Button variant="outlined" startIcon={<AutoAwesomeIcon />} onClick={handlePlan} disabled={planning || busy}>
              {planning ? 'Planning…' : subject.bible ? 'Re-plan sheet' : 'Plan reference sheet'}
            </Button>
            <Button variant="contained" startIcon={<AutoAwesomeIcon />} onClick={handleGenerate}
                    disabled={busy || planning || generateActive || sheetSamples.some(isPending)
                      || !(sheetSamples.length || subject.lora_path || subject.bible || samples.length)}>
              {subject.lora_path ? 'Generate with trained LoRA' : 'Generate base sheet (no LoRA)'}
            </Button>
            {(generateActive || sheetSamples.some(isPending)) && (
              <Button variant="outlined" color="error" onClick={handleCancelGenerate} disabled={busy}>
                Cancel generation
              </Button>
            )}
            <Button size="small" onClick={approveAllDone} disabled={!sheetSamples.some((s) => s.status === 'done')}>
              Approve all generated
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Chip label={`${approvedCount}/${total} approved`} size="small"
                  color={approvedCount > 0 ? 'success' : 'default'} variant="outlined" />
          </Box>

          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
            {subject.lora_path ? (
              <>
                <strong>Generate with trained LoRA</strong> — trigger + shot variation only
                (no full bible dump); identity from the adapter
                (<code>{subject.trigger_word || subject.name}</code>
                {subject.base_model_name ? <> · {subject.base_model_name}</> : null}
                ). <strong>Re-plan sheet</strong> refreshes angles only (keeps vision bible when
                refs exist). <strong>Train LoRA</strong> trains weights — it does not rewrite the bible.
                Use Overview → <strong>Sync identity from photos</strong> to rescan appearance.
              </>
            ) : (
              <>
                <strong>Generate base sheet (no LoRA)</strong> — uses the identity bible in prompts.
                Sync identity from photos on Overview first if you uploaded refs.
                <strong> Train LoRA</strong> trains the adapter; it does not invent or rewrite the bible.
                <strong> Re-plan sheet</strong> refreshes shot angles (keeps vision bible when refs exist).
              </>
            )}
          </Typography>
          {(subject.ref_image_paths || []).length > 0 && !subject.bible_vision_grounded && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              {subject.bible_manual_override
                ? <>Manual bible edit — go to Overview and <strong>Sync identity from photos</strong> before training or generating.</>
                : <>Bible may not match your photos — go to Overview and click{' '}
                  <strong>Sync identity from photos</strong> before training or generating.</>}
            </Alert>
          )}
          {subject.smoke_identity?.ok && (
            <Alert severity={Number(subject.smoke_identity.score) >= 0.75 ? 'success' : 'warning'} sx={{ mb: 2 }}>
              Post-train smoke identity score:{' '}
              {subject.smoke_identity.score != null
                ? Number(subject.smoke_identity.score).toFixed(2)
                : 'n/a'}{' '}
              ({subject.smoke_identity.method || 'hist'}
              {subject.smoke_identity.family ? ` · ${subject.smoke_identity.family}` : ''})
            </Alert>
          )}

          {/* Progress + honest status — so a planned-but-not-generated sheet doesn't
              read as "stuck", and a real render shows how far along it is. */}
          {total > 0 && (
            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography variant="caption" color="text.secondary">
                  {active
                    ? `Generating… ${doneCount}/${total} done`
                    : samples.some((s) => s.status === 'cancelled') && doneCount < total
                      ? `Cancelled — ${doneCount}/${total} kept${failedCount ? ` · ${failedCount} failed` : ''}.`
                    : doneCount === 0
                      ? `Planned ${total} shots — click "Generate images" to render them.`
                      : doneCount < total
                        ? `${doneCount}/${total} rendered${failedCount ? ` · ${failedCount} failed` : ''} — click "Generate images" to finish.`
                        : `All ${total} rendered.`}
                </Typography>
                <Typography variant="caption" color="text.secondary">{doneCount}/{total}</Typography>
              </Box>
              <LinearProgress
                variant={active && doneCount === 0 ? 'indeterminate' : 'determinate'}
                value={total ? (doneCount / total) * 100 : 0}
              />
            </Box>
          )}

          {!sheetSamples.length ? (
            <Typography color="text.secondary" sx={{ p: 4, textAlign: 'center' }}>
              {samples.some((s) => s.promoted_to_training)
                ? <>All generated keepers are on the <b>Training Data</b> tab (promoted after train). Plan or generate a new batch here when you want more variety.</>
                : <>No reference sheet yet. Click <b>Plan reference sheet</b> to plan shot angles (identity from photos / LoRA). The planner will write a
                  frozen identity bible + ~32 varied shot prompts, then <b>Generate images</b>.</>}
            </Typography>
          ) : (
            <Box sx={{ maxHeight: '60vh', overflowY: 'auto', pr: 1, mx: -0.5, px: 0.5 }}>
            <Grid container spacing={2}>
              {sheetSamples.map((s, idx) => {
                const trainSt = sampleTrainStatus(s, subject);
                return (
                <Grid item xs={6} sm={4} md={3} lg={2} key={s.id}>
                  <Card variant="outlined">
                    {s.image_url ? (
                      <Box sx={{ position: 'relative' }}>
                        <CardMedia component="img" height="160" image={s.image_url} alt={s.angle || `sample ${s.index}`}
                                   onClick={() => setLightboxIdx(idx)}
                                   sx={{ objectFit: 'cover', cursor: 'zoom-in' }} />
                        {trainSt && (
                          <Box sx={{ position: 'absolute', left: 6, bottom: 6 }}>
                            <TrainStatusChip status={trainSt} />
                          </Box>
                        )}
                      </Box>
                    ) : (
                      <Box sx={{ height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                 bgcolor: 'action.hover' }}>
                        {s.status === 'generating' ? <CircularProgress size={22} />
                          : s.status === 'pending' ? <Typography variant="caption" color="text.disabled">queued</Typography>
                          : s.status === 'failed' ? <Typography variant="caption" color="error">failed</Typography>
                          : <Typography variant="caption" color="text.secondary">{s.status}</Typography>}
                      </Box>
                    )}
                    <CardContent sx={{ py: 1 }}>
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 0.5 }}>
                        <Tooltip title={s.image_prompt || ''}>
                          <Typography variant="caption" noWrap>{s.angle || `Shot ${s.index + 1}`}</Typography>
                        </Tooltip>
                        <StatusChip status={s.status} />
                      </Box>
                    </CardContent>
                    <CardActions sx={{ pt: 0, justifyContent: 'space-between' }}>
                      <Tooltip title={s.approved ? 'Approved — click to un-approve' : 'Approve this sample'}>
                        <IconButton size="small" color={s.approved ? 'success' : 'default'}
                                    onClick={() => toggleApprove(s)} aria-label="toggle approval">
                          {s.approved ? <CheckCircleIcon fontSize="small" /> : <RadioButtonUncheckedIcon fontSize="small" />}
                        </IconButton>
                      </Tooltip>
                      <Box>
                        <Tooltip title="Regenerate this sample">
                          <span>
                            <IconButton size="small" onClick={() => { setRegenTarget(s); setRegenPrompt(s.image_prompt || ''); }}
                                        disabled={isPending(s)} aria-label="regenerate sample">
                              <RefreshIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Tooltip title="Remove this generation">
                          <IconButton size="small" onClick={() => handleDeleteSample(s)} aria-label="remove sample">
                            <CloseIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Box>
                    </CardActions>
                  </Card>
                </Grid>
                );
              })}
            </Grid>
            </Box>
          )}

          <Divider sx={{ my: 3 }} />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Button variant="contained" color="secondary" onClick={handleTrain}
                    disabled={busy || training || approvedCount === 0}>
              {training ? 'Training…' : hasPendingAmend ? 'Train LoRA (catch-up/amend)' : 'Train LoRA'}
            </Button>
            <Typography variant="caption" color="text.secondary">
              {approvedCount === 0
                ? 'Approve at least one sample to train from this sheet (or train from Training Data if you already have refs).'
                : `Trains on your ${refCount} training-set image${refCount === 1 ? '' : 's'} + ${approvedCount} approved sample${approvedCount > 1 ? 's' : ''}. On success, approved gens promote to Training Data. ~hours on a 16GB GPU; runs in the background.`}
            </Typography>
          </Box>
        </Box>
      )}

      {/* ── Versions ───────────────────────────────────────────────────────── */}
      {tab === 3 && (
        <Box sx={{ maxWidth: 560 }}>
          <Typography variant="subtitle2" gutterBottom>Trained LoRA</Typography>
          <Typography variant="body2">Status: <b>{subject.training_status}</b></Typography>
          <Typography variant="body2">Version: {subject.lora_version || 0}</Typography>
          <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
            Path: {subject.lora_path || <em>none yet</em>}
          </Typography>
          {subject.training_status === 'trained' && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" color="text.secondary">Use this character in:</Typography>
              <Box sx={{ display: 'flex', gap: 2, mt: 0.5 }}>
                <Link component="button" onClick={() => navigate(`/images?character=${subject.id}`)}>Images →</Link>
                <Link component="button" onClick={() => navigate('/music-video')}>Music Video →</Link>
                <Link component="button" onClick={() => navigate('/video')}>Video Gen →</Link>
              </Box>
            </Box>
          )}
        </Box>
      )}

      {/* Regenerate dialog */}
      <Dialog open={!!regenTarget} onClose={() => setRegenTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Regenerate sample</DialogTitle>
        <DialogContent>
          <TextField label="Prompt override (optional)" value={regenPrompt}
                     onChange={(e) => setRegenPrompt(e.target.value)} fullWidth multiline rows={4} sx={{ mt: 1 }}
                     helperText="This is the frozen shot prompt; Sync identity / Re-plan updates it. Leave as-is to re-roll the seed." />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRegenTarget(null)}>Cancel</Button>
          <Button variant="contained" onClick={submitRegen}>Regenerate</Button>
        </DialogActions>
      </Dialog>

      {/* Lightbox — click a thumbnail to enlarge; ←/→ or buttons to step, Esc to close.
          Indices are into sheetSamples (non-promoted Generate Character set). */}
      {lightboxIdx !== null && sheetSamples[lightboxIdx] && (
        <Dialog open onClose={() => setLightboxIdx(null)} maxWidth="lg" fullWidth
                PaperProps={{ sx: { bgcolor: 'grey.900' } }}>
          <DialogContent sx={{ position: 'relative', p: 0, display: 'flex', alignItems: 'center',
                                justifyContent: 'center', minHeight: '70vh', bgcolor: 'black' }}>
            {sheetSamples[lightboxIdx].image_url ? (
              <Box component="img" src={sheetSamples[lightboxIdx].image_url}
                   alt={sheetSamples[lightboxIdx].angle || `sample ${lightboxIdx + 1}`}
                   sx={{ maxWidth: '100%', maxHeight: '82vh', objectFit: 'contain', display: 'block' }} />
            ) : (
              <Typography color="grey.500">{sheetSamples[lightboxIdx].status}</Typography>
            )}
            <IconButton onClick={() => setLightboxIdx((i) => Math.max(0, i - 1))} disabled={lightboxIdx === 0}
                        sx={{ position: 'absolute', left: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label="previous">
              <ChevronLeftIcon />
            </IconButton>
            <IconButton onClick={() => setLightboxIdx((i) => Math.min(sheetSamples.length - 1, i + 1))}
                        disabled={lightboxIdx === sheetSamples.length - 1}
                        sx={{ position: 'absolute', right: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label="next">
              <ChevronRightIcon />
            </IconButton>
            <IconButton onClick={() => setLightboxIdx(null)}
                        sx={{ position: 'absolute', top: 8, right: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label="close">
              <CloseIcon />
            </IconButton>
          </DialogContent>
          <DialogActions sx={{ justifyContent: 'space-between', bgcolor: 'grey.900' }}>
            <Tooltip title={sheetSamples[lightboxIdx].image_prompt || ''}>
              <Typography variant="caption" color="grey.400" noWrap sx={{ px: 1, maxWidth: '70%' }}>
                {lightboxIdx + 1}/{sheetSamples.length} · {sheetSamples[lightboxIdx].angle || `Shot ${lightboxIdx + 1}`}
                {sampleTrainStatus(sheetSamples[lightboxIdx], subject)
                  ? ` · ${sampleTrainStatus(sheetSamples[lightboxIdx], subject).label}`
                  : ''}
              </Typography>
            </Tooltip>
            <Box>
              <Button size="small" startIcon={<CloseIcon />} color="inherit"
                      onClick={() => handleDeleteSample(sheetSamples[lightboxIdx])}>
                Remove
              </Button>
              <Button size="small" startIcon={sheetSamples[lightboxIdx].approved ? <CheckCircleIcon /> : <RadioButtonUncheckedIcon />}
                      onClick={() => toggleApprove(sheetSamples[lightboxIdx])}
                      color={sheetSamples[lightboxIdx].approved ? 'success' : 'inherit'}>
                {sheetSamples[lightboxIdx].approved ? 'Approved' : 'Approve'}
              </Button>
            </Box>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
};

export default CastMemberPage;
