// frontend/src/components/jobs/videoJob.js
//
// A scheduled video job as the fields its editor shows, and back. Two kinds
// of row make a clip: a saved generate_video tool call (chat, /schedule, MCP)
// and a batch the Video page saved. Their requests are shaped differently
// (backend/services/job_handoff.py); this is the one place that knows how.

export const VIDEO_TOOL = "generate_video";

const configOf = (task) => {
  const parsed = task?.workflow_config_parsed;
  if (parsed && typeof parsed === "object") return parsed;
  try {
    return JSON.parse(task?.workflow_config || "{}") || {};
  } catch {
    return {};
  }
};

const isToolJob = (task) => task?.type === "tool_call" && configOf(task).request?.tool_name === VIDEO_TOOL;

export const isVideoJob = (task) => Boolean(task) && (task.type === "video_batch" || isToolJob(task));

const list = (value) => (Array.isArray(value) ? value : value ? [value] : []);

const num = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

// The simplest ratio a width and height make, when it is one models declare.
const KNOWN_RATIOS = ["21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16"];
export const ratioOf = (width, height) => {
  const w = num(width);
  const h = num(height);
  if (!w || !h) return "";
  return KNOWN_RATIOS.find((ratio) => {
    const [a, b] = ratio.split(":").map(Number);
    return Math.abs(w / h / (a / b) - 1) < 0.03;
  }) || "";
};

/**
 * The editor's fields for a video job.
 *
 * `locked` names the fields this kind of job cannot change here: a Video page
 * batch carries sampler settings worked out for its model, so its model and
 * speed profile stay as that page saved them.
 */
export const readVideoJob = (task) => {
  const config = configOf(task);
  const request = config.request || {};
  if (isToolJob(task)) {
    const p = request.parameters || {};
    const references = [...list(p.reference_images), ...list(p.reference_clips), ...list(p.reference_audio)];
    return {
      kind: "tool",
      role: references.length ? "ref2v" : p.first_image ? "i2v" : "t2v",
      locked: [],
      has: { seed: false, negative: false },
      prompts: [String(p.prompt || "")],
      model: p.model || "",
      aspect: p.aspect_ratio || "",
      size: "",
      seconds: num(p.duration_s),
      frames: p.duration_s == null ? num(p.duration_frames) : null,
      fps: null,
      steps: num(p.num_inference_steps),
      style: p.style || "",
      speed: p.speed_profile || "",
      seed: null,
      negative: "",
      firstImages: list(p.first_image),
      lastImage: p.last_image || "",
      references: references.length,
      audio: Boolean(p.audio),
    };
  }
  const variant = config.variant || "text";
  const prompts = variant === "text" ? list(request.prompts).map(String) : [String(request.prompt || "")];
  return {
    kind: "batch",
    variant,
    role: variant === "references" ? "ref2v" : variant === "image" ? "i2v" : "t2v",
    locked: ["model", "speed"],
    has: { seed: true, negative: true },
    // One storyboard concept the Director expands into shots: the prompt list is its placeholder.
    storyboard: Boolean(request.storyboard_concept),
    prompts: request.storyboard_concept ? [String(request.storyboard_concept)] : prompts,
    model: request.model || "",
    aspect: request.aspect_ratio || ratioOf(request.width, request.height),
    size: request.width && request.height ? `${request.width}×${request.height}` : "",
    seconds: null,
    frames: num(request.duration_frames),
    fps: num(request.fps),
    steps: num(request.num_inference_steps),
    // The batch routes read a missing style as cinematic.
    style: request.prompt_style || "cinematic",
    speed: request.speed_profile || "",
    seed: num(request.seed),
    negative: request.negative_prompt || "",
    firstImages: list(request.image_paths),
    lastImage: list(request.last_frame_paths)[0] || "",
    references: variant === "references" ? Object.values(request.references || {}).flat().length : 0,
    audio: false,
  };
};

/** Clip length in seconds as the job has it, given the model's frame rate. */
export const secondsOf = (fields, caps) => {
  if (fields.seconds != null) return fields.seconds;
  const fps = fields.fps || caps?.native_fps;
  return fields.frames && fps ? Math.round((fields.frames / fps) * 10) / 10 : null;
};

/** The shortest and longest clip a model declares, in seconds. */
export const lengthRange = (caps) => {
  const fps = caps?.native_fps || 24;
  const longest = caps?.max_clip_s || (caps?.max_frames ? caps.max_frames / fps : null);
  return {
    min: caps?.min_clip_s || 1,
    max: longest ? Math.floor(longest * 10) / 10 : null,
  };
};

/** The fewest steps the job's model renders well at, under its speed profile. */
export const stepFloor = (caps, speed) =>
  (speed && caps?.speed_profiles?.[speed]?.min_steps) || caps?.min_steps || 1;

/** The step count the model uses when the job names none. */
export const defaultSteps = (caps, speed) =>
  (speed && caps?.speed_profiles?.[speed]?.steps) || caps?.default_steps || null;

/** Whether a model can make this job's kind of clip. */
export const modelFits = (caps, role) => {
  if (!caps?.modes?.length) return false;
  if (role === "ref2v") return caps.modes.includes("ref2v");
  if (role === "i2v") return Boolean(caps.supports_i2v);
  return Boolean(caps.supports_t2v);
};

const setOrDrop = (target, key, value) => {
  if (value === null || value === undefined || value === "") delete target[key];
  else target[key] = value;
};

/**
 * The job's saved request with the editor's fields written into it. Fields
 * the editor does not show (first frame, references, guides) are kept.
 * `edited.seconds` is the length the person set, or undefined when untouched.
 */
export const writeVideoJob = (task, edited, caps) => {
  const config = configOf(task);
  const before = readVideoJob(task);
  const prompts = edited.prompts.map((p) => p.trim()).filter(Boolean);
  if (before.kind === "tool") {
    const parameters = { ...(config.request?.parameters || {}) };
    parameters.prompt = prompts[0] || "";
    setOrDrop(parameters, "model", edited.model);
    setOrDrop(parameters, "aspect_ratio", edited.aspect);
    setOrDrop(parameters, "style", edited.style);
    setOrDrop(parameters, "speed_profile", edited.speed);
    setOrDrop(parameters, "num_inference_steps", edited.steps);
    if (edited.seconds !== undefined) {
      setOrDrop(parameters, "duration_s", edited.seconds);
      delete parameters.duration_frames;
    }
    return { tool_name: VIDEO_TOOL, parameters };
  }
  const request = { ...(config.request || {}) };
  if (before.storyboard) request.storyboard_concept = prompts[0] || "";
  else if (before.variant === "text") request.prompts = prompts;
  else request.prompt = prompts[0] || "";
  if (edited.aspect !== before.aspect) {
    // The backend sizes the canvas for the ratio from the model's own table.
    delete request.width;
    delete request.height;
    setOrDrop(request, "aspect_ratio", edited.aspect);
  }
  if (edited.seconds !== undefined) {
    const fps = before.fps || caps?.native_fps;
    if (edited.seconds == null || !fps) delete request.duration_frames;
    else request.duration_frames = Math.round(edited.seconds * fps);
  }
  if (edited.steps !== before.steps) {
    setOrDrop(request, "num_inference_steps", edited.steps);
    // A typed step count is kept as typed; without one the model's own applies.
    setOrDrop(request, "steps_explicit", edited.steps == null ? null : true);
  }
  if (edited.style !== before.style) setOrDrop(request, "prompt_style", edited.style);
  setOrDrop(request, "seed", edited.seed);
  setOrDrop(request, "negative_prompt", edited.negative.trim());
  // The Video page's snapshot of its controls no longer matches the request.
  delete request.ui_config;
  return request;
};

/** What POST /api/night-shift/jobs takes to save this request as a new job. */
export const asNewJob = (task, request, name) =>
  task.type === "tool_call"
    ? { tool: request.tool_name, arguments: request.parameters, name }
    : { type: "video_batch", variant: configOf(task).variant || "text", request, name };

/** A servable URL for a frame the job starts from, or null for a path only the backend can read. */
export const frameUrl = (ref) => {
  const text = String(ref || "");
  if (/^(\/api\/|https?:\/\/|blob:|data:image\/)/.test(text)) return text;
  return null;
};

/** The batch a started job rendered, for the link to its clips. */
export const batchIdOf = (task) => {
  let handler = task?.handler_config;
  if (typeof handler === "string") {
    try {
      handler = JSON.parse(handler);
    } catch {
      handler = null;
    }
  }
  const ref = handler?.handoff;
  return ref?.kind === "video_batch" && ref.batch_id ? ref.batch_id : null;
};
