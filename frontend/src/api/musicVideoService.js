import axios from "axios";
import { translate } from "../i18n";

/**
 * Music Video Service for the Music Video page.
 * Drives the song-driven pipeline: create (→ analyze) / inspect / approve.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const listMusicVideos = async () => {
  const response = await axios.get(`${API_BASE}/music-video`);
  return response.data;
};

export const getMusicVideo = async (id) => {
  const response = await axios.get(`${API_BASE}/music-video/${id}`);
  return response.data;
};

export const createMusicVideo = async (data) => {
  const response = await axios.post(`${API_BASE}/music-video`, data);
  return response.data;
};

export const approveMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/approve`);
  return response.data;
};

/** Clear one past generation from the log. */
export const deleteMusicVideo = async (id) => {
  const response = await axios.delete(`${API_BASE}/music-video/${id}`);
  return response.data;
};

/** Clear finished generations (complete/failed), or all with {all:true}. */
export const clearMusicVideos = async ({ all = false } = {}) => {
  const response = await axios.delete(`${API_BASE}/music-video`, {
    params: all ? { all: true } : {},
  });
  return response.data;
};

/** Serve URL for a rendered output Document (same pattern as the Video Editor). */
export const documentDownloadUrl = (docId) =>
  `${API_BASE}/files/document/${docId}/download`;

/** Update per-cut prompts (and optionally the global style_prompt) before approval.
 *  Body shape: { prompts: { "0": "new prompt for cut 0", ... }, style_prompt?: "..." }
 *  or { clips: [ { index: 0, prompt: "..." }, ... ] }
 */
export const updateMusicVideoPlan = async (id, data) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/plan`, data);
  return response.data;
};

/** Re-run the Director over the existing cut plan (pre-approval only).
 *  Optional: {
 *    feedback: "...",
 *    planning_mode: "narrative" | "visual",
 *    director_model: "gemma4:e4b"   // dedicated small model for the Director agent
 *  }
 */
export const regenerateMusicVideoPlan = async (id, data = {}) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/regenerate-plan`, data);
  return response.data;
};

/** Reset a completed (or failed) music video back to the plan approval stage so it can be re-rendered
 * (with the same or edited treatment/prompts/settings). Useful for re-doing with different seeds, models, or tweaks.
 */
export const replanMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/replan`);
  return response.data;
};

/** (Re)start song analysis on an existing job (preserves upload + settings). */
export const analyzeMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/analyze`);
  return response.data;
};

export const cancelMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/cancel`);
  return response.data;
};

/** Keep a cut a quality check held for review; the video is assembled once none is held. */
export const approveMusicVideoCut = async (id, index) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/cut/${index}/approve`);
  return response.data;
};

/** Render a held cut again (checked like the first render). */
export const rerenderMusicVideoCut = async (id, index) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/cut/${index}/rerender`);
  return response.data;
};

/** Assemble the final video again from the clips already rendered (no clip re-renders). */
export const reassembleMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/reassemble`);
  return response.data;
};

export const generateMusicVideoStoryboards = async (id, data = {}) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/generate-storyboards`, data);
  return response.data;
};

/** Poll until async storyboard generation finishes (202 + Celery dispatch). */
export const pollMusicVideoStoryboards = async (id, { intervalMs = 5000, maxAttempts = 360 } = {}) => {
  for (let i = 0; i < maxAttempts; i += 1) {
    const mv = await getMusicVideo(id);
    if (!mv.storyboard_generating) {
      if (mv.storyboard_error) {
        throw new Error(mv.storyboard_error);
      }
      return mv;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(translate("messages.musicVideo.storyboardTimeout"));
};

export const regenMusicVideoStoryboard = async (id, index, data = {}) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/regen-storyboard/${index}`, data);
  return response.data;
};

/** The progress meter alone: { id, current_stage, status, progress }. */
export const getMusicVideoProgress = async (id) => {
  const response = await axios.get(`${API_BASE}/music-video/${id}/progress`);
  return response.data;
};

/** Music videos filed under one project (projectId "none" lists unfiled ones). */
export const listMusicVideosForProject = async (projectId) => {
  const response = await axios.get(`${API_BASE}/music-video`, { params: { project_id: projectId } });
  return response.data;
};

/** Rename / file under a project / notes / tags: { name?, project_id?, notes?, tags? }. */
export const updateMusicVideo = async (id, data) => {
  const response = await axios.patch(`${API_BASE}/music-video/${id}`, data);
  return response.data;
};

/** Copy into a new music video: { name?, project_id?, keep_storyboards?, keep_clips? }. */
export const duplicateMusicVideo = async (id, data = {}) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/duplicate`, data);
  return response.data;
};

/** Reopen a finished/failed/cancelled video for editing; finished clips are kept and
 *  only cuts you change render again on approval. */
export const reviseMusicVideo = async (id) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/revise`);
  return response.data;
};

/** Edit one cut: { prompt?, seed? (int|null), locked? (bool) }. */
export const editMusicVideoCut = async (id, index, data) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/cut/${index}`, data);
  return response.data;
};

export const listMusicVideoRevisions = async (id) => {
  const response = await axios.get(`${API_BASE}/music-video/${id}/revisions`);
  return response.data;
};

export const saveMusicVideoRevision = async (id, label) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/revisions`, { label });
  return response.data;
};

export const restoreMusicVideoRevision = async (id, revisionId) => {
  const response = await axios.post(`${API_BASE}/music-video/${id}/revisions/${revisionId}/restore`);
  return response.data;
};

/** Every video project (music video, Film Crew) filed under a Guaardvark Project. */
export const listProjectVideos = async (projectId) => {
  const response = await axios.get(`${API_BASE}/projects/${projectId}/videos`);
  return response.data;
};
