// frontend/src/utils/modelUtils.js
// Dynamic vision model detection - fetches current models from Ollama API

// Vision model detection patterns (for fallback when API unavailable)
const VISION_MODEL_PATTERNS = [
  "vision", "llava", "gpt-4", "gpt4", "gpt-4o",
  "qwen.*vl", "minicpm-v", "moondream", "bakllava", 
  "llama.*vision", "granite.*vision", "gemma.*vision"
];

// Cache for dynamic model detection (5 minute cache)
let visionModelsCache = {
  models: [],
  lastUpdated: 0,
  cacheTtl: 300000 // 5 minutes in milliseconds
};

/**
 * Get available models from the backend API
 */
async function getAvailableModels() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    const response = await fetch('/api/model/list', { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    const data = await response.json();
    // Handle non-standard envelope: {data: "string", message: {models: [...]}}
    // Also handle: {data: {models: [...]}} or {models: [...]} or [...]
    if (data?.message?.models) return data.message.models;
    const actualData = data?.data || data;
    return Array.isArray(actualData) ? actualData : actualData?.models || [];
  } catch (error) {
    console.warn('Failed to fetch available models:', error);
    return [];
  }
}

/**
 * Check if a model supports vision based on its name patterns
 */
function isVisionCapableByName(modelName) {
  if (!modelName) return false;
  
  const lowerName = modelName.toLowerCase();
  
  // Check against known vision model patterns
  for (const pattern of VISION_MODEL_PATTERNS) {
    const regex = new RegExp(pattern, 'i');
    if (regex.test(lowerName)) {
      return true;
    }
  }
  
  return false;
}

/**
 * Update the cached list of vision models from the API
 */
async function updateVisionModelsCache() {
  const currentTime = Date.now();
  
  // Check if cache is still valid
  if ((currentTime - visionModelsCache.lastUpdated) < visionModelsCache.cacheTtl) {
    return;
  }
  
  try {
    console.debug('Updating vision models cache from API');
    const modelsData = await getAvailableModels();
    
    if (Array.isArray(modelsData)) {
      // Extract model names and detect vision capabilities
      const visionModels = [];
      for (const model of modelsData) {
        if (model && typeof model === 'object') {
          const modelName = model.name || model.full_name || '';
          if (modelName && isVisionCapableByName(modelName)) {
            visionModels.push(modelName);
          }
        }
      }
      
      visionModelsCache.models = visionModels;
      visionModelsCache.lastUpdated = currentTime;
      console.debug(`Updated vision models cache with ${visionModels.length} models:`, visionModels);
    } else {
      console.warn('Failed to get models from API, keeping existing cache');
    }
    
  } catch (error) {
    console.warn('Error updating vision models cache:', error);
  }
}

/**
 * Check if a model is vision capable using dynamic detection
 * @param {string} modelName - The model name to check
 * @returns {boolean} True if the model supports vision
 */
export const isVisionModel = async (modelName) => {
  if (!modelName) return false;
  
  // Update cache if needed
  await updateVisionModelsCache();
  
  // Check if model is in cached vision models list
  if (visionModelsCache.models.length > 0) {
    const lowerName = modelName.toLowerCase();
    for (const visionModel of visionModelsCache.models) {
      if (lowerName === visionModel.toLowerCase() || lowerName.includes(visionModel.toLowerCase())) {
        console.debug(`Model '${modelName}' detected as vision-capable from cache`);
        return true;
      }
    }
  }
  
  // Fallback to pattern-based detection
  const result = isVisionCapableByName(modelName);
  if (result) {
    console.debug(`Model '${modelName}' detected as vision-capable by pattern matching`);
  }
  
  return result;
};

/**
 * Synchronous version that uses cached data only (for immediate UI updates)
 * @param {string} modelName - The model name to check
 * @returns {boolean} True if the model supports vision (based on cache or patterns)
 */
export const isVisionModelSync = (modelName) => {
  if (!modelName) return false;
  
  // Check cached vision models first
  if (visionModelsCache.models.length > 0) {
    const lowerName = modelName.toLowerCase();
    for (const visionModel of visionModelsCache.models) {
      if (lowerName === visionModel.toLowerCase() || lowerName.includes(visionModel.toLowerCase())) {
        return true;
      }
    }
  }
  
  // Fallback to pattern-based detection
  return isVisionCapableByName(modelName);
};

/**
 * Get list of currently available vision models
 * @returns {Promise<string[]>} Array of vision model names
 */
export const getAvailableVisionModels = async () => {
  await updateVisionModelsCache();
  return [...visionModelsCache.models];
};

/**
 * Clear the vision models cache to force refresh
 */
export const clearVisionModelsCache = () => {
  visionModelsCache.models = [];
  visionModelsCache.lastUpdated = 0;
  console.debug('Vision models cache cleared');
};

/**
 * Initialize the cache (call this when the app starts)
 */
export const initializeModelCache = async () => {
  try {
    await updateVisionModelsCache();
    console.log('Model cache initialized successfully');
  } catch (error) {
    console.warn('Failed to initialize model cache:', error);
  }
};

// Legacy export for backward compatibility
export const OLLAMA_VISION_MODELS = []; // Deprecated - use dynamic detection
