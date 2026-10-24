// What the progress footer shows, built from both progress feeds:
// activeProcesses (job_progress: image batches, indexing, a clip's ComfyUI
// steps as video_render) and unifiedJobs (job:event: video batches, whose
// queued / GPU-wait / keyframe / post stages only arrive there).

import { videoGenStageLabel } from "../videogen/stageLabels";

export const PROCESS_TYPE_LABELS = {
  production: "Production",
  lora_train: "Training Subject",
  indexing: "Indexing",
  image_generation: "Image Gen",
  video_gen: "Video Gen",
  video_render: "Video Gen",
  upscale: "Upscale",
  csv_processing: "CSV Gen",
  file_generation: "File Gen",
  analysis: "Analysis",
  upload: "Upload",
  llm_processing: "LLM",
  web_scraping: "Web Scrape",
  backup: "Backup",
  training: "Training",
  task_processing: "Task",
  voice_processing: "Voice",
  document_processing: "Documents",
  wordpress_pull: "WP Pull",
  wordpress_push: "WP Push",
  wordpress_processing: "WordPress",
  outreach: "Outreach",
  code: "Code",
  processing: "Processing",
  unknown: "Working",
};

// Chip prefixes for the generation kinds people queue side by side.
const CHIP_LABELS = { image_generation: "Image", video_gen: "Video", video_render: "Video", upscale: "Upscale" };

// Which running job leads when several run at once; waiting jobs always follow running ones.
const PRIORITY = [
  "production", "indexing", "video_gen", "video_render", "image_generation", "upscale", "csv_processing",
  "file_generation", "analysis", "upload", "llm_processing", "web_scraping", "outreach", "backup",
  "training", "lora_train", "task_processing", "voice_processing", "document_processing",
  "wordpress_pull", "wordpress_push", "wordpress_processing", "code", "processing",
];

export const TERMINAL_PROCESS_STATUSES = new Set(["complete", "end", "error", "cancelled"]);
const ACTIVE_JOB_STATUSES = new Set(["pending", "running", "paused"]);
const CHIP_TEXT_MAX = 32;

/** The process type a job_progress entry carries, under either casing. */
export function processType(p) {
  return p?.processType || p?.process_type || "processing";
}

/** Friendly name for a process type. */
export function typeLabel(type) {
  return PROCESS_TYPE_LABELS[type] || type;
}

function values(collection) {
  if (!collection) return [];
  if (typeof collection.values === "function" && !Array.isArray(collection)) {
    return Array.from(collection.values());
  }
  return Array.from(collection);
}

function clampPct(value) {
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : null;
}

function waitKind(reason, fallback) {
  if (!reason) return fallback;
  return /^waiting for the system/i.test(reason) ? "system" : "gpu";
}

/**
 * Active video batches: the job:event feed reconciled against the latest
 * /api/jobs/active seed, so a batch whose finishing event was missed drops out.
 * An event received after the seed was requested wins over the seed.
 * @param {Map<string, object>|Array<object>} unifiedJobs  job dicts (with _receivedAt)
 * @param {{fetchedAt: number, jobs: Array<object>}|null} seed
 * @returns {Array<object>} video_gen jobs whose status is pending, running or paused
 */
export function activeVideoJobs(unifiedJobs, seed) {
  const out = new Map();
  for (const job of values(unifiedJobs)) {
    if (job?.kind !== "video_gen") continue;
    if (seed && (job._receivedAt || 0) < seed.fetchedAt) continue;
    out.set(job.id, job);
  }
  for (const job of seed?.jobs || []) {
    if (job?.kind === "video_gen" && !out.has(job.id)) out.set(job.id, job);
  }
  return Array.from(out.values()).filter((j) => ACTIVE_JOB_STATUSES.has(j.status));
}

function videoJobEntry(job) {
  const md = job.metadata || {};
  const stage = md.stage || null;
  const reason = md.gpu_wait_reason || null;
  const waiting = Boolean(reason) || stage === "gpu_wait" || stage === "queued" || job.status === "pending";
  const done = (md.completed_videos || 0) + (md.failed_videos || 0);
  const total = md.total_videos || 0;
  let text = videoGenStageLabel(stage) || "Rendering";
  if (waiting) text = reason || (stage === "gpu_wait" ? "Waiting for GPU" : "Queued");
  return {
    key: job.id,
    type: "video_gen",
    name: md.display_name || md.batch_id || job.native_id || "",
    waiting,
    waitKind: waiting ? waitKind(reason, stage === "gpu_wait" ? "gpu" : "queue") : null,
    text,
    progress: clampPct(job.progress),
    itemCount: total > 1 ? { current: done, total } : null,
    timestamp: job._receivedAt || 0,
    done,
    total,
  };
}

function processEntry(p) {
  const ad = p.additional_data || {};
  const reason = ad.gpu_wait_reason || null;
  const waiting = Boolean(reason) || p.status === "pending" || p.status === "queued";
  return {
    key: p.job_id,
    type: processType(p),
    name: "",
    waiting,
    waitKind: waiting ? waitKind(reason, "queue") : null,
    text: p.message || "Processing...",
    progress: clampPct(p.progress) ?? 0,
    itemCount:
      ad.generated_count != null && ad.target_count != null
        ? { current: ad.generated_count, total: ad.target_count }
        : null,
    timestamp: p.timestamp || 0,
  };
}

function rank(type) {
  const i = PRIORITY.indexOf(type);
  return i === -1 ? PRIORITY.length : i;
}

// Each uploaded file indexes under its own progress record, so a dropped
// folder lists one row per file. Two or more fold into one line that says how
// many are running and how many wait; a single indexing job is unchanged.
function foldIndexingEntries(entries) {
  const indexing = entries.filter((e) => e.type === "indexing");
  if (indexing.length <= 1) return entries;
  const rest = entries.filter((e) => e.type !== "indexing");
  const running = indexing.filter((e) => !e.waiting && (e.progress ?? 0) > 0);
  const lead = running.length
    ? running.reduce((best, e) => (e.timestamp > best.timestamp ? e : best))
    : null;
  const waitingCount = indexing.length - running.length;
  const folded = {
    key: "indexing-batch",
    type: "indexing",
    name: "",
    waiting: running.length === 0,
    waitKind: running.length === 0 ? "queue" : null,
    text: running.length
      ? `${lead.text} (${running.length} running, ${waitingCount} waiting)`
      : `${indexing.length} documents waiting to start`,
    progress: running.length ? lead.progress : 0,
    itemCount: null,
    timestamp: indexing.reduce((max, e) => Math.max(max, e.timestamp || 0), 0),
  };
  return [...rest, folded];
}

/**
 * One entry per job in flight, running ones first. A clip's video_render steps
 * fold into their batch, matched by batch_id or by the batch's current item.
 * @param {Iterable<object>} processes  activeProcesses values
 * @param {Array<object>} videoJobs     activeVideoJobs(...)
 * @returns {Array<object>} {key, type, name, waiting, waitKind, text, progress, itemCount, timestamp}
 */
export function buildFooterEntries(processes, videoJobs) {
  const entries = [];
  const byBatch = new Map();
  const byItem = new Map();
  for (const job of videoJobs || []) {
    const entry = videoJobEntry(job);
    entries.push(entry);
    byBatch.set(job.metadata?.batch_id || job.native_id, entry);
    if (job.metadata?.current_item) byItem.set(job.metadata.current_item, entry);
  }
  for (const p of values(processes)) {
    if (!p || TERMINAL_PROCESS_STATUSES.has(p.status)) continue;
    const type = processType(p);
    if (type === "video_render") {
      const batch = byBatch.get(p.additional_data?.batch_id) || byItem.get(p.job_id);
      if (batch) {
        const step = clampPct(p.progress) ?? 0;
        batch.waiting = false;
        batch.waitKind = null;
        batch.text = p.message || batch.text;
        batch.progress = batch.total > 1 ? ((batch.done + step / 100) / batch.total) * 100 : step;
        batch.timestamp = Math.max(batch.timestamp, p.timestamp || 0);
        continue;
      }
    }
    entries.push(processEntry(p));
  }
  return foldIndexingEntries(entries).sort(
    (a, b) => Number(a.waiting) - Number(b.waiting) || rank(a.type) - rank(b.type) || b.timestamp - a.timestamp,
  );
}

/** The footer's main line for an entry: the type label unless the message names it. */
export function statusLine(entry) {
  const label = typeLabel(entry.type);
  const lower = entry.text.toLowerCase();
  const named = lower.includes(entry.type.replace(/_/g, " ")) || lower.includes(label.toLowerCase());
  return named ? entry.text : `${label}: ${entry.text}`;
}

/** Compact text for a job that is not the bar's: "Video: denoising 12/30 · 40%", "Image: waiting for GPU". */
export function chipText(entry) {
  const label = CHIP_LABELS[entry.type] || typeLabel(entry.type);
  if (entry.waiting) {
    const why = { gpu: "waiting for GPU", system: "waiting for the system" }[entry.waitKind] || "queued";
    return `${label}: ${why}`;
  }
  const text = entry.text.length > CHIP_TEXT_MAX ? `${entry.text.slice(0, CHIP_TEXT_MAX - 1).trimEnd()}…` : entry.text;
  const pct = entry.progress != null ? ` · ${Math.round(entry.progress)}%` : "";
  return `${label}: ${text}${pct}`;
}

/**
 * Everything the footer renders while something is in flight, or null.
 * The bar and main line follow the first running job (else the first waiting one).
 */
export function footerView(entries) {
  if (!entries || entries.length === 0) return null;
  const [primary, ...others] = entries;
  return {
    primary,
    statusText: statusLine(primary),
    progress: primary.progress ?? 0,
    itemCount: primary.itemCount,
    chips: others.map((e) => ({ key: e.key, text: chipText(e), waiting: e.waiting })),
    entries,
  };
}
