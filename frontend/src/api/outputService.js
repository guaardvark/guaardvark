// frontend/src/api/outputService.js
// Output Management Service - API calls for managing bulk generation outputs

import { BASE_URL, handleResponse } from "./apiClient";

const API_URL = `${BASE_URL}/outputs`;

/**
 * Get list of all tracking files with metadata
 */
export const getOutputs = async () => {
  return fetch(API_URL)
    .then(handleResponse)
    .catch(error => {
      console.error('Error fetching outputs:', error);
      throw error;
    });
};

/**
 * Get detailed content of a specific tracking file
 */
export const getOutputDetails = async (filename) => {
  return fetch(`${API_URL}/${filename}`)
    .then(handleResponse)
    .catch(error => {
      console.error(`Error fetching output details for ${filename}:`, error);
      throw error;
    });
};

/**
 * Download CSV file converted from tracking JSON
 */
export const downloadOutputCSV = async (filename) => {
  try {
    // Use merged CSV endpoint to include retry results
    const response = await fetch(`${API_URL}/${filename}/merged-csv`);
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP error! Status: ${response.status}`);
    }
    
    // Get filename from Content-Disposition header or use default
    const contentDisposition = response.headers.get('Content-Disposition');
    let downloadFilename = filename.replace('_tracking_', '_content_').replace('.json', '.csv');
    
    if (contentDisposition) {
      const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);
      if (filenameMatch) {
        downloadFilename = filenameMatch[1];
      }
    }
    
    // Create blob and download
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = downloadFilename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    
    return { success: true, filename: downloadFilename };
  } catch (error) {
    console.error(`Error downloading CSV for ${filename}:`, error);
    throw error;
  }
};

/**
 * Download XML file converted from tracking JSON
 */
export const downloadOutputXML = async (filename) => {
  try {
    const response = await fetch(`${API_URL}/${filename}/xml`);
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP error! Status: ${response.status}`);
    }
    
    // Get filename from Content-Disposition header or use default
    const contentDisposition = response.headers.get('Content-Disposition');
    let downloadFilename = filename.replace('_tracking_', '_content_').replace('.json', '.xml');
    
    if (contentDisposition) {
      const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);
      if (filenameMatch) {
        downloadFilename = filenameMatch[1];
      }
    }
    
    // Create blob and download
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = downloadFilename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    
    return { success: true, filename: downloadFilename };
  } catch (error) {
    console.error(`Error downloading XML for ${filename}:`, error);
    throw error;
  }
};

/**
 * Delete a tracking file
 */
export const deleteOutput = async (filename) => {
  return fetch(`${API_URL}/${filename}`, {
    method: 'DELETE',
  })
    .then(handleResponse)
    .catch(error => {
      console.error(`Error deleting output ${filename}:`, error);
      throw error;
    });
};

/**
 * Retry failed rows from a tracking file
 */
export const retryFailedRows = async (tracking_filename, options) => {
  return fetch(`${BASE_URL}/bulk-generate/retry`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tracking_file: tracking_filename,
      retry_mode: options.retry_mode || 'all_inactive',
      output_filename: options.output_filename || 'retry_output.csv',
      model_name: options.model_name,
      prompt_rule_id: options.prompt_rule_id,
      max_retries: options.max_retries || 5,
      concurrent_workers: options.concurrent_workers || 10,
      batch_size: options.batch_size || 50
    })
  })
    .then(handleResponse)
    .catch(error => {
      console.error(`Error retrying failed rows for ${tracking_filename}:`, error);
      throw error;
    });
};
