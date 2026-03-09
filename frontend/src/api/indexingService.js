// frontend/src/api/indexingService.js
// Version 1.0: Service for indexing and job status related calls.
import { BASE_URL, handleResponse } from "./apiClient";

export const triggerIndexing = async (documentId, parentJobId = null) => {
  try {
    const payload = parentJobId ? { parent_job_id: parentJobId } : {};
    const response = await fetch(`${BASE_URL}/index/${documentId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("indexingService: Error triggering indexing:", err.message);
    throw err;
  }
};

export const indexBulk = async (folderIds = [], documentIds = []) => {
  try {
    const response = await fetch(`${BASE_URL}/index/bulk`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_ids: folderIds, document_ids: documentIds }),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("indexingService: Error triggering bulk indexing:", err.message);
    throw err;
  }
};

export const startIndexing = async (indexingData) => {
  try {
    const response = await fetch(`${BASE_URL}/indexing/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(indexingData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("indexingService: Error starting indexing:", err.message);
    throw err;
  }
};

export const getIndexingJobStatus = async (jobId) => {
  if (!jobId) return { error: "Job ID is required." };
  try {
    const response = await fetch(`${BASE_URL}/indexing/status/${jobId}`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `indexingService: Error fetching indexing job status for ${jobId}:`,
      err.message,
    );
    throw err;
  }
};

export const cancelJob = async (jobId) => {
  if (!jobId) return { error: "Job ID is required for cancel." };
  try {
    const response = await fetch(`${BASE_URL}/meta/cancel_job/${jobId}`, {
      method: "POST",
    });
    const data = await handleResponse(response);
    return data;
  } catch (err) {
    console.error(`indexingService: Error cancelling job ${jobId}:`, err);
    return { error: err.message || "Failed to cancel job." };
  }
};

export const getAllJobs = async (queryParams = {}) => {
  try {
    const params = new URLSearchParams(queryParams);
    const queryString = params.toString();
    const url = `${BASE_URL}/indexing/jobs${queryString ? `?${queryString}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("indexingService: Error fetching all jobs:", err.message);
    return { error: err.message || "Failed to fetch jobs." };
  }
};
