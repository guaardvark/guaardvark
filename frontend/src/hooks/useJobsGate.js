import { useState, useEffect, useCallback, useMemo } from "react";
import { getJobsGate } from "../api/jobsService";
import { translate, useUiTranslation } from "../i18n";

const POLL_MS = 4000;

// Plain words for the job holding the card. video_render is the general
// GPU-exclusive kind (stills, edits, songs and storyboards claim it too), so the
// kind alone does not say what is rendering; the native id does. Batch ids carry
// their kind as a prefix (output_registration.BATES_PREFIXES); the other prefixes
// are the ids each caller passes to gpu_session. Mirrors
// job_operation_gate._HOLDER_ID_LABELS on the backend. Each label is a
// function so it is read in the interface's language.
const videoRendering = () => translate("progress.gpu.videoRendering");
const picturesRendering = () => translate("progress.gpu.picturesRendering");
const pictureRendering = () => translate("progress.gpu.pictureRendering");
const HOLDER_ID_LABELS = [
  ["VideoBatch", videoRendering],
  ["ImageBatch", picturesRendering],
  ["blueprint_", picturesRendering],
  ["upload_", picturesRendering],
  ["stills_", pictureRendering],
  ["gen_", pictureRendering],
  ["chat_edit_", () => translate("progress.gpu.pictureEditing")],
  ["chat_anim_", () => translate("progress.gpu.animationRendering")],
  ["infographic_", () => translate("progress.gpu.infographicRendering")],
  ["char_", () => translate("progress.gpu.characterPicturesRendering")],
  ["storyboard_", () => translate("progress.gpu.storyboardRendering")],
  ["prod_", () => translate("progress.gpu.filmCrewRendering")],
  ["mv_", () => translate("progress.gpu.musicVideoRendering")],
  ["video:music3:", () => translate("progress.gpu.songRendering")],
  ["editor_", videoRendering],
  ["router:", videoRendering],
];
// Only the kinds that can hold the card (job_operation_gate.GPU_EXCLUSIVE_KINDS)
// and say what they are by themselves; video_render falls to the generic line.
const HOLDER_KIND_LABELS = {
  training: () => translate("progress.gpu.modelTraining"),
  lora_train: () => translate("progress.gpu.characterTraining"),
};
const genericHolder = () => translate("progress.gpu.generic");

/**
 * Plain words for a gate holder ({kind, native_id}): what is using the card,
 * and the kind:id pair for a tooltip. Never shows the id in the sentence.
 */
export function describeGpuHolder(holder) {
  if (!holder) return { reason: null, detail: null };
  const id = String(holder.native_id || "");
  const kind = String(holder.kind || "");
  const byId = HOLDER_ID_LABELS.find(([prefix]) => id.startsWith(prefix));
  const reason = byId ? byId[1]() : (HOLDER_KIND_LABELS[kind] || genericHolder)();
  const detail = [kind, id].filter(Boolean).join(":") || null;
  return { reason, detail };
}

/**
 * Poll GET /api/jobs/gate so GPU-heavy pages can reflect GPU occupancy.
 *
 * submitMode:
 *   - "immediate" (default): gpuSubmitBlocked while GPU is held or cooling down.
 *     Use for ops that need the GPU right now (editor render, inline storyboard gen).
 *   - "queue": submission is never blocked — the backend stacks jobs and the worker
 *     waits for the GPU (batch video, Celery-dispatched pipelines). gpuBusy stays
 *     true for informational banners and for disabling immediate GPU actions.
 */
export default function useJobsGate({
  enabled = true,
  pollMs = POLL_MS,
  submitMode = "immediate",
} = {}) {
  const { language } = useUiTranslation();
  const [gate, setGate] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await getJobsGate();
      setGate(data);
    } catch {
      // Keep the last known snapshot on transient failures.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    refresh();
    const id = setInterval(refresh, pollMs);
    return () => clearInterval(id);
  }, [enabled, pollMs, refresh]);

  const gpuBusy = Boolean(
    gate?.gpu_busy || (gate?.gpu_cooldown_remaining_s ?? 0) > 0
  );

  const gpuSubmitBlocked = submitMode === "queue" ? false : gpuBusy;
  // Backward compat — existing callers treat gpuBlocked as "disable submit".
  const gpuBlocked = gpuSubmitBlocked;

  // blockReason is a plain sentence for the banner; blockDetail is the
  // holder's kind and id for a tooltip, never printed in the sentence.
  const { blockReason, blockDetail } = useMemo(() => {
    if (!gate) return { blockReason: null, blockDetail: null };
    const cooldown = gate.gpu_cooldown_remaining_s ?? 0;
    if (cooldown > 0) {
      return {
        blockReason: translate("progress.gpu.coolingDown", { seconds: Math.ceil(cooldown) }),
        blockDetail: null,
      };
    }
    if (gate.gpu_busy && gate.gpu_holder) {
      const { reason, detail } = describeGpuHolder(gate.gpu_holder);
      return { blockReason: reason, blockDetail: detail };
    }
    if (gate.gpu_busy) {
      return { blockReason: genericHolder(), blockDetail: null };
    }
    return { blockReason: null, blockDetail: null };
  }, [gate, language]);

  return {
    gate,
    loading,
    gpuBusy,
    gpuSubmitBlocked,
    gpuBlocked,
    blockReason,
    blockDetail,
    refresh,
  };
}