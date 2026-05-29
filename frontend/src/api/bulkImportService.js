// frontend/src/api/bulkImportService.js
// Bulk import jobs for Documents

import { BASE_URL } from './apiClient';
const API_URL = BASE_URL;

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

export const startBulkImport = async (payload) => {
  try {
    debugLog('Starting bulk import', {
      fileCount: payload?.files?.length || payload?.file_ids?.length || 0,
    });
    const response = await fetch(`${API_URL}/files/bulk-import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    debugLog('Bulk import response status:', response.status, 'ok:', response.ok);

    if (!response.ok) {
      const errorData = await response.json().catch((e) => {
        console.error('Failed to parse error response:', e);
        return {};
      });
      debugLog('Bulk import error response received', {
        hasMessage: Boolean(errorData.message || errorData.error),
      });

      // Handle both string and object error formats
      const errorMessage =
        errorData.message ||
        (typeof errorData.error === 'object' ? errorData.error?.message : errorData.error) ||
        `HTTP ${response.status}: ${response.statusText}`;
      throw new Error(errorMessage);
    }

    const data = await response.json();
    debugLog('Bulk import started', { jobId: data?.job_id || data?.id });
    return data;
  } catch (error) {
    console.error("bulkImportService:startBulkImport error:", error);
    throw error; // Re-throw the original error instead of wrapping it
  }
};

export const getBulkImportStatus = async (jobId) => {
  if (!jobId) throw new Error("jobId is required to fetch status");
  try {
    const response = await fetch(`${API_URL}/files/bulk-import/${jobId}/status`);
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      // Handle both string and object error formats
      const errorMessage =
        errorData.message ||
        (typeof errorData.error === 'object' ? errorData.error.message : errorData.error) ||
        `HTTP ${response.status}`;
      throw new Error(errorMessage);
    }
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("bulkImportService:getBulkImportStatus error:", error);
    throw error; // Re-throw the original error instead of wrapping it
  }
};
