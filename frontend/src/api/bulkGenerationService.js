// frontend/src/api/bulkGenerationService.js
// Bulk CSV and XML Generation Service for Chat Integration

import { BASE_URL } from './apiClient';
import { API_TIMEOUT_GENERATION, API_TIMEOUT_BULK_XML } from '../config/constants';
const API_URL = BASE_URL;

/**
 * Generate bulk CSV content using natural language
 * @param {string} naturalLanguage - Natural language description of what to generate
 * @param {string} outputFilename - Output filename
 * @param {object} contextVariables - Optional context variables
 * @returns {Promise<object>} - Generation response
 */
export const generateBulkCSV = async (naturalLanguage, outputFilename, contextVariables = {}) => {
  try {
    const payload = {
      output_filename: outputFilename,
      natural_language: naturalLanguage,
      context_variables: contextVariables
    };

    console.log("bulkGenerationService: Sending bulk CSV generation request:", payload);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_GENERATION);

    const response = await fetch(`${API_URL}/bulk-generate/csv`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    console.log("bulkGenerationService: Bulk CSV generation response:", data);
    return data;

  } catch (error) {
    if (error.name === 'AbortError') {
      console.error("bulkGenerationService: Request timeout after 5 minutes");
      throw new Error('Request timeout - bulk CSV generation took too long');
    }
    console.error("bulkGenerationService: Error calling bulk CSV generation API:", error.message);
    throw new Error(error.message || 'Bulk CSV generation failed');
  }
};

/**
 * Generate structured CSV content for specific industries (e.g., books, products)
 * @param {Object} params - Structured generation parameters
 * @param {string} params.output_filename - Output filename
 * @param {string} params.client - Client name
 * @param {string} params.project - Project name  
 * @param {string} params.website - Website URL
 * @param {Array<string>} params.topics - List of topics/products to generate
 * @param {number} params.num_items - Number of items to generate
 * @param {number} params.target_word_count - Target word count per item
 * @param {string} params.model_name - LLM model to use
 * @returns {Promise<object>} - Generation response
 */
export const generateStructuredCSV = async (params) => {
  try {
    const {
      output_filename,
      client,
      project = "Content Generation",
      website,
      competitor_url,
      client_notes,
      topics = [],
      num_items = 10,
      target_word_count = 500,
      concurrent_workers = 5,
      batch_size = 25,
      model_name,
      existing_task_id,
      prompt_rule_id, // Fixed: Added missing prompt_rule_id parameter
      // NEW: Enhanced context parameters
      context_mode = 'basic', // 'basic' | 'enhanced' | 'auto'
      use_entity_context = false,
      use_competitor_analysis = false,
      use_document_intelligence = false,
      client_id // NEW: Required for enhanced context
    } = params;

    const payload = {
      output_filename,
      client,
      project,
      website,
      topics,
      num_items,
      target_word_count,
      concurrent_workers,
      batch_size,
      // NEW: Enhanced context options
      context_mode,
      use_entity_context,
      use_competitor_analysis,
      use_document_intelligence
    };

    // BUG FIX #7: Only include optional fields if they have values
    if (competitor_url) payload.competitor_url = competitor_url;
    if (client_notes) payload.client_notes = client_notes;
    if (prompt_rule_id) payload.prompt_rule_id = prompt_rule_id;
    if (client_id) payload.client_id = client_id;
    if (model_name) payload.model_name = model_name;
    if (existing_task_id) payload.existing_task_id = existing_task_id;

    console.log("bulkGenerationService: Sending structured CSV generation request:", payload);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_GENERATION);

    const response = await fetch(`${API_URL}/bulk-generate/csv`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    console.log("bulkGenerationService: Structured CSV generation response:", data);
    return data;

  } catch (error) {
    if (error.name === 'AbortError') {
      console.error("bulkGenerationService: Request timeout after 5 minutes");
      throw new Error('Request timeout - structured CSV generation took too long');
    }
    console.error("bulkGenerationService: Error calling structured CSV generation API:", error.message);
    throw new Error(error.message || 'Structured CSV generation failed');
  }
};

/**
 * Get bulk generation status
 * @param {string} jobId - Job ID to check status for
 * @returns {Promise<object>} - Status response
 */
export const getBulkGenerationStatus = async (jobId) => {
  try {
    const response = await fetch(`${API_URL}/bulk-generate/status/${jobId}`);
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("bulkGenerationService: Error getting bulk generation status:", error.message);
    throw new Error(error.message || 'Failed to get generation status');
  }
};

/**
 * Estimate generation time for bulk CSV
 * @param {object} generationParams - Generation parameters
 * @returns {Promise<object>} - Estimation response
 */
export const estimateBulkGenerationTime = async (generationParams) => {
  try {
    const response = await fetch(`${API_URL}/bulk-generate/estimate`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(generationParams)
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("bulkGenerationService: Error estimating generation time:", error.message);
    throw new Error(error.message || 'Failed to estimate generation time');
  }
};

/**
 * Generate topics for bulk CSV
 * @param {object} topicParams - Topic generation parameters
 * @returns {Promise<object>} - Topics response
 */
export const generateTopics = async (topicParams) => {
  try {
    const response = await fetch(`${API_URL}/bulk-generate/topics/generate`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(topicParams)
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    return data;
  } catch (error) {
    console.error("bulkGenerationService: Error generating topics:", error.message);
    throw new Error(error.message || 'Failed to generate topics');
  }
};

/**
 * Generate bulk XML content for WordPress imports
 * @param {object} params - Generation parameters
 * @param {string} params.output_filename - Output XML filename
 * @param {string} params.client - Client name
 * @param {string} params.project - Project name
 * @param {string} params.website - Website URL
 * @param {string} params.client_notes - Client business description
 * @param {Array<string>} params.topics - List of topics/items to generate
 * @param {number} params.num_items - Number of items to generate
 * @param {number} params.target_word_count - Target word count per item
 * @param {number} params.concurrent_workers - Number of concurrent workers
 * @param {number} params.batch_size - Batch size for processing
 * @param {string} params.model_name - AI model to use
 * @param {number} params.prompt_rule_id - Prompt rule ID
 * @param {string} params.insert_content - Content to insert into each page
 * @param {string} params.insert_position - Position to insert content ('top', 'bottom', 'none')
 * @param {string} params.context_mode - Context mode: 'basic', 'enhanced', or 'auto' (NEW)
 * @param {boolean} params.use_entity_context - Use entity relationship context (NEW)
 * @param {boolean} params.use_competitor_analysis - Use competitor analysis (NEW)
 * @param {boolean} params.use_document_intelligence - Use document intelligence (NEW)
 * @param {number} params.client_id - Client ID for enhanced context (NEW)
 * @returns {Promise<object>} - Generation response with job_id and statistics
 */
export const generateBulkXML = async (params) => {
  try {
    console.log("bulkGenerationService: Sending bulk XML generation request:", params);

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_BULK_XML);

    const response = await fetch(`${API_URL}/bulk-generate/xml`, {
      method: 'POST',
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(params),
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.details || errorData.error || `HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    console.log("bulkGenerationService: Bulk XML generation response:", data);
    return data;

  } catch (error) {
    if (error.name === 'AbortError') {
      console.error("bulkGenerationService: XML request timeout after 10 minutes");
      throw new Error('Request timeout - bulk XML generation took too long');
    }
    console.error("bulkGenerationService: Error generating bulk XML:", error.message);
    throw new Error(error.message || 'Failed to generate XML content');
  }
}; 