// frontend/src/api/trainingService.js
// Version 1.0: Service for training dataset API calls.
import { BASE_URL, handleResponse } from "./apiClient";

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
    return { error: err.message || "Failed to get training datasets." };
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
export const getBaseModels = async () => {
  try {
    const response = await fetch(`${BASE_URL}/training/base-models`);
    const data = await handleResponse(response);
    if (data.error) throw new Error(data.error);
    return data.data || data;
  } catch (err) {
    console.error("trainingService: Error getting base models:", err.message);
    throw err;
  }
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
