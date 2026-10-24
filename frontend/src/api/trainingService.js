// frontend/src/api/trainingService.js
// Version 1.0: Service for training dataset API calls.
import { BASE_URL, handleResponse } from "./apiClient";
import { translate } from "../i18n";

export const getTrainingDatasets = async () => {
  try {
    const response = await fetch(`${BASE_URL}/training_datasets`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error(
      "trainingService: Error getting training datasets:",
      err.message,
    );
    return { error: err.message || translate("messages.training.datasetsFailed") };
  }
};

export const createTrainingDataset = async (datasetData) => {
  try {
    const response = await fetch(`${BASE_URL}/training_datasets/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datasetData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      "trainingService: Error creating training dataset:",
      err.message,
    );
    throw err;
  }
};

export const updateTrainingDataset = async (id, datasetData) => {
  if (!id) return { error: "Invalid dataset ID provided for update." };
  try {
    const response = await fetch(`${BASE_URL}/training_datasets/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(datasetData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `trainingService: Error updating training dataset ${id}:`,
      err.message,
    );
    throw err;
  }
};

export const deleteTrainingDataset = async (id) => {
  if (!id) return { error: "Invalid dataset ID provided for delete." };
  try {
    const response = await fetch(`${BASE_URL}/training_datasets/${id}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `trainingService: Error deleting training dataset ${id}:`,
      err.message,
    );
    throw err;
  }
};

/**
 * What a dataset path holds, read the way the trainer reads it: files, rows,
 * usable rows, formats, a few clipped samples, the first problems and
 * whether it can be trained on.
 */
export const inspectTrainingDataset = async (path) => {
  const response = await fetch(
    `${BASE_URL}/training/datasets/inspect?path=${encodeURIComponent(path)}`,
  );
  const data = await handleResponse(response, { quiet: true });
  return data.data || data;
};

/** Where the dataset picker opens: { locations: [...], default: path }. */
export const getTrainingDatasetLocations = async () => {
  const response = await fetch(`${BASE_URL}/training/datasets/locations`);
  const data = await handleResponse(response, { quiet: true });
  return data.data || data;
};

// Training Jobs API
export const getTrainingJobs = async (filters = {}) => {
  try {
    const params = new URLSearchParams();
    if (filters.status) params.append("status", filters.status);
    if (filters.dataset_id) params.append("dataset_id", filters.dataset_id);
    
    const url = `${BASE_URL}/training/jobs${params.toString() ? `?${params}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error getting training jobs:", err.message);
    throw err;
  }
};

export const createTrainingJob = async (jobData) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(jobData),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error creating training job:", err.message);
    throw err;
  }
};

export const getTrainingJob = async (id) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${id}`);
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error getting training job ${id}:`, err.message);
    throw err;
  }
};

export const cancelTrainingJob = async (id) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${id}/cancel`, {
      method: "POST",
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error cancelling training job ${id}:`, err.message);
    throw err;
  }
};

/** Start a job that was created without starting; the backend checks it as it did at creation. */
export const startTrainingJob = async (id) => {
  const response = await fetch(`${BASE_URL}/training/jobs/${id}/start`, { method: "POST" });
  const data = await handleResponse(response);
  return data.data || data;
};

export const resumeTrainingJob = async (id) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${id}/resume`, {
      method: "POST",
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error resuming training job ${id}:`, err.message);
    throw err;
  }
};

export const deleteTrainingJob = async (id) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${id}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error deleting training job ${id}:`, err.message);
    throw err;
  }
};

// Device Profiles API
export const getDeviceProfiles = async () => {
  try {
    const response = await fetch(`${BASE_URL}/training/device-profiles`);
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error getting device profiles:", err.message);
    throw err;
  }
};

export const createDeviceProfile = async (profileData) => {
  try {
    const response = await fetch(`${BASE_URL}/training/device-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profileData),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error creating device profile:", err.message);
    throw err;
  }
};

export const updateDeviceProfile = async (id, profileData) => {
  try {
    const response = await fetch(`${BASE_URL}/training/device-profiles/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profileData),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error updating device profile ${id}:`, err.message);
    throw err;
  }
};

export const deleteDeviceProfile = async (id) => {
  try {
    const response = await fetch(`${BASE_URL}/training/device-profiles/${id}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error deleting device profile ${id}:`, err.message);
    throw err;
  }
};

// Base Models API
/**
 * The declared base models: { models: [{ id, name, installed, fits,
 * fit_reason, size_gb, license, max_batch_size, max_seq_length, vision, ... }],
 * hardware, download }. plan: also ask for the one-use token Download and
 * Remove send back.
 */
export const getBaseModels = async ({ plan = false } = {}) => {
  const response = await fetch(`${BASE_URL}/training/base-models${plan ? "?plan=1" : ""}`);
  const data = await handleResponse(response);
  return data.data || data;
};

export const installTrainingBaseModel = async (planToken, modelId) => {
  const response = await fetch(`${BASE_URL}/training/base-models/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: "download", model: modelId, plan_token: planToken }),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

export const removeTrainingBaseModel = async (planToken, modelId) => {
  const response = await fetch(`${BASE_URL}/training/base-models/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: "remove", model: modelId, plan_token: planToken }),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

// Whether a base model is already on this machine (a miss means the job
// downloads it from huggingface.co — the form says so before Create).
export const getBaseModelStatus = async (name) => {
  const response = await fetch(
    `${BASE_URL}/training/base-models/status?name=${encodeURIComponent(name)}`,
  );
  const data = await handleResponse(response);
  if (data.error) throw new Error(data.error);
  return data.data || data;
};

// Settings > Training libraries (Unsloth, TRL, Datasets). plan: also ask for
// the one-use token that the Install and Remove buttons send back; the backend
// refuses an install or remove without it.
export const getTrainingLibraries = async ({ plan = false } = {}) => {
  const response = await fetch(`${BASE_URL}/training/libraries${plan ? "?plan=1" : ""}`);
  const data = await handleResponse(response);
  return data.data || data;
};

export const installTrainingLibraries = async (planToken, { anyway = false } = {}) => {
  const response = await fetch(`${BASE_URL}/training/libraries/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: "install", plan_token: planToken, anyway }),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

export const removeTrainingLibraries = async (planToken) => {
  const response = await fetch(`${BASE_URL}/training/libraries/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: "remove", plan_token: planToken }),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

// Settings > Training libraries > GGUF converter (llama.cpp's converter that
// Export to Ollama runs). plan: also ask for the one-use token Install and
// Remove send back; the backend downloads nothing without it.
export const getGgufConverter = async ({ plan = false } = {}) => {
  const response = await fetch(`${BASE_URL}/training/libraries/gguf-converter${plan ? "?plan=1" : ""}`);
  const data = await handleResponse(response);
  return data.data || data;
};

export const installGgufConverter = async (planToken) => {
  const response = await fetch(`${BASE_URL}/training/libraries/gguf-converter/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: "install", plan_token: planToken }),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

export const removeGgufConverter = async (planToken) => {
  const response = await fetch(`${BASE_URL}/training/libraries/gguf-converter/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm: "remove", plan_token: planToken }),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

export const getImageFolders = async () => {
  try {
    const response = await fetch(`${BASE_URL}/training/images`);
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error getting image folders:", err.message);
    throw err;
  }
};

export const getHardwareCapabilities = async () => {
  try {
    const response = await fetch(`${BASE_URL}/training/hardware`);
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error getting hardware capabilities:", err.message);
    throw err;
  }
};

// Pipeline API
// Build from folder: a messages dataset from a folder of notes. check scans
// and scrubs (no model), probe times three passages with the local model,
// start queues the build on the training queue.
const postFolderBuild = async (suffix, body) => {
  const response = await fetch(`${BASE_URL}/training/pipeline/build-from-folder${suffix}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await handleResponse(response);
  return data.data || data;
};

export const checkFolderBuild = (body) => postFolderBuild("/check", body);
export const probeFolderBuild = (body) => postFolderBuild("/probe", body);
export const startFolderBuild = (body) => postFolderBuild("", body);

// The word list a folder build replaces with [name]; kept on this machine.
export const getScrubTerms = async () => {
  const response = await fetch(`${BASE_URL}/training/scrub-terms`);
  const data = await handleResponse(response);
  return (data.data || data).terms || [];
};

export const saveScrubTerms = async (terms) => {
  const response = await fetch(`${BASE_URL}/training/scrub-terms`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ terms }),
  });
  const data = await handleResponse(response);
  return (data.data || data).terms || [];
};

export const startParseJob = async (parseData) => {
  try {
    const response = await fetch(`${BASE_URL}/training/pipeline/parse`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(parseData),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error starting parse job:", err.message);
    throw err;
  }
};

export const startFilterJob = async (filterData) => {
  try {
    const response = await fetch(`${BASE_URL}/training/pipeline/filter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(filterData),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error starting filter job:", err.message);
    throw err;
  }
};

// Export/Import API
export const exportToGGUF = async (jobId, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${jobId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error exporting job ${jobId} to GGUF:`, err.message);
    throw err;
  }
};

export const importToOllama = async (jobId, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${jobId}/import-ollama`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error importing job ${jobId} to Ollama:`, err.message);
    throw err;
  }
};

/**
 * Release a run the export gate held as worse than its base model. The
 * choice (who, from where, when) is recorded on the job; nothing is exported
 * until exportToOllama is called.
 */
export const exportTrainingJobAnyway = async (jobId, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${jobId}/export-anyway`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error overriding the export gate for job ${jobId}:`, err.message);
    throw err;
  }
};

export const exportToOllama = async (jobId, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}/training/jobs/${jobId}/export-to-ollama`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error(`trainingService: Error exporting job ${jobId} to Ollama:`, err.message);
    throw err;
  }
};
