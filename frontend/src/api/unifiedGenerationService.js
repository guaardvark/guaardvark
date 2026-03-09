// unifiedGenerationService.js
// Unified generation service for both single and bulk CSV generation
// Version 1.0: Consolidated generation API

import { BACKEND_URL } from './apiClient';
import { DEFAULT_WORD_COUNT, BATCH_SIZE_MAX } from '../config/constants';
const API_URL = BACKEND_URL;

/**
 * Generate CSV content using the unified generation API
 * Automatically detects whether to use single or bulk generation
 * 
 * @param {Object} params - Generation parameters
 * @param {string} params.prompt - User prompt/instructions
 * @param {string} params.filename - Output filename
 * @param {string} [params.client] - Client name
 * @param {string} [params.project] - Project name
 * @param {string} [params.website] - Website URL
 * @param {number} [params.quantity] - Number of items to generate (for bulk)
 * @param {string} [params.type] - Force generation type: "single", "bulk", or "auto"
 * @param {Object} [options] - Additional options
 * @returns {Promise<Object>} Generation result
 */
export const generateCSV = async (params, options = {}) => {
  const {
    prompt,
    filename,
    client = "Professional Services",
    project = "Content Generation",
    website = "professional-website.com",
    quantity,
    type = "auto"
  } = params;

  // Validate required parameters
  if (!prompt) {
    throw new Error("Prompt is required for CSV generation");
  }
  if (!filename) {
    throw new Error("Filename is required for CSV generation");
  }

  // Prepare request payload
  const payload = {
    output_filename: filename,
    prompt: prompt,
    client: client,
    project: project,
    website: website,
    type: type
  };

  // Add bulk-specific parameters if quantity is specified
  if (quantity && quantity > 1) {
    payload.num_items = quantity;
    payload.concurrent_workers = Math.min(quantity, 10); // Cap at 10 workers
    payload.target_word_count = DEFAULT_WORD_COUNT;
    payload.batch_size = Math.min(quantity, BATCH_SIZE_MAX);
  }

  console.log("unifiedGenerationService: Generating CSV with payload:", payload);

  try {
    const response = await fetch(`${API_URL}/api/generate/csv`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
      signal: options.signal, // Support request cancellation
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    const result = await response.json();
    console.log("unifiedGenerationService: Generation successful:", result);
    
    return result;

  } catch (error) {
    console.error("unifiedGenerationService: Generation failed:", error);
    throw error;
  }
};

/**
 * Generate a single CSV row
 * 
 * @param {Object} params - Generation parameters
 * @param {string} params.prompt - User prompt/instructions
 * @param {string} params.filename - Output filename
 * @param {string} [params.client] - Client name
 * @param {string} [params.project] - Project name
 * @param {string} [params.website] - Website URL
 * @param {Object} [options] - Additional options
 * @returns {Promise<Object>} Generation result
 */
export const generateSingleCSV = async (params, options = {}) => {
  return generateCSV({
    ...params,
    type: "single"
  }, options);
};

/**
 * Generate bulk CSV content
 * 
 * @param {Object} params - Generation parameters
 * @param {string} params.prompt - User prompt/instructions
 * @param {string} params.filename - Output filename
 * @param {number} params.quantity - Number of items to generate
 * @param {string} [params.client] - Client name
 * @param {string} [params.project] - Project name
 * @param {string} [params.website] - Website URL
 * @param {number} [params.concurrent_workers] - Number of concurrent workers
 * @param {number} [params.target_word_count] - Target word count per item
 * @param {Object} [options] - Additional options
 * @returns {Promise<Object>} Generation result
 */
export const generateBulkCSV = async (params, options = {}) => {
  const {
    prompt,
    filename,
    quantity,
    client = "Professional Services",
    project = "Content Generation",
    website = "professional-website.com",
    concurrent_workers = Math.min(quantity, 10),
    target_word_count = DEFAULT_WORD_COUNT
  } = params;

  return generateCSV({
    ...params,
    type: "bulk",
    num_items: quantity,
    concurrent_workers: concurrent_workers,
    target_word_count: target_word_count,
    batch_size: Math.min(quantity, 50)
  }, options);
};

/**
 * Get generation job status
 * 
 * @param {string} jobId - Job ID to check
 * @param {Object} [options] - Additional options
 * @returns {Promise<Object>} Job status
 */
export const getGenerationStatus = async (jobId, options = {}) => {
  if (!jobId) {
    throw new Error("Job ID is required");
  }

  try {
    const response = await fetch(`${API_URL}/api/generate/status?job_id=${jobId}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
      signal: options.signal,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    const result = await response.json();
    return result;

  } catch (error) {
    console.error("unifiedGenerationService: Failed to get job status:", error);
    throw error;
  }
};

/**
 * Auto-detect generation type based on prompt and parameters
 * 
 * @param {Object} params - Generation parameters
 * @returns {string} Detected generation type: "single" or "bulk"
 */
export const detectGenerationType = (params) => {
  const { prompt, quantity } = params;
  
  // Check for explicit bulk indicators
  if (quantity && quantity > 1) {
    return "bulk";
  }
  
  // Check prompt for bulk indicators
  if (prompt) {
    const promptLower = prompt.toLowerCase();
    const bulkIndicators = [
      "multiple", "several", "many", "bulk", "batch", "generate", "create",
      "rows", "items", "pages", "articles", "content pieces"
    ];
    
    for (const indicator of bulkIndicators) {
      if (promptLower.includes(indicator)) {
        return "bulk";
      }
    }
  }
  
  // Default to single generation
  return "single";
};
