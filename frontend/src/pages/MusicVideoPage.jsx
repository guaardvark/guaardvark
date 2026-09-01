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
} from "@mui/material";
import MusicVideoIcon from "@mui/icons-material/MusicVideo";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import CloseIcon from "@mui/icons-material/Close";

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
} from "../api/musicVideoService";
import { getAllPluginStatus } from "../api/pluginsService";
import { getAvailableModels } from "../api/modelService";
import GpuGateBanner from "../components/common/GpuGateBanner";
import useJobsGate from "../hooks/useJobsGate";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import LiveLatentPreview from "../components/videogen/LiveLatentPreview";
import { formatUiError } from "../utils/uiError";

const POLL_MS = 5000;
const DEFAULT_KEYFRAME_MODEL = "flux-schnell";

// Local presentational + interactive component for the cut plan + Director prompts.
// Keeps the main page component from exploding in size.
function PlanViewer({ detail, busy, models = [], onSavePlan, onRegeneratePlan, onGenerateStoryboards, onRegenStoryboard, comfyReady = true, comfyDisabled = false, comfyInfo = null, gpuBlocked = false }) {
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
      section: c.section_label || c.section || "unlabeled",
      prompt: clip.prompt || "",
      storyboard_path: clip.storyboard_path || null,
    };
  });

  const totalDuration = rows.length ? Math.max(...rows.map((r) => r.end || 0)) : 0;
  const sections = [...new Set(rows.map((r) => r.section))];

  // Local editable state (only used while editable)
  const [edits, setEdits] = useState({}); // { index: prompt }
  const [treatmentEdit, setTreatmentEdit] = useState(detail.director_treatment || detail.director_storyline || "");
  const [storyboardVersions, setStoryboardVersions] = useState({}); // per-cut cache buster for thumbnails
  const [regenErrors, setRegenErrors] = useState({}); // per-cut last error for regen feedback
  const [guidance, setGuidance] = useState("");
  const [regenMode, setRegenMode] = useState(detail.planning_mode || "narrative");

  const isLikelyEmbeddingModel = (m) => {
    if (!m) return false;
    const n = String(m).toLowerCase();
    return ["embed", "embedding", "bge", "nomic", "snowflake", "minilm"].some((k) => n.includes(k));
  };

  const [directorModel, setDirectorModel] = useState(() => {
    const m = detail.director_model || "gemma4:e4b";
    return isLikelyEmbeddingModel(m) ? "gemma4:e4b" : m;
  });

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
    const currentRow = rows.find(r => r.index === index) || {};
    const currentPrompt = currentRow.prompt || "";

    try {
      await onRegenStoryboard(index, { variation, prompt: currentPrompt });
      // clear error for this cut on success
      setRegenErrors(prev => {
        const next = { ...prev };
        delete next[index];
        return next;
      });
    } catch (e) {
      const msg = formatUiError(e?.response?.data?.error) || e?.message || 'Regen failed';
      setRegenErrors(prev => ({ ...prev, [index]: msg }));
      // do not re-throw; parent already surfaced global error
    }
  };

  // When the server detail changes (poll / refresh after save/regen), reset local edits
  useEffect(() => {
    setEdits({});
    setTreatmentEdit(detail.director_treatment || detail.director_storyline || "");
    const m = detail.director_model || "gemma4:e4b";
    setDirectorModel(isLikelyEmbeddingModel(m) ? "gemma4:e4b" : m);
    setRegenMode(detail.planning_mode || "narrative");
  }, [detail.id, detail.current_stage, JSON.stringify(detail.clips?.map((c) => c.prompt)), detail.director_treatment, detail.director_storyline, detail.director_model, detail.planning_mode]);

  const getDisplayedPrompt = (row) => (Object.prototype.hasOwnProperty.call(edits, row.index) ? edits[row.index] : row.prompt);

  const handlePromptChange = (idx, val) => {
    setEdits((prev) => ({ ...prev, [idx]: val }));
  };

  const hasLocalEdits = Object.keys(edits).length > 0 || treatmentEdit !== (detail.director_treatment || detail.director_storyline || "");

  const handleSave = async () => {
    if (!hasLocalEdits && treatmentEdit === (detail.director_treatment || detail.director_storyline || "")) return;
    // Send prompt edits + the (possibly edited) treatment
    const payload = { prompts: edits };
    const currentTreatment = detail.director_treatment || detail.director_storyline || "";
    if (treatmentEdit !== currentTreatment) {
      payload.treatment = treatmentEdit;
    }
    await onSavePlan(payload);
    setEdits({});
  };

  const handleRegen = async () => {
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
        <Typography variant="subtitle2">Video Plan</Typography>
        <Chip size="small" label={`${rows.length} cuts`} />
        {sections.length > 0 && (
          <Typography variant="caption" color="text.secondary">
            sections: {sections.join(" → ")}
          </Typography>
        )}
        {totalDuration > 0 && (
          <Typography variant="caption" color="text.secondary">
            ~{totalDuration.toFixed(1)}s
          </Typography>
        )}
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}>
          Clip stretch (max_stretch / fill_method from settings) scales Director-suggested source motion into exact timeline slots — higher stretch = longer atmospheric holds or slower peaks for the visual arc.
        </Typography>
        {detail.planning_mode && (
          <Chip size="small" variant="outlined" label={`dir: ${detail.planning_mode}`} />
        )}
        {detail.director_model && (
          <Chip
            size="small"
            variant="outlined"
            color={isLikelyEmbeddingModel(detail.director_model) ? "warning" : "default"}
            label={`model: ${isLikelyEmbeddingModel(detail.director_model) ? "gemma4:e4b (was bad)" : detail.director_model}`}
          />
        )}
        {detail.director_enabled === false && (
          <Chip size="small" color="warning" label="Director disabled" />
        )}
        {detail.director_diagnostics && (() => {
          const d = detail.director_diagnostics || {};
          const r = (d.reason || '').toLowerCase();
          const label = (r.includes('empty') || r.includes('no usable') || r.includes('cardinality'))
            ? 'Director: LLM no shots (energy cues used)'
            : (r.includes('llm') || r.includes('exception'))
              ? 'Director: LLM error (cued fallback)'
              : 'Director fallback (prompts may not be unique)';
          const rh = (d.raw_head || '').trim();
          const looksPolluted = /INFO |WARNING |backend\.|PID:|Restoring plugin|health check/.test(rh);
          const rhDisplay = !rh || looksPolluted
            ? '(no clean model reply captured — Ollama connection failed or plugin unavailable before reply)'
            : rh.slice(0, 400) + (rh.length > 400 ? '...' : '');
          const tipLines = [
            `reason: ${d.reason || 'fallback'}`,
            d.note ? `note: ${d.note}` : null,
            d.error ? `error: ${d.error}` : null,
            `raw_head (model reply or error context):\n${rhDisplay}`,
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
          Director LLM did not produce distinct per-cut visual prompts (hover the chip for the raw model output head + reason, e.g. ``` or echoed style).
          Automatic energy cue variations were injected instead of pure repeats. Edit the boxes above or use the <b>Regenerate</b> form below with more specific guidance.
          For very long songs (75+ cuts) the small local model often struggles — shorter guidance or a stronger pulled gemma helps.
        </Alert>
      )}
      {hasLocalEdits && (
        <Typography variant="caption" color="info.main" sx={{ display: 'block', mb: 1 }}>
          Treatment/arc edited — Generate Storyboards or per-cut Regen will use the updated visuals. (Bulk force-refresh available via API for existing stills.)
        </Typography>
      )}

      {/* Pipeline / Model summary (the new controls) */}
      <Stack direction="row" spacing={1} sx={{ mb: 1, flexWrap: "wrap" }}>
        {detail.use_lora_consistency ? (
          <Chip size="small" color="secondary" label="LoRA consistency ON (cast identity stills)" />
        ) : (
          <Chip size="small" label="LoRA consistency OFF (flexible models)" />
        )}
        {detail.keyframe_model && (
          <Chip size="small" variant="outlined" label={`keyframe: ${detail.keyframe_model}`} />
        )}
        {detail.i2v_model && (
          <Chip size="small" variant="outlined" color="primary" label={`I2V: ${detail.i2v_model}`} />
        )}
      </Stack>

      {/* Treatment / Story editor (user can paste full short story here, or edit the AI-generated one).
          This is treated as the screenplay. Per-cut prompts should advance this story. */}
      <Box sx={{ mb: 1.5 }}>
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          Visual Treatment / Story {isEditable ? "(edit and save to use as screenplay)" : ""}
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
            placeholder="Paste or edit the full narrative/treatment the Director should follow for this video..."
            sx={{ mt: 0.5 }}
          />
        ) : (
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", mt: 0.5, fontSize: "0.875rem", opacity: 0.9 }}>
            {detail.director_treatment || detail.director_storyline || "(no treatment — prompts were generated from style only)"}
          </Typography>
        )}
      </Box>

      {/* Dedicated Storyboards review section (thumbnails first flow).
          These appear after you click "Generate Storyboards". Review here or inline in the cuts below.
          Use per-cut regen if one doesn't match the story/treatment. */}
      {rows.some(r => r.storyboard_path) && (
        <Box sx={{ mb: 1.5, p: 1, border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
          <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
            Reviewed Storyboards / Thumbnails (click Regen per cut if needed)
            {hasLocalEdits && " — arc updated; per-cut Regen applies new prompts"}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
            {rows.filter(r => r.storyboard_path).map(r => (
              <Box key={r.index} sx={{ textAlign: "center" }}>
                <img
                  src={`/api/music-video/${detail.id}/storyboard/${r.index}?v=${storyboardVersions[r.index] || 0}`}
                  alt={`SB ${r.index}`}
                  style={{ width: 120, height: 68, objectFit: "cover", borderRadius: 4, border: "1px solid #444" }}
                  onError={(e) => { e.target.style.display = "none"; }}
                />
                <Typography variant="caption" display="block">#{r.index} {r.section}</Typography>
                <Typography variant="caption" sx={{ fontSize: "0.55rem", opacity: 0.65, display: "block" }}>
                  advances {r.section} (energy {r.energy.toFixed(1)}) of the visual treatment arc
                </Typography>
                {isEditable && onRegenStoryboard && (
                  <Button size="small" onClick={() => handleRegenStoryboard(r.index)} disabled={busy || gpuBlocked || !comfyReady} sx={{ fontSize: "0.65rem", py: 0 }}>
                    Regen
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
      {(detail.director_treatment || detail.director_storyline) && (
        <Box sx={{ mb: 1.5, p: 1.25, backgroundColor: "rgba(255,215,0,0.07)", borderRadius: 1, border: "1px solid rgba(255,215,0,0.25)" }}>
          <Typography variant="caption" sx={{ fontWeight: 600, color: "#ffd700", display: "block", mb: 0.5 }}>
            VISUAL TREATMENT (screenwriter-style story arc)
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
            height: 6,
            mb: 1,
            borderRadius: 1,
            overflow: "hidden",
            backgroundColor: "rgba(255,255,255,0.08)",
          }}
          title="Energy arc (left-to-right = cuts). Color = intensity (cool blue=low/calm, warm red=high/drop). Matches the song analysis the Director uses."
        >
          {rows.map((r, i) => (
            <Box
              key={i}
              title={`cut ${r.index}: ${r.section} @ ${(r.energy || 0).toFixed(2)}`}
              sx={{
                flex: 1,
                height: "100%",
                backgroundColor: arcColors(r.energy || 0),
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
                <Box key={row.index} sx={{ pl: 0.5 }}>
                  <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.25 }}>
                    <Chip size="small" label={`#${row.index}`} />
                    <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                      {timeLabel}
                    </Typography>
                    <Chip size="small" variant="outlined" label={row.section} />
                    <Typography variant="caption" color="text.secondary">
                      energy {(row.energy || 0).toFixed(2)}
                    </Typography>
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
                      placeholder="Visual description for this cut (Director will have filled this)..."
                      sx={{ fontSize: "0.875rem" }}
                    />
                  ) : (
                    <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", opacity: 0.9, pl: 0.5 }}>
                      {displayed || "(no prompt — global style used)"}
                    </Typography>
                  )}
                  <Typography variant="caption" sx={{ fontSize: "0.55rem", opacity: 0.65, mt: 0.25, display: "block" }}>
                    advances {row.section} (energy {row.energy.toFixed(1)}) of the visual treatment arc above
                  </Typography>
                  {detail.director_diagnostics && displayed && (displayed.length < ((detail.style_prompt || '').length + 80)) && (
                    <Typography variant="caption" color="warning.main" sx={{ fontSize: '0.6rem', display: 'block', mt: 0.25 }}>
                      ⚡ energy-cued only (LLM supplied no unique scene description for this cut — edit or regenerate for richer shots)
                    </Typography>
                  )}

                  {/* Storyboard thumbnail (generated in the "thumbnails first" review step) */}
                  {row.storyboard_path && (
                    <Box sx={{ mt: 0.75 }}>
                      <img
                        src={`/api/music-video/${detail.id}/storyboard/${row.index}?v=${storyboardVersions[row.index] || 0}`}
                        alt={`Storyboard for cut ${row.index}`}
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
                          Regen this storyboard
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
              onClick={handleSave}
            >
              Save plan changes
            </Button>
            {hasLocalEdits && (
              <Link component="button" variant="caption" onClick={() => setEdits({})}>
                discard local edits
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
                Generate Storyboards (thumbnails first)
              </Button>
            )}
            {comfyDisabled && onGenerateStoryboards && (
              <Typography variant="caption" color="warning.main" sx={{ ml: 1 }}>
                ComfyUI plugin must be enabled for storyboards (keyframe_model: {detail?.keyframe_model || 'flux-schnell'})
              </Typography>
            )}
            {!comfyDisabled && comfyInfo && comfyInfo.status !== 'running' && onGenerateStoryboards && (
              <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                ComfyUI will start on demand for storyboards (keyframe_model: {detail?.keyframe_model || 'flux-schnell'})
              </Typography>
            )}
          </Stack>

          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
            Regenerate all prompts with the Director (uses current song cut plan + your guidance)
          </Typography>
          <Stack direction="row" spacing={1} alignItems="flex-start">
            <TextField
              size="small"
              fullWidth
              multiline
              minRows={1}
              maxRows={3}
              placeholder="Optional guidance (e.g. 'abstract mood arc, slow light play and textures in the intro, sharp pulsing geometry at the drop, ethereal and dissolving in the outro')"
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
            />
            <TextField
              select
              size="small"
              sx={{ minWidth: 140 }}
              value={regenMode}
              onChange={(e) => setRegenMode(e.target.value)}
              label="mode for regen"
            >
              <MenuItem value="narrative">narrative</MenuItem>
              <MenuItem value="visual">visual / mood arc</MenuItem>
            </TextField>
            <TextField
              select
              size="small"
              sx={{ minWidth: 170 }}
              value={isLikelyEmbeddingModel(directorModel) ? "gemma4:e4b" : directorModel}
              onChange={(e) => setDirectorModel(e.target.value)}
              label="director model"
            >
              {directorModelOptions.map((m) => (
                <MenuItem key={m} value={m}>
                  {m === "gemma4:e4b" ? "gemma4:e4b (default small)" : m}
                </MenuItem>
              ))}
            </TextField>
            <Button
              size="small"
              variant="contained"
              disabled={busy || gpuBlocked}
              onClick={handleRegen}
            >
              Regenerate
            </Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
            Tip: Guidance shapes the VISUAL TREATMENT first (the creative story arc in your exact style), then the per-cut prompts are derived from it. Example: “a lone vampire gunslinger seeks revenge; the town awakens with undead in the drop; tragic quiet beauty in the outro”.
            The Director uses a dedicated small/fast model (gemma4:e4b by default, overridable via director_model in settings) — separate from your main chat/brain model — for reliable structured JSON output.
            Manual "Regen this storyboard" now adds a random variation to the seed so the same prompt usually produces a visibly different image.
          </Typography>
        </Box>
      )}

      {!isEditable && rows.length > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
          Plan is locked (generation started or complete). The prompts above are what the Director produced and what was used for the stills + motion.
        </Typography>
      )}
    </Box>
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

const stageLabel = (mv) => {
  if (!mv) return "";
  if (needsAnalysis(mv) && mv.current_stage === "awaiting_approval") return "needs analysis";
  if (needsAnalysis(mv) && (mv.status || "").startsWith("failed")) return "analysis failed";
  if (needsAnalysis(mv) && ((mv.status || "").startsWith("cancelled") || mv.current_stage === "cancelled")) {
    return "cancelled (no plan)";
  }
  return mv.current_stage || mv.status || "unknown";
};

const MusicVideoPage = () => {
  const { gpuBusy, gpuSubmitBlocked, blockReason } = useJobsGate({ submitMode: "queue" });
  const { getProcessesByType, activeProcesses, getPreviewUrl } = useUnifiedProgress();
  const [videos, setVideos] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);

  const [name, setName] = useState("");
  const [stylePrompt, setStylePrompt] = useState("");
  const [userTreatment, setUserTreatment] = useState("");  // pasted or edited full story/treatment
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [pluginStatus, setPluginStatus] = useState(null); // {comfyui_reachable, ...} or {status:{comfyui:'stopped',...}} or {plugins:[...]} for storyboard guards
  const [models, setModels] = useState([]); // installed Ollama models for the director-model dropdown (embedding models filtered backend-side)
  const fileInputRef = useRef(null);

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
  const [i2vModel, setI2vModel] = useState("wan22-14b-i2v");

  const keyframeModelOptions = useMemo(
    () =>
      useLoraConsistency
        ? [
            {
              value: "from-lora",
              label: "Auto (character LoRA base — Z-Image / SDXL / FLUX)",
            },
          ]
        : [
            { value: "flux-schnell", label: "FLUX.1-schnell (fast, beautiful stills) — default" },
            { value: "flux-dev-lora", label: "FLUX-dev + LoRA" },
            { value: "sdxl", label: "SDXL (no LoRA)" },
            { value: "sdxl-lora", label: "SDXL + LoRAs" },
          ],
    [useLoraConsistency],
  );

  useEffect(() => {
    if (!keyframeModelOptions.some((opt) => opt.value === keyframeModel)) {
      setKeyframeModel(keyframeModelOptions[0]?.value || DEFAULT_KEYFRAME_MODEL);
    }
  }, [keyframeModelOptions, keyframeModel]);

  // Cast picker: trained character Subjects whose LoRA + bible lock identity per cut.
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

  // I2V-capable models. The registry decides which models animate a keyframe
  // (capabilities.supports_i2v); this static list only stands in until the
  // API answers or when it is down.
  const FALLBACK_I2V_MODEL_OPTIONS = {
    "wan22-14b-i2v": {
      label: "Wan 2.2 14B I2V (GGUF Q5) — Recommended",
      description: "Excellent cinematic motion, ~5s clips, good VRAM efficiency",
    },
    "cogvideox-5b-i2v": {
      label: "CogVideoX 5B I2V",
      description: "Solid alternative I2V (~6s)",
    },
    "hunyuan-i2v": {
      label: "HunyuanVideo 13B I2V (GGUF Q5)",
      description: "Follows the keyframe closely, 24fps, ~3s clips",
    },
  };
  const [i2vModelOptions, setI2vModelOptions] = useState(FALLBACK_I2V_MODEL_OPTIONS);
  useEffect(() => {
    let alive = true;
    fetch("/api/batch-video/models")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!alive) return;
        const rows = (data?.data?.models || []).filter((m) => m.capabilities?.supports_i2v);
        if (!rows.length) return;
        const opts = {};
        rows.forEach((m) => {
          const caps = m.capabilities;
          const seconds = caps.max_frames && caps.native_fps ? Math.round(caps.max_frames / caps.native_fps) : null;
          opts[m.id] = {
            label: `${m.name}${m.is_ready ? "" : " (not installed)"}`,
            description: [
              caps.audio_out ? "renders each cut in one pass with the song slice as its beat anchor" : "keyframe → silent clip",
              seconds ? `up to ~${seconds}s per clip` : null,
              m.license?.attribution || null,
            ].filter(Boolean).join(" · "),
            ready: !!m.is_ready,
          };
        });
        setI2vModelOptions(opts);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const refreshList = useCallback(async () => {
    try {
      const data = await listMusicVideos();
      setVideos(data.music_videos || []);
    } catch (e) {
      // non-fatal — keep the last list
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

  const handleCreate = async () => {
    setError(null);
    if (!name.trim() || !stylePrompt.trim() || !file) {
      setError("Name, a song file, and a style prompt are all required.");
      return;
    }
    setBusy(true);
    try {
      // 1) upload the song → Document id. /api/docs/upload resolves the raw body
      // {document_id, filename, job_id, ...}; tolerate a couple of shapes.
      const up = await uploadFile(file, null, "music-video-song", {});
      const songDocId = up?.document_id ?? up?.data?.id ?? up?.id;
      if (!songDocId) throw new Error("Song upload failed (no document id returned).");
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
      setKeyframeModel(DEFAULT_KEYFRAME_MODEL);
      setI2vModel("wan22-14b-i2v");
      // Reset any future advanced model params here too
      if (fileInputRef.current) fileInputRef.current.value = "";
      await refreshList();
      setSelectedId(mv.id);
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to create music video.");
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
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to start analysis.");
    } finally {
      setBusy(false);
    }
  };

  const handleApprove = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await approveMusicVideo(detail.id);
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || "Approve failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!detail) return;
    const analyzing = detail.current_stage === "analyzing";
    const msg = analyzing
      ? "Cancel song analysis? You can retry later with Continue & Analyze — your song and settings are kept."
      : "Cancel this music video generation? Any pending clips will be skipped (current clip may finish).";
    if (!window.confirm(msg)) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await cancelMusicVideo(detail.id);
      setDetail(updated);
      await refreshList();
    } catch (e) {
      setError(formatUiError(e?.response?.data?.error) || e.message || "Cancel failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id, label) => {
    if (!window.confirm(`Remove "${label}" from the log? The entry is cleared; any rendered output file is left on disk.`)) {
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
      setError(formatUiError(e?.response?.data?.error) || e.message || "Delete failed.");
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
    if (!window.confirm(`Clear ${finished.length} finished generation(s) from the log?`)) {
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
      setError(formatUiError(e?.response?.data?.error) || e.message || "Clear failed.");
    }
  };

  const finishedCount = videos.filter(
    (v) =>
      v.current_stage === "complete" ||
      (v.status || "").startsWith("failed") ||
      (v.status || "").startsWith("cancelled") ||
      v.current_stage === "cancelled",
  ).length;

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
          <Typography variant="h5">Music Video</Typography>
          <Typography variant="body2" color="text.secondary">
            Upload a song and a visual style. The beats and energy drive the edits;
            a unique clip is generated per cut and assembled in sync with your song.
          </Typography>
        </Box>
      </Stack>

      <Box sx={{ display: "flex", gap: 3, alignItems: "flex-start", flexWrap: "wrap" }}>
        {/* Create + list */}
        <Stack spacing={2} sx={{ flex: "1 1 360px", minWidth: 320 }}>
          <Paper variant="outlined" sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ mb: 1.5 }}>New music video</Typography>
            <Stack spacing={1.5}>
              <TextField
                label="Name" size="small" fullWidth value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <TextField
                label="Visual style / prompt" size="small" fullWidth multiline minRows={3}
                placeholder="animation style, deep blue colors, loss and heartache, slow movement"
                value={stylePrompt}
                onChange={(e) => setStylePrompt(e.target.value)}
              />
              <TextField
                label="Visual Treatment / Short Story (optional — paste or write the full narrative the Director should follow)"
                size="small" fullWidth multiline minRows={5}
                placeholder="Paste a detailed story or treatment here (e.g. the 1000-word gothic western vignette). The Director will use this as the screenplay and map it to the song's energy arc and cuts. Leave blank to let the AI invent one from the style prompt."
                value={userTreatment}
                onChange={(e) => setUserTreatment(e.target.value)}
              />
              <Button
                component="label" variant="outlined" startIcon={<UploadFileIcon />}
                sx={{ justifyContent: "flex-start" }}
              >
                {file ? file.name : "Choose song (mp3 / wav)"}
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
                {showAdvanced ? "▾ Advanced render options" : "▸ Advanced render options"}
              </Link>
              <Collapse in={showAdvanced}>
                <Stack spacing={1.5} sx={{ pt: 0.5 }}>
                  <TextField
                    select size="small" fullWidth label="Clip motion fill"
                    value={fillMethod} onChange={(e) => setFillMethod(e.target.value)}
                    helperText="How a clip is stretched to fill its cut. Forward = no reverse (fixes the moonwalk)."
                  >
                    <MenuItem value="forward">Forward (no reverse) — recommended</MenuItem>
                    <MenuItem value="boomerang">Boomerang (forward + reverse)</MenuItem>
                    <MenuItem value="loop">Loop (forward repeat)</MenuItem>
                  </TextField>
                  <TextField
                    type="number" size="small" fullWidth label="Clip stretch (×)"
                    value={maxStretch} onChange={(e) => setMaxStretch(e.target.value)}
                    inputProps={{ min: 1, max: 4, step: 0.5 }}
                    helperText="Higher = fewer clips, more slow-mo. 2 = natural. Raise to trade GPU for slowdown."
                  />
                  <TextField
                    select size="small" fullWidth label="Frame interpolation (RIFE)"
                    value={interp} onChange={(e) => setInterp(e.target.value)}
                    helperText="More frames for smoother slow-mo. Cheap (no extra diffusion)."
                  >
                    <MenuItem value="1">Off</MenuItem>
                    <MenuItem value="2">2× (smooth)</MenuItem>
                    <MenuItem value="4">4×</MenuItem>
                  </TextField>
                  <TextField
                    type="number" size="small" fullWidth label="Denoising steps (optional)"
                    value={i2vSteps} onChange={(e) => setI2vSteps(e.target.value)}
                    inputProps={{ min: 8, max: 60, step: 1 }}
                    placeholder="engine default (25)"
                    helperText="Bump a hair for crisper frames when slowing clips down more."
                  />

                  {/* Director / storyboarding controls */}
                  <TextField
                    select
                    size="small"
                    fullWidth
                    label="Director planning mode"
                    value={planningMode}
                    onChange={(e) => setPlanningMode(e.target.value)}
                    helperText="Narrative = consistent world + characters. Visual = abstract mood arc / energy-driven visuals (great for instrumental, ambient, soundtrack)."
                  >
                    <MenuItem value="narrative">Narrative continuity (default)</MenuItem>
                    <MenuItem value="visual">Visual / mood arc (abstract, textures, energy poem)</MenuItem>
                  </TextField>

                  <Stack direction="row" alignItems="center" spacing={1} sx={{ pl: 0.5 }}>
                    <input
                      type="checkbox"
                      checked={directorEnabled}
                      onChange={(e) => setDirectorEnabled(e.target.checked)}
                      id="director-enabled"
                    />
                    <label htmlFor="director-enabled" style={{ fontSize: "0.875rem", color: "rgba(255,255,255,0.7)" }}>
                      Use Director for distinct per-cut prompts (recommended)
                    </label>
                  </Stack>

                  {/* === Storyboard / Keyframe + I2V Pipeline (modeled after VideoGeneratorPage) === */}
                  <Divider sx={{ my: 1 }} />
                  <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
                    Storyboard Keyframe &amp; Animation (I2V)
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
                          setKeyframeModel(DEFAULT_KEYFRAME_MODEL);
                        }
                      }}
                      id="lora-consistency"
                    />
                    <label htmlFor="lora-consistency" style={{ fontSize: "0.875rem", color: "rgba(255,255,255,0.7)" }}>
                      Use LoRA consistency (bake cast identity into keyframes — Z-Image/SDXL/FLUX by train base; i2v does not reload face LoRAs)
                    </label>
                  </Stack>
                  <Typography variant="caption" color="text.secondary" sx={{ pl: 2.5, display: "block" }}>
                    Checked = lock a trained character into every cut (LoRA + identity core). Pick cast below; keyframes are generated on demand from each cut prompt. Unchecked = free stills without identity lock.
                  </Typography>

                  {useLoraConsistency && (
                    <TextField
                      select
                      size="small"
                      fullWidth
                      label="Cast (trained characters to lock)"
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
                            .join(", ") || "Select cast…",
                      }}
                      helperText={
                        castSubjects.length === 0
                          ? "No trained characters found. Train one in the Cast Library first."
                          : "Their trigger word + appearance bible are injected into every keyframe."
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
                    label="I2V / Animation Model"
                    value={i2vModel}
                    onChange={(e) => setI2vModel(e.target.value)}
                    helperText="Wan 2.2 I2V generally produces superior motion/quality (may use more time/VRAM — your choice)"
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
                      Keyframe still model is automatic from the cast character&apos;s training base
                      (Z-Image / SDXL / FLUX). Storyboard frames are generated on demand from each cut prompt + LoRA.
                    </Alert>
                  ) : (
                    <TextField
                      select
                      size="small"
                      fullWidth
                      label="Keyframe / Storyboard Image Model"
                      value={keyframeModel}
                      onChange={(e) => setKeyframeModel(e.target.value)}
                      helperText="FLUX-schnell for fast beautiful stills; SDXL for higher-fidelity stills without LoRA."
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
              <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} queueMode />
              <Button variant="contained" onClick={handleCreate} disabled={busy || gpuSubmitBlocked}>
                {busy ? <CircularProgress size={20} /> : (hasActiveGeneration ? "Add another to queue" : "Create & Analyze")}
              </Button>
              {hasActiveGeneration && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5 }}>
                  A generation is in progress — additional creations will queue (GPU work serializes automatically).
                </Typography>
              )}
            </Stack>
          </Paper>

          <Paper variant="outlined" sx={{ p: 2 }}>
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle1">Your music videos</Typography>
              {finishedCount > 0 && (
                <Link
                  component="button" type="button" variant="caption" underline="hover"
                  color="text.secondary" onClick={handleClearFinished}
                >
                  Clear finished ({finishedCount})
                </Link>
              )}
            </Stack>
            {videos.length === 0 && (
              <Typography variant="body2" color="text.secondary">None yet.</Typography>
            )}
            <Stack spacing={1}>
              {videos.map((v) => (
                <Paper
                  key={v.id}
                  variant="outlined"
                  onClick={() => setSelectedId(v.id)}
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
                    <Tooltip title="Remove from log">
                      <IconButton
                        size="small"
                        onClick={(e) => { e.stopPropagation(); handleDelete(v.id, v.name); }}
                        sx={{ opacity: 0.5, "&:hover": { opacity: 1 } }}
                      >
                        <CloseIcon fontSize="inherit" />
                      </IconButton>
                    </Tooltip>
                  </Stack>
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
                Select a music video to see its progress.
              </Typography>
            </Paper>
          ) : (
            <Paper variant="outlined" sx={{ p: 2.5 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="h6">{detail.name}</Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip label={stageLabel(detail)} color={stageColor(detail.current_stage, detail.status, detail.cut_count)} />
                  {needsAnalysis(detail) && !isAnalysisRunning(detail) && (
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={handleContinueAnalyze}
                      disabled={busy}
                    >
                      {busy ? <CircularProgress size={18} /> : "Continue & Analyze"}
                    </Button>
                  )}
                  {hasCutPlan(detail) && (detail.current_stage === "complete" || (detail.status || "").match(/^(failed|cancelled)/) || detail.current_stage === "cancelled") && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={async () => {
                        const msg = "Reset this video back to plan review so you can re-edit the treatment/prompts or re-render? The previous output will be cleared.";
                        if (!window.confirm(msg)) return;
                        try {
                          setBusy(true);
                          await replanMusicVideo(detail.id);
                          await refreshDetail(detail.id);
                        } catch (e) {
                          setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to re-plan");
                        } finally {
                          setBusy(false);
                        }
                      }}
                      disabled={busy}
                    >
                      Re-plan & Re-render
                    </Button>
                  )}
                  {needsAnalysis(detail) && ((detail.status || "").match(/^(failed|cancelled)/) || detail.current_stage === "cancelled") && (
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={handleContinueAnalyze}
                      disabled={busy}
                    >
                      Retry Analysis
                    </Button>
                  )}
                </Stack>
              </Stack>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {detail.style_prompt}
              </Typography>
              <Divider sx={{ mb: 2 }} />

              {needsAnalysis(detail) && !isAnalysisRunning(detail) && (
                <Stack spacing={1.5} sx={{ mb: 2 }}>
                  <Alert severity={(detail.status || "").startsWith("failed") ? "error" : "info"}>
                    {(detail.status || "").startsWith("failed") ? (
                      <>
                        Analysis failed
                        {detail.error_blob?.error ? `: ${String(detail.error_blob.error)}` : ""}.
                        {" "}Your song and settings are saved — click <b>Continue &amp; Analyze</b> to retry.
                      </>
                    ) : (
                      <>
                        This job was created but the song has not been analyzed yet
                        {(detail.current_stage === "awaiting_approval") ? " (stuck at approval gate with no cut plan)" : ""}.
                        {" "}Click <b>Continue &amp; Analyze</b> to build the beat map and Director plan — no need to re-upload.
                      </>
                    )}
                  </Alert>
                  <Box>
                    <Button variant="contained" onClick={handleContinueAnalyze} disabled={busy}>
                      {busy ? <CircularProgress size={20} /> : "Continue & Analyze"}
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
                        Cancel
                      </Button>
                    )}
                  </Box>
                </Stack>
              )}

              {/* === Video Plan / Director output (visible after analysis) === */}
              {detail.cut_plan && detail.cut_plan.length > 0 && (
                <PlanViewer
                  detail={detail}
                  busy={busy}
                  gpuBlocked={gpuBusy}
                  models={models}
                  comfyReady={comfyReady}
                  comfyDisabled={comfyDisabled}
                  comfyInfo={comfyInfo}
                  onSavePlan={async (arg1, arg2) => {
                    try {
                      setBusy(true);
                      let payload = {};
                      if (arg1 && typeof arg1 === 'object' && !Array.isArray(arg1)) {
                        // New shape from PlanViewer: {prompts, treatment?, style_prompt? }
                        payload = { ...arg1 };
                      } else {
                        // Legacy shape
                        payload = { prompts: arg1 || {} };
                        if (arg2 && arg2.trim()) payload.style_prompt = arg2.trim();
                      }
                      await updateMusicVideoPlan(detail.id, payload);
                      await refreshDetail(detail.id);
                    } catch (e) {
                      setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to save plan edits");
                    } finally {
                      setBusy(false);
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
                      setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to generate storyboards");
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
                      setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to regen storyboard");
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
                      setError(formatUiError(e?.response?.data?.error) || e.message || "Failed to regenerate plan");
                    } finally {
                      setBusy(false);
                    }
                  }}
                />
              )}

              <Divider sx={{ my: 2 }} />

              {isAnalysisRunning(detail) && (
                <Stack spacing={1} sx={{ mb: 2 }}>
                  <Typography variant="body2">Analyzing the song for beats &amp; energy…</Typography>
                  <LinearProgress />
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    onClick={handleCancel}
                    disabled={busy}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    Cancel Analysis
                  </Button>
                </Stack>
              )}

              {detail.current_stage === "awaiting_approval" && hasCutPlan(detail) && detail.estimate && (
                <Stack spacing={1.5}>
                  <Alert severity="warning">
                    Plan ready for review. { (detail.clips || []).some(c => c.storyboard_path) 
                      ? "Storyboards generated — review/regen individuals below, then Approve & Generate the full video (i2v)." 
                      : "Use the 'Generate Storyboards' button in the plan below to create thumbnails first for review." }
                    <br />Estimated full video time after storyboards: <b>{detail.estimate.estimated_human}</b>.
                  </Alert>
                  <Box>
                    <GpuGateBanner gpuBusy={gpuBusy} blockReason={blockReason} queueMode />
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Button variant="contained" color="warning" onClick={handleApprove} disabled={busy || gpuSubmitBlocked}>
                        {busy ? <CircularProgress size={20} /> : "Approve & Generate Video"}
                      </Button>
                      <Button
                        size="small"
                        variant="outlined"
                        color="error"
                        onClick={handleCancel}
                        disabled={busy}
                      >
                        Cancel
                      </Button>
                    </Stack>
                  </Box>
                </Stack>
              )}

              {detail.current_stage === "generating" && (
                <Stack spacing={1}>
                  <Typography variant="body2">
                    Generating clips: {detail.clips_done} / {detail.clip_count}
                  </Typography>
                  <LinearProgress
                    variant={detail.clip_count ? "determinate" : "indeterminate"}
                    value={detail.clip_count ? (detail.clips_done / detail.clip_count) * 100 : 0}
                  />
                  {activeClipStep && (
                    <Box>
                      <LiveLatentPreview src={getPreviewUrl(activeClipStep.job_id)} />
                      <LinearProgress
                        variant={activeClipStep.progress != null ? "determinate" : "indeterminate"}
                        value={activeClipStep.progress || 0}
                        sx={{ height: 6, borderRadius: 1 }}
                      />
                      <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                        {activeClipStep.message || "Rendering current clip…"}
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
                    Cancel Generation
                  </Button>
                </Stack>
              )}

              {detail.current_stage === "assembling" && (
                <Stack spacing={1}>
                  <Typography variant="body2">Assembling the final cut in sync with your song…</Typography>
                  <LinearProgress />
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    onClick={handleCancel}
                    disabled={busy}
                  >
                    Cancel Assembly
                  </Button>
                </Stack>
              )}

              {detail.current_stage === "complete" && detail.output_document_id && (
                <Stack spacing={1}>
                  <Typography variant="subtitle2">Done — your music video:</Typography>
                  <video
                    controls
                    style={{ width: "100%", borderRadius: 8, background: "#000" }}
                    src={documentDownloadUrl(detail.output_document_id)}
                  />
                </Stack>
              )}

              {(detail.status || "").startsWith("failed") && (
                <Alert severity="error">
                  Failed at stage <b>{detail.error_blob?.stage || detail.status}</b>:{" "}
                  {String(detail.error_blob?.error || "unknown error")}
                </Alert>
              )}

              <Divider sx={{ my: 2 }} />
              <Typography variant="caption" color="text.secondary">
                {detail.cut_count} cuts · {detail.clips_done}/{detail.clip_count} clips rendered
              </Typography>
            </Paper>
          )}
        </Box>
      </Box>
    </Box>
  );
};

export default MusicVideoPage;
