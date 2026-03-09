// frontend/src/api/bulkImportService.js
// Bulk import jobs for Documents

import { BASE_URL } from './apiClient';
const API_URL = BASE_URL;

export const startBulkImport = async (payload) => {
  try {
    console.log('Starting bulk import with payload:', payload);
    const response = await fetch(`${API_URL}/files/bulk-import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    console.log('Response status:', response.status, 'ok:', response.ok);

    if (!response.ok) {
      const errorData = await response.json().catch((e) => {
        console.error('Failed to parse error response:', e);
        return {};
      });
      console.log('Error data:', errorData);

      // Handle both string and object error formats
      const errorMessage =
        errorData.message ||
        (typeof errorData.error === 'object' ? errorData.error?.message : errorData.error) ||
        `HTTP ${response.status}: ${response.statusText}`;
      throw new Error(errorMessage);
    }

    const data = await response.json();
    console.log('Success response:', data);
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
