// Submit a Plan request and poll for completion. Exposes status, progress,
// the final arrangement, and an error. Mirrors the renderJob pattern used for
// the legacy /render-timeline endpoint.

import { useState, useEffect, useRef, useCallback } from "react";
import { submitPlan, getPlanJob } from "../../api/videoEditorService";

export function usePlanJob() {
  const [job, setJob] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const start = useCallback(async (planRequest) => {
    setPlanning(true);
    setError(null);
    setResult(null);
    setJob(null);
    try {
      const { job_id } = await submitPlan(planRequest);
      setJob({ id: job_id, status: "running", progress: 0 });
    } catch (e) {
      setError(e.response?.data?.error?.message || e.message || "Plan failed");
      setPlanning(false);
    }
  }, []);

  const cancel = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    setPlanning(false);
    setJob(null);
  }, []);

  // Poll while job is in flight.
  useEffect(() => {
    if (!job?.id || job.status === "done" || job.status === "failed") return;
    pollRef.current = setInterval(async () => {
      try {
        const fresh = await getPlanJob(job.id);
        setJob(fresh);
        if (fresh.status === "done") {
          setResult(fresh.result || null);
          setPlanning(false);
          if (pollRef.current) clearInterval(pollRef.current);
        } else if (fresh.status === "failed") {
          setError(fresh.error || "Plan failed");
          setPlanning(false);
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch (e) {
        // Network blip; keep polling.
        console.warn("plan poll:", e);
      }
    }, 1000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [job?.id, job?.status]);

  return { start, cancel, planning, job, result, error };
}
