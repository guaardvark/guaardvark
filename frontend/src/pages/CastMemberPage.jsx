import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { formatUiError } from "../utils/uiError";
import { useParams, useNavigate } from 'react-router-dom';
import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';
import {
  Box, Tabs, Tab, Typography, Button, TextField, Card, CardMedia, CardContent,
  CardActions, Chip, CircularProgress, Alert, Dialog, DialogTitle, DialogContent,
  DialogActions, Grid, IconButton, Tooltip, Divider, Link, LinearProgress, MenuItem,
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
  deleteSample, trainSubject, cancelTrainSubject, importSubjectLora, makeDefaultSubjectLora,
} from '../api/productionService';
import { SubjectThumb } from '../components/filmcrew/CastLibraryView';
import DragDropImageUpload from '../components/filmcrew/DragDropImageUpload';
import CastVoicePicker from '../components/filmcrew/CastVoicePicker';
import ChangedElsewhereNotice from '../components/common/ChangedElsewhereNotice';
import EntityContextMenu from '../components/common/EntityContextMenu';
import useContextMenu from '../hooks/useContextMenu';
import useServerSyncedForm from '../hooks/useServerSyncedForm';
import useUnsavedChangesGuard from '../hooks/useUnsavedChangesGuard';
import { translate, useUiTranslation, Trans } from '../i18n';

const POLL_MS = 5000;
const POLL_CAP = 180; // 15 min safety cap on a generate/train poll loop
// Follow-up poll for work that outlives POLL_CAP (training runs for hours) or
// whose terminal event beat the worker's DB commit. Runs only while the member
// still reads as training or has queued samples.
const SETTLE_POLL_MS = 30000;

const TERMINAL_JOB_STATUSES = ['complete', 'end', 'error', 'cancelled', 'failed'];

const EMPTY_OVERVIEW = { name: '', description: '', trigger_word: '', voice_id: '', bible: '' };

// Matches the Import dialog's two options; falls back to the raw id for any
// base the registry adds later.
const BASE_MODEL_LABELS = {
  'zimage-turbo': 'Z-Image Turbo',
  'flux-dev': 'FLUX.1 Dev',
};

const overviewFromSubject = (subject) => ({
  name: subject.name || '',
  description: subject.description || '',
  trigger_word: subject.trigger_word || '',
  voice_id: subject.voice_id || '',
  bible: subject.bible || '',
});

const overviewLabels = () => ({
  name: translate('cast.fields.name'),
  trigger_word: translate('cast.fields.triggerWord'),
  voice_id: translate('cast.fields.voice'),
  description: translate('cast.fields.description'),
  bible: translate('cast.fields.bible'),
});

const trainingLabels = () => ({
  base_model_id: translate('cast.fields.trainBase'),
  resolution: translate('cast.fields.resolution'),
  rank: translate('cast.fields.rank'),
  alpha: translate('cast.fields.alpha'),
  learning_rate: translate('cast.fields.learningRate'),
  steps: translate('cast.fields.steps'),
});

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
  if (sample.promoted_to_training) return { label: translate('cast.trainStatus.trained'), color: 'success' };
  if (!sample.approved || sample.status !== 'done') return null;
  if (subject?.training_status === 'training') return { label: translate('cast.trainStatus.training'), color: 'warning' };
  return { label: translate('cast.trainStatus.approved'), color: 'info' };
};

/** Training-lifecycle chip for a durable ref path on Training Data. */
const refTrainStatus = (path, subject) => {
  const last = subject?.last_trained_image_paths || [];
  if (path && last.includes(path)) return { label: translate('cast.trainStatus.trained'), color: 'success' };
  if (subject?.training_status === 'training') return { label: translate('cast.trainStatus.training'), color: 'warning' };
  return { label: translate('cast.trainStatus.ref'), color: 'default' };
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
  const { t } = useUiTranslation();
  const { subjectId } = useParams();
  const navigate = useNavigate();

  const [subject, setSubject] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState(0);

  // Overview edit form. Server copies merge into it field by field and never
  // overwrite an unsaved edit (hooks/useServerSyncedForm).
  const overview = useServerSyncedForm(EMPTY_OVERVIEW);
  const form = overview.values;
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
  const sampleMenu = useContextMenu();

  // Import LoRA (externally-trained checkpoint attached to this Subject)
  const [importOpen, setImportOpen] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importBaseModel, setImportBaseModel] = useState('zimage-turbo');
  const [importTrigger, setImportTrigger] = useState('');
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState(null);

  const submitImportLora = async () => {
    if (!importFile || !importTrigger.trim()) return;
    setImportBusy(true);
    setImportError(null);
    try {
      const res = await importSubjectLora(subject.id, {
        file: importFile,
        baseModelId: importBaseModel,
        triggerWord: importTrigger.trim(),
      });
      setSubject(res.subject);
      setImportOpen(false);
      setImportFile(null);
      setImportTrigger('');
    } catch (e) {
      setImportError(formatUiError(e.response?.data?.error) || t('cast.errors.importFailed'));
    } finally {
      setImportBusy(false);
    }
  };

  // Which base's "Make default" button is mid-request, so only that one row
  // shows a spinner; null when none is.
  const [makingDefaultBase, setMakingDefaultBase] = useState(null);
  const [makeDefaultError, setMakeDefaultError] = useState(null);

  const submitMakeDefault = async (baseModelId) => {
    setMakingDefaultBase(baseModelId);
    setMakeDefaultError(null);
    try {
      const res = await makeDefaultSubjectLora(subject.id, baseModelId);
      setSubject(res.subject);
    } catch (e) {
      setMakeDefaultError(formatUiError(e.response?.data?.error) || t('cast.errors.makeDefaultFailed'));
    } finally {
      setMakingDefaultBase(null);
    }
  };

  // subject.lora_versions is one row per LoRA this member holds, newest
  // version of each base first (see _serialize in cast_library_api.py).
  // Grouped here into one card per base for the Versions tab.
  const loraBaseGroups = useMemo(() => {
    const rows = subject?.lora_versions || [];
    const byBase = new Map();
    for (const row of rows) {
      if (!byBase.has(row.base_model_id)) byBase.set(row.base_model_id, []);
      byBase.get(row.base_model_id).push(row);
    }
    const groups = Array.from(byBase.entries()).map(([base, baseRows]) => ({
      base,
      history: baseRows,
      current: baseRows[0],
      isDefault: baseRows[0].lora_path === subject?.lora_path,
    }));
    // A default LoRA that predates this table has no row of its own.
    if (subject?.lora_path && !groups.some((g) => g.isDefault)) {
      groups.unshift({
        base: subject.base_model_id || 'unknown',
        history: [],
        current: {
          version: subject.lora_version || 1,
          source: subject.training_settings_json?.imported ? 'imported' : 'trained',
          trigger_word: subject.trigger_word,
          lora_path: subject.lora_path,
        },
        isDefault: true,
      });
    }
    return groups.sort((a, b) => (b.isDefault - a.isDefault) || a.base.localeCompare(b.base));
  }, [subject]);

  // Local state to surface training progress from unified jobs (so frontend "knows"
  // when GPU is crunching on long LoRA train, even if subject poll lags or health/celery 503s).
  const [trainingJob, setTrainingJob] = useState(null);
  // Training hyperparameters (Training Data tab): same merge rules as Overview.
  const trainingForm = useServerSyncedForm(DEFAULT_TRAINING_SETTINGS);
  const trainingSettings = trainingForm.values;
  const [savingTrainingSettings, setSavingTrainingSettings] = useState(false);
  const [trainingSettingsSaved, setTrainingSettingsSaved] = useState(false);

  // Responses can land out of order (a slow poll after a save). Each subject
  // fetch takes a number; a response older than the newest one applied, or
  // issued before the last write this page made, is dropped so it cannot
  // put pre-save values back.
  const fetchSeq = useRef(0);
  const acceptFrom = useRef(0);
  const ignoreEarlierFetches = () => { acceptFrom.current = fetchSeq.current + 1; };

  const { reset: resetOverview, syncFromServer: syncOverview } = overview;
  const { reset: resetTraining, syncFromServer: syncTraining } = trainingForm;

  // Fetch the member (with its samples when the detail endpoint answers) and
  // fold it into the page. `reset` starts both forms over (first load or a
  // different member); otherwise they merge. `overwrite` passes through to the
  // Overview merge for fields an explicit action just rewrote on the server.
  // Resolves to { subject, samples } — samples null when the fallback path
  // was used — or { stale: true } when the response was dropped.
  const refreshSubject = useCallback(async ({ reset = false, overwrite } = {}) => {
    const seq = ++fetchSeq.current;
    let s;
    let rows = null;
    try {
      const detail = await getCastSubjectDetail(subjectId, { includeSamples: true });
      s = detail?.subject || detail;
      rows = detail?.samples || null;
    } catch (e) {
      s = await getCastSubject(subjectId);
    }
    if (seq < acceptFrom.current) return { stale: true };
    acceptFrom.current = seq;
    setSubject(s);
    if (s) {
      if (reset) {
        resetOverview(overviewFromSubject(s));
        resetTraining(trainingSettingsFromSubject(s));
      } else {
        syncOverview(overviewFromSubject(s), { overwrite });
        syncTraining(trainingSettingsFromSubject(s));
      }
    }
    if (rows) setSamples(rows);
    return { subject: s, samples: rows };
  }, [subjectId, resetOverview, resetTraining, syncOverview, syncTraining]);

  const loadSubject = useCallback(
    async (options) => (await refreshSubject(options)).subject,
    [refreshSubject],
  );

  const loadSamples = useCallback(async () => {
    const data = await listSamples(subjectId);
    setSamples(data.samples || []);
    return data.samples || [];
  }, [subjectId]);

  // Subject and samples; one request when the detail endpoint includes samples.
  const refreshAll = useCallback(async (options) => {
    const result = await refreshSubject(options);
    if (result.stale) return result;
    const rows = result.samples || (await loadSamples());
    return { ...result, samples: rows };
  }, [refreshSubject, loadSamples]);

  useEffect(() => {
    let alive = true;
    ignoreEarlierFetches();
    (async () => {
      setLoading(true);
      try {
        const { subject: s, samples: rows, stale } = await refreshAll({ reset: true });
        if (!alive || stale) return;
        if (!s) setError(t('cast.errors.notFound'));
        // If a generation/regeneration is already in flight when we land on the
        // page (e.g. after a refresh), auto-resume polling so finished images
        // appear without a manual reload.
        if (rows.some(isPending)) {
          setGenerateActive(true);
        }
        if (rows.some(isPending) || s?.training_status === 'training') {
          pollCount.current = 0;
          setPolling(true);
        }
      } catch (e) {
        if (alive) setError(t('cast.errors.loadFailed'));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [refreshAll]);

  // Poll while a generate/regenerate/train job is running; auto-stop when the
  // work settles (no pending samples and not training) or the cap is hit.
  // The forms merge, so a poll never touches what the person is typing.
  useEffect(() => {
    if (!polling) return undefined;
    const id = setInterval(async () => {
      pollCount.current += 1;
      try {
        const { subject: s, samples: rows, stale } = await refreshAll();
        if (stale) return;
        const stillGenerating = rows.some(isPending);
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
  }, [polling, refreshAll]);

  const startPolling = () => { pollCount.current = 0; setPolling(true); };

  // React to unified progress jobs for *this* subject (key for batch queue of many cast trainings).
  // The backend now creates processes with additional_data.subject_id on dispatch.
  // This lets the page get live updates / terminal notifications without sole reliance on 5s poll,
  // enabling long-running day+ batch jobs to surface correctly.
  // A finished job stays in activeProcesses for a few seconds, and this effect
  // re-runs on every progress update of any job, so each job's end is acted on
  // once (settledJobs). Otherwise it reloads repeatedly and can switch off a
  // poll just started for the next job.
  const { activeProcesses } = useUnifiedProgress();
  const settledJobs = useRef(new Set());
  useEffect(() => {
    if (!subjectId) return;
    const procs = Array.from(activeProcesses.values());
    const isTerminal = (p) => TERMINAL_JOB_STATUSES.includes((p.status || '').toLowerCase());
    const firstTimeSettled = (p) => {
      const key = p.job_id || p.id;
      if (key == null) return true;
      if (settledJobs.current.has(key)) return false;
      settledJobs.current.add(key);
      return true;
    };

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

    let settled = false;
    if (trainingMatch) {
      if (isTerminal(trainingMatch)) {
        setTrainingJob(null);
        if (firstTimeSettled(trainingMatch)) settled = true;
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

    if (subjectMatch && isTerminal(subjectMatch)) {
      setGenerateActive(false);
      if (firstTimeSettled(subjectMatch)) settled = true;
    }

    if (settled) {
      refreshAll().catch(() => {});
      setPolling(false);
    }
  }, [activeProcesses, subjectId, refreshAll]);

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

  // Slow follow-up poll once the fast poll is off but the member has not
  // settled: training outlives POLL_CAP, and a terminal event can arrive
  // before the worker's DB commit, leaving 'training' on screen. The safety
  // net above clears the spinner once this sees the final status. An idle
  // member is not polled.
  const settling = subject?.training_status === 'training' || samples.some(isPending) || !!trainingJob;
  useEffect(() => {
    if (!subjectId || polling || !settling) return undefined;
    const id = setInterval(() => { refreshAll().catch(() => {}); }, SETTLE_POLL_MS);
    return () => clearInterval(id);
  }, [subjectId, polling, settling, refreshAll]);

  // Returning to the tab picks up changes made elsewhere in the meantime
  // (another tab, a Film Crew run); the merge keeps any edit in progress.
  useEffect(() => {
    if (!subjectId) return undefined;
    const onVisible = () => {
      if (document.visibilityState === 'visible') refreshAll().catch(() => {});
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [subjectId, refreshAll]);

  useUnsavedChangesGuard(overview.isDirty || trainingForm.isDirty);

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

  const editOverview = (key, value) => {
    setSavedNote(false);
    overview.setField(key, value);
  };

  const editTraining = (key, value) => {
    setTrainingSettingsSaved(false);
    trainingForm.setField(key, value);
  };

  // Sends only the fields the person changed, so a save cannot write this
  // page's older copy of an untouched field over a newer one saved elsewhere.
  const handleSave = async () => {
    const changes = overview.changes;
    if (!Object.keys(changes).length) return;
    setSaving(true); setError(null);
    try {
      await updateCastSubject(subjectId, changes);
      ignoreEarlierFetches();
      overview.markSaved(changes);
      await loadSubject();
      setSavedNote(true);
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleRebuildBible = async () => {
    const refs = subject?.ref_image_paths || [];
    if (!refs.length) {
      setError(t('cast.errors.noRefsForSync'));
      return;
    }
    if (overview.dirty.includes('bible') && !window.confirm(
      t('cast.overview.syncConfirm'),
    )) {
      return;
    }
    setRebuildingBible(true); setError(null);
    try {
      await rebuildBibleFromRefs(subjectId, {
        refresh_captions: true,
        refresh_sample_prompts: true,
      });
      ignoreEarlierFetches();
      // The bible is what the person asked to rewrite; the sync may also set a
      // new trigger word, which merges like any other server change. Samples
      // come back too: their prompts were recomposed.
      await refreshAll({ overwrite: ['bible'] });
      setSavedNote(true);
    } catch (e) {
      setError(
        e.response?.data?.message
        || formatUiError(e.response?.data?.error)
        || t('cast.errors.syncFailed'),
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
      const ok = window.confirm(t('cast.generate.replanConfirm', { count: approvedCount }));
      if (!ok) return;
    }
    setPlanning(true); setError(null);
    try {
      await planCharacter(subjectId);
      ignoreEarlierFetches();
      // Reload the member and the full sample list: the plan may rewrite the
      // bible and trigger word (merged into Overview without touching edits),
      // and its response only carries the new sheet rows, not the promoted
      // Training Data keepers.
      await refreshAll();
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.planFailed'));
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
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.generateFailed'));
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
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.regenerateFailed'));
    }
  };

  const toggleApprove = async (sample) => {
    try {
      await approveSamples(subjectId, [sample.id], !sample.approved);
      await loadSamples();
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.approvalFailed'));
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
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.deleteSampleFailed'));
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
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.approveAllFailed'));
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

  // The backend stores training settings as one object, so the whole set is
  // sent; untouched fields already hold the latest server values.
  const handleSaveTrainingSettings = async () => {
    const sent = trainingForm.values;
    setSavingTrainingSettings(true);
    setError(null);
    try {
      await updateCastSubject(subjectId, { training_settings: buildTrainingSettingsPayload() });
      ignoreEarlierFetches();
      trainingForm.markSaved(sent);
      await loadSubject();
      setTrainingSettingsSaved(true);
    } catch (e) {
      setError(formatUiError(e.response?.data?.error) || t('cast.errors.saveTrainingFailed'));
    } finally {
      setSavingTrainingSettings(false);
    }
  };

  const handleTrainClick = () => {
    if (subject?.training_settings_json?.imported) {
      const ok = window.confirm(t('cast.trainingData.replaceImportedConfirm'));
      if (!ok) return;
    }
    handleTrain();
  };

  const handleTrain = async () => {
    const sent = trainingForm.values;
    setBusy(true); setError(null);
    try {
      // Starting a run stores the settings it was started with.
      const res = await trainSubject(subjectId, { training_settings: buildTrainingSettingsPayload() });
      ignoreEarlierFetches();
      trainingForm.markSaved(sent);
      await loadSubject();
      if (res?.job_id) {
        setTrainingJob({ id: res.job_id, operation: 'train_lora' });
        console.debug('[CastMemberPage] train job started', res.job_id);
      }
      startPolling();
    } catch (e) {
      const data = e.response?.data;
      if (data?.error === 'train_base_not_ready') {
        setError(data.message || data.train_status_note || t('cast.errors.trainBaseNotReady'));
      } else {
        setError(
          (typeof data?.error === 'string' ? data.error : null)
          || data?.message
          || (e.response?.status === 409 ? t('cast.errors.alreadyTraining') : t('cast.errors.trainFailed')),
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
      setError(e.response?.data?.reason || formatUiError(e.response?.data?.error) || t('cast.errors.cancelTrainFailed'));
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
        setError(data?.reason || data?.error || t('cast.errors.cancelGenerateFailed'));
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
        <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/cast')}>{t('cast.page.backToStudio')}</Button>
        <Alert severity="error" sx={{ mt: 2 }}>{error || t('cast.errors.notFound')}</Alert>
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
    ? t('cast.trainingData.trainedPendingAmend')
    : subject.training_status;

  // Approved generated samples still awaiting promotion — shown on Training Data
  // next to refs so both tabs reflect the live training pool.
  const pendingPromoteThumbs = approvedPending
    .filter((s) => s.image_url)
    .map((s) => ({
      key: `sample-${s.id}`,
      src: s.image_url,
      name: s.angle || t('cast.generate.sampleName', { number: s.index + 1 }),
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
        <IconButton onClick={() => navigate('/cast')} aria-label={t('cast.page.backAria')}><ArrowBackIcon /></IconButton>
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
            t('cast.page.baseTooltip')
          }
        >
          <Chip
            label={subject.base_model_name || subject.base_model_id || t('cast.page.baseUnknown')}
            size="small"
            color={subject.train_ready === false ? 'warning' : 'primary'}
            variant="outlined"
          />
        </Tooltip>
        {polling && <CircularProgress size={18} sx={{ ml: 1 }} />}
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>{error}</Alert>}

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label={t('cast.tabs.overview')} />
        <Tab label={t('cast.tabs.trainingData')} />
        <Tab label={t('cast.tabs.generate')} />
        <Tab label={t('cast.tabs.versions')} />
      </Tabs>

      {/* ── Overview ───────────────────────────────────────────────────────── */}
      {tab === 0 && (
        <Grid container spacing={3}>
          <Grid item xs={12} sm={4} md={3}>
            <Box sx={{ borderRadius: 1, overflow: 'hidden' }}><SubjectThumb subject={subject} /></Box>
          </Grid>
          <Grid item xs={12} sm={8} md={9}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, maxWidth: 560 }}>
              <ChangedElsewhereNotice
                conflicts={overview.conflicts}
                labels={overviewLabels()}
                onReload={() => overview.resolve('server')}
                onKeep={() => overview.resolve('mine')}
              />
              <TextField label={t('cast.fields.name')} value={form.name}
                         onChange={(e) => editOverview('name', e.target.value)} fullWidth />
              {subject.kind === 'character' && (
                <TextField label={t('cast.overview.triggerLabel')} value={form.trigger_word}
                           onChange={(e) => editOverview('trigger_word', e.target.value)} fullWidth
                           helperText={t('cast.overview.triggerHelp')} />
              )}
              <CastVoicePicker
                value={form.voice_id}
                onChange={(voiceId) => editOverview('voice_id', voiceId)}
                disabled={saving}
              />
              <TextField
                label={t('cast.fields.description')}
                value={form.description}
                multiline
                rows={3}
                onChange={(e) => editOverview('description', e.target.value)}
                fullWidth
                helperText={t('cast.overview.descriptionHelp')}
              />
              <Divider sx={{ my: 1 }} />
              <Typography variant="overline" color="text.secondary">{t('cast.fields.bible')}</Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                {t('cast.overview.bibleHelp')}
              </Typography>
              {(subject.ref_image_paths || []).length > 0 && !subject.bible_vision_grounded && (
                <Alert severity="warning" sx={{ mb: 1 }}>
                  {subject.bible_manual_override
                    ? <Trans i18nKey="cast.overview.manualEditWarning" components={{ bold: <strong /> }} />
                    : <Trans i18nKey="cast.overview.mismatchWarning" components={{ bold: <strong /> }} />}
                </Alert>
              )}
              <TextField
                label={t('cast.fields.bible')}
                value={form.bible}
                onChange={(e) => editOverview('bible', e.target.value)}
                multiline
                rows={5}
                fullWidth
                helperText={
                  subject.bible_vision_grounded
                    ? t('cast.overview.bibleGrounded')
                    : subject.bible_manual_override
                      ? t('cast.overview.bibleManual')
                      : t('cast.overview.bibleNotSynced')
                }
              />
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
                <Button variant="contained" onClick={handleSave}
                        disabled={saving || !form.name || !overview.isDirty}>
                  {saving ? t('cast.common.saving') : t('cast.overview.save')}
                </Button>
                <Button
                  variant="outlined"
                  onClick={handleRebuildBible}
                  disabled={rebuildingBible || busy || !(subject.ref_image_paths || []).length}
                >
                  {rebuildingBible ? t('cast.overview.scanning') : t('cast.overview.sync')}
                </Button>
                {savedNote && !overview.isDirty && <Typography variant="caption" color="success.main">{t('cast.common.saved')}</Typography>}
                {overview.isDirty && !saving && (
                  <Typography variant="caption" color="text.secondary">{t('cast.common.unsaved')}</Typography>
                )}
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
            {t('cast.trainingData.intro')}
          </Typography>

          {/* Prominent display of last-trained count and amend status */}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', mb: 1.5 }}>
            <Chip 
              size="small" 
              label={t('cast.trainingData.currentPool', { count: currentPoolSize })} 
              variant="outlined" 
            />
            {lastTrainedCount > 0 && (
              <Chip 
                size="small" 
                label={t('cast.trainingData.lastTrained', { count: lastTrainedCount })} 
                color="success" 
                variant="outlined" 
              />
            )}
            {hasPendingAmend && (
              <Chip size="small" label={t('cast.trainingData.pendingAmend')} color="warning" />
            )}
            {lastTrainedCount === 0 && subject.training_status === 'trained' && (
              <Chip size="small" label={t('cast.trainingData.trainedNoList')} color="default" />
            )}
          </Box>
          <DragDropImageUpload
            subjectId={subject.id}
            existingPaths={subject.ref_image_paths || []}
            onUploaded={() => loadSubject()}
            helperText={t('cast.trainingData.uploadHelp')}
            extraItems={pendingPromoteThumbs}
            getPathStatus={(path) => refTrainStatus(path, subject)}
          />

          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle2" gutterBottom>{t('cast.trainingData.hyperparameters')}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
            {t('cast.trainingData.hyperparametersHelp')}
          </Typography>
          <Box sx={{ maxWidth: 720, mb: 1.5 }}>
            <ChangedElsewhereNotice
              conflicts={trainingForm.conflicts}
              labels={trainingLabels()}
              onReload={() => trainingForm.resolve('server')}
              onKeep={() => trainingForm.resolve('mine')}
            />
          </Box>
          <Grid container spacing={2} sx={{ mb: 2, maxWidth: 720 }}>
            <Grid item xs={12} sm={8}>
              <TextField
                select
                label={t('cast.fields.trainBase')}
                size="small"
                fullWidth
                value={trainingSettings.base_model_id || 'zimage-turbo'}
                onChange={(e) => editTraining('base_model_id', e.target.value)}
                SelectProps={{ native: true }}
                helperText={t('cast.trainingData.trainBaseHelp')}
              >
                <option value="zimage-turbo">{t('cast.trainingData.baseZimage')}</option>
                <option value="flux-dev">{t('cast.trainingData.baseFlux')}</option>
                <option value="sdxl-legacy">{t('cast.trainingData.baseSdxl')}</option>
              </TextField>
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label={t('cast.fields.resolution')} type="number" size="small" fullWidth
                value={trainingSettings.resolution}
                onChange={(e) => editTraining('resolution', e.target.value)}
                helperText={t('cast.trainingData.resolutionHelp')} />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label={t('cast.fields.rank')} type="number" size="small" fullWidth
                value={trainingSettings.rank}
                onChange={(e) => editTraining('rank', e.target.value)}
                helperText="4–64" />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label={t('cast.fields.alpha')} type="number" size="small" fullWidth
                value={trainingSettings.alpha}
                onChange={(e) => editTraining('alpha', e.target.value)}
                helperText={t('cast.trainingData.alphaHelp')} />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label={t('cast.fields.learningRate')} type="number" size="small" fullWidth
                value={trainingSettings.learning_rate}
                onChange={(e) => editTraining('learning_rate', e.target.value)}
                inputProps={{ step: '0.00001' }} />
            </Grid>
            <Grid item xs={6} sm={4}>
              <TextField label={t('cast.trainingData.stepsOptional')} type="number" size="small" fullWidth
                value={trainingSettings.steps}
                onChange={(e) => editTraining('steps', e.target.value)}
                helperText={t('cast.trainingData.stepsHelp')} />
            </Grid>
            <Grid item xs={12}>
              <Button variant="outlined" size="small" onClick={handleSaveTrainingSettings}
                disabled={savingTrainingSettings || training || !trainingForm.isDirty}>
                {savingTrainingSettings ? t('cast.common.saving') : t('cast.trainingData.saveSettings')}
              </Button>
              {trainingSettingsSaved && !trainingForm.isDirty && (
                <Typography variant="caption" color="success.main" sx={{ ml: 2 }}>{t('cast.common.saved')}</Typography>
              )}
              {trainingForm.isDirty && !savingTrainingSettings && (
                <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
                  {t('cast.trainingData.unsavedSettings')}
                </Typography>
              )}
            </Grid>
          </Grid>

          <Divider sx={{ my: 3 }} />
          <Typography variant="subtitle2" gutterBottom>{t('cast.trainingData.trainTitle')}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {refCount > 0
              ? approvedCount > 0
                ? t('cast.trainingData.poolPending', { images: t('cast.trainingSetImages', { count: refCount }), count: approvedCount })
                : t('cast.trainingData.pool', { images: t('cast.trainingSetImages', { count: refCount }) })
              : approvedCount > 0
                ? t('cast.trainingData.noUploads', { count: approvedCount })
                : t('cast.trainingData.nothingToTrain')}
            {' '}{t('cast.trainingData.incorporates')}
          </Typography>
          {subject.caption_coverage && subject.caption_coverage.images > 0 && (
            <Typography variant="caption" color={
              (subject.caption_coverage.bare_captions || 0) > (subject.caption_coverage.images / 2)
                ? 'warning.main' : 'text.secondary'
            } sx={{ display: 'block', mb: 1 }}>
              {(subject.caption_coverage.bare_captions || 0) > (subject.caption_coverage.images / 2)
                ? t('cast.trainingData.captionsMostlyBare', {
                  rich: subject.caption_coverage.rich_captions || 0,
                  bare: subject.caption_coverage.bare_captions || 0,
                  images: subject.caption_coverage.images,
                })
                : t('cast.trainingData.captions', {
                  rich: subject.caption_coverage.rich_captions || 0,
                  bare: subject.caption_coverage.bare_captions || 0,
                  images: subject.caption_coverage.images,
                })}
            </Typography>
          )}
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
            <Button variant="contained" color="secondary" onClick={handleTrainClick}
                    disabled={busy || training || !trainable}>
              {training ? t('cast.trainingData.training') : hasPendingAmend ? t('cast.trainingData.trainAmend') : t('cast.trainingData.train')}
            </Button>
            {(training || trainingJob) && (
              <Button variant="outlined" color="error" onClick={handleCancelTrain} disabled={busy}>
                {t('cast.trainingData.cancelTraining')}
              </Button>
            )}
          </Box>
          {subject.training_status && subject.training_status !== 'untrained' && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
              {t('cast.trainingData.status', { status: trainingStatusLabel })} {lastTrainedCount > 0 ? t('cast.trainingData.lastVerified', { count: lastTrainedCount }) : ''}
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
                {trainingJob?.message || t('cast.trainingData.running')}
                {trainingJob?.id && ` · ${t('cast.trainingData.jobRef', { id: String(trainingJob.id).slice(0, 12) })}`}
                {` · ${t('cast.trainingData.busyNote')}`}
              </Typography>
            </Box>
          )}

          {/* Enhanced error + recovery for real hardware (a 16 GB consumer GPU etc.)
              Matches user's "no simulations" requirement and the exact failure mode
              seen when the venv-torch cannot see CUDA despite a working GPU. */}
          {subject.training_status === 'failed' && subject.training_error && (
            <Alert severity="error" sx={{ mt: 1.5 }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {t('cast.trainingData.failed')}
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
                {t('cast.trainingData.failedHelp')}
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
              {planning ? t('cast.generate.planning') : subject.bible ? t('cast.generate.replan') : t('cast.generate.plan')}
            </Button>
            <Button variant="contained" startIcon={<AutoAwesomeIcon />} onClick={handleGenerate}
                    disabled={busy || planning || generateActive || sheetSamples.some(isPending)
                      || !(sheetSamples.length || subject.lora_path || subject.bible || samples.length)}>
              {subject.lora_path ? t('cast.generate.withLora') : t('cast.generate.baseSheet')}
            </Button>
            {(generateActive || sheetSamples.some(isPending)) && (
              <Button variant="outlined" color="error" onClick={handleCancelGenerate} disabled={busy}>
                {t('cast.generate.cancel')}
              </Button>
            )}
            <Button size="small" onClick={approveAllDone} disabled={!sheetSamples.some((s) => s.status === 'done')}>
              {t('cast.generate.approveAll')}
            </Button>
            <Box sx={{ flexGrow: 1 }} />
            <Chip label={t('cast.generate.approvedCount', { approved: approvedCount, total })} size="small"
                  color={approvedCount > 0 ? 'success' : 'default'} variant="outlined" />
          </Box>

          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
            {subject.lora_path ? (
              subject.base_model_name ? (
                <Trans
                  i18nKey="cast.generate.loraHelpWithBase"
                  components={{ bold: <strong />, code: <code /> }}
                  values={{ trigger: subject.trigger_word || subject.name, base: subject.base_model_name }}
                />
              ) : (
                <Trans
                  i18nKey="cast.generate.loraHelp"
                  components={{ bold: <strong />, code: <code /> }}
                  values={{ trigger: subject.trigger_word || subject.name }}
                />
              )
            ) : (
              <Trans i18nKey="cast.generate.baseHelp" components={{ bold: <strong /> }} />
            )}
          </Typography>
          {(subject.ref_image_paths || []).length > 0 && !subject.bible_vision_grounded && (
            <Alert severity="warning" sx={{ mb: 2 }}>
              {subject.bible_manual_override
                ? <Trans i18nKey="cast.generate.manualEditWarning" components={{ bold: <strong /> }} />
                : <Trans i18nKey="cast.generate.mismatchWarning" components={{ bold: <strong /> }} />}
            </Alert>
          )}
          {subject.smoke_identity?.ok && (
            subject.smoke_identity.status === 'measured' ? (
              <Alert
                severity={
                  Number(subject.smoke_identity.score) >= Number(subject.smoke_identity.threshold ?? 0.75)
                    ? 'success'
                    : 'warning'
                }
                sx={{ mb: 2 }}
              >
                {subject.smoke_identity.family
                  ? t('cast.generate.smokeScoreFamily', {
                    score: Number(subject.smoke_identity.score).toFixed(2),
                    method: subject.smoke_identity.method,
                    family: subject.smoke_identity.family,
                  })
                  : t('cast.generate.smokeScore', {
                    score: Number(subject.smoke_identity.score).toFixed(2),
                    method: subject.smoke_identity.method,
                  })}
              </Alert>
            ) : (
              /* No badge and no number: the score is withheld until the method is
                 proven on labelled pairs, and a colour would be a verdict. */
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
                {subject.smoke_identity.reason
                  ? t('cast.generate.identityNotMeasuredReason', { reason: subject.smoke_identity.reason })
                  : t('cast.generate.identityNotMeasured')}
              </Typography>
            )
          )}

          {/* Progress + honest status — so a planned-but-not-generated sheet doesn't
              read as "stuck", and a real render shows how far along it is. */}
          {total > 0 && (
            <Box sx={{ mb: 2 }}>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography variant="caption" color="text.secondary">
                  {active
                    ? t('cast.generate.progressGenerating', { done: doneCount, total })
                    : samples.some((s) => s.status === 'cancelled') && doneCount < total
                      ? failedCount
                        ? t('cast.generate.progressCancelledFailed', { done: doneCount, total, failed: failedCount })
                        : t('cast.generate.progressCancelled', { done: doneCount, total })
                    : doneCount === 0
                      ? t('cast.generate.progressPlanned', { total })
                      : doneCount < total
                        ? failedCount
                          ? t('cast.generate.progressPartialFailed', { done: doneCount, total, failed: failedCount })
                          : t('cast.generate.progressPartial', { done: doneCount, total })
                        : t('cast.generate.progressDone', { total })}
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
                ? <Trans i18nKey="cast.generate.allPromoted" components={{ bold: <b /> }} />
                : <Trans i18nKey="cast.generate.emptySheet" components={{ bold: <b /> }} />}
            </Typography>
          ) : (
            <Box sx={{ maxHeight: '60vh', overflowY: 'auto', pr: 1, mx: -0.5, px: 0.5 }}>
            <Grid container spacing={2}>
              {sheetSamples.map((s, idx) => {
                const trainSt = sampleTrainStatus(s, subject);
                return (
                <Grid item xs={6} sm={4} md={3} lg={2} key={s.id}>
                  <Card variant="outlined" onContextMenu={(e) => sampleMenu.open(e, { sample: s, idx })}>
                    {s.image_url ? (
                      <Box sx={{ position: 'relative' }}>
                        <CardMedia component="img" height="160" image={s.image_url} alt={s.angle || t('cast.generate.sampleAlt', { number: s.index })}
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
                          : s.status === 'pending' ? <Typography variant="caption" color="text.secondary">{t('cast.generate.queued')}</Typography>
                          : s.status === 'failed' ? <Typography variant="caption" color="error">{t('cast.generate.failedSample')}</Typography>
                          : <Typography variant="caption" color="text.secondary">{s.status}</Typography>}
                      </Box>
                    )}
                    <CardContent sx={{ py: 1 }}>
                      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 0.5 }}>
                        <Tooltip title={s.image_prompt || ''}>
                          <Typography variant="caption" noWrap>{s.angle || t('cast.generate.shot', { number: s.index + 1 })}</Typography>
                        </Tooltip>
                        <StatusChip status={s.status} />
                      </Box>
                      {s.angle_state === 'unverified' && (
                        <Tooltip title={t('cast.generate.angleUnverifiedHelp')}>
                          <Chip size="small" variant="outlined" color="warning" label={t('cast.generate.angleUnverified')} sx={{ mt: 0.5 }} />
                        </Tooltip>
                      )}
                    </CardContent>
                    <CardActions sx={{ pt: 0, justifyContent: 'space-between' }}>
                      <Tooltip title={s.approved ? t('cast.generate.approvedTooltip') : t('cast.generate.approveTooltip')}>
                        <IconButton size="small" color={s.approved ? 'success' : 'default'}
                                    onClick={() => toggleApprove(s)} aria-label={t('cast.generate.toggleApprovalAria')}>
                          {s.approved ? <CheckCircleIcon fontSize="small" /> : <RadioButtonUncheckedIcon fontSize="small" />}
                        </IconButton>
                      </Tooltip>
                      <Box>
                        <Tooltip title={t('cast.generate.regenerateTooltip')}>
                          <span>
                            <IconButton size="small" onClick={() => { setRegenTarget(s); setRegenPrompt(s.image_prompt || ''); }}
                                        disabled={isPending(s)} aria-label={t('cast.generate.regenerateAria')}>
                              <RefreshIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                        <Tooltip title={t('cast.generate.remove')}>
                          <IconButton size="small" onClick={() => handleDeleteSample(s)} aria-label={t('cast.generate.removeAria')}>
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
          <EntityContextMenu
            anchorPosition={sampleMenu.anchorPosition}
            onClose={sampleMenu.close}
            actions={
              sampleMenu.payload
                ? [
                    sampleMenu.payload.sample.image_url && {
                      label: t('cast.generate.viewLarger'),
                      onClick: () => setLightboxIdx(sampleMenu.payload.idx),
                    },
                    {
                      label: sampleMenu.payload.sample.approved ? t('cast.generate.unapprove') : t('cast.generate.approve'),
                      onClick: () => toggleApprove(sampleMenu.payload.sample),
                    },
                    {
                      label: t('cast.generate.regenerateMenu'),
                      onClick: () => {
                        setRegenTarget(sampleMenu.payload.sample);
                        setRegenPrompt(sampleMenu.payload.sample.image_prompt || '');
                      },
                      disabled: isPending(sampleMenu.payload.sample),
                    },
                    {
                      label: t('cast.generate.remove'),
                      onClick: () => handleDeleteSample(sampleMenu.payload.sample),
                      color: 'error.main',
                      dividerBefore: true,
                    },
                  ]
                : []
            }
          />

          <Divider sx={{ my: 3 }} />
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
            <Button variant="contained" color="secondary" onClick={handleTrainClick}
                    disabled={busy || training || approvedCount === 0}>
              {training ? t('cast.trainingData.training') : hasPendingAmend ? t('cast.trainingData.trainAmend') : t('cast.trainingData.train')}
            </Button>
            <Typography variant="caption" color="text.secondary">
              {approvedCount === 0
                ? t('cast.generate.approveToTrain')
                : t('cast.generate.trainsOn', { images: t('cast.trainingSetImages', { count: refCount }), samples: t('cast.generate.approvedSamples', { count: approvedCount }) })}
            </Typography>
          </Box>
        </Box>
      )}

      {/* ── Versions ───────────────────────────────────────────────────────── */}
      {tab === 3 && (
        <Box sx={{ maxWidth: 560 }}>
          <Typography variant="subtitle2" gutterBottom>{t('cast.versions.title')}</Typography>
          <Typography variant="body2"><Trans i18nKey="cast.versions.status" components={{ bold: <b /> }} values={{ status: subject.training_status }} /></Typography>
          <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
            {subject.lora_path
              ? t('cast.versions.defaultPath', { path: subject.lora_path })
              : <Trans i18nKey="cast.versions.defaultPathNone" components={{ italic: <em /> }} />}
          </Typography>
          <Box sx={{ mt: 1.5 }}>
            <Tooltip title={training ? t('cast.versions.importBlocked') : ''}>
              <span>
                <Button size="small" variant="outlined" onClick={() => setImportOpen(true)} disabled={training}>
                  {t('cast.versions.import')}
                </Button>
              </span>
            </Tooltip>
            {subject.lora_path && (
              <Typography variant="caption" color="text.secondary" sx={{ ml: 1.5 }}>
                {t('cast.versions.importHelp')}
              </Typography>
            )}
          </Box>

          {/* One card per base the member holds a LoRA for (trained or imported). */}
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" gutterBottom>{t('cast.versions.byBase')}</Typography>
            {makeDefaultError && <Alert severity="error" sx={{ mb: 1 }}>{makeDefaultError}</Alert>}
            {loraBaseGroups.length === 0 ? (
              <Typography variant="body2" color="text.secondary">{t('cast.versions.empty')}</Typography>
            ) : (
              loraBaseGroups.map(({ base, current, isDefault }) => (
                <Box key={base} sx={{
                  mb: 1.5, p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 1,
                }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Typography variant="body2">
                      <b>{BASE_MODEL_LABELS[base] || base}</b>
                      {isDefault && <Chip label={t('cast.versions.defaultChip')} size="small" color="primary" sx={{ ml: 1 }} />}
                    </Typography>
                    {!isDefault && (
                      <Button size="small" onClick={() => submitMakeDefault(base)}
                              disabled={makingDefaultBase === base}>
                        {makingDefaultBase === base ? t('cast.versions.setting') : t('cast.versions.makeDefault')}
                      </Button>
                    )}
                  </Box>
                  <Typography variant="caption" color="text.secondary" display="block">
                    {current.trigger_word
                      ? t('cast.versions.versionLine', {
                        version: current.version,
                        source: current.source === 'imported' ? t('cast.versions.imported') : t('cast.versions.trained'),
                        trigger: current.trigger_word,
                      })
                      : t('cast.versions.versionLineDefaultTrigger', {
                        version: current.version,
                        source: current.source === 'imported' ? t('cast.versions.imported') : t('cast.versions.trained'),
                      })}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" display="block" sx={{ wordBreak: 'break-all' }}>
                    {current.lora_path}
                  </Typography>
                </Box>
              ))
            )}
          </Box>

          {subject.training_status === 'trained' && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="body2" color="text.secondary">{t('cast.versions.useIn')}</Typography>
              <Box sx={{ display: 'flex', gap: 2, mt: 0.5 }}>
                <Link component="button" onClick={() => navigate(`/batch-images?character=${subject.id}`)}>{t('cast.versions.useInImages')}</Link>
                <Link component="button" onClick={() => navigate('/music-video')}>{t('cast.versions.useInMusicVideo')}</Link>
                <Link component="button" onClick={() => navigate('/video')}>{t('cast.versions.useInVideo')}</Link>
              </Box>
            </Box>
          )}
        </Box>
      )}

      {/* Regenerate dialog */}
      <Dialog open={!!regenTarget} onClose={() => setRegenTarget(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('cast.regen.title')}</DialogTitle>
        <DialogContent>
          <TextField label={t('cast.regen.promptLabel')} value={regenPrompt}
                     onChange={(e) => setRegenPrompt(e.target.value)} fullWidth multiline rows={4} sx={{ mt: 1 }}
                     helperText={t('cast.regen.promptHelp')} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRegenTarget(null)}>{t('cast.common.cancel')}</Button>
          <Button variant="contained" onClick={submitRegen}>{t('cast.regen.submit')}</Button>
        </DialogActions>
      </Dialog>

      {/* Import LoRA dialog */}
      <Dialog open={importOpen} onClose={() => !importBusy && setImportOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('cast.versions.import')}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t('cast.import.intro')}
          </Typography>
          {importError && <Alert severity="error" sx={{ mb: 2 }}>{importError}</Alert>}
          <TextField
            select fullWidth label={t('cast.import.baseModel')} value={importBaseModel} sx={{ mb: 2 }}
            onChange={(e) => setImportBaseModel(e.target.value)}
          >
            <MenuItem value="zimage-turbo">Z-Image Turbo</MenuItem>
            <MenuItem value="flux-dev">FLUX.1 Dev</MenuItem>
          </TextField>
          <TextField
            fullWidth label={t('cast.fields.triggerWord')} value={importTrigger} sx={{ mb: 2 }}
            onChange={(e) => setImportTrigger(e.target.value)}
            helperText={t('cast.import.triggerHelp')}
          />
          <Button variant="outlined" component="label">
            {importFile ? importFile.name : t('cast.import.chooseFile')}
            <input type="file" hidden accept=".safetensors"
                   onChange={(e) => setImportFile(e.target.files?.[0] || null)} />
          </Button>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setImportOpen(false)} disabled={importBusy}>{t('cast.common.cancel')}</Button>
          <Button variant="contained" onClick={submitImportLora}
                  disabled={importBusy || !importFile || !importTrigger.trim()}>
            {importBusy ? t('cast.import.importing') : t('cast.import.submit')}
          </Button>
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
                   alt={sheetSamples[lightboxIdx].angle || t('cast.generate.sampleAlt', { number: lightboxIdx + 1 })}
                   sx={{ maxWidth: '100%', maxHeight: '82vh', objectFit: 'contain', display: 'block' }} />
            ) : (
              <Typography color="grey.500">{sheetSamples[lightboxIdx].status}</Typography>
            )}
            <IconButton onClick={() => setLightboxIdx((i) => Math.max(0, i - 1))} disabled={lightboxIdx === 0}
                        sx={{ position: 'absolute', left: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label={t('cast.lightbox.previous')}>
              <ChevronLeftIcon />
            </IconButton>
            <IconButton onClick={() => setLightboxIdx((i) => Math.min(sheetSamples.length - 1, i + 1))}
                        disabled={lightboxIdx === sheetSamples.length - 1}
                        sx={{ position: 'absolute', right: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label={t('cast.lightbox.next')}>
              <ChevronRightIcon />
            </IconButton>
            <IconButton onClick={() => setLightboxIdx(null)}
                        sx={{ position: 'absolute', top: 8, right: 8, color: 'white', bgcolor: 'rgba(0,0,0,0.45)',
                              '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' } }} aria-label={t('cast.lightbox.close')}>
              <CloseIcon />
            </IconButton>
          </DialogContent>
          <DialogActions sx={{ justifyContent: 'space-between', bgcolor: 'grey.900' }}>
            <Tooltip title={sheetSamples[lightboxIdx].image_prompt || ''}>
              <Typography variant="caption" color="grey.400" noWrap sx={{ px: 1, maxWidth: '70%' }}>
                {lightboxIdx + 1}/{sheetSamples.length} · {sheetSamples[lightboxIdx].angle || t('cast.generate.shot', { number: lightboxIdx + 1 })}
                {sampleTrainStatus(sheetSamples[lightboxIdx], subject)
                  ? ` · ${sampleTrainStatus(sheetSamples[lightboxIdx], subject).label}`
                  : ''}
              </Typography>
            </Tooltip>
            <Box>
              <Button size="small" startIcon={<CloseIcon />} color="inherit"
                      onClick={() => handleDeleteSample(sheetSamples[lightboxIdx])}>
                {t('cast.lightbox.remove')}
              </Button>
              <Button size="small" startIcon={sheetSamples[lightboxIdx].approved ? <CheckCircleIcon /> : <RadioButtonUncheckedIcon />}
                      onClick={() => toggleApprove(sheetSamples[lightboxIdx])}
                      color={sheetSamples[lightboxIdx].approved ? 'success' : 'inherit'}>
                {sheetSamples[lightboxIdx].approved ? t('cast.lightbox.approved') : t('cast.generate.approve')}
              </Button>
            </Box>
          </DialogActions>
        </Dialog>
      )}
    </Box>
  );
};

export default CastMemberPage;
