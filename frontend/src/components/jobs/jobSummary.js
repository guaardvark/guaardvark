// frontend/src/components/jobs/jobSummary.js
//
// What a job is, in the few words and one picture a card or a list row has
// room for: its kind, a title that tells it from its neighbours, the settings
// worth seeing at a glance, and the frame it starts from.
import { BASE_URL } from "../../api/apiClient";
import { frameUrl, isVideoJob, readVideoJob } from "./videoJob";
import { isImageJob, readImageJob } from "./imageJob";

const HANDOFF_KINDS = new Set([
  "infographic", "cast_lora", "model_training", "music_video", "music", "upscale", "swarm", "lesson_replay",
  "film_crew", "tool_call",
]);

/** The kind a job is filed under: the filter chip it answers to and its label. */
export const jobKind = (task) => {
  const type = task?.type || "";
  if (isVideoJob(task)) return "video";
  if (isImageJob(task)) return "images";
  if (type.startsWith("social_outreach_")) return "outreach";
  if (type.startsWith("website_")) return "website";
  return type || "custom";
};

const TYPE_KEYS = {
  content_generation: "tasks.types.content_generation",
  file_generation: "tasks.types.file_generation",
  code_generation: "tasks.types.code_generation",
  bulk_content_generation: "tasks.types.bulk_content_generation",
  data_analysis: "tasks.types.data_analysis",
  website_analysis: "tasks.types.website_analysis",
  custom: "tasks.types.custom",
};

/** A kind's name. Kinds without an entry show their own name. */
export const kindLabel = (kind, t) => {
  if (!kind) return "";
  if (["video", "images", "outreach", "website"].includes(kind)) return t(`jobs.nightShift.kind.${kind}`);
  if (kind === "image_batch") return t("jobs.nightShift.kind.images");
  if (kind === "video_batch") return t("jobs.nightShift.kind.video");
  if (HANDOFF_KINDS.has(kind)) return t(`jobs.nightShift.kind.${kind}`);
  return TYPE_KEYS[kind] ? t(TYPE_KEYS[kind]) : kind.replace(/_/g, " ");
};

const PREVIEW_ROUTE = { video_batch: "batch-video", image_batch: "batch-image", infographic: "batch-image" };

/** A picture of what a started job has made so far: its batch's preview. Null before it starts. */
export const outputThumb = (task) => {
  let handler = task?.handler_config;
  if (typeof handler === "string") {
    try {
      handler = JSON.parse(handler);
    } catch {
      handler = null;
    }
  }
  const ref = handler?.handoff;
  const route = ref?.batch_id ? PREVIEW_ROUTE[ref.kind] : null;
  return route ? `${BASE_URL}/${route}/preview/${encodeURIComponent(ref.batch_id)}` : null;
};

// Names a job gets when nothing named it: the tool's own name.
const GENERIC_NAME = /^(generate (video|image)|tool|video batch|image batch)$/i;

const firstLine = (text) => String(text || "").split("\n").find((line) => line.trim())?.trim() || "";

const seconds = (job) => {
  if (job.seconds != null) return job.seconds;
  return job.frames && job.fps ? Math.round((job.frames / job.fps) * 10) / 10 : null;
};

/**
 * @returns {{kind: string, title: string, text: string, details: string[], thumb: string|null}}
 *   `text` is the prompt or description; `details` are short settings (model, shape, length).
 */
export const jobSummary = (task, t) => {
  const kind = jobKind(task);
  const name = String(task?.name || "").trim();
  if (kind === "video") {
    const job = readVideoJob(task);
    const prompt = firstLine(job.prompts[0]);
    const length = seconds(job);
    return {
      kind,
      title: !name || GENERIC_NAME.test(name) ? prompt || name : name,
      text: job.prompts.filter((p) => p.trim()).join(" · "),
      details: [
        job.model || null,
        job.aspect || job.size || null,
        length != null ? t("jobs.summary.seconds", { seconds: length }) : null,
        job.prompts.length > 1 ? t("jobs.summary.clips", { count: job.prompts.length }) : null,
      ].filter(Boolean),
      thumb: outputThumb(task) || frameUrl(job.firstImages[0]),
    };
  }
  if (kind === "images") {
    const job = readImageJob(task);
    const prompt = firstLine(job.prompts[0]);
    return {
      kind,
      title: !name || GENERIC_NAME.test(name) ? prompt || name : name,
      text: job.prompts.filter((p) => p.trim()).join(" · "),
      details: [
        job.model && job.model !== "auto" ? job.model : null,
        job.width && job.height ? `${job.width} × ${job.height}` : null,
        job.prompts.length > 1 ? t("jobs.summary.pictures", { count: job.prompts.length }) : null,
      ].filter(Boolean),
      thumb: outputThumb(task),
    };
  }
  return {
    kind,
    title: name,
    text: String(task?.description || "").trim(),
    details: [task?.project?.name || task?.project_info?.name, task?.client_name, task?.output_filename].filter(Boolean),
    thumb: outputThumb(task),
  };
};
