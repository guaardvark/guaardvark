// frontend/src/components/jobs/imageJob.js
//
// A scheduled image job as the fields its editor shows, and back. Two kinds
// of row make pictures: a saved generate_image tool call (chat, /schedule,
// MCP) and a batch the Images page saved. This is the one place that knows
// how each request is shaped (backend/services/job_handoff.py).

export const IMAGE_TOOL = "generate_image";

const configOf = (task) => {
  const parsed = task?.workflow_config_parsed;
  if (parsed && typeof parsed === "object") return parsed;
  try {
    return JSON.parse(task?.workflow_config || "{}") || {};
  } catch {
    return {};
  }
};

const isToolJob = (task) => task?.type === "tool_call" && configOf(task).request?.tool_name === IMAGE_TOOL;

export const isImageJob = (task) => Boolean(task) && (task.type === "image_batch" || isToolJob(task));

const num = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

// The sides generate_image takes; another value is reset to 1024 by the tool.
const TOOL_SIDES = [256, 384, 512, 640, 768, 896, 1024, 1280, 1536];
const TOOL_SIZES = [[1024, 1024], [1280, 768], [768, 1280], [1536, 1024], [1024, 1536], [1536, 896], [896, 1536], [768, 768]];
// The Images page's 1K sizes, which every current model family renders.
const BATCH_SIZES = [[1024, 1024], [1344, 768], [768, 1344], [1216, 832], [832, 1216], [768, 768]];
export const TOOL_STYLES = ["realistic", "artistic", "anime", "photographic", "digital-art"];
export const BATCH_STYLES = ["realistic", "artistic", "cartoon", "sketch", "infographic", "technical"];

export const sizeKey = (width, height) => `${width}x${height}`;

/**
 * The editor's fields for an image job. `locked` names the fields this kind
 * of job cannot change here: an Images page batch carries steps, guidance,
 * LoRAs and encoders chosen for its model, so its model stays as saved.
 */
export const readImageJob = (task) => {
  const config = configOf(task);
  const request = config.request || {};
  if (isToolJob(task)) {
    const p = request.parameters || {};
    return {
      kind: "tool",
      locked: [],
      has: { seed: false, negative: false },
      styles: TOOL_STYLES,
      prompts: [String(p.prompt || "")],
      model: !p.model || p.model === "auto" ? "auto" : p.model,
      // The tool renders 1024 x 1024 when a side is missing.
      width: num(p.width) || 1024,
      height: num(p.height) || 1024,
      steps: num(p.steps),
      style: p.style || "realistic",
      seed: null,
      negative: "",
      cast: Array.isArray(p.subject_ids) ? p.subject_ids.length : 0,
    };
  }
  return {
    kind: "batch",
    locked: ["model"],
    has: { seed: true, negative: true },
    styles: BATCH_STYLES,
    prompts: (Array.isArray(request.prompts) ? request.prompts : []).map(String),
    model: request.model || "auto",
    width: num(request.width),
    height: num(request.height),
    steps: num(request.steps),
    // The batch route reads a missing style as realistic.
    style: request.style || "realistic",
    seed: num(request.seed),
    negative: request.negative_prompt || "",
    cast: Array.isArray(request.subject_ids) ? request.subject_ids.length : 0,
  };
};

// The Images page saves a Single-mode prompt whole, line breaks included, and
// repeats it once per picture; Bulk mode saves one prompt per line. The editor
// shows a repeated prompt once and writes it back the same number of times, so
// a multi-line prompt is never cut into pieces.
const repeated = (prompts) => prompts.length > 0 && prompts.every((p) => p === prompts[0]);

/** Whether the prompts can be shown as one text field and read back unchanged. */
export const promptsEditable = (job) =>
  job.kind === "tool" || repeated(job.prompts) || !job.prompts.some((p) => p.includes("\n"));

/** The prompt text the editor shows. */
export const promptText = (job) =>
  job.kind === "tool" || repeated(job.prompts) ? job.prompts[0] || "" : job.prompts.join("\n");

/** The prompt list to save for the text in the editor. */
export const promptsFromText = (job, text) => {
  if (job.kind === "tool") return [text];
  if (repeated(job.prompts) || job.prompts.length <= 1) return Array(Math.max(job.prompts.length, 1)).fill(text);
  return text.split("\n");
};

/**
 * The sizes to offer: the kind's own list, without those past the model's
 * largest picture, plus the job's current size when it is not among them.
 * `limits` is the model's /api/batch-image/model-info configuration.
 */
export const sizeChoices = (fields, limits) => {
  const base = fields.kind === "tool" ? TOOL_SIZES : BATCH_SIZES;
  const maxPixels = limits?.max_pixels || (limits?.max_dimensions ? limits.max_dimensions[0] * limits.max_dimensions[1] : null);
  const fits = ([w, h]) => !maxPixels || w * h <= maxPixels;
  const list = base.filter(fits);
  if (fields.width && fields.height && !list.some(([w, h]) => w === fields.width && h === fields.height)) {
    list.push([fields.width, fields.height]);
  }
  return list;
};

/** Whether the tool would keep this size as given. */
export const toolTakesSize = (width, height) => TOOL_SIDES.includes(width) && TOOL_SIDES.includes(height);

const setOrDrop = (target, key, value) => {
  if (value === null || value === undefined || value === "") delete target[key];
  else target[key] = value;
};

/**
 * The job's saved request with the editor's fields written into it. What the
 * editor does not show (Cast characters, LoRAs, enhancement switches) is kept.
 */
export const writeImageJob = (task, edited) => {
  const config = configOf(task);
  const before = readImageJob(task);
  const prompts = edited.prompts.map((p) => p.trim()).filter(Boolean);
  if (before.kind === "tool") {
    const parameters = { ...(config.request?.parameters || {}) };
    parameters.prompt = prompts[0] || "";
    setOrDrop(parameters, "model", edited.model === "auto" ? null : edited.model);
    parameters.width = edited.width;
    parameters.height = edited.height;
    parameters.style = edited.style;
    setOrDrop(parameters, "steps", edited.steps);
    return { tool_name: IMAGE_TOOL, parameters };
  }
  const request = { ...(config.request || {}) };
  request.prompts = prompts;
  setOrDrop(request, "width", edited.width);
  setOrDrop(request, "height", edited.height);
  if (edited.steps !== before.steps) {
    setOrDrop(request, "steps", edited.steps);
    // A typed step count is kept as typed; without one the model's own applies.
    setOrDrop(request, "steps_explicit", edited.steps == null ? null : true);
  }
  if (edited.style !== before.style) setOrDrop(request, "style", edited.style);
  setOrDrop(request, "seed", edited.seed);
  request.negative_prompt = edited.negative.trim();
  // The Images page's snapshot of its controls no longer matches the request.
  delete request.ui_config;
  return request;
};

/** What POST /api/night-shift/jobs takes to save this request as a new job. */
export const asNewImageJob = (task, request, name) =>
  task.type === "tool_call"
    ? { tool: request.tool_name, arguments: request.parameters, name }
    : { type: "image_batch", variant: configOf(task).variant || "prompts", request, name };

/** The batch a started job rendered, for the link to its pictures. */
export const imageBatchIdOf = (task) => {
  let handler = task?.handler_config;
  if (typeof handler === "string") {
    try {
      handler = JSON.parse(handler);
    } catch {
      handler = null;
    }
  }
  const ref = handler?.handoff;
  return ref?.kind === "image_batch" && ref.batch_id ? ref.batch_id : null;
};
