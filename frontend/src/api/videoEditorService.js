// Thin client for the video_editor plugin's bin-driven Plan pipeline.
// Endpoints live under /api/video-editor on the Flask backend, which proxies
// to plugins/video_editor/ on port 8207.

import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const getVideoEditorErrorMessage = (error, fallback = "Video Editor request failed") => {
  const data = error?.response?.data;
  if (!data) return error?.message || fallback;

  const fromDetail = (detail) => {
    if (!detail) return null;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (!item || typeof item !== "object") return String(item);
          const loc = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
          const msg = item.msg || item.message || JSON.stringify(item);
          return loc ? `${loc}: ${msg}` : msg;
        })
        .join("; ");
    }
    if (typeof detail === "object") return detail.message || detail.error || JSON.stringify(detail);
    return String(detail);
  };

  return (
    fromDetail(data.error) ||
    fromDetail(data.detail) ||
    data.message ||
    error?.message ||
    fallback
  );
};

const request = async (promise, fallback) => {
  try {
    return await promise;
  } catch (error) {
    error.videoEditorMessage = getVideoEditorErrorMessage(error, fallback);
    throw error;
  }
};

export const listStyleRecipes = async () => {
  const res = await request(axios.get(`${API_BASE}/video-editor/recipes`), "Could not load style recipes");
  return res.data?.recipes || [];
};

// Submit a Plan job. Returns { job_id, status }.
// `bin_clips` is an array of { clip_id, document_id } OR { clip_id, source_path }.
// The Flask proxy resolves document_id → path before forwarding.
export const submitPlan = async ({
  bin_clips,
  song_document_id,
  song_path,
  scan_mode = "both-and",
  audio_threshold = 0.04,
  motion_threshold = 0.02,
  margin = "0.2sec",
  style_recipe_name = "Default",
  seed = 0,
  clip_overrides = {},
}) => {
  const body = {
    bin_clips, scan_mode, audio_threshold, motion_threshold, margin,
    style_recipe_name, seed,
    clip_overrides,
  };
  if (song_document_id) body.song_document_id = song_document_id;
  if (song_path) body.song_path = song_path;
  const res = await request(axios.post(`${API_BASE}/video-editor/plan`, body), "Could not submit Plan job");
  return res.data;
};

// Force-bust the cache for one clip and re-run vision analysis.
// Returns { analysis: ClipAnalysis, frames: [paths], frame_count }.
export const rescanClip = async ({ document_id, source_path, style_recipe_name = "Default", n_frames = 3 }) => {
  const body = { style_recipe_name, n_frames };
  if (document_id) body.document_id = document_id;
  if (source_path) body.source_path = source_path;
  const res = await request(axios.post(`${API_BASE}/video-editor/vision/rescan-clip`, body), "Re-analyze failed");
  return res.data;
};

// Resolve a clip's hash so we can build frame thumbnail URLs.
export const getClipHash = async ({ document_id, source_path }) => {
  const body = {};
  if (document_id) body.document_id = document_id;
  if (source_path) body.source_path = source_path;
  const res = await request(axios.post(`${API_BASE}/video-editor/vision/clip-hash`, body), "Clip thumbnail lookup failed");
  return res.data?.hash || null;
};

// URL builder for one sampled frame.
export const frameThumbnailUrl = (clipHash, frameIndex) =>
  `${API_BASE}/video-editor/vision/frames/${encodeURIComponent(clipHash)}/${frameIndex}`;

// Poll job state. Returns the full Job dict with .status, .progress, .result.
export const getPlanJob = async (jobId) => {
  const res = await request(
    axios.get(`${API_BASE}/video-editor/jobs/${encodeURIComponent(jobId)}`),
    "Could not poll Plan job",
  );
  return res.data;
};

// A2: full arrangement render — multi-clip + per-clip filters + transitions.
// Plugin emits .mlt + .mp4 in one synchronous call (~seconds for short songs).
export const renderArrangement = async ({
  arrangement,
  song_document_id,
  audio_path,
  audio_volume = 1.0,
  song_duration_seconds,
  fps_num = 30,
  fps_den = 1,
  width = 1920,
  height = 1080,
  render_mp4 = true,
}) => {
  const body = {
    arrangement,
    audio_volume,
    song_duration_seconds,
    fps_num, fps_den, width, height,
    render_mp4,
    register: true,
  };
  if (song_document_id) body.song_document_id = song_document_id;
  if (audio_path) body.audio_path = audio_path;
  const res = await request(
    axios.post(`${API_BASE}/video-editor/shotcut/compose-arrangement`, body),
    "Render failed",
  );
  return res.data;
};

export const listFilterCatalog = async () => {
  const res = await request(axios.get(`${API_BASE}/video-editor/catalog/filters`), "Could not load filters");
  return res.data?.categories || {};
};

export const listTransitionCatalog = async () => {
  const res = await request(axios.get(`${API_BASE}/video-editor/catalog/transitions`), "Could not load transitions");
  return res.data?.transitions || [];
};

export const openInShotcut = async (mlt_path) => {
  const res = await request(axios.post(`${API_BASE}/video-editor/open-in-shotcut`, { mlt_path }), "Could not launch Shotcut");
  return res.data;
};
