// frontend/src/api/imageEditService.js
// The Image Edit page's backend (backend/api/image_edit_api.py). A run starts a
// tool job; its progress and result are read from /api/tools/jobs/<id>.

import { BASE_URL as API_BASE_URL, handleResponse } from "./apiClient";

const BASE_URL = `${API_BASE_URL}/image-edit`;

const postJson = async (path, body) => {
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return handleResponse(response);
};

export const getCapabilities = async () => {
  const response = await fetch(`${BASE_URL}/capabilities`, { method: "GET" });
  return handleResponse(response);
};

// { url, name, width, height, consented, faces?, face_check_error? }
export const uploadPhoto = async (file, { checkFaces = false } = {}) => {
  const formData = new FormData();
  formData.append("file", file);
  if (checkFaces) formData.append("check_faces", "true");
  const response = await fetch(`${BASE_URL}/uploads`, { method: "POST", body: formData });
  return handleResponse(response);
};

export const checkFaces = (imageUrl) => postJson("/faces", { image: imageUrl });

export const recordConsent = (imageUrl) => postJson("/consent", { image: imageUrl, confirmed: true });

// { mode, image, references, prompt, settings } -> the job's first snapshot
export const startRun = (payload) => postJson("/run", payload);

// The job's snapshot; waitS holds the answer until it finishes (at most 60 s).
export const getJob = async (jobId, waitS = 0) => {
  const query = waitS > 0 ? `?wait_s=${waitS}` : "";
  const response = await fetch(`${API_BASE_URL}/tools/jobs/${encodeURIComponent(jobId)}${query}`, {
    method: "GET",
  });
  return handleResponse(response);
};
