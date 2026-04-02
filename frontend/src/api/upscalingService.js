// frontend/src/api/upscalingService.js
// API service for the Upscaling plugin

import { BASE_URL as API_BASE_URL, handleResponse } from "./apiClient";

const BASE_URL = `${API_BASE_URL}/upscaling`;

export const getHealth = async () => {
  const response = await fetch(`${BASE_URL}/health`, { method: "GET" });
  return handleResponse(response);
};

export const getModels = async () => {
  const response = await fetch(`${BASE_URL}/models`, { method: "GET" });
  return handleResponse(response);
};

export const uploadAndUpscale = async (file, options = {}) => {
  const formData = new FormData();
  formData.append("file", file);
  if (options.model) formData.append("model", options.model);
  if (options.scale) formData.append("scale", String(options.scale));
  if (options.target_width) formData.append("target_width", String(options.target_width));
  if (options.two_pass) formData.append("two_pass", "true");

  const response = await fetch(`${BASE_URL}/upload`, {
    method: "POST",
    body: formData,
  });
  return handleResponse(response);
};

export const upscaleVideo = async (inputPath, options = {}) => {
  const response = await fetch(`${BASE_URL}/upscale/video`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input_path: inputPath,
      output_path: options.output_path,
      model: options.model,
      scale: options.scale,
      suffix: options.suffix || "upscaled",
    }),
  });
  return handleResponse(response);
};

export const listJobs = async () => {
  const response = await fetch(`${BASE_URL}/jobs`, { method: "GET" });
  return handleResponse(response);
};

export const getJob = async (jobId) => {
  const response = await fetch(`${BASE_URL}/jobs/${jobId}`, { method: "GET" });
  return handleResponse(response);
};

export const cancelJob = async (jobId) => {
  const response = await fetch(`${BASE_URL}/jobs/${jobId}`, { method: "DELETE" });
  return handleResponse(response);
};

export default {
  getHealth,
  getModels,
  uploadAndUpscale,
  upscaleVideo,
  listJobs,
  getJob,
  cancelJob,
};
