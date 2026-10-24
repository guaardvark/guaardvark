import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import {
  Box,
  Typography,
  Button,
  TextField,
  Paper,
  Stack,
  LinearProgress,
  Chip,
  Alert,
  Divider,
  CircularProgress,
  Collapse,
  MenuItem,
  Link,
  IconButton,
  Tooltip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  List,
  ListItem,
  ListItemText,
} from "@mui/material";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import CloseIcon from "@mui/icons-material/Close";
import LockIcon from "@mui/icons-material/Lock";
import LockOpenIcon from "@mui/icons-material/LockOpen";
import EditIcon from "@mui/icons-material/Edit";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import HistoryIcon from "@mui/icons-material/History";
import { Link as RouterLink, useSearchParams } from "react-router-dom";

import { uploadFile } from "../api/documentService";
import {
  listMusicVideos,
  getMusicVideo,
  createMusicVideo,
  approveMusicVideo,
  deleteMusicVideo,
  clearMusicVideos,
  documentDownloadUrl,
  updateMusicVideoPlan,
  regenerateMusicVideoPlan,
  replanMusicVideo,
  analyzeMusicVideo,
  generateMusicVideoStoryboards,
  pollMusicVideoStoryboards,
  regenMusicVideoStoryboard,
  cancelMusicVideo,
  approveMusicVideoCut,
  rerenderMusicVideoCut,
  reassembleMusicVideo,
  updateMusicVideo,
  duplicateMusicVideo,
  reviseMusicVideo,
  editMusicVideoCut,
  listMusicVideoRevisions,
  saveMusicVideoRevision,
  restoreMusicVideoRevision,
} from "../api/musicVideoService";
import { getProjects } from "../api/projectService";
import MusicVideoProgress from "../components/videogen/MusicVideoProgress";
import { getAllPluginStatus } from "../api/pluginsService";
import { getAvailableModels } from "../api/modelService";
import GpuGateBanner from "../components/common/GpuGateBanner";
import useJobsGate from "../hooks/useJobsGate";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import LiveLatentPreview from "../components/videogen/LiveLatentPreview";
import CutQualityList from "../components/videogen/CutQualityList";
import { formatUiError } from "../utils/uiError";
import { handleResponse } from "../api/apiClient";
import { dispatchWarning } from "../api/taskQueue";
import ChangedElsewhereNotice from "../components/common/ChangedElsewhereNotice";
import useServerSyncedForm from "../hooks/useServerSyncedForm";
import useUnsavedChangesGuard from "../hooks/useUnsavedChangesGuard";
import { useUiTranslation, translate, uiLocale, Trans } from "../i18n";

const POLL_MS = 5000;
const DEFAULT_KEYFRAME_MODEL = "flux-schnell";

// Plan form field for one cut's prompt.
const CUT_PREFIX = "cut:";
const cutKey = (index) => `${CUT_PREFIX}${index}`;

// Plan autosave is on unless this browser turned it off. Storage can be
// unavailable (private window, blocked site data): then it is simply on.
const AUTOSAVE_PREF_KEY = "mv.autosave";
const readAutosavePref = () => {
  try {
    return window.localStorage.getItem(AUTOSAVE_PREF_KEY) !== "off";
  } catch {
    return true;
  }
};
const writeAutosavePref = (on) => {
  try {
    window.localStorage.setItem(AUTOSAVE_PREF_KEY, on ? "on" : "off");
  } catch {
    // not remembered; the toggle still applies to this visit
  }
};

const planFieldLabel = (key) => (key === "treatment"
  ? translate("musicVideo.plan.treatmentField")
  : translate("musicVideo.plan.cutField", { index: key.slice(CUT_PREFIX.length) }));

// Local presentational + interactive component for the cut plan + Director prompts.
// Keeps the main page component from exploding in size.
// onDirtyChange(bool) tells the page whether the plan has unsaved edits.
function PlanViewer({ detail, busy, models = [], onSavePlan, onRegeneratePlan, onGenerateStoryboards, onRegenStoryboard, onEditCut, onDirtyChange, onRegisterFlush, comfyReady = true, comfyDisabled = false, comfyInfo = null, gpuBlocked = false }) {
  const { t } = useUiTranslation();
  const cutPlan = detail.cut_plan || [];
  const clips = detail.clips || [];
  const isEditable = detail.current_stage === "awaiting_approval";

  // Merge cut_plan (timing/energy/section) with clip prompts + optional storyboard stills
  const rows = cutPlan.map((c, i) => {
    const clip = clips.find((cl) => cl && cl.index === c.index) || {};
    return {
      index: c.index ?? i,
      start: c.start_s ?? c.start ?? 0,
      end: c.end_s ?? c.end ?? 0,
      energy: typeof c.energy === "number" ? c.energy : 0,
      section: c.section_label || c.section || t("musicVideo.plan.unlabeled"),
      prompt: clip.prompt || "",
      storyboard_path: clip.storyboard_path || null,
      locked: !!clip.locked,
      seed: Number.isInteger(clip.seed) ? clip.seed : null,
    };
  });

  const totalDuration = rows.length ? Math.max(...rows.map((r) => r.end || 0)) : 0;
  // Sections in song order (a label can come back, e.g. verse → drop → verse).
  const sections = rows.map((r) => r.section).filter((s, i, all) => i === 0 || s !== all[i - 1]);

  const [storyboardVersions, setStoryboardVersions] = useState({}); // per-cut cache buster for thumbnails
  const [regenErrors, setRegenErrors] = useState({}); // per-cut last error for regen feedback
  const [guidance, setGuidance] = useState("");

  const isLikelyEmbeddingModel = (m) => {
    if (!m) return false;
    const n = String(m).toLowerCase();
    return ["embed", "embedding", "bge", "nomic", "snowflake", "minilm"].some((k) => n.includes(k));
  };

  // The editable plan (treatment + one prompt per cut) and the Regenerate
  // pickers follow the server copy field by field: the page's 5 s poll, a save
  // from another tab or an agent's edit updates what the person has not
  // touched and never overwrites an edit (utils/serverFormSync). A picker the
  // person changed keeps their choice; its conflicts are not shown.
  const serverPlan = {
    treatment: detail.user_treatment || detail.director_treatment || detail.director_storyline || "",
    ...Object.fromEntries(rows.map((r) => [cutKey(r.index), r.prompt])),
  };
  const serverPickers = {
    directorModel: isLikelyEmbeddingModel(detail.director_model) ? "gemma4:e4b" : detail.director_model || "gemma4:e4b",
    regenMode: detail.planning_mode || "narrative",
  };
  const planForm = useServerSyncedForm(serverPlan);
  const pickers = useServerSyncedForm(serverPickers);
  const serverPlanKey = JSON.stringify(serverPlan);
  const serverPickersKey = JSON.stringify(serverPickers);
  const syncedVideoId = useRef(detail.id);
  useEffect(() => {
    // Another video, or a plan that can no longer be edited: start over from
    // the server copy. Otherwise merge.
    if (syncedVideoId.current !== detail.id || !isEditable) {
      syncedVideoId.current = detail.id;
      planForm.reset(serverPlan);
      pickers.reset(serverPickers);
      return;
    }
    planForm.syncFromServer(serverPlan);
    pickers.syncFromServer(serverPickers);
  }, [detail.id, isEditable, serverPlanKey, serverPickersKey]);

  const treatmentEdit = planForm.values.treatment ?? "";
  const setTreatmentEdit = (value) => planForm.setField("treatment", value);
  const { regenMode, directorModel } = pickers.values;
  const setRegenMode = (value) => pickers.setField("regenMode", value);
  const setDirectorModel = (value) => pickers.setField("directorModel", value);

  // A locked plan shows what was saved and used, never a leftover edit.
  const getDisplayedPrompt = (row) =>
    (isEditable ? planForm.values[cutKey(row.index)] ?? row.prompt : row.prompt);
  const handlePromptChange = (idx, val) => planForm.setField(cutKey(idx), val);

  const planDirty = isEditable && planForm.isDirty;
  useUnsavedChangesGuard(planDirty);
  useEffect(() => {
    if (!onDirtyChange) return undefined;
    onDirtyChange(planDirty);
    return () => onDirtyChange(false);
  }, [planDirty, onDirtyChange]);

  // Director-model options come from the installed models (embedding models filtered out).
  // Fall back to a small static list if the model API is unavailable. Always keep the default
  // and the currently-selected value selectable so the MUI Select value never goes orphaned.
  const directorModelOptions = useMemo(() => {
    const fromApi = (Array.isArray(models) ? models : [])
      .map((m) => (typeof m === "string" ? m : m?.name || m?.model))
      .filter(Boolean)
      .filter((m) => !isLikelyEmbeddingModel(m));
    const base = fromApi.length ? fromApi : ["gemma4:e4b", "gemma4:e2b", "gemma3:latest"];
    const ensured = new Set(base);
    ensured.add("gemma4:e4b");
    if (directorModel && !isLikelyEmbeddingModel(directorModel)) ensured.add(directorModel);
    return Array.from(ensured);
  }, [models, directorModel]);

  const handleRegenStoryboard = async (index) => {
    // Bump version immediately to change the <img> src (adds ?v=N).
    // This defeats browser HTTP caching of the previous image bytes for that URL.
    // After the backend regen + refreshDetail, the new file at the (possibly updated)
    // storyboard_path will be fetched fresh. This directly addresses "the thumbnail
    // in the list is not the same as Open Image in New Tab".
    setStoryboardVersions(prev => ({
      ...prev,
      [index]: (prev[index] || 0) + 1
    }));

    // Pass a random variation so that the same prompt + cut doesn't produce an
    // identical image every time. The backend adds this to the fixed per-cut seed.
    // This gives the user a "variations" effect without having to manually edit
    // the prompt text for the cut.
    const variation = Math.floor(Math.random() * 100000);
    const currentRow = rows.find(r => r.index === index);
    // The cut's prompt as shown, unsaved edit included; the backend renders it
    // without storing it.
    const currentPrompt = currentRow ? getDisplayedPrompt(currentRow) : "";

    try {
      await onRegenStoryboard(index, { variation, prompt: currentPrompt });
      // clear error for this cut on success
      setRegenErrors(prev => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
    } catch (e) {
      const msg = formatUiError(e?.response?.data?.error) || e?.message || t('musicVideo.plan.regenFailed');
      setRegenErrors(prev => ({ ...prev, [index]: msg }));
      // do not re-throw; parent already surfaced global error
    }
  };

  const hasLocalEdits = planDirty;

  // Autosave: plan edits are saved ~1.5 s after the last keystroke. Approving,
  // duplicating or opening another video saves what is pending first; leaving
  // the page with unsaved edits still asks. The Save button saves at once.
  // Nothing autosaves while a conflict notice is open (the person picks) or
  // while another action on the video is running.
  const [autosave, setAutosave] = useState(readAutosavePref);
  const [savedAt, setSavedAt] = useState(null);
  const [saveState, setSaveState] = useState("idle"); // idle | saving | error
  const changesKey = JSON.stringify(planForm.changes);
  const hasConflicts = Object.keys(planForm.conflicts || {}).length > 0;
  const formRef = useRef(planForm);
  formRef.current = planForm;
  const saveRef = useRef(onSavePlan);
  saveRef.current = onSavePlan;
  const autosaveRef = useRef(autosave);
  autosaveRef.current = autosave;
  const inFlight = useRef(null);
  const unsavedAfterSave = useRef([]);
  const autosaveTimer = useRef(null);
  useEffect(() => {
    writeAutosavePref(autosave);
  }, [autosave]);

  // Sends what is pending when it starts, one save at a time, so two saves
  // never land out of order. An emptied field is not sent (the server refuses
  // an empty prompt, and an empty treatment would erase the Director's); it
  // stays dirty. Edits stay pending when the save fails.
  const handleSave = useCallback(async ({ quiet = false } = {}) => {
    while (inFlight.current) {
      try {
        await inFlight.current;
      } catch {
        // the earlier save already reported its own failure
      }
    }
    const run = (async () => {
      const changes = formRef.current.changes;
      const payload = { prompts: {} };
      const sent = {};
      Object.entries(changes).forEach(([key, value]) => {
        if (!String(value || "").trim()) return;
        if (key === "treatment") payload.treatment = value;
        else payload.prompts[key.slice(CUT_PREFIX.length)] = value;
        sent[key] = value;
      });
      unsavedAfterSave.current = Object.keys(changes).filter((k) => !(k in sent));
      if (!Object.keys(sent).length) return true;
      const saved = await saveRef.current(payload, undefined, { quiet, videoId: detail.id });
      if (saved !== false) {
        formRef.current.markSaved(sent);
        setSavedAt(new Date());
      } else {
        unsavedAfterSave.current = Object.keys(changes);
      }
      return saved;
    })();
    inFlight.current = run;
    try {
      return await run;
    } finally {
      if (inFlight.current === run) inFlight.current = null;
    }
  }, [detail.id]);

  useEffect(() => {
    if (!autosave || !isEditable || !planForm.isDirty || busy || hasConflicts) return undefined;
    autosaveTimer.current = setTimeout(async () => {
      setSaveState("saving");
      const ok = await handleSave({ quiet: true });
      setSaveState(ok === false ? "error" : "idle");
    }, 1500);
    return () => clearTimeout(autosaveTimer.current);
  }, [autosave, isEditable, changesKey, busy, hasConflicts, handleSave]);

  // For the page: save pending edits now. True when nothing unsaved is left;
  // false sends the page to its "drop the unsaved edits?" question.
  const flushPlanEdits = useCallback(async () => {
    clearTimeout(autosaveTimer.current);
    const form = formRef.current;
    if (!isEditable || !form.isDirty) return true;
    if (!autosaveRef.current || Object.keys(form.conflicts || {}).length) return false;
    const saved = await handleSave({ quiet: true });
    return saved !== false && unsavedAfterSave.current.length === 0;
  }, [isEditable, handleSave]);
  useEffect(() => {
    if (!onRegisterFlush) return undefined;
    onRegisterFlush(flushPlanEdits);
    return () => onRegisterFlush(null);
  }, [onRegisterFlush, flushPlanEdits]);

  const handleRegen = async () => {
    if (planForm.isDirty && !window.confirm(t("musicVideo.plan.regenDiscardConfirm"))) {
      return;
    }
    planForm.reset(serverPlan);
    await onRegeneratePlan(guidance, regenMode, directorModel);
    setGuidance("");
  };

  // Very lightweight energy arc visualization: horizontal segments
  // colored by rough energy (cool→warm) and height by energy.
  const arcColors = (e) => {
    // low energy cool blue-ish, high warm / energetic
    if (e < 0.25) return "#4a90e2";
    if (e < 0.55) return "#7ed321";
    if (e < 0.8) return "#f5a623";
    return "#d0021b";
  };

  return (
    <Box sx={{ mb: 1 }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
        <Typography variant="subtitle2">{t("musicVideo.plan.title")}</Typography>
        <Chip size="small" label={t("musicVideo.plan.cutCount", { count: rows.length })} />
        {detail.song_analysis?.tempo_bpm > 0 && (
          <Chip
            size="small"
            variant="outlined"
            label={[
              t("musicVideo.plan.bpm", { bpm: Math.round(detail.song_analysis.tempo_bpm) }),
              t("musicVideo.plan.beats", { count: detail.song_analysis.beat_count }),
              detail.song_analysis.bar_count ? t("musicVideo.plan.bars", { count: detail.song_analysis.bar_count }) : null,
            ].filter(Boolean).join(" · ")}
          />
        )}
        {sections.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            {t("musicVideo.plan.sections", { sections: sections.join(" → ") })}
          </Typography>
        )}
        {totalDuration > 0 && (
          <Typography variant="caption" color="text.secondary">
            ~{totalDuration.toFixed(1)}s
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}>
          {t("musicVideo.plan.stretchHint")}
        </Typography>
        {detail.planning_mode && (
          <Chip size="small" variant="outlined" label={t("musicVideo.plan.dirChip", { mode: detail.planning_mode })} />
        )}
        {detail.director_model && (
          <Chip
            size="small"
            variant="outlined"
            color={isLikelyEmbeddingModel(detail.director_model) ? "warning" : "default"}
            label={t("musicVideo.plan.modelChip", {
              model: isLikelyEmbeddingModel(detail.director_model)
                ? t("musicVideo.plan.modelWasBad", { model: "gemma4:e4b" })
                : detail.director_model,
            })}
          />
        )}
        {detail.director_enabled === false && (
          <Chip size="small" color="warning" label={t("musicVideo.plan.directorDisabled")} />
        )}
        {detail.director_diagnostics && (() => {
          const d = detail.director_diagnostics || {};
          const r = (d.reason || '').toLowerCase();
          const label = (r.includes('empty') || r.includes('no usable') || r.includes('cardinality'))
            ? t('musicVideo.plan.diagNoShots')
            : (r.includes('llm') || r.includes('exception'))
              ? t('musicVideo.plan.diagLlmError')
              : t('musicVideo.plan.diagFallback');
          const rh = (d.raw_head || '').trim();
          const looksPolluted = /INFO |WARNING |backend\.|PID:|Restoring plugin|health check/.test(rh);
          const rhDisplay = !rh || looksPolluted
            ? t('musicVideo.plan.diagNoReply')
            : rh.slice(0, 400) + (rh.length > 400 ? '...' : '');
          const tipLines = [
            t('musicVideo.plan.diagReason', { reason: d.reason || 'fallback' }),
            d.note ? t('musicVideo.plan.diagNote', { note: d.note }) : null,
            d.error ? t('musicVideo.plan.diagError', { error: d.error }) : null,
            t('musicVideo.plan.diagRawHead', { text: rhDisplay }),
          ].filter(Boolean).join('\n\n');
          return (
            <Tooltip
              title={
                <Box sx={{ maxWidth: 420, whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: '11px', lineHeight: 1.3 }}>
                  {tipLines}
                </Box>
              }
              arrow
              placement="bottom-start"
            >
              <Chip size="small" color="warning" label={label} />
            </Tooltip>
          );
        })()}
      </Stack>
      {detail.director_diagnostics && isEditable && (
        <Alert severity="info" sx={{ mb: 1, py: 0.25, '& .MuiAlert-message': { fontSize: '0.75rem' } }}>
          <Trans i18nKey="musicVideo.plan.diagAlert" components={{ bold: <b /> }} />
        </Alert>
      )}
      {isEditable && (
        <ChangedElsewhereNotice
          conflicts={planForm.conflicts}
          labels={Object.fromEntries(Object.keys(planForm.conflicts).map((k) => [k, planFieldLabel(k)]))}
          onReload={() => planForm.resolve("server")}
          onKeep={() => planForm.resolve("mine")}
          sx={{ mb: 1 }}
        />
      )}
      {hasLocalEdits && (
        <Typography variant="caption" color="info.main" sx={{ display: 'block', mb: 1 }}>
          {t("musicVideo.plan.unsavedHint")}
        </Typography>
      )}

      {/* Pipeline / Model summary (the new controls) */}
      <Stack direction="row" spacing={1} sx={{ mb: 1, flexWrap: "wrap" }}>
        {detail.use_lora_consistency ? (
          <Chip size="small" color="secondary" label={t("musicVideo.plan.loraOn")} />
        ) : (
          <Chip size="small" label={t("musicVideo.plan.loraOff")} />
        )}
        {detail.keyframe_model && (
          <Chip size="small" variant="outlined" label={t("musicVideo.plan.keyframeChip", { model: detail.keyframe_model })} />
        )}
        {detail.i2v_model && (
          <Chip size="small" variant="outlined" color="primary" label={`I2V: ${detail.i2v_model}`} />
        )}
      </Stack>

      {/* Treatment / Story editor (user can paste full short story here, or edit the AI-generated one).
          This is treated as the screenplay. Per-cut prompts should advance this story. */}
      <Box sx={{ mb: 1.5 }}>
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          {detail.user_treatment ? t("musicVideo.plan.yourStory") : t("musicVideo.plan.treatmentTitle")}{" "}
          {isEditable ? t("musicVideo.plan.editToUse") : ""}
        </Typography>
        {isEditable ? (
          <TextField
            size="small"
            fullWidth
            multiline
            minRows={4}
            maxRows={10}
            value={treatmentEdit}
            onChange={(e) => setTreatmentEdit(e.target.value)}
            placeholder={t("musicVideo.plan.treatmentPlaceholder")}
            sx={{ mt: 0.5 }}
          />
        ) : (
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", mt: 0.5, fontSize: "0.875rem", opacity: 0.9 }}>
            {detail.user_treatment || detail.director_treatment || detail.director_storyline || t("musicVideo.plan.noTreatment")}
          </Typography>
        )}
      </Box>

      {/* Dedicated Storyboards review section (thumbnails first flow).
          These appear after you click "Generate Storyboards". Review here or inline in the cuts below.
          Use per-cut regen if one doesn't match the story/treatment. */}
      {rows.some(r => r.storyboard_path) && (
        <Box sx={{ mb: 1.5, p: 1, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            {t("musicVideo.plan.reviewedStoryboards")}
            {hasLocalEdits && ` — ${t("musicVideo.plan.arcUpdated")}`}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
            {rows.filter(r => r.storyboard_path).map(r => (
              <Box key={r.index} sx={{ textAlign: "center" }}>
                <img
                  src={`/api/music-video/${detail.id}/storyboard/${r.index}?v=${storyboardVersions[r.index] || 0}`}
                  alt={t("musicVideo.plan.sbAlt", { index: r.index })}
                  style={{ width: 120, height: 68, objectFit: "cover", borderRadius: 4, border: "1px solid #444" }}
                  onError={(e) => { e.target.style.display = "none"; }}
                />
                <Typography variant="caption" display="block">#{r.index} {r.section}</Typography>
                <Typography variant="caption" sx={{ fontSize: "0.55rem", opacity: 0.65, display: "block" }}>
                  {t("musicVideo.plan.advancesArc", { section: r.section, energy: r.energy.toFixed(1) })}
                </Typography>
                {isEditable && onRegenStoryboard && (
                  <Button size="small" onClick={() => handleRegenStoryboard(r.index)} disabled={busy || gpuBlocked || !comfyReady} sx={{ fontSize: "0.65rem", py: 0 }}>
                    {t("musicVideo.plan.regen")}
                  </Button>
                )}
                {regenErrors[r.index] && (
                  <Typography variant="caption" color="error" sx={{ maxWidth: 120, display: 'block', mt: 0.25 }}>
                    {regenErrors[r.index]}
                  </Typography>
                )}
              </Box>
            ))}
          </Stack>
        </Box>
      )}

      {/* Visual Treatment / Story — the actual creative screenwriting output.
          The Director now acts more like the Film Crew Screenwriter: it invents a rich
          narrative treatment first (in the requested visual style), then derives per-cut
          prompts that advance it. This is what prevents the "identical prompt repeated
          for every scene" problem you saw. */}
      {(detail.director_treatment || detail.director_storyline)
        && (detail.director_treatment || detail.director_storyline) !== detail.user_treatment && (
        <Box sx={{ mb: 1.5, p: 1.25, backgroundColor: "rgba(255,215,0,0.07)", borderRadius: 1, border: "1px solid rgba(255,215,0,0.25)" }}>
          <Typography variant="caption" sx={{ fontWeight: 600, color: "#ffd700", display: "block", mb: 0.5 }}>
            {detail.user_treatment ? t("musicVideo.plan.directorTreatment") : t("musicVideo.plan.visualTreatment")}
          </Typography>
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", fontSize: "0.875rem", lineHeight: 1.4 }}>
            {detail.director_treatment || detail.director_storyline}
          </Typography>
        </Box>
      )}

      {/* Thin energy / mood arc bar (timeline style, fixed small height like a progress bar).
          Segments are colored by energy intensity. Thin and unobtrusive. */}
      {rows.length > 0 && (
        <Box
          sx={{
            display: "flex",
            height: 10,
            mb: 1,
            borderRadius: 1,
            overflow: "hidden",
            backgroundColor: "rgba(255,255,255,0.08)",
          }}
          title={t("musicVideo.plan.arcTitle")}
        >
          {rows.map((r, i) => (
            <Box
              key={i}
              title={t("musicVideo.plan.segmentTitle", {
                index: r.index,
                start: r.start.toFixed(1),
                end: r.end.toFixed(1),
                section: r.section,
                energy: (r.energy || 0).toFixed(2),
              })}
              sx={{
                flex: Math.max(0.01, r.end - r.start),
                height: "100%",
                backgroundColor: arcColors(r.energy || 0),
                borderLeft: i > 0 ? "1px solid rgba(0,0,0,0.55)" : "none",
                opacity: 0.9,
              }}
            />
          ))}
        </Box>
      )}

      {/* The actual cut / prompt list */}
      {rows.length > 0 && (
        <Box
          sx={{
            maxHeight: isEditable ? 420 : 260,
            overflow: "auto",
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            p: 1,
            backgroundColor: "rgba(0,0,0,0.2)",
          }}
        >
          <Stack spacing={1.25}>
            {rows.map((row) => {
              const displayed = getDisplayedPrompt(row);
              const timeLabel = `${row.start.toFixed(2)}s – ${row.end.toFixed(2)}s`;
              return (
                <Box key={row.index} id={`mv-cut-${row.index}`} sx={{ pl: 0.5 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.25 }}>
                    <Chip size="small" label={`#${row.index}`} />
                    <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                      {timeLabel}
                    </Typography>
                    <Chip size="small" variant="outlined" label={row.section} />
                    <Typography variant="caption" color="text.secondary">
                      {t("musicVideo.plan.energy", { energy: (row.energy || 0).toFixed(2) })}
                    </Typography>
                    <Box sx={{ flex: 1 }} />
                    {onEditCut && isEditable && (
                      <>
                        <TextField
                          size="small"
                          type="number"
                          label={t("musicVideo.plan.seed")}
                          placeholder={t("musicVideo.plan.seedAuto")}
                          defaultValue={row.seed ?? ""}
                          key={`seed-${detail.id}-${row.index}-${row.seed ?? "auto"}`}
                          onBlur={(e) => {
                            // Text a number field rejects reads as "": keep the seed.
                            if (e.target.validity?.badInput) {
                              e.target.value = row.seed ?? "";
                              return;
                            }
                            const raw = e.target.value.trim();
                            const next = raw === "" ? null : Number.parseInt(raw, 10);
                            if (raw !== "" && !Number.isInteger(next)) return;
                            if (next !== row.seed) onEditCut(row.index, { seed: next });
                          }}
                          inputProps={{ min: 0, step: 1, style: { width: 82, padding: "2px 6px" } }}
                          InputLabelProps={{ shrink: true }}
                          sx={{ "& .MuiInputBase-root": { fontSize: "0.75rem" } }}
                        />
                        <Tooltip title={row.locked
                          ? t("musicVideo.plan.lockedTooltip")
                          : t("musicVideo.plan.lockTooltip")}
                        >
                          <span>
                            <IconButton
                              size="small"
                              aria-label={row.locked ? t("musicVideo.plan.unlockAria", { index: row.index }) : t("musicVideo.plan.lockAria", { index: row.index })}
                              onClick={() => onEditCut(row.index, { locked: !row.locked })}
                              disabled={busy}
                              color={row.locked ? "warning" : "default"}
                            >
                              {row.locked ? <LockIcon fontSize="inherit" /> : <LockOpenIcon fontSize="inherit" />}
                            </IconButton>
                          </span>
                        </Tooltip>
                      </>
                    )}
                    {!isEditable && row.locked && <LockIcon fontSize="inherit" color="warning" />}
                  </Stack>

                  {isEditable ? (
                    <TextField
                      size="small"
                      fullWidth
                      multiline
                      minRows={1}
                      maxRows={4}
                      value={displayed}
                      onChange={(e) => handlePromptChange(row.index, e.target.value)}
                      placeholder={t("musicVideo.plan.cutPlaceholder")}
                      sx={{ fontSize: "0.875rem" }}
                    />
                  ) : (
                    <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", opacity: 0.9, pl: 0.5 }}>
                      {displayed || t("musicVideo.plan.noPrompt")}
                    </Typography>
                  )}
                  <Typography variant="caption" sx={{ fontSize: "0.55rem", opacity: 0.65, mt: 0.25, display: "block" }}>
                    {t("musicVideo.plan.advancesArcAbove", { section: row.section, energy: row.energy.toFixed(1) })}
                  </Typography>
                  {detail.director_diagnostics && displayed && (displayed.length < ((detail.style_prompt || '').length + 80)) && (
                    <Typography variant="caption" color="warning.main" sx={{ fontSize: '0.6rem', display: 'block', mt: 0.25 }}>
                      {t("musicVideo.plan.energyCuedOnly")}
                    </Typography>
                  )}

                  {/* Storyboard thumbnail (generated in the "thumbnails first" review step) */}
                  {row.storyboard_path && (
                    <Box sx={{ mt: 0.75 }}>
                      <img
                        src={`/api/music-video/${detail.id}/storyboard/${row.index}?v=${storyboardVersions[row.index] || 0}`}
                        alt={t("musicVideo.plan.storyboardAlt", { index: row.index })}
                        style={{ maxWidth: "100%", maxHeight: 110, borderRadius: 4, border: "1px solid #444", display: "block" }}
                        onError={(e) => { e.target.style.display = "none"; }}
                      />
                      {isEditable && onRegenStoryboard && (
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => handleRegenStoryboard(row.index)}
                          disabled={busy || gpuBlocked || !comfyReady}
                          sx={{ mt: 0.5 }}
                        >
                          {t("musicVideo.plan.regenStoryboard")}
                        </Button>
                      )}
                      {regenErrors[row.index] && (
                        <Typography variant="caption" color="error" sx={{ mt: 0.25, display: 'block' }}>
                          {regenErrors[row.index]}
                        </Typography>
                      )}
                    </Box>
                  )}
                </Box>
              );
            })}
          </Stack>
        </Box>
      )}

      {/* Editing & regeneration controls — only when we can still change the plan */}
      {isEditable && (
        <Box sx={{ mt: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Button
              size="small"
              variant="outlined"
              disabled={!hasLocalEdits || busy}
              onClick={() => handleSave()}
            >
              {t("musicVideo.plan.save")}
            </Button>
            <Tooltip title={t("musicVideo.plan.autosaveTooltip")}>
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <input
                  type="checkbox"
                  id="mv-autosave"
                  checked={autosave}
                  onChange={(e) => setAutosave(e.target.checked)}
                />
                <label htmlFor="mv-autosave" style={{ fontSize: "0.75rem" }}>{t("musicVideo.plan.autosave")}</label>
              </Stack>
            </Tooltip>
            <Typography variant="caption" color={saveState === "error" ? "error" : "text.secondary"} data-testid="mv-save-state">
              {saveState === "saving"
                ? t("musicVideo.plan.saving")
                : saveState === "error"
                  ? t("musicVideo.plan.notSaved")
                  : hasLocalEdits
                    ? t("musicVideo.plan.unsavedEdits")
                    : savedAt
                      ? t("musicVideo.plan.savedAt", { time: savedAt.toLocaleTimeString(uiLocale()) })
                      : ""}
            </Typography>
            {hasLocalEdits && (
              <Link component="button" variant="caption" onClick={() => planForm.reset(serverPlan)}>
                {t("musicVideo.plan.discardLocal")}
              </Link>
            )}
            {onGenerateStoryboards && (
              <Button
                size="small"
                variant="contained"
                color="secondary"
                disabled={busy || gpuBlocked || comfyDisabled}
                onClick={onGenerateStoryboards}
              >
                {t("musicVideo.plan.generateStoryboards")}
              </Button>
            )}
            {comfyDisabled && onGenerateStoryboards && (
              <Typography variant="caption" color="warning.main" sx={{ ml: 1 }}>
                {t("musicVideo.plan.comfyDisabled", { model: detail?.keyframe_model || 'flux-schnell' })}
              </Typography>
            )}
            {!comfyDisabled && comfyInfo && comfyInfo.status !== 'running' && onGenerateStoryboards && (
              <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                {t("musicVideo.plan.comfyOnDemand", { model: detail?.keyframe_model || 'flux-schnell' })}
              </Typography>
            )}
          </Stack>

          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
            {t("musicVideo.plan.regenAllHint")}
          </Typography>
          <Stack direction="row" spacing={1} alignItems="flex-start">
            <TextField
              size="small"
              fullWidth
              multiline
              minRows={1}
              maxRows={3}
              placeholder={t("musicVideo.plan.guidancePlaceholder")}
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
            />
            <TextField
              select
              size="small"
              sx={{ minWidth: 140 }}
              value={regenMode}
              onChange={(e) => setRegenMode(e.target.value)}
              label={t("musicVideo.plan.regenMode")}
            >
              <MenuItem value="narrative">{t("musicVideo.plan.modeNarrative")}</MenuItem>
              <MenuItem value="visual">{t("musicVideo.plan.modeVisual")}</MenuItem>
            </TextField>
            <TextField
              select
              size="small"
              sx={{ minWidth: 170 }}
              value={isLikelyEmbeddingModel(directorModel) ? "gemma4:e4b" : directorModel}
              onChange={(e) => setDirectorModel(e.target.value)}
              label={t("musicVideo.plan.directorModel")}
            >
              {directorModelOptions.map((m) => (
                <MenuItem key={m} value={m}>
                  {m === "gemma4:e4b" ? t("musicVideo.plan.defaultSmall", { model: "gemma4:e4b" }) : m}
                </MenuItem>
              ))}
            </TextField>
            <Button
              size="small"
              variant="contained"
              disabled={busy || gpuBlocked}
              onClick={handleRegen}
            >
              {t("musicVideo.plan.regenerate")}
            </Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
            {t("musicVideo.plan.tip")}
          </Typography>
        </Box>
      )}

      {!isEditable && rows.length > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
          {t("musicVideo.plan.locked")}
        </Typography>
      )}
    </Box>
  );
}


// Saved versions of one music video: list, save the current state, restore one.
function VersionsDialog({ open, video, onClose, onRestored }) {
  const { t } = useUiTranslation();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [label, setLabel] = useState("");
  // Shown in the dialog itself: the page's error area sits behind it.
  const [dialogError, setDialogError] = useState(null);
  const showError = (e, fallback) => setDialogError(formatUiError(e?.response?.data?.error) || e.message || fallback);
  const load = useCallback(async () => {
    if (!video) return;
    setLoading(true);
    try {
      setRows((await listMusicVideoRevisions(video.id)).revisions || []);
      setLoaded(true);
      setDialogError(null);
    } catch (e) {
      setLoaded(false);
      setDialogError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.versions.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [video?.id]);
  useEffect(() => { if (open) load(); }, [open, load]);

  const busyStage = video && ["analyzing", "generating", "assembling"].includes(video.current_stage)
    && !/^(failed|cancelled)/.test(video.status || "");

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("musicVideo.versions.title", { name: video?.name })}</DialogTitle>
      <DialogContent dividers>
        <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
          <TextField
            size="small" fullWidth label={t("musicVideo.versions.nameLabel")} value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <Button
            variant="outlined"
            onClick={async () => {
              try {
                await saveMusicVideoRevision(video.id, label.trim() || "Saved version");
                setLabel("");
                await load();
              } catch (e) {
                showError(e, t("musicVideo.versions.saveFailed"));
              }
            }}
          >
            {t("musicVideo.versions.save")}
          </Button>
        </Stack>
        {dialogError && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setDialogError(null)}>{dialogError}</Alert>}
        {loading && <LinearProgress />}
        {!loading && loaded && rows.length === 0 && (
          <Typography variant="body2" color="text.secondary">
            {t("musicVideo.versions.none")}
          </Typography>
        )}
        <List dense>
          {rows.map((r) => (
            <ListItem
              key={r.id}
              secondaryAction={
                <Button
                  size="small"
                  disabled={busyStage || !r.cut_count}
                  onClick={async () => {
                    if (!window.confirm(t("musicVideo.versions.restoreConfirm"))) return;
                    try {
                      const updated = await restoreMusicVideoRevision(video.id, r.id);
                      onRestored(updated);
                      onClose();
                    } catch (e) {
                      showError(e, t("musicVideo.versions.restoreFailed"));
                    }
                  }}
                >
                  {t("musicVideo.versions.restore")}
                </Button>
              }
            >
              <ListItemText
                primary={r.label || r.reason}
                secondary={t("musicVideo.versions.summary", {
                  date: new Date(r.created_at).toLocaleString(uiLocale()),
                  reason: r.reason,
                  cuts: r.cut_count,
                  rendered: r.clips_done,
                })}
              />
            </ListItem>
          ))}
        </List>
        {busyStage && (
          <Typography variant="caption" color="text.secondary">
            {t("musicVideo.versions.cancelFirst")}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("musicVideo.versions.close")}</Button>
      </DialogActions>
    </Dialog>
  );
}

const stageColor = (stage, status, cutCount = 0) => {
  if ((status || "").startsWith("cancelled") || stage === "cancelled") return "default";
  if ((status || "").startsWith("failed")) return "error";
  if (stage === "complete") return "success";
  if (stage === "awaiting_approval" && !cutCount) return "warning";
  if (stage === "awaiting_approval") return "warning";
  if (stage === "generating" || stage === "assembling") return "info";
  if (stage === "analyzing") return "info";
  return "default";
};

const hasCutPlan = (mv) => (mv?.cut_plan?.length || mv?.cut_count || 0) > 0;
const needsAnalysis = (mv) => !!mv && !hasCutPlan(mv);
const isAnalysisRunning = (mv) =>
  mv?.current_stage === "analyzing" && !(mv?.status || "").startsWith("failed");

// Working right now (shows the compact meter in the list).
const isRunning = (mv) =>
  ["analyzing", "generating", "assembling"].includes(mv?.current_stage)
  && !/^(failed|cancelled)/.test(mv?.status || "");

const stageLabel = (mv) => {
  if (!mv) return "";
  if (needsAnalysis(mv) && mv.current_stage === "awaiting_approval") return translate("musicVideo.stage.needsAnalysis");
  if (needsAnalysis(mv) && (mv.status || "").startsWith("failed")) return translate("musicVideo.stage.analysisFailed");
  if (needsAnalysis(mv) && ((mv.status || "").startsWith("cancelled") || mv.current_stage === "cancelled")) {
    return translate("musicVideo.stage.cancelledNoPlan");
  }
  return mv.current_stage || mv.status || translate("musicVideo.stage.unknown");
};

const MusicVideoPage = () => {
  const { t, language } = useUiTranslation();
  const { gpuBusy, gpuSubmitBlocked, blockReason, blockDetail } = useJobsGate({ submitMode: "queue" });
  const { getProcessesByType, activeProcesses, getPreviewUrl } = useUnifiedProgress();
  const [videos, setVideos] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  // The open plan has edits not yet saved (reported by PlanViewer).
  const [planDirty, setPlanDirty] = useState(false);
  // `question` is the whole "The plan has unsaved edits. … without them?" sentence.
  const confirmDropPlanEdits = (question, dirty = planDirty) =>
    !dirty || window.confirm(question);
  // The plan editor's "save what is pending now", registered while it is open.
  const planFlushRef = useRef(null);
  const registerPlanFlush = useCallback((fn) => { planFlushRef.current = fn; }, []);
  // Saves pending plan edits (autosave on), then asks about any that remain.
  const settlePlanEdits = async (question) => {
    const clean = planFlushRef.current ? await planFlushRef.current() : !planDirty;
    return confirmDropPlanEdits(question, !clean);
  };
  const [detail, setDetail] = useState(null);

  const [name, setName] = useState("");
  const [stylePrompt, setStylePrompt] = useState("");
  const [userTreatment, setUserTreatment] = useState("");  // pasted or edited full story/treatment
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // A step the backend saved but could not queue ({dispatched: false,
  // warning}); shown on the music video it belongs to.
  const [notice, setNotice] = useState(null);
  const [pluginStatus, setPluginStatus] = useState(null); // {comfyui_reachable, ...} or {status:{comfyui:'stopped',...}} or {plugins:[...]} for storyboard guards
  const [models, setModels] = useState([]); // installed Ollama models for the director-model dropdown (embedding models filtered backend-side)
  const fileInputRef = useRef(null);

  // Guaardvark Projects: file a music video under one (create form, header, list filter).
  const [searchParams] = useSearchParams();
  const urlProjectId = searchParams.get("project_id") || "";
  const [projects, setProjects] = useState([]);
  const [createProjectId, setCreateProjectId] = useState(urlProjectId);
  const [projectFilter, setProjectFilter] = useState(urlProjectId || "all");
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [renaming, setRenaming] = useState(null); // draft name while editing the header
  // Escape closes the field; the blur that follows must not save the draft.
  const renameCancelled = useRef(false);
  const [duplicateAsk, setDuplicateAsk] = useState(false);
  useEffect(() => {
    let alive = true;
    getProjects().then((res) => {
      if (alive && Array.isArray(res)) setProjects(res);
    }).catch((e) => {
      if (alive) setError(t("musicVideo.page.projectsFailed", { reason: formatUiError(e?.response?.data?.error) || e.message }));
    });
    return () => { alive = false; };
  }, []);
  const urlVideoId = searchParams.get("id") || "";
  useEffect(() => {
    // Opened from a project's page: new videos are filed under it and the list
    // shows its videos; ?id= opens that video.
    if (urlProjectId) {
      setCreateProjectId(urlProjectId);
      setProjectFilter(urlProjectId);
    }
    if (urlVideoId && Number(urlVideoId)) setSelectedId(Number(urlVideoId));
  }, [urlProjectId, urlVideoId]);

  // Load installed chat models once so the director-model dropdown only offers models that
  // actually exist (the list is small and rarely changes; /api/model/list already filters
  // out embedding models, which can't do chat/JSON).
  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const res = await getAvailableModels();
        if (mounted && Array.isArray(res)) setModels(res);
      } catch { /* fall back to the static option list in PlanViewer */ }
    })();
    return () => { mounted = false; };
  }, []);

  // Lightweight plugin status poll (for storyboard guards; no full WS subscribe here to keep quiet)
  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const s = await getAllPluginStatus();
        if (mounted && s?.success) setPluginStatus(s.data || s);
      } catch { /* plugin status poll is best-effort */ }
    };
    load();
    const t = setInterval(load, 15000);
    return () => { mounted = false; clearInterval(t); };
  }, []);

  // Normalize comfy status from different backend shapes:
  // - getAllPluginStatus /status often returns {status: {comfyui: 'running'|'stopped', ...}}
  // - some paths or list return {plugins: [{id, status, running, ...}] }
  // - legacy had comfyui_reachable top level.
  const comfyInfo = useMemo(() => {
    if (!pluginStatus) return null;
    // Array form (e.g. from list_plugins or augmented)
    const arr = pluginStatus.plugins || [];
    if (Array.isArray(arr) && arr.length) {
      const found = arr.find(p => p && p.id === 'comfyui');
      if (found) return found;
    }
    // Map under .status or flat (from get_all_status data)
    const st = pluginStatus.status || pluginStatus;
    if (st && typeof st === 'object' && !Array.isArray(st)) {
      const val = st.comfyui ?? st['comfyui'];
      if (val != null) {
        const s = typeof val === 'string' ? val : (val.status || 'unknown');
        return { id: 'comfyui', status: s, running: s === 'running' || !!val.running, reachable: !!pluginStatus.comfyui_reachable };
      }
    }
    if (pluginStatus.comfyui_reachable) {
      return { id: 'comfyui', status: 'running', running: true, reachable: true };
    }
    return null;
  }, [pluginStatus]);

  // For MV storyboards we only hard-block when the plugin is *disabled* (user pref off).
  // If it is merely 'stopped' (but enabled) we allow the Generate button click — the handler
  // already calls ensure_plugins_for_stage("music-video", "storyboard") which will auto-start
  // (persist_user_pref=false for the phase path). This removes the chicken-egg where the
  // guard prevented ever reaching the code that brings ComfyUI up for flux-schnell keyframes.
  const comfyDisabled = !!(comfyInfo && (comfyInfo.status === 'disabled' || comfyInfo.status === 'error'));
  const comfyReady = !comfyDisabled; // allow click-to-start when pref is on (even if currently stopped)

  const videoEditorInfo = useMemo(() => {
    if (!pluginStatus) return null;
    const arr = pluginStatus.plugins || [];
    if (Array.isArray(arr) && arr.length) {
      const found = arr.find((p) => p && p.id === "video_editor");
      if (found) return found;
    }
    const st = pluginStatus.status || pluginStatus;
    if (st && typeof st === "object" && !Array.isArray(st)) {
      const val = st.video_editor ?? st["video_editor"];
      if (val != null) {
        const s = typeof val === "string" ? val : (val.status || "unknown");
        return { id: "video_editor", status: s, running: s === "running" || !!val.running };
      }
    }
    return null;
  }, [pluginStatus]);
  const videoEditorDisabled = !!(
    videoEditorInfo && (videoEditorInfo.status === "disabled" || videoEditorInfo.status === "error")
  );

  // Advanced render tuning (per video) — collapsed by default; defaults match the
  // backend _settings so leaving this untouched is a no-op.
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [fillMethod, setFillMethod] = useState("forward");
  const [maxStretch, setMaxStretch] = useState("2");
  const [i2vSteps, setI2vSteps] = useState("");
  const [interp, setInterp] = useState("2");

  // Director / planning controls (new)
  const [directorEnabled, setDirectorEnabled] = useState(true);
  const [planningMode, setPlanningMode] = useState("narrative"); // "narrative" | "visual"

  // === New: Storyboard / Keyframe + I2V model selection (inspired by VideoGeneratorPage) ===
  // LoRA consistency: when true, bake cast LoRAs into keyframes via character_still_pipeline.
  // When false, more options for beautiful output (better keyframe models + top-tier I2V like Wan2.2).
  const [useLoraConsistency, setUseLoraConsistency] = useState(false);
  const [keyframeModel, setKeyframeModel] = useState(DEFAULT_KEYFRAME_MODEL);
  const [i2vModel, setI2vModel] = useState("wan22-5b");
  // Z-Image Turbo stills render offline (as on the Images page); offered when it is installed.
  const [zimageInstalled, setZimageInstalled] = useState(false);
  const keyframeTouched = useRef(false);
  const defaultKeyframe = zimageInstalled ? "zimage-turbo" : DEFAULT_KEYFRAME_MODEL;

  const keyframeModelOptions = useMemo(
    () =>
      useLoraConsistency
        ? [
            {
              value: "from-lora",
              label: t("musicVideo.create.keyframeFromLora"),
            },
          ]
        : [
            ...(zimageInstalled ? [{ value: "zimage-turbo", label: t("musicVideo.create.keyframeZimageDefault") }] : []),
            { value: "flux-schnell", label: zimageInstalled ? t("musicVideo.create.keyframeFluxFast") : t("musicVideo.create.keyframeFluxDefault") },
            { value: "flux-dev-lora", label: "FLUX-dev + LoRA" },
            { value: "sdxl", label: t("musicVideo.create.keyframeSdxlNoLora") },
            { value: "sdxl-lora", label: "SDXL + LoRAs" },
          ],
    [useLoraConsistency, zimageInstalled, t, language],
  );

  useEffect(() => {
    if (!keyframeModelOptions.some((opt) => opt.value === keyframeModel)) {
      setKeyframeModel(keyframeModelOptions[0]?.value || DEFAULT_KEYFRAME_MODEL);
    }
  }, [keyframeModelOptions, keyframeModel]);

  // Cast picker: trained character Subjects whose LoRA + bible lock identity per cut.
  const [castSubjects, setCastSubjects] = useState([]);
  const [selectedSubjectIds, setSelectedSubjectIds] = useState([]);
  // Why the Cast Library could not be read; shown instead of "no characters".
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
        if (alive) setCastError(formatUiError(e) || t("musicVideo.page.requestFailed"));
      });
    return () => { alive = false; };
  }, [castReload]);

  // I2V-capable models. The registry decides which models animate a keyframe
  // (capabilities.supports_i2v); this static list only stands in until the
  // API answers or when it is down. The registry rows are kept as data and
  // labelled when shown.
  const FALLBACK_I2V_MODEL_OPTIONS = {
    "wan22-5b": {
      label: t("musicVideo.create.i2vWan5bLabel"),
      description: t("musicVideo.create.i2vWan5bDescription"),
    },
    "wan22-14b-i2v": {
      label: "Wan 2.2 14B I2V (GGUF Q5)",
      description: t("musicVideo.create.i2vWan14bDescription"),
    },
    "cogvideox-5b-i2v": {
      label: "CogVideoX 1.5 5B I2V",
      description: t("musicVideo.create.i2vCogDescription"),
    },
    "hunyuan-i2v": {
      label: "HunyuanVideo 13B I2V (GGUF Q5)",
      description: t("musicVideo.create.i2vHunyuanDescription"),
    },
  };
  const [i2vModelRows, setI2vModelRows] = useState(null);
  const i2vModelOptions = i2vModelRows
    ? Object.fromEntries(Object.entries(i2vModelRows).map(([id, m]) => [id, {
      label: m.ready ? m.name : t("musicVideo.create.notInstalledName", { name: m.name }),
      description: [
        m.audioOut ? t("musicVideo.create.i2vAudioOut") : t("musicVideo.create.i2vSilent"),
        m.seconds ? t("musicVideo.create.i2vUpTo", { seconds: m.seconds }) : null,
        m.attribution || null,
      ].filter(Boolean).join(" · "),
      ready: m.ready,
    }]))
    : FALLBACK_I2V_MODEL_OPTIONS;
  useEffect(() => {
    let alive = true;
    fetch("/api/batch-image/models")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive) return;
        const models = (data?.data || data || {}).models || [];
        // "models" also lists downloadable ones; only weights on disk count.
        const ready = models.some((m) => m && m.id === "zimage-turbo" && (m.availability === "ready" || m.is_downloaded));
        setZimageInstalled(ready);
        if (ready && !keyframeTouched.current) setKeyframeModel("zimage-turbo");
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    fetch("/api/batch-video/models")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive) return;
        const payload = data?.data || data || {};
        const rows = (payload.models || []).filter((m) => m.capabilities?.supports_i2v);
        if (!rows.length) return;
        const opts = {};
        rows.forEach((m) => {
          const caps = m.capabilities;
          const seconds = caps.max_frames && caps.native_fps ? Math.round(caps.max_frames / caps.native_fps) : null;
          opts[m.id] = {
            name: m.name,
            audioOut: !!caps.audio_out,
            seconds,
            attribution: m.license?.attribution || null,
            ready: !!m.is_ready,
          };
        });
        setI2vModelRows(opts);
        const active = payload.active_i2v;
        if (active && opts[active]) setI2vModel(active);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Why the list could not be read; shown instead of "None yet." while the
  // poll keeps failing, and cleared by the next good read.
  const [listError, setListError] = useState(null);
  const refreshList = useCallback(async () => {
    try {
      const data = await listMusicVideos();
      setVideos(data.music_videos || []);
      setListError(null);
    } catch (e) {
      // keep the last list; say why it is not updating
      setListError(formatUiError(e?.response?.data) || formatUiError(e) || t("musicVideo.page.requestFailed"));
    }
  }, []);

  const refreshDetail = useCallback(async (id) => {
    if (!id) return;
    try {
      setDetail(await getMusicVideo(id));
    } catch (e) {
      /* non-fatal */
    }
  }, []);

  // Poll the list, and the selected detail, on an interval.
  useEffect(() => {
    refreshList();
    const t = setInterval(() => {
      refreshList();
      if (selectedId) refreshDetail(selectedId);
    }, POLL_MS);
    return () => clearInterval(t);
  }, [refreshList, refreshDetail, selectedId]);

  useEffect(() => {
    refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  const activeClipStep = useMemo(() => {
    if (!detail || detail.current_stage !== "generating") return null;
    const live = (getProcessesByType("video_render") || []).filter((p) => {
      const ad = p.additional_data || {};
      const jobId = String(p.job_id || p.id || "");
      return (
        String(ad.music_video_id) === String(detail.id)
        || ad.kind === "music_video_clip"
        || jobId.startsWith(`mv_${detail.id}_`)
      );
    });
    if (!live.length) return null;
    return live.reduce((a, b) => ((b.timestamp || 0) > (a.timestamp || 0) ? b : a));
  }, [detail, getProcessesByType, activeProcesses]);

  const noteDispatch = (id, result) => {
    const warning = dispatchWarning(result);
    if (warning) setNotice({ id, text: warning });
  };

  const handleCreate = async () => {
    setError(null);
    if (!name.trim() || !stylePrompt.trim() || !file) {
      setError(t("musicVideo.page.createRequired"));
      return;
    }
    setBusy(true);
    try {
      // 1) upload the song → Document id. /api/docs/upload resolves the raw body
      // {document_id, filename, job_id, ...}; tolerate a couple of shapes.
      const up = await uploadFile(file, null, "music-video-song", {});
      const songDocId = up?.document_id ?? up?.data?.id ?? up?.id;
      if (!songDocId) throw new Error(t("musicVideo.page.songUploadFailed"));
      // 2) create the music video (kicks off analysis)
      const settings = {
        fill_method: fillMethod,
        max_stretch: Number(maxStretch) || 2.0,
        interpolation_multiplier: Number(interp) || 1,
        director_enabled: !!directorEnabled,
        planning_mode: planningMode,
        // New pipeline controls
        use_lora_consistency: !!useLoraConsistency,
        keyframe_model: keyframeModel,
        i2v_model: i2vModel,
      };
      if (useLoraConsistency && selectedSubjectIds.length > 0) {
        settings.subject_ids = selectedSubjectIds;
      }
      const stepsNum = Number(i2vSteps);
      if (i2vSteps !== "" && stepsNum > 0) settings.i2v_steps = stepsNum;
      const mv = await createMusicVideo({
        name: name.trim(),
        song_document_id: songDocId,
        style_prompt: stylePrompt.trim(),
        user_treatment: userTreatment.trim() || undefined,
        project_id: createProjectId ? Number(createProjectId) : undefined,
        settings,
      });
      setName("");
      setStylePrompt("");
      setUserTreatment("");
      setFile(null);
      setDirectorEnabled(true);
      setPlanningMode("narrative");
      setUseLoraConsistency(false);
      setSelectedSubjectIds([]);
      setKeyframeModel(defaultKeyframe);
      setI2vModel("wan22-5b");
      // Reset any future advanced model params here too
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refreshList();
      setSelectedId(mv.id);
      noteDispatch(mv.id, mv);
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleContinueAnalyze = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await analyzeMusicVideo(detail.id);
      noteDispatch(detail.id, updated);
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.analysisStartFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleApprove = async () => {
    if (!detail) return;
    // Approving locks the plan, so unsaved edits could never be saved after.
    if (!(await settlePlanEdits(t("musicVideo.page.dropEditsApprove")))) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await approveMusicVideo(detail.id);
      noteDispatch(detail.id, updated);
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.approveFailed"));
    } finally {
      setBusy(false);
    }
  };

  // A cut the quality check held: keep it, or render it again.
  const handleHeldCut = async (index, action) => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const updated = action === "approve"
        ? await approveMusicVideoCut(detail.id, index)
        : await rerenderMusicVideoCut(detail.id, index);
      noteDispatch(detail.id, updated);
      setDetail(updated);
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message
        || (action === "approve" ? t("musicVideo.page.approveFailed") : t("musicVideo.page.rerenderFailed")));
    } finally {
      setBusy(false);
    }
  };

  const handleReassemble = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await reassembleMusicVideo(detail.id);
      noteDispatch(detail.id, updated);
      setDetail(updated);
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.reassembleFailed"));
    } finally {
      setBusy(false);
    }
  };

  // --- Saving / editing the video as a project ---------------------------------
  const runUpdate = async (fn, failText) => {
    if (!detail) return null;
    setBusy(true);
    setError(null);
    try {
      const updated = await fn();
      noteDispatch(updated?.id ?? detail.id, updated);
      return updated;
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || failText);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const handleRename = async () => {
    if (renameCancelled.current) {
      renameCancelled.current = false;
      return;
    }
    const next = (renaming || "").trim();
    setRenaming(null);
    if (!next || next === detail.name) return;
    const updated = await runUpdate(() => updateMusicVideo(detail.id, { name: next }), t("musicVideo.page.renameFailed"));
    if (updated) { setDetail(updated); await refreshList(); }
  };

  const handleMoveProject = async (value) => {
    const updated = await runUpdate(
      () => updateMusicVideo(detail.id, { project_id: value === "" ? null : Number(value) }),
      t("musicVideo.page.moveFailed"),
    );
    if (updated) { setDetail(updated); await refreshList(); }
  };

  const handleDuplicate = async () => {
    if (!(await settlePlanEdits(t("musicVideo.page.dropEditsDuplicate")))) return;
    if ((detail.clips_done || 0) > 0) {
      setDuplicateAsk(true);  // reuse the rendered clips, render fresh, or cancel
      return;
    }
    await runDuplicate(false);
  };

  const runDuplicate = async (keepClips) => {
    setDuplicateAsk(false);
    const copy = await runUpdate(
      () => duplicateMusicVideo(detail.id, { keep_clips: keepClips }),
      t("musicVideo.page.duplicateFailed"),
    );
    if (copy) { await refreshList(); setSelectedId(copy.id); }
  };

  const handleRevise = async () => {
    if (!window.confirm(t("musicVideo.page.reviseConfirm"))) return;
    const updated = await runUpdate(() => reviseMusicVideo(detail.id), t("musicVideo.page.reviseFailed"));
    if (updated) { setDetail(updated); await refreshList(); }
  };

  const handleEditCut = async (index, data) => {
    const updated = await runUpdate(() => editMusicVideoCut(detail.id, index, data), t("musicVideo.page.cutEditFailed"));
    if (updated) setDetail(updated);
  };

  const handleCancel = async () => {
    if (!detail) return;
    const analyzing = detail.current_stage === "analyzing";
    const msg = analyzing
      ? t("musicVideo.page.cancelAnalysisConfirm")
      : t("musicVideo.page.cancelGenerationConfirm");
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await cancelMusicVideo(detail.id);
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.cancelFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id, label) => {
    if (!window.confirm(t("musicVideo.page.removeConfirm", { name: label }))) {
      return;
    }
    setError(null);
    try {
      await deleteMusicVideo(id);
      if (selectedId === id) {
        setSelectedId(null);
        setDetail(null);
      }
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.deleteFailed"));
    }
  };

  const handleClearFinished = async () => {
    const finished = videos.filter(
      (v) =>
        v.current_stage === "complete" ||
        (v.status || "").startsWith("failed") ||
        (v.status || "").startsWith("cancelled") ||
        v.current_stage === "cancelled",
    );
    if (finished.length === 0) return;
    if (!window.confirm(t("musicVideo.page.clearConfirm", { count: finished.length }))) {
      return;
    }
    setError(null);
    try {
      await clearMusicVideos({ all: false });
      if (selectedId && finished.some((v) => v.id === selectedId)) {
        setSelectedId(null);
        setDetail(null);
      }
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.page.clearFailed"));
    }
  };

  const finishedCount = videos.filter(
    (v) =>
      v.current_stage === "complete" ||
      (v.status || "").startsWith("failed") ||
      (v.status || "").startsWith("cancelled") ||
      v.current_stage === "cancelled",
  ).length;

  const shownVideos = videos.filter((v) => {
    if (projectFilter === "all") return true;
    if (projectFilter === "none") return !v.project_id;
    return String(v.project_id) === String(projectFilter);
  });

  const hasActiveGeneration = videos.some(
    (v) =>
      (v.current_stage === "generating" || v.current_stage === "assembling") &&
      !((v.status || "").startsWith("cancelled") || v.current_stage === "cancelled")
  );

  return (
    <Box sx={{ p: 3, height: "100%", overflow: "auto" }}>
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <MusicVideoIcon fontSize="large" />
        <Box>
          <Typography variant="h5">{t("musicVideo.page.title")}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t("musicVideo.page.subtitle")}
          </Typography>
        </Box>
      </Stack>

      <Box sx={{ display: "flex", gap: 3, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* Create + list */}
        <Stack spacing={2} sx={{ flex: "1 1 360px", minWidth: 320 }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ mb: 1.5 }}>{t("musicVideo.create.title")}</Typography>
            {videoEditorDisabled && (
              <Typography variant="caption" color="warning.main" sx={{ display: "block", mb: 1 }}>
                {t("musicVideo.create.enableVideoEditor")}
              </Typography>
            )}
            <Stack spacing={1.5}>
              <TextField
                label={t("musicVideo.create.name")} size="small" fullWidth value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <TextField
                select size="small" fullWidth label={t("musicVideo.create.project")}
                value={createProjectId}
                onChange={(e) => setCreateProjectId(e.target.value)}
                helperText={t("musicVideo.create.projectHelp")}
              >
                <MenuItem value="">{t("musicVideo.create.noProject")}</MenuItem>
                {projects.map((p) => (
                  <MenuItem key={p.id} value={String(p.id)}>{p.name}</MenuItem>
                ))}
              </TextField>
              <TextField
                label={t("musicVideo.create.style")} size="small" fullWidth multiline minRows={3}
                placeholder={t("musicVideo.create.stylePlaceholder")}
                value={stylePrompt}
                onChange={(e) => setStylePrompt(e.target.value)}
              />
              <TextField
                label={t("musicVideo.create.treatment")}
                size="small" fullWidth multiline minRows={5}
                placeholder={t("musicVideo.create.treatmentPlaceholder")}
                value={userTreatment}
                onChange={(e) => setUserTreatment(e.target.value)}
              />
              <Button
                component="label" variant="outlined" startIcon={<UploadFileIcon />}
                sx={{ justifyContent: "flex-start" }}
              >
                {file ? file.name : t("musicVideo.create.chooseSong")}
                <input
                  ref={fileInputRef} type="file" hidden accept="audio/*,.mp3,.wav,.flac"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                />
              </Button>

              <Link
                component="button" type="button" variant="body2" underline="hover"
                sx={{ alignSelf: "flex-start" }}
                onClick={() => setShowAdvanced((v) => !v)}
              >
                {showAdvanced ? `▾ ${t("musicVideo.create.advanced")}` : `▸ ${t("musicVideo.create.advanced")}`}
              </Link>
              <Collapse in={showAdvanced}>
                <Stack spacing={1.5} sx={{ pt: 0.5 }}>
                  <TextField
                    select size="small" fullWidth label={t("musicVideo.create.fill")}
                    value={fillMethod} onChange={(e) => setFillMethod(e.target.value)}
                    helperText={t("musicVideo.create.fillHelp")}
                  >
                    <MenuItem value="forward">{t("musicVideo.create.fillForward")}</MenuItem>
                    <MenuItem value="boomerang">{t("musicVideo.create.fillBoomerang")}</MenuItem>
                    <MenuItem value="loop">{t("musicVideo.create.fillLoop")}</MenuItem>
                  </TextField>
                  <TextField
                    type="number" size="small" fullWidth label={t("musicVideo.create.stretch")}
                    value={maxStretch} onChange={(e) => setMaxStretch(e.target.value)}
                    inputProps={{ min: 1, max: 4, step: 0.5 }}
                    helperText={t("musicVideo.create.stretchHelp")}
                  />
                  <TextField
                    select size="small" fullWidth label={t("musicVideo.create.interp")}
                    value={interp} onChange={(e) => setInterp(e.target.value)}
                    helperText={t("musicVideo.create.interpHelp")}
                  >
                    <MenuItem value="1">{t("musicVideo.create.interpOff")}</MenuItem>
                    <MenuItem value="2">{t("musicVideo.create.interpSmooth")}</MenuItem>
                    <MenuItem value="4">4×</MenuItem>
                  </TextField>
                  <TextField
                    type="number" size="small" fullWidth label={t("musicVideo.create.steps")}
                    value={i2vSteps} onChange={(e) => setI2vSteps(e.target.value)}
                    inputProps={{ min: 8, max: 60, step: 1 }}
                    placeholder={t("musicVideo.create.stepsPlaceholder")}
                    helperText={t("musicVideo.create.stepsHelp")}
                  />

                  {/* Director / storyboarding controls */}
                  <TextField
                    select
                    size="small"
                    fullWidth
                    label={t("musicVideo.create.planningMode")}
                    value={planningMode}
                    onChange={(e) => setPlanningMode(e.target.value)}
                    helperText={t("musicVideo.create.planningModeHelp")}
                  >
                    <MenuItem value="narrative">{t("musicVideo.create.planningNarrative")}</MenuItem>
                    <MenuItem value="visual">{t("musicVideo.create.planningVisual")}</MenuItem>
                  </TextField>

                  <Stack direction="row" alignItems="center" spacing={1} sx={{ pl: 0.5 }}>
                    <input
                      type="checkbox"
                      checked={directorEnabled}
                      onChange={(e) => setDirectorEnabled(e.target.checked)}
                      id="director-enabled"
                    />
                    <label htmlFor="director-enabled" style={{ fontSize: "0.875rem", color: "rgba(255,255,255,0.7)" }}>
                      {t("musicVideo.create.useDirector")}
                    </label>
                  </Stack>

                  {/* === Storyboard / Keyframe + I2V Pipeline (modeled after VideoGeneratorPage) === */}
                  <Divider sx={{ my: 1 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                    {t("musicVideo.create.keyframeSection")}
                  </Typography>

                  <Stack direction="row" alignItems="center" spacing={1} sx={{ pl: 0.5, mt: 0.5 }}>
                    <input
                      type="checkbox"
                      checked={useLoraConsistency}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setUseLoraConsistency(checked);
                        if (checked) {
                          setKeyframeModel("from-lora");
                        } else if (
                          keyframeModel === "from-lora"
                          || keyframeModel === "sdxl-lora"
                          || keyframeModel === "flux-dev-lora"
                        ) {
                          setKeyframeModel(defaultKeyframe);
                        }
                      }}
                      id="lora-consistency"
                    />
                    <label htmlFor="lora-consistency" style={{ fontSize: "0.875rem", color: "rgba(255,255,255,0.7)" }}>
                      {t("musicVideo.create.useLora")}
                    </label>
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ pl: 2.5, display: "block" }}>
                    {t("musicVideo.create.loraHint")}
                  </Typography>

                  {useLoraConsistency && (
                    <TextField
                      select
                      size="small"
                      fullWidth
                      label={t("musicVideo.create.cast")}
                      value={selectedSubjectIds}
                      onChange={(e) => {
                        const v = e.target.value;
                        setSelectedSubjectIds(typeof v === "string" ? v.split(",").map(Number) : v);
                      }}
                      SelectProps={{
                        multiple: true,
                        renderValue: (sel) =>
                          castSubjects
                            .filter((s) => sel.includes(s.id))
                            .map((s) => s.name)
                            .join(", ") || t("musicVideo.create.selectCast"),
                      }}
                      error={Boolean(castError)}
                      helperText={
                        castError ? (
                          <>
                            <Trans
                              i18nKey="musicVideo.create.castLoadFailed"
                              values={{ reason: castError }}
                              components={{ retry: <Link component="button" type="button" onClick={() => setCastReload((n) => n + 1)} /> }}
                            />
                          </>
                        ) : castSubjects.length === 0
                          ? t("musicVideo.create.noCast")
                          : t("musicVideo.create.castHelp")
                      }
                      sx={{ mt: 1 }}
                    >
                      {castSubjects.map((s) => (
                        <MenuItem key={s.id} value={s.id}>
                          {s.name}
                          {s.trigger_word ? ` (${s.trigger_word})` : ""}
                        </MenuItem>
                      ))}
                    </TextField>
                  )}

                  <TextField
                    select
                    size="small"
                    fullWidth
                    label={t("musicVideo.create.i2vModel")}
                    value={i2vModel}
                    onChange={(e) => setI2vModel(e.target.value)}
                    helperText={t("musicVideo.create.i2vModelHelp")}
                    sx={{ mt: 1 }}
                  >
                    {Object.entries(i2vModelOptions).map(([key, cfg]) => (
                      <MenuItem key={key} value={key} disabled={cfg.ready === false}>
                        <Box>
                          <Typography variant="body2">{cfg.label}</Typography>
                          {cfg.description && (
                            <Typography variant="caption" color="text.secondary">{cfg.description}</Typography>
                          )}
                        </Box>
                      </MenuItem>
                    ))}
                  </TextField>

                  {useLoraConsistency || keyframeModelOptions.length <= 1 ? (
                    <Alert severity="info" sx={{ mt: 1 }}>
                      {t("musicVideo.create.keyframeAuto")}
                    </Alert>
                  ) : (
                    <TextField
                      select
                      size="small"
                      fullWidth
                      label={t("musicVideo.create.keyframeModel")}
                      value={keyframeModel}
                      onChange={(e) => {
                        keyframeTouched.current = true;
                        setKeyframeModel(e.target.value);
                      }}
                      helperText={t("musicVideo.create.keyframeModelHelp")}
                      sx={{ mt: 1 }}
                    >
                      {keyframeModelOptions.map((opt) => (
                        <MenuItem key={opt.value} value={opt.value}>{opt.label}</MenuItem>
                      ))}
                    </TextField>
                  )}
                </Stack>
              </Collapse>

              {error && <Alert severity="error" onClose={() => setError(null)}>{formatUiError(error)}</Alert>}
              <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} blockDetail={blockDetail} queueMode />
              <Button variant="contained" onClick={handleCreate} disabled={busy || gpuSubmitBlocked}>
                {busy ? <CircularProgress size={20} /> : (hasActiveGeneration ? t("musicVideo.create.addToQueue") : t("musicVideo.create.submit"))}
              </Button>
              {hasActiveGeneration && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                  {t("musicVideo.create.queueHint")}
                </Typography>
              )}
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle1">{t("musicVideo.list.title")}</Typography>
              {finishedCount > 0 && (
                <Link
                  component="button" type="button" variant="caption" underline="hover"
                  color="text.secondary" onClick={handleClearFinished}
                >
                  {t("musicVideo.list.clearFinished", { count: finishedCount })}
                </Link>
              )}
            </Stack>
            {projects.length > 0 && (
              <TextField
                select size="small" fullWidth label={t("musicVideo.list.show")} sx={{ mb: 1 }}
                value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)}
              >
                <MenuItem value="all">{t("musicVideo.list.all")}</MenuItem>
                <MenuItem value="none">{t("musicVideo.list.noProject")}</MenuItem>
                {projects.map((p) => (
                  <MenuItem key={p.id} value={String(p.id)}>{t("musicVideo.list.project", { name: p.name })}</MenuItem>
                ))}
              </TextField>
            )}
            {listError && (
              <Alert
                severity="error"
                sx={{ mb: 1 }}
                action={<Button color="inherit" size="small" onClick={refreshList}>{t("musicVideo.list.retry")}</Button>}
              >
                {t("musicVideo.list.loadFailed", { reason: listError })}
              </Alert>
            )}
            {shownVideos.length === 0 && !listError && (
              <Typography variant="body2" color="text.secondary">
                {videos.length === 0 ? t("musicVideo.list.none") : t("musicVideo.list.noneInProject")}
              </Typography>
            )}
            <Stack spacing={1}>
              {shownVideos.map((v) => (
                <Paper
                  key={v.id}
                  variant="outlined"
                  onClick={async () => {
                    if (v.id !== selectedId && !(await settlePlanEdits(t("musicVideo.page.dropEditsOpen")))) return;
                    setSelectedId(v.id);
                  }}
                  sx={{
                    p: 1.25, cursor: "pointer",
                    borderColor: v.id === selectedId ? "primary.main" : "divider",
                  }}
                >
                  <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
                    <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0 }}>{v.name}</Typography>
                    <Chip
                      size="small" label={stageLabel(v)}
                      color={stageColor(v.current_stage, v.status, v.cut_count)}
                    />
                    <Tooltip title={t("musicVideo.list.remove")}>
                      <IconButton
                        size="small"
                        onClick={(e) => { e.stopPropagation(); handleDelete(v.id, v.name); }}
                        sx={{ opacity: 0.5, "&:hover": { opacity: 1 } }}
                      >
                        <CloseIcon fontSize="inherit" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                  {(v.project_name || isRunning(v)) && (
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5 }}>
                      {v.project_name && (
                        <Chip size="small" variant="outlined" label={v.project_name} sx={{ maxWidth: 140 }} />
                      )}
                      {isRunning(v) && (
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <MusicVideoProgress progress={v.progress} compact running />
                        </Box>
                      )}
                    </Stack>
                  )}
                </Paper>
              ))}
            </Stack>
          </Paper>
        </Stack>

        {/* Detail */}
        <Box sx={{ flex: "2 1 460px", minWidth: 360 }}>
          {!detail ? (
            <Paper variant="outlined" sx={{ p: 3 }}>
              <Typography variant="body2" color="text.secondary">
                {t("musicVideo.detail.selectVideo")}
              </Typography>
            </Paper>
          ) : (
            <Paper variant="outlined" sx={{ p: 2.5 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }} flexWrap="wrap" useFlexGap spacing={1}>
                {renaming !== null ? (
                  <TextField
                    size="small" autoFocus value={renaming}
                    onChange={(e) => setRenaming(e.target.value)}
                    onBlur={handleRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleRename();
                      if (e.key === "Escape") {
                        renameCancelled.current = true;
                        setRenaming(null);
                      }
                    }}
                    inputProps={{ "aria-label": t("musicVideo.detail.nameAria") }}
                  />
                ) : (
                  <Stack direction="row" alignItems="center" spacing={0.5} sx={{ minWidth: 0 }}>
                    <Typography variant="h6" noWrap>{detail.name}</Typography>
                    <Tooltip title={t("musicVideo.detail.rename")}>
                      <IconButton size="small" aria-label={t("musicVideo.detail.rename")} onClick={() => setRenaming(detail.name)}>
                        <EditIcon fontSize="inherit" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
                )}
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                  <Tooltip title={t("musicVideo.detail.duplicateTooltip")}>
                    <span>
                      <IconButton size="small" aria-label={t("musicVideo.detail.duplicate")} onClick={handleDuplicate} disabled={busy}>
                        <ContentCopyIcon fontSize="small" />
                      </IconButton>
                    </span>
                  </Tooltip>
                  <Tooltip title={t("musicVideo.detail.versionsTooltip")}>
                    <IconButton size="small" aria-label={t("musicVideo.detail.versions")} onClick={() => setVersionsOpen(true)}>
                      <HistoryIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Chip label={stageLabel(detail)} color={stageColor(detail.current_stage, detail.status, detail.cut_count)} />
                  {needsAnalysis(detail) && !isAnalysisRunning(detail) && (
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={handleContinueAnalyze}
                      disabled={busy}
                    >
                      {busy ? <CircularProgress size={18} /> : t("musicVideo.detail.continueAnalyze")}
                    </Button>
                  )}
                  {hasCutPlan(detail) && (detail.current_stage === "complete" || (detail.status || "").match(/^(failed|cancelled)/) || detail.current_stage === "cancelled") && (
                    <Tooltip title={t("musicVideo.detail.reviseTooltip")}>
                      <span>
                        <Button size="small" variant="contained" onClick={handleRevise} disabled={busy}>
                          {t("musicVideo.detail.revise")}
                        </Button>
                      </span>
                    </Tooltip>
                  )}
                  {hasCutPlan(detail) && (detail.current_stage === "complete" || (detail.status || "").match(/^(failed|cancelled)/) || detail.current_stage === "cancelled") && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={async () => {
                        const msg = t("musicVideo.detail.replanConfirm");
                        if (!window.confirm(msg)) return;
                        try {
                          setBusy(true);
                          const replanned = await replanMusicVideo(detail.id);
                          noteDispatch(detail.id, replanned);
                          await refreshDetail(detail.id);
                        } catch (e) {
                          setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.detail.replanFailed"));
                        } finally {
                          setBusy(false);
                        }
                      }}
                      disabled={busy}
                    >
                      {t("musicVideo.detail.rerenderAll")}
                    </Button>
                  )}
                  {needsAnalysis(detail) && ((detail.status || "").match(/^(failed|cancelled)/) || detail.current_stage === "cancelled") && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={handleContinueAnalyze}
                      disabled={busy}
                    >
                      {t("musicVideo.detail.retryAnalysis")}
                    </Button>
                  )}
                </Stack>
              </Stack>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                {detail.style_prompt}
              </Typography>
              <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
                <TextField
                  select size="small" label={t("musicVideo.detail.project")} sx={{ minWidth: 220 }}
                  value={detail.project_id ? String(detail.project_id) : ""}
                  onChange={(e) => handleMoveProject(e.target.value)}
                  disabled={busy}
                >
                  <MenuItem value="">{t("musicVideo.create.noProject")}</MenuItem>
                  {projects.map((p) => (
                    <MenuItem key={p.id} value={String(p.id)}>{p.name}</MenuItem>
                  ))}
                </TextField>
                {detail.project_id && (
                  <Link component={RouterLink} to={`/projects/${detail.project_id}?tab=videos`} variant="caption">
                    {t("musicVideo.detail.openProject")}
                  </Link>
                )}
              </Stack>

              <Box sx={{ mb: 2 }}>
                <MusicVideoProgress
                  progress={detail.progress}
                  clips={detail.clips}
                  running={isRunning(detail)}
                  onCutClick={(index) => {
                    const el = document.getElementById(`mv-cut-${index}`);
                    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
                  }}
                />
              </Box>
              <Divider sx={{ mb: 2 }} />

              {notice && notice.id === detail.id && (
                <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setNotice(null)}>
                  {notice.text}
                </Alert>
              )}

              {needsAnalysis(detail) && !isAnalysisRunning(detail) && (
                <Stack spacing={1.5} sx={{ mb: 2 }}>
                  <Alert severity={(detail.status || "").startsWith("failed") ? "error" : "info"}>
                    {(detail.status || "").startsWith("failed") ? (
                      <>
                        <Trans
                          i18nKey="musicVideo.detail.analysisFailed"
                          values={{ error: detail.error_blob?.error ? `: ${String(detail.error_blob.error)}` : "" }}
                          components={{ bold: <b /> }}
                        />
                      </>
                    ) : (
                      <>
                        <Trans
                          i18nKey="musicVideo.detail.notAnalyzed"
                          values={{
                            stuck: (detail.current_stage === "awaiting_approval") ? ` ${t("musicVideo.detail.stuckAtGate")}` : "",
                          }}
                          components={{ bold: <b /> }}
                        />
                      </>
                    )}
                  </Alert>
                  <Box>
                    <Button variant="contained" onClick={handleContinueAnalyze} disabled={busy}>
                      {busy ? <CircularProgress size={20} /> : t("musicVideo.detail.continueAnalyze")}
                    </Button>
                    {(detail.current_stage === "analyzing" || detail.current_stage === "awaiting_approval") && (
                      <Button
                        size="small"
                        variant="outlined"
                        color="error"
                        onClick={handleCancel}
                        disabled={busy}
                        sx={{ ml: 1 }}
                      >
                        {t("musicVideo.detail.cancel")}
                      </Button>
                    )}
                  </Box>
                </Stack>
              )}

              {/* === Video Plan / Director output (visible after analysis) === */}
              {detail.cut_plan && detail.cut_plan.length > 0 && (
                <PlanViewer
                  key={detail.id}
                  detail={detail}
                  busy={busy}
                  onDirtyChange={setPlanDirty}
                  onRegisterFlush={registerPlanFlush}
                  gpuBlocked={gpuBusy}
                  models={models}
                  comfyReady={comfyReady}
                  comfyDisabled={comfyDisabled}
                  comfyInfo={comfyInfo}
                  onEditCut={handleEditCut}
                  onSavePlan={async (arg1, arg2, opts = {}) => {
                    const quiet = !!opts.quiet;
                    const videoId = opts.videoId ?? detail.id;
                    try {
                      if (!quiet) setBusy(true);
                      let payload = {};
                      if (arg1 && typeof arg1 === 'object' && !Array.isArray(arg1)) {
                        // New shape from PlanViewer: {prompts, treatment?, style_prompt? }
                        payload = { ...arg1 };
                      } else {
                        // Legacy shape
                        payload = { prompts: arg1 || {} };
                        if (arg2 && arg2.trim()) payload.style_prompt = arg2.trim();
                      }
                      await updateMusicVideoPlan(videoId, payload);
                      if (videoId === detail.id) await refreshDetail(videoId);
                      return true;
                    } catch (e) {
                      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.detail.savePlanFailed"));
                      return false;
                    } finally {
                      if (!quiet) setBusy(false);
                    }
                  }}
                  onGenerateStoryboards={async () => {
                    try {
                      setBusy(true);
                      const dispatched = await generateMusicVideoStoryboards(detail.id);
                      if (dispatched?.task_id || dispatched?.status === "dispatched") {
                        const updated = await pollMusicVideoStoryboards(detail.id);
                        setDetail(updated);
                      } else {
                        await refreshDetail(detail.id);
                      }
                    } catch (e) {
                      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.detail.storyboardsFailed"));
                    } finally {
                      setBusy(false);
                    }
                  }}
                  onRegenStoryboard={async (index, data = {}) => {
                    try {
                      setBusy(true);
                      await regenMusicVideoStoryboard(detail.id, index, data);
                      await refreshDetail(detail.id);
                    } catch (e) {
                      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.detail.regenStoryboardFailed"));
                      throw e;  // re-throw so local handler in PlanViewer can show per-cut error and still bump version for re-fetch
                    } finally {
                      setBusy(false);
                    }
                  }}
                  onRegeneratePlan={async (feedback, mode, dmodel) => {
                    try {
                      setBusy(true);
                      const payload = {};
                      if (feedback && feedback.trim()) payload.feedback = feedback.trim();
                      if (mode) payload.planning_mode = mode;
                      if (dmodel) payload.director_model = dmodel;
                      await regenerateMusicVideoPlan(detail.id, payload);
                      await refreshDetail(detail.id);
                    } catch (e) {
                      setError(formatUiError(e?.response?.data?.error) || e.message || t("musicVideo.detail.regenPlanFailed"));
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
              )}

              <Divider sx={{ my: 2 }} />

              {isAnalysisRunning(detail) && (
                <Stack spacing={1} sx={{ mb: 2 }}>
                  <Typography variant="body2">{t("musicVideo.detail.analyzing")}</Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    onClick={handleCancel}
                    disabled={busy}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    {t("musicVideo.detail.cancelAnalysis")}
                  </Button>
                </Stack>
              )}

              {detail.current_stage === "awaiting_approval" && hasCutPlan(detail) && detail.estimate && (
                <Stack spacing={1.5}>
                  <Alert severity="warning">
                    {t("musicVideo.detail.planReady")}{" "}{(detail.clips || []).some(c => c.storyboard_path)
                      ? t("musicVideo.detail.storyboardsReady")
                      : t("musicVideo.detail.storyboardsFirst")}
                    <br /><Trans
                      i18nKey="musicVideo.detail.estimate"
                      values={{ time: detail.estimate.estimated_human }}
                      components={{ bold: <b /> }}
                    />
                    {detail.cuts_to_render != null && detail.cuts_to_render < detail.cut_count && (
                      <>
                        <br /><Trans
                          i18nKey="musicVideo.detail.onlySomeRender"
                          values={{ count: detail.cuts_to_render, total: detail.cut_count }}
                          components={{ bold: <b /> }}
                        />
                      </>
                    )}
                  </Alert>
                  <Box>
                    <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} blockDetail={blockDetail} queueMode />
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Button variant="contained" color="warning" onClick={handleApprove} disabled={busy || gpuSubmitBlocked}>
                        {busy ? <CircularProgress size={20} /> : t("musicVideo.detail.approve")}
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        color="error"
                        onClick={handleCancel}
                        disabled={busy}
                      >
                        {t("musicVideo.detail.cancel")}
                      </Button>
                    </Stack>
                  </Box>
                </Stack>
              )}

              {detail.current_stage === "generating" && (
                <Stack spacing={1}>
                  <Typography variant="body2">
                    {t("musicVideo.detail.generatingClips", { done: detail.clips_done, total: detail.clip_count })}
                  </Typography>
                  {activeClipStep && (
                    <Box>
                      <LiveLatentPreview src={getPreviewUrl(activeClipStep.job_id)} />
                      <LinearProgress
                        variant={activeClipStep.progress != null ? "determinate" : "indeterminate"}
                        value={activeClipStep.progress || 0}
                        sx={{ height: 6, borderRadius: 1 }}
                      />
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                        {activeClipStep.message || t("musicVideo.detail.renderingClip")}
                      </Typography>
                    </Box>
                  )}
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    onClick={handleCancel}
                    disabled={busy}
                  >
                    {t("musicVideo.detail.cancelGeneration")}
                  </Button>
                </Stack>
              )}

              {["generating", "assembling", "complete"].includes(detail.current_stage) && (
                <CutQualityList
                  clips={detail.clips}
                  busy={busy}
                  onApprove={(index) => handleHeldCut(index, "approve")}
                  onRerender={(index) => handleHeldCut(index, "rerender")}
                  clipUrl={(index) => `/api/music-video/${detail.id}/clip/${index}?r=${detail.clips?.find((c) => c.index === index)?.rerenders || 0}`}
                />
              )}

              {detail.current_stage === "assembling" && !(detail.status || "").startsWith("failed") && (
                <Stack spacing={1}>
                  <Typography variant="body2">{t("musicVideo.detail.assembling")}</Typography>
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    onClick={handleCancel}
                    disabled={busy}
                  >
                    {t("musicVideo.detail.cancelAssembly")}
                  </Button>
                </Stack>
              )}

              {detail.current_stage === "complete" && detail.output_document_id && (
                <Stack spacing={1}>
                  <Typography variant="subtitle2">{t("musicVideo.detail.done")}:</Typography>
                  <video
                    controls
                    style={{ width: "100%", borderRadius: 8, background: "#000" }}
                    src={documentDownloadUrl(detail.output_document_id)}
                  />
                </Stack>
              )}

              {detail.current_stage === "complete" && !detail.output_document_id && (
                <Alert
                  severity="warning"
                  action={
                    <Button color="inherit" size="small" onClick={handleReassemble} disabled={busy}>
                      {t("musicVideo.detail.reassemble")}
                    </Button>
                  }
                >
                  {t("musicVideo.detail.notInLibrary")}
                </Alert>
              )}

              {(detail.status || "").startsWith("failed") && (
                <Alert
                  severity="error"
                  action={detail.status === "failed_assembling" ? (
                    <Button color="inherit" size="small" onClick={handleReassemble} disabled={busy}>
                      {t("musicVideo.detail.reassemble")}
                    </Button>
                  ) : undefined}
                >
                  <Trans
                    i18nKey="musicVideo.detail.failedAtStage"
                    values={{
                      stage: detail.error_blob?.stage || detail.status,
                      error: String(detail.error_blob?.error || t("musicVideo.detail.unknownError")),
                    }}
                    components={{ bold: <b /> }}
                  />
                </Alert>
              )}

              <Divider sx={{ my: 2 }} />
              <Typography variant="caption" color="text.secondary">
                {t("musicVideo.detail.footer", { cuts: detail.cut_count, done: detail.clips_done, total: detail.clip_count })}
              </Typography>
            </Paper>
          )}
        </Box>
      </Box>
      <Dialog open={duplicateAsk} onClose={() => setDuplicateAsk(false)} maxWidth="xs" fullWidth>
        <DialogTitle>{t("musicVideo.duplicate.title", { name: detail?.name })}</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            {t("musicVideo.duplicate.text")}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDuplicateAsk(false)}>{t("musicVideo.duplicate.cancel")}</Button>
          <Button onClick={() => runDuplicate(false)}>{t("musicVideo.duplicate.fresh")}</Button>
          <Button variant="contained" onClick={() => runDuplicate(true)}>{t("musicVideo.duplicate.reuse")}</Button>
        </DialogActions>
      </Dialog>
      <VersionsDialog
        open={versionsOpen}
        video={detail}
        onClose={() => setVersionsOpen(false)}
        onRestored={async (updated) => {
          setDetail(updated);
          await refreshList();
        }}
      />
    </Box>
  );
};

export default MusicVideoPage;
