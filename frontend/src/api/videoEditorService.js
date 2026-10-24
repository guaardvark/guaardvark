// Thin client for the video_editor plugin's bin-driven Plan pipeline.
// Endpoints live under /api/video-editor on the Flask backend, which proxies
// to plugins/video_editor/ on port 8207.

import axios from "axios";
import { translate } from "../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const getVideoEditorErrorMessage = (error, fallback = translate("messages.videoEditor.requestFailed")) => {
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
  const res = await request(axios.get(`${API_BASE}/video-editor/recipes`), translate("messages.videoEditor.loadRecipesFailed"));
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
  const res = await request(axios.post(`${API_BASE}/video-editor/plan`, body), translate("messages.videoEditor.submitPlanFailed"));
  return res.data;
};

// Force-bust the cache for one clip and re-run vision analysis.
// Returns { analysis: ClipAnalysis, frames: [paths], frame_count }.
export const rescanClip = async ({ document_id, source_path, style_recipe_name = "Default", n_frames = 3 }) => {
  const body = { style_recipe_name, n_frames };
  if (document_id) body.document_id = document_id;
  if (source_path) body.source_path = source_path;
  const res = await request(axios.post(`${API_BASE}/video-editor/vision/rescan-clip`, body), translate("messages.videoEditor.reanalyzeFailed"));
  return res.data;
};

// Resolve a clip's hash so we can build frame thumbnail URLs.
export const getClipHash = async ({ document_id, source_path }) => {
  const body = {};
  if (document_id) body.document_id = document_id;
  if (source_path) body.source_path = source_path;
  const res = await request(axios.post(`${API_BASE}/video-editor/vision/clip-hash`, body), translate("messages.videoEditor.clipHashFailed"));
  return res.data?.hash || null;
};

// URL builder for one sampled frame.
export const frameThumbnailUrl = (clipHash, frameIndex) =>
  `${API_BASE}/video-editor/vision/frames/${encodeURIComponent(clipHash)}/${frameIndex}`;

// Poll job state. Returns the full Job dict with .status, .progress, .result.
export const getPlanJob = async (jobId) => {
  const res = await request(
    axios.get(`${API_BASE}/video-editor/jobs/${encodeURIComponent(jobId)}`),
    translate("messages.videoEditor.pollPlanFailed"),
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
    translate("messages.videoEditor.renderFailed"),
  );
  return res.data;
};

export const listFilterCatalog = async () => {
  const res = await request(axios.get(`${API_BASE}/video-editor/catalog/filters`), translate("messages.videoEditor.loadFiltersFailed"));
  return res.data?.categories || {};
};

export const listTransitionCatalog = async () => {
  const res = await request(axios.get(`${API_BASE}/video-editor/catalog/transitions`), translate("messages.videoEditor.loadTransitionsFailed"));
  return res.data?.transitions || [];
};

export const openInShotcut = async (mlt_path) => {
  const res = await request(axios.post(`${API_BASE}/video-editor/open-in-shotcut`, { mlt_path }), translate("messages.videoEditor.launchShotcutFailed"));
  return res.data;
};

// ---------- Named projects -------------------------------------------------
// Draft-buffer model: autosaveCurrentProject() writes a draft; saveProject()
// is the explicit Save that promotes the draft to the named project file.
const PROJECTS = `${API_BASE}/video-editor/projects`;

// Gallery catalog: { currentId, projects: [{id,name,createdAt,updatedAt,posterDocumentId,clipCount}] }.
export const listProjects = async () => {
  const res = await request(axios.get(PROJECTS), translate("messages.videoEditor.listProjectsFailed"));
  return res.data || { currentId: null, projects: [] };
};

// The project to open on load (migrates the legacy session / creates Untitled).
// Returns the working state (draft if newer) plus a `_meta` { id, name, isDirty, savedAt, draftAt }.
export const getCurrentProject = async () => {
  const res = await request(axios.get(`${PROJECTS}/current`), translate("messages.videoEditor.loadCurrentProjectFailed"));
  return res.data;
};

export const openProject = async (id) => {
  const res = await request(axios.get(`${PROJECTS}/${encodeURIComponent(id)}`), translate("messages.videoEditor.openProjectFailed"));
  return res.data;
};

export const createProject = async (name = "Untitled", editable = undefined) => {
  const res = await request(axios.post(PROJECTS, { name, editable }), translate("messages.videoEditor.createProjectFailed"));
  return res.data;
};

// Autosave → writes the draft of the current project. Returns { id, name, isDirty, savedAt, draftAt }.
export const autosaveCurrentProject = async (editable) => {
  const res = await request(axios.put(`${PROJECTS}/current`, editable), translate("messages.videoEditor.autosaveFailed"));
  return res.data;
};

// Autosave → writes the draft of a SPECIFIC project id (race-safe: targets the
// project being edited regardless of the server's 'current' pointer).
export const autosaveProjectDraft = async (id, editable) => {
  const res = await request(
    axios.put(`${PROJECTS}/${encodeURIComponent(id)}/draft`, editable),
    translate("messages.videoEditor.autosaveFailed"),
  );
  return res.data;
};

// Explicit Save → promote the draft (or send an explicit body) to the project file.
export const saveProject = async (id, editable = undefined) => {
  const res = await request(axios.put(`${PROJECTS}/${encodeURIComponent(id)}`, editable || {}), translate("messages.videoEditor.saveProjectFailed"));
  return res.data;
};

export const saveProjectAs = async (id, name, editable = undefined) => {
  const res = await request(
    axios.post(`${PROJECTS}/${encodeURIComponent(id)}/save-as`, { name, editable }),
    translate("messages.videoEditor.saveProjectAsFailed"),
  );
  return res.data;
};

export const renameProject = async (id, name) => {
  const res = await request(
    axios.patch(`${PROJECTS}/${encodeURIComponent(id)}`, { name }),
    translate("messages.videoEditor.renameProjectFailed"),
  );
  return res.data;
};

export const deleteProject = async (id) => {
  const res = await request(axios.delete(`${PROJECTS}/${encodeURIComponent(id)}`), translate("messages.videoEditor.deleteProjectFailed"));
  return res.data;
};

// Reference-integrity report: { clips: [{clipId, status: ok|missing|stale}], missing, stale }.
export const validateProject = async (id) => {
  const res = await request(axios.post(`${PROJECTS}/${encodeURIComponent(id)}/validate`), translate("messages.videoEditor.validateProjectFailed"));
  return res.data;
};
