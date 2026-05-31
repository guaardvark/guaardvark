// Submit a Plan request and poll for completion. This hook is the single
// lifecycle model for the Plan pipeline: request submission, queued/running
// polling, terminal result/error state, and explicit result updates.

import { useState, useEffect, useRef, useCallback } from "react";
import { submitPlan, getPlanJob, getVideoEditorErrorMessage } from "../../api/videoEditorService";

const ACTIVE_STATUSES = new Set(["submitting", "queued", "running"]);

const initialState = {
  status: "idle",
  job: null,
  result: null,
  error: null,
  progress: 0,
  stageLabel: "Idle",
  startedAt: null,
  finishedAt: null,
};

const normalizeJobStatus = (jobStatus) => {
  if (jobStatus === "done" || jobStatus === "failed") return jobStatus;
  if (jobStatus === "queued") return "queued";
  return "running";
};

export function usePlanJob() {
  const [state, setState] = useState(initialState);
  const pollRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }, []);

  const applyJob = useCallback((fresh) => {
    const nextStatus = normalizeJobStatus(fresh.status);
    setState((prev) => ({
      ...prev,
      job: fresh,
      status: nextStatus,
      result: nextStatus === "done" ? fresh.result || null : prev.result,
      error: nextStatus === "failed" ? fresh.error || "Plan failed" : null,
      progress: typeof fresh.progress === "number" ? fresh.progress : prev.progress,
      stageLabel: fresh.message || (nextStatus === "done" ? "Plan ready" : nextStatus === "failed" ? "Plan failed" : prev.stageLabel),
      finishedAt: nextStatus === "done" || nextStatus === "failed" ? Date.now() : null,
    }));
    if (nextStatus === "done" || nextStatus === "failed") {
      stopPolling();
    }
  }, [stopPolling]);

  const start = useCallback(async (planRequest) => {
    stopPolling();
    setState({
      ...initialState,
      status: "submitting",
      stageLabel: "Submitting Plan",
      startedAt: Date.now(),
    });
    try {
      const submitted = await submitPlan(planRequest);
      const submittedJob = {
        id: submitted.job_id,
        status: submitted.status || "queued",
        progress: 0,
        message: submitted.message || "Queued",
      };
      setState((prev) => ({
        ...prev,
        job: submittedJob,
        status: normalizeJobStatus(submittedJob.status),
        progress: 0,
        stageLabel: submittedJob.message,
      }));
    } catch (e) {
      setState((prev) => ({
        ...prev,
        status: "failed",
        error: e.videoEditorMessage || getVideoEditorErrorMessage(e, "Plan failed"),
        progress: 0,
        stageLabel: "Plan failed",
        finishedAt: Date.now(),
      }));
    }
  }, [stopPolling]);

  const cancel = useCallback(() => {
    stopPolling();
    setState((prev) => ({
      ...prev,
      status: "idle",
      job: null,
      error: null,
      progress: 0,
      stageLabel: "Idle",
      finishedAt: Date.now(),
    }));
  }, [stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    setState(initialState);
  }, [stopPolling]);

  // Seed a previously-computed result (e.g. an arrangement restored from a saved
  // project) so reopening doesn't force a re-Plan. Marks the job done.
  const hydrate = useCallback((result) => {
    if (!result) return;
    stopPolling();
    setState({
      ...initialState,
      status: "done",
      result,
      progress: 1,
      stageLabel: "Plan restored",
      finishedAt: Date.now(),
    });
  }, [stopPolling]);

  const clearResult = useCallback(() => {
    setState((prev) => ({
      ...prev,
      status: ACTIVE_STATUSES.has(prev.status) ? prev.status : "idle",
      result: null,
      error: null,
      progress: ACTIVE_STATUSES.has(prev.status) ? prev.progress : 0,
      stageLabel: ACTIVE_STATUSES.has(prev.status) ? prev.stageLabel : "Idle",
      finishedAt: null,
    }));
  }, []);

  const updateClipAnalysis = useCallback((clipId, nextAnalysis) => {
    setState((prev) => {
      if (!prev.result?.clip_analyses) return prev;
      const clipAnalyses = prev.result.clip_analyses.map((analysis) =>
        analysis.clip_id === clipId ? { ...analysis, ...nextAnalysis, clip_id: clipId } : analysis,
      );
      return {
        ...prev,
        result: {
          ...prev.result,
          clip_analyses: clipAnalyses,
        },
      };
    });
  }, []);

  // Poll while job is in flight.
  useEffect(() => {
    const jobId = state.job?.id;
    if (!jobId || !ACTIVE_STATUSES.has(state.status) || state.status === "submitting") return;
    pollRef.current = setInterval(async () => {
      try {
        const fresh = await getPlanJob(jobId);
        applyJob(fresh);
      } catch (e) {
        setState((prev) => ({
          ...prev,
          stageLabel: "Waiting for Plan status",
          error: e.videoEditorMessage || getVideoEditorErrorMessage(e, "Could not poll Plan job"),
        }));
        console.warn("plan poll:", e);
      }
    }, 1000);
    return () => {
      stopPolling();
    };
  }, [state.job?.id, state.status, applyJob, stopPolling]);

  const planning = ACTIVE_STATUSES.has(state.status);

  return {
    start,
    cancel,
    reset,
    clearResult,
    hydrate,
    updateClipAnalysis,
    planning,
    status: state.status,
    stageLabel: state.stageLabel,
    progress: state.progress,
    job: state.job,
    result: state.result,
    error: state.error,
    startedAt: state.startedAt,
    finishedAt: state.finishedAt,
  };
}
