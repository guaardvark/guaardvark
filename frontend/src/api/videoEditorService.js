// Thin client for the video_editor plugin's bin-driven Plan pipeline.
// Endpoints live under /api/video-editor on the Flask backend, which proxies
// to plugins/video_editor/ on port 8207.

import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const listStyleRecipes = async () => {
  const res = await axios.get(`${API_BASE}/video-editor/recipes`);
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
}) => {
  const body = {
    bin_clips, scan_mode, audio_threshold, motion_threshold, margin,
    style_recipe_name, seed,
  };
  if (song_document_id) body.song_document_id = song_document_id;
  if (song_path) body.song_path = song_path;
  const res = await axios.post(`${API_BASE}/video-editor/plan`, body);
  return res.data;
};

// Poll job state. Returns the full Job dict with .status, .progress, .result.
export const getPlanJob = async (jobId) => {
  const res = await axios.get(`${API_BASE}/video-editor/jobs/${encodeURIComponent(jobId)}`);
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
  const res = await axios.post(`${API_BASE}/video-editor/shotcut/compose-arrangement`, body);
  return res.data;
};

export const listFilterCatalog = async () => {
  const res = await axios.get(`${API_BASE}/video-editor/catalog/filters`);
  return res.data?.categories || {};
};

export const listTransitionCatalog = async () => {
  const res = await axios.get(`${API_BASE}/video-editor/catalog/transitions`);
  return res.data?.transitions || [];
};

export const openInShotcut = async (mlt_path) => {
  const res = await axios.post(`${API_BASE}/video-editor/open-in-shotcut`, { mlt_path });
  return res.data;
};
