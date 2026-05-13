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

// Render: hand the arrangement straight to /shotcut/compose. Single video at a
// time in A1 — wired with the arrangement's first clip and a video_path. A2
// will switch to a multi-clip compose body when timeline_compose supports it.
export const renderFromArrangement = async ({
  video_path,
  audio_path,
  text_elements = [],
  video_trim_start = 0,
  video_trim_end = null,
  audio_volume = 1.0,
  render_mp4 = true,
}) => {
  const res = await axios.post(`${API_BASE}/video-editor/shotcut/compose`, {
    video_path, audio_path, text_elements,
    video_trim_start, video_trim_end, audio_volume,
    render_mp4, register: true,
  });
  return res.data;
};

export const openInShotcut = async (mlt_path) => {
  const res = await axios.post(`${API_BASE}/video-editor/open-in-shotcut`, { mlt_path });
  return res.data;
};
