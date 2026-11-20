import axios from "axios";

/**
 * Production Service for Film Crew page.
 * Handles productions, subjects (casting), and storyboard approvals.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const listProductions = async () => {
  const response = await axios.get(`${API_BASE}/production`);
  return response.data;
};

export const getProduction = async (id) => {
  const response = await axios.get(`${API_BASE}/production/${id}`);
  return response.data;
};

export const listProductionSubjects = async (id) => {
  const response = await axios.get(`${API_BASE}/production/${id}/subjects`);
  return response.data;
};

export const createProduction = async (data) => {
  const response = await axios.post(`${API_BASE}/production`, data);
  return response.data;
};

export const deleteProduction = async (id) => {
  const response = await axios.delete(`${API_BASE}/production/${id}`);
  return response.data;
};

export const retryProduction = async (id) => {
  const response = await axios.post(`${API_BASE}/production/${id}/retry`);
  return response.data;
};

export const castSubject = async (productionId, subjectId, data) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/cast/${subjectId}`, data);
  return response.data;
};

export const confirmCasting = async (productionId) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/casting/confirm`);
  return response.data;
};

// confirmFlagged: the person has seen the shots the curator flagged and wants
// them rendered anyway. Without it the server refuses while any are flagged.
export const approveStoryboard = async (productionId, { confirmFlagged = false } = {}) => {
  const url = `${API_BASE}/production/${productionId}/storyboard/approve`;
  const response = confirmFlagged
    ? await axios.post(url, { confirm_flagged: true })
    : await axios.post(url);
  return response.data;
};

export const regenerateShot = async (productionId, shotId, data) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/storyboard/shot/${shotId}/regenerate`, data);
  return response.data;
};

export const listCastLibrary = async () => {
  const response = await axios.get(`${API_BASE}/cast-library`);
  return response.data;
};

export const createCastSubject = async (data) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects`, data);
  return response.data;
};

export const deleteCastSubject = async (id) => {
  await axios.delete(`${API_BASE}/cast-library/subjects/${id}`);
};

// Remove a single reference image (by its index in ref_image_paths). Returns the
// updated subject so the caller can refresh the thumbnail grid.
export const deleteSubjectRef = async (id, index) => {
  const response = await axios.delete(`${API_BASE}/cast-library/subjects/${id}/refs/${index}`);
  return response.data;
};

// ── Cast & LoRA Studio (Casting Director) ────────────────────────────────────
// Efficient single-subject detail (with optional samples). Falls back to
// list+find only for legacy callers.
export const getCastSubject = async (id) => {
  const { subjects = [] } = await listCastLibrary();
  return subjects.find((s) => String(s.id) === String(id)) || null;
};

// Preferred for CastMemberPage — single fetch, optionally includes samples.
export const getCastSubjectDetail = async (id, { includeSamples = false } = {}) => {
  const qs = includeSamples ? "?include=samples" : "";
  const response = await axios.get(`${API_BASE}/cast-library/subjects/${id}${qs}`);
  return response.data; // { subject, samples? }
};

export const updateCastSubject = async (id, patch) => {
  const response = await axios.patch(`${API_BASE}/cast-library/subjects/${id}`, patch);
  return response.data;
};

// Casting Director: plan the bible + N shot prompts (SYNC — returns {bible, samples}).
export const planCharacter = async (id, body = {}) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/plan`, body);
  return response.data;
};

// Vision-rescan reference photos → rewrite Subject.bible (does NOT train LoRA).
export const rebuildBibleFromRefs = async (id, body = {}) => {
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/bible/from-refs`,
    body,
  );
  return response.data;
};

// Dispatch the FLUX image loop for the whole sheet (ASYNC — returns {task_id}).
// Pass {use_trained_lora: true} after a successful training to generate additional
// images that are consistent with the trained LoRA (per user vision for the
// Generate Character tab).
export const generateSamples = async (id, options = {}) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/generate`, options);
  return response.data;
};

// Cancel an in-flight Generate Character batch (or single-sample regen).
export const cancelGenerateSamples = async (id) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/generate/cancel`);
  return response.data;
};

export const listSamples = async (id) => {
  const response = await axios.get(`${API_BASE}/cast-library/subjects/${id}/samples`);
  return response.data;
};

// Regenerate one sample (ASYNC). body: { prompt_override?, seed? }.
export const regenerateSample = async (id, sampleId, body = {}) => {
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/samples/${sampleId}/regenerate`,
    body,
  );
  return response.data;
};

export const deleteSample = async (id, sampleId) => {
  await axios.delete(`${API_BASE}/cast-library/subjects/${id}/samples/${sampleId}`);
};

export const approveSamples = async (id, sampleIds, approved = true) => {
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/samples/approve`,
    { sample_ids: sampleIds, approved },
  );
  return response.data;
};

// Kick off LoRA training for this subject (ASYNC — returns {task_id}).
export const trainSubject = async (id, options = {}) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/train`, options);
  return response.data;
};

// The training adapter the train base declares: { needed, installed, size_mb, install }.
export const getTrainAdapterStatus = async (baseModelId) => {
  const response = await axios.get(`${API_BASE}/cast-library/train-adapter`, {
    params: baseModelId ? { base_model_id: baseModelId } : {},
  });
  return response.data;
};

export const installTrainAdapter = async (baseModelId) => {
  const response = await axios.post(`${API_BASE}/cast-library/train-adapter/install`,
    baseModelId ? { base_model_id: baseModelId } : {});
  return response.data;
};

export const cancelTrainSubject = async (id) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects/${id}/train/cancel`);
  return response.data;
};

const productionService = {
  listProductions,
  getProduction,
  listProductionSubjects,
  createProduction,
  castSubject,
  confirmCasting,
  approveStoryboard,
  regenerateShot,
  listCastLibrary,
  createCastSubject,
  deleteCastSubject,
  getCastSubject,
  getCastSubjectDetail,
  updateCastSubject,
  planCharacter,
  rebuildBibleFromRefs,
  generateSamples,
  cancelGenerateSamples,
  listSamples,
  regenerateSample,
  approveSamples,
  deleteSample,
  trainSubject,
  getTrainAdapterStatus,
  installTrainAdapter,
  cancelTrainSubject,
};

// Import a LoRA checkpoint trained OUTSIDE Guaardvark (Ostris AI-Toolkit,
// kohya, diffusers/PEFT) and attach it to a Subject. baseModelId is
// 'zimage-turbo' or 'flux-dev'; triggerWord is the token the LoRA was
// trained on. Returns { subject } on success, throws on validation failure
// (mismatched base model, unsupported layout, oversized file).
export const importSubjectLora = async (id, { file, baseModelId, triggerWord }) => {
  const fd = new FormData();
  fd.append("lora_file", file);
  fd.append("base_model_id", baseModelId);
  fd.append("trigger_word", triggerWord);
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/import-lora`,
    fd,
    { headers: { "Content-Type": "multipart/form-data" } },
  );
  return response.data;
};

// Make the member's current LoRA for baseModelId its default (Versions tab,
// one "Make default" button per base the member holds a LoRA for). Returns
// { subject } on success, throws (400) when the member has no LoRA for that
// base.
export const makeDefaultSubjectLora = async (id, baseModelId) => {
  const response = await axios.post(
    `${API_BASE}/cast-library/subjects/${id}/loras/${baseModelId}/make-default`,
  );
  return response.data;
};

// The image encoder behind the Cast identity check (Settings > Generation):
// what it is, whether it is installed, which check the smoke still gets, and
// any install in progress. Install is the only path that downloads it. A
// refused Install or Remove (409) still carries the status, plus `error`.
const identityCheckCall = async (method, path) => {
  try {
    const response = await axios({ method, url: `${API_BASE}/cast-library/identity-check${path}` });
    return response.data;
  } catch (e) {
    if (e.response?.data?.error) return e.response.data;
    throw e;
  }
};

export const getIdentityCheck = () => identityCheckCall("get", "");
export const installIdentityEncoder = () => identityCheckCall("post", "/install");
export const removeIdentityEncoder = () => identityCheckCall("post", "/remove");

export default productionService;
