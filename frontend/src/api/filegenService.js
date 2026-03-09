// frontend/src/api/filegenService.js
// Version 1.5: HTTP CLIENT STANDARDIZATION - Converted from axios to fetch while preserving memory leak fixes

import { BASE_URL } from './apiClient';
import { API_TIMEOUT_GENERATION, API_TIMEOUT_PROVEN_JOB } from '../config/constants';
const API_URL = BASE_URL;

// MEMORY LEAK FIX: Track active requests to prevent accumulation
const activeRequests = new Map();
let requestIdCounter = 0;

/**
 * MEMORY LEAK FIX: Clean up response data and associated resources
 * @param {Object} responseData - Response data object
 * @param {string} requestId - Unique request identifier
 */
const cleanupResponse = (responseData, requestId) => {
  try {
    // Remove from active requests tracking
    if (activeRequests.has(requestId)) {
      activeRequests.delete(requestId);
    }
    
    // Clear large response data references to prevent memory leaks
    if (responseData && typeof responseData === 'object') {
      // Clear any large arrays or objects that might cause memory leaks
      if (responseData.large_data) {
        responseData.large_data = null;
      }
      if (responseData.debug_info) {
        responseData.debug_info = null;
      }
    }
  } catch (error) {
    console.warn(`filegenService: Error during response cleanup for request ${requestId}:`, error);
  }
};

/**
 * MEMORY LEAK FIX: Create abort controller with cleanup
 * @param {string} requestId - Unique request identifier
 * @returns {AbortController} - Configured abort controller
 */
const createAbortController = (requestId) => {
  const controller = new AbortController();
  
  // Store controller reference for cleanup
  activeRequests.set(requestId, {
    controller,
    timestamp: Date.now(),
    type: 'generation'
  });
  
  return controller;
};

/**
 * MEMORY LEAK FIX: Cleanup expired requests to prevent memory accumulation
 */
const cleanupExpiredRequests = () => {
  const now = Date.now();
  const maxAge = 5 * 60 * 1000; // 5 minutes
  
  for (const [requestId, requestInfo] of activeRequests.entries()) {
    if (now - requestInfo.timestamp > maxAge) {
      try {
        if (requestInfo.controller && !requestInfo.controller.signal.aborted) {
          requestInfo.controller.abort();
        }
        activeRequests.delete(requestId);
      } catch (error) {
        console.warn(`filegenService: Error cleaning up expired request ${requestId}:`, error);
      }
    }
  }
};

// MEMORY LEAK FIX: Periodic cleanup of expired requests
setInterval(cleanupExpiredRequests, 2 * 60 * 1000); // Clean every 2 minutes

/**
 * Sends a request to the backend to generate a SINGLE file based on LLM instructions.
 * Used by single /createfile commands or similar direct requests.
 *
 * @param {object} payload - The data payload for the request.
 * @param {string} payload.filename - The desired output filename (e.g., 'report.csv').
 * @param {string|null} [payload.csv_file=null] - Optional input CSV filename (relative to uploads).
 * @param {string|null} [payload.xml_file=null] - Optional input XML filename (relative to uploads).
 * @param {string} [payload.user_instructions=''] - Instructions for the LLM.
 * @param {number|string|null} [payload.project_id=null] - Optional project context.
 * @param {string[]|string|null} [payload.tags=null] - Optional tags context.
 * @param {object} [options={}] - Additional options including signal for cancellation.
 * @returns {Promise<object>} A promise that resolves to the JSON response from the backend
 * (either success message with output_file or an error object).
 */
export const generateFileFromChat = async ({
  filename,
  csv_file = null,
  xml_file = null,
  user_instructions = "",
  project_id = null,
  tags = null,
  rule_id = null,
}, options = {}) => {
  // MEMORY LEAK FIX: Generate unique request ID and create abort controller
  const requestId = `single_${++requestIdCounter}_${Date.now()}`;
  const controller = options.signal ? null : createAbortController(requestId);
  const signal = options.signal || controller?.signal;
  
  // Log the data being sent (limit size to prevent memory issues)
  const logPayload = {
    filename,
    csv_file,
    xml_file,
    user_instructions: user_instructions.length > 200 ? 
      `${user_instructions.substring(0, 200)}...` : user_instructions,
    project_id,
    tags: Array.isArray(tags) ? `[${tags.length} tags]` : tags,
    rule_id,
  };
  console.log(`filegenService (single): Requesting file generation. Request ID: ${requestId}`, logPayload);

  // Validate filename presence before sending
  if (!filename) {
    const errorMsg = "filegenService (single) Error: 'filename' is missing.";
    console.error(errorMsg);
    // MEMORY LEAK FIX: Clean up request tracking
    if (controller) {
      activeRequests.delete(requestId);
    }
    return { error: errorMsg };
  }

  let responseData = null;
  try {
    // MEMORY LEAK FIX: Use fetch with proper cleanup and abort signal
    const timeoutId = setTimeout(() => {
      if (controller && !controller.signal.aborted) {
        controller.abort();
      }
    }, API_TIMEOUT_GENERATION);
    
    const response = await fetch(`${API_URL}/generate/direct_generate_and_save`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        outputfile: filename,
        prompt_text: user_instructions,
        project_id,
        tags,
        rule_id,
      }),
      signal, // MEMORY LEAK FIX: Support request cancellation
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    // MEMORY LEAK FIX: Extract data immediately
    responseData = await response.json();

    // Log success with limited data to prevent memory issues
    console.log(
      `filegenService (single): File generation successful. Request ID: ${requestId}`,
      responseData ? { 
        ...responseData, 
        // Limit logged data size
        content: responseData.content ? `[${responseData.content.length} chars]` : undefined 
      } : 'No data'
    );
    
    // MEMORY LEAK FIX: Clean up response data
    cleanupResponse(responseData, requestId);
    
    return responseData;
    
  } catch (error) {
    // MEMORY LEAK FIX: Handle errors without accumulating objects
    let errorData = {};
    
    if (error.name === 'AbortError') {
      errorData = { error: 'Request was cancelled or timed out.' };
    } else {
      // Extract essential error info without keeping full error object
      errorData = {
        error: error.message || "An unknown error occurred.",
        requestId, // Include for debugging
      };
    }
    
    console.error(
      `filegenService (single): Error during file generation request. Request ID: ${requestId}:`,
      errorData
    );
    
    // MEMORY LEAK FIX: Clean up request tracking
    if (responseData) {
      cleanupResponse(responseData, requestId);
    } else if (controller) {
      activeRequests.delete(requestId);
    }
    
    return errorData;
  }
};

/**
 * Calls the backend endpoint to generate a batch CSV file.
 * Used by the dedicated FileGenerationPage.
 *
 * @param {object} data - The data payload.
 * @param {number|null} data.client_id - Optional client ID.
 * @param {number|null} data.project_id - Optional project ID.
 * @param {number|null} data.website_id - Optional website ID.
 * @param {number} data.prompt_id - The ID of the prompt template to use.
 * @param {string[]} data.items - An array of strings (e.g., city names) to process.
 * @param {string} data.output_filename - The desired output filename (e.g., 'my_pages.csv').
 * @param {object} [options={}] - Additional options including signal for cancellation.
 * @returns {Promise<object>} - A promise that resolves with the essential response data.
 * @throws {Error} - Throws an error if the API call fails (caught by the calling component).
 */
export const generateBatchCsvProven = async (data, options = {}) => {
  // MEMORY LEAK FIX: Generate unique request ID and create abort controller
  const requestId = `batch_proven_${++requestIdCounter}_${Date.now()}`;
  const controller = options.signal ? null : createAbortController(requestId);
  const signal = options.signal || controller?.signal;
  
  // Log request with limited data to prevent memory issues
  const logData = {
    ...data,
    items: Array.isArray(data.items) ? `[${data.items.length} items]` : data.items
  };
  console.log(`filegenService (proven): Sending proven CSV generation request. Request ID: ${requestId}:`, logData);
  
  let responseData = null;
  try {
    // MEMORY LEAK FIX: Use fetch with proper cleanup and abort signal
    const timeoutId = setTimeout(() => controller?.abort(), API_TIMEOUT_PROVEN_JOB);
    
    // FIXED: Changed from csv-proven (hardcoded legal content) to csv (dynamic content)
    const response = await fetch(`${API_URL}/bulk-generate/csv`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        output_filename: data.output_filename,
        topics: data.items || [],
        num_items: data.items?.length || data.page_count || 10,
        client: data.client_name || "Professional Services",
        project: data.project_name || "Content Generation",
        website: data.client_website || "website.com",
        target_website: data.target_website || null,
        target_word_count: 500,
        batch_size: 25,
        prompt_rule_id: data.prompt_rule_id || null
      }),
      signal, // MEMORY LEAK FIX: Support request cancellation
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    // MEMORY LEAK FIX: Extract data immediately
    responseData = await response.json();
    
    console.log(
      `filegenService (batch): Batch CSV generation response. Request ID: ${requestId}:`,
      responseData ? { 
        ...responseData,
        // Limit logged data size
        content: responseData.content ? `[${responseData.content.length} chars]` : undefined 
      } : 'No data'
    );
    
    // MEMORY LEAK FIX: Clean up response data
    cleanupResponse(responseData, requestId);
    
    return responseData;
    
    } catch (error) {
    console.error(
      `filegenService (proven): Error calling proven CSV generation API. Request ID: ${requestId}:`,
      error.message
    );

    // MEMORY LEAK FIX: Clean up response data if exists
    if (responseData) {
      cleanupResponse(responseData, requestId);
    } else if (controller) {
      activeRequests.delete(requestId);
    }
    
    // MEMORY LEAK FIX: Create clean error object without accumulating references
    const cleanError = new Error(error.message || 'Batch CSV generation failed');
    
    // Add essential error properties without keeping full error object
    cleanError.status = error.response?.status;
    cleanError.requestId = requestId;
    
    throw cleanError;
  }
};

// Keep original method for backwards compatibility
export const generateBatchCsv = generateBatchCsvProven;

/**
 * MEMORY LEAK FIX: Cancel all active requests (useful for component cleanup)
 */
export const cancelAllRequests = () => {
  console.log(`filegenService: Cancelling ${activeRequests.size} active requests`);
  
  for (const [requestId, requestInfo] of activeRequests.entries()) {
    try {
      if (requestInfo.controller && !requestInfo.controller.signal.aborted) {
        requestInfo.controller.abort();
      }
    } catch (error) {
      console.warn(`filegenService: Error cancelling request ${requestId}:`, error);
    }
  }
  
  activeRequests.clear();
};

/**
 * MEMORY LEAK FIX: Get active request count for debugging
 */
export const getActiveRequestCount = () => activeRequests.size;
