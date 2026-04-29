// frontend/src/api/jobsService.js
//
// Client for the unified /api/jobs resource (Phase 3 of the Tasks/Jobs
// unification). Speaks the canonical Job wire format from
// backend/services/job_types.py — all fields are normalized server-side
// so the UI doesn't need to know about per-kind status enums.
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// JobKind values must match backend/services/job_types.py::JobKind. Keeping
// them as constants here so consumers can build kind filters without
// stringly-typed magic.
export const JOB_KINDS = {
  TASK: "task",
  TRAINING: "training",
  SELF_IMPROVEMENT: "self_improvement",
  EXPERIMENT: "experiment",
  DEMO: "demo",
  BATCH_CSV: "batch_csv",
  VIDEO_RENDER: "video_render",
  UNIFIED_PROGRESS: "unified",
};

// Two top-level views: user-initiated Jobs vs system-driven Activity.
// Backend returns one canonical shape; the difference is just which kinds
// each page filters in. Keep these in sync with the two sidebar entries.
export const JOB_KINDS_FOR_JOBS_PAGE = [
  JOB_KINDS.TASK,
  JOB_KINDS.BATCH_CSV,
  JOB_KINDS.VIDEO_RENDER,
];

export const JOB_KINDS_FOR_ACTIVITY_PAGE = [
  JOB_KINDS.TRAINING,
  JOB_KINDS.SELF_IMPROVEMENT,
  JOB_KINDS.EXPERIMENT,
  JOB_KINDS.DEMO,
  JOB_KINDS.UNIFIED_PROGRESS,
];

export const JOB_STATUSES = ["pending", "running", "paused", "completed", "failed", "cancelled"];

const _toCsv = (xs) => (Array.isArray(xs) && xs.length ? xs.join(",") : "");

export const listJobs = async ({ kinds, statuses, since, limit = 100 } = {}) => {
  const params = {};
  const k = _toCsv(kinds);
  const s = _toCsv(statuses);
  if (k) params.kind = k;
  if (s) params.status = s;
  if (since) params.since = since;
  if (limit) params.limit = limit;
  const res = await axios.get(`${API_BASE}/jobs`, { params });
  return res.data;
};

export const listActiveJobs = async ({ kinds, limit = 100 } = {}) => {
  const params = {};
  const k = _toCsv(kinds);
  if (k) params.kind = k;
  if (limit) params.limit = limit;
  const res = await axios.get(`${API_BASE}/jobs/active`, { params });
  return res.data;
};

export const listJobHistory = async ({ kind, status, limit = 100, offset = 0 } = {}) => {
  const params = { limit, offset };
  if (kind) params.kind = kind;
  if (status) params.status = status;
  const res = await axios.get(`${API_BASE}/jobs/history`, { params });
  return res.data;
};

export const jobsSummary = async () => {
  const res = await axios.get(`${API_BASE}/jobs/summary`);
  return res.data;
};

export const getJob = async (jobId) => {
  const res = await axios.get(`${API_BASE}/jobs/${encodeURIComponent(jobId)}`);
  return res.data;
};

// Phase 7 — POST /api/jobs/:id/cancel. Returns {id, cancelled, reason}.
// Always 200; check `cancelled` for success.
export const cancelJob = async (jobId) => {
  const res = await axios.post(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/cancel`);
  return res.data;
};

// Phase 9 — GET /api/jobs/gate. Returns a JobOperationGate snapshot:
// {gpu_busy, gpu_holder: {kind, native_id, duration_s}, gpu_cooldown_remaining_s,
//  in_progress: {kind: [native_id]}, gpu_exclusive_kinds: [...]}.
// Used by Jobs/Activity pages and the editor's Render button to know whether
// the GPU is currently held by another exclusive job.
export const getJobsGate = async () => {
  const res = await axios.get(`${API_BASE}/jobs/gate`);
  return res.data;
};
