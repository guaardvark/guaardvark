import { BASE_URL, handleResponse } from "./apiClient";
import { API_TIMEOUT_GENERATION } from "../config/constants";

export const getVersion = async () => {
  try {
    const response = await fetch(`${BASE_URL}/system/version`);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error fetching version:", err.message);
    return { error: err.message };
  }
};

export const clearPycache = async () => {
  const endpoint = `${BASE_URL}/meta/clear-pycache`;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      "settingsService: Error clearing pycache folders:",
      err.message,
    );
    throw err;
  }
};

export const getProfile = async () => {
  const response = await fetch(`${BASE_URL}/settings/profile`);
  return await handleResponse(response);
};

export const setProfile = async (name) => {
  const response = await fetch(`${BASE_URL}/settings/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const data = await handleResponse(response);
  if (typeof data === "object" && data !== null && data.error) throw new Error(data.error);
  return data;
};

/**
 * Tools chat runs without asking: no approval card, no file card.
 * @returns {Promise<string[]>}
 */
export const getAlwaysApprovedTools = async () => {
  const response = await fetch(`${BASE_URL}/settings/chat_tools_always_approved`);
  const data = await handleResponse(response, { quiet: true });
  return data?.data?.tools || [];
};

/**
 * @param {{add?: string[], remove?: string[]}} change
 * @returns {Promise<string[]>} the stored list after the change
 */
export const updateAlwaysApprovedTools = async (change) => {
  const response = await fetch(`${BASE_URL}/settings/chat_tools_always_approved`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(change),
  });
  const data = await handleResponse(response);
  return data?.data?.tools || [];
};

/**
 * How the start/stop scripts treat Ollama, as recorded in .env.
 * @returns {Promise<{keep_running: boolean, external: boolean, env_writable: boolean}>}
 */
export const getOllamaLifecycle = async () => {
  const response = await fetch(`${BASE_URL}/settings/ollama_lifecycle`);
  return await handleResponse(response);
};

/**
 * Persist the Ollama policy. Takes effect on the next stop/start; no restart needed.
 * @param {{keep_running?: boolean, external?: boolean}} body
 */
export const setOllamaLifecycle = async (body) => {
  const response = await fetch(`${BASE_URL}/settings/ollama_lifecycle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await handleResponse(response);
  if (typeof data === "object" && data !== null && data.error) throw new Error(data.error);
  return data;
};

export const getGenerationHistoryCounts = async () => {
  const endpoint = `${BASE_URL}/meta/generation-history`;
  try {
    const response = await fetch(endpoint);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error fetching generation history counts:", err.message);
    return null;
  }
};

export const deleteGenerationHistory = async () => {
  const endpoint = `${BASE_URL}/meta/generation-history`;
  try {
    const response = await fetch(endpoint, { method: "DELETE" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error deleting generation history:", err.message);
    throw err;
  }
};

export const getChatHistoryCounts = async () => {
  const endpoint = `${BASE_URL}/enhanced-chat/history/all`;
  try {
    const response = await fetch(endpoint);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error fetching chat history counts:", err.message);
    return null;
  }
};

export const clearChatHistory = async (sessionId = "all") => {
  const endpoint = `${BASE_URL}/enhanced-chat/history/all`;
  try {
    const response = await fetch(endpoint, { method: "DELETE" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    
    try {
      localStorage.removeItem("guaardvark_chat_session_id");

      // Clear per-project session keys
      const lsKeysToRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith("guaardvark_chat_session_id_")) {
          lsKeysToRemove.push(key);
        }
      }
      lsKeysToRemove.forEach(key => localStorage.removeItem(key));

      const keysToRemove = [];
      for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i);
        if (key && key.startsWith("session_logged_")) {
          keysToRemove.push(key);
        }
      }
      keysToRemove.forEach(key => sessionStorage.removeItem(key));
      
      console.log("DEBUG: Cleared frontend session storage after chat history clear");
      console.log("DEBUG: Removed session logging flags:", keysToRemove.length);
      
      window.dispatchEvent(new CustomEvent('chatHistoryCleared', {
        detail: { sessionId }
      }));
      
    } catch (storageError) {
      console.warn("Failed to clear session storage:", storageError);
    }
    
    return data;
  } catch (err) {
    console.error(
      `settingsService: Error clearing chat history for ${sessionId}:`,
      err.message,
    );
    throw err;
  }
};

export const resetIndexStorage = async () => {
  const endpoint = `${BASE_URL}/meta/reset-index`;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      "settingsService: Error resetting index storage:",
      err.message,
    );
    throw err;
  }
};

export const purgeIndex = async (options = {}) => {
  const endpoint = `${BASE_URL}/meta/purge-index`;
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error purging index:", err.message);
    throw err;
  }
};

/** The search reranker: enabled, installed, size, and any install in progress. */
export const getRerankerStatus = async () => {
  const response = await fetch(`${BASE_URL}/meta/reranker`);
  return await handleResponse(response, { quiet: true });
};

/** Start downloading the reranker's weights (the only path that fetches them). */
export const installReranker = async () => {
  const response = await fetch(`${BASE_URL}/meta/reranker/install`, { method: "POST" });
  return await handleResponse(response);
};

export const optimizeIndex = async () => {
  const endpoint = `${BASE_URL}/meta/optimize-index`;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error optimizing index:", err.message);
    throw err;
  }
};

export const pauseIndexing = async () => {
  const endpoint = `${BASE_URL}/index/pause`;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error pausing indexing:", err.message);
    throw err;
  }
};

export const resumeIndexing = async () => {
  const endpoint = `${BASE_URL}/index/resume`;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error resuming indexing:", err.message);
    throw err;
  }
};

export const getIndexingPaused = async () => {
  const endpoint = `${BASE_URL}/index/paused`;
  try {
    const response = await fetch(endpoint);
    const data = await handleResponse(response);
    return data?.paused === true;
  } catch (err) {
    console.error("settingsService: Error checking indexing paused state:", err.message);
    return false;
  }
};

export const resumePendingIndexing = async () => {
  const endpoint = `${BASE_URL}/index/resume-pending`;
  try {
    const response = await fetch(endpoint, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error resuming pending indexing:", err.message);
    throw err;
  }
};

export const buildCorpusSummaries = async (options = {}) => {
  // RAPTOR: minutes of sustained GPU work, so the backend queues it and returns a
  // task id rather than holding the request open.
  const endpoint = `${BASE_URL}/index/build-corpus-summaries`;
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error queuing corpus summary build:", err.message);
    throw err;
  }
};

export const runSelfTest = async (options = {}) => {
  const endpoint = `${BASE_URL}/meta/selftest`;
  try {
    const requestBody = {
      mode: options.mode || "basic",
      include_legacy: options.include_legacy !== undefined ? options.include_legacy : true,
      ...options
    };
    
    const response = await fetch(endpoint, { 
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(requestBody)
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error running self-test:", err.message);
    throw err;
  }
};

export const testLLM = async () => {
  try {
    const response = await fetch(`${BASE_URL}/meta/test-llm`, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("settingsService: Error testing LLM:", err.message);
    throw err;
  }
};

export const runAllTests = async () => {
  const endpoint = `${BASE_URL}/meta/run-tests`;
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_GENERATION);

    const response = await fetch(endpoint, {
      method: "POST",
      signal: controller.signal
    });

    clearTimeout(timeoutId);

    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('Test suite timed out after 5 minutes');
    }
    console.error("settingsService: Error running test suite:", err.message);
    throw err;
  }
};

export const getWebAccess = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/web_access`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting web access setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setWebAccess = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/web_access`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow_web_search: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting web access:", err.message);
    return { error: err.message };
  }
};

export const getAdvancedDebug = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/advanced_debug`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting advanced debug setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setAdvancedDebug = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/advanced_debug`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ advanced_debug: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error setting advanced debug:",
      err.message,
    );
    return { error: err.message };
  }
};

export const getVerbatimPrompts = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/verbatim_prompts`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting verbatim prompts setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const getImageKeepLoaded = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/image_keep_loaded`);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error getting image keep-loaded setting:", err.message);
    return { error: err.message };
  }
};

export const setImageKeepLoaded = async (minutes) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/image_keep_loaded`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ minutes: Number(minutes) || 0 }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting image keep-loaded:", err.message);
    return { error: err.message };
  }
};

export const setVerbatimPrompts = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/verbatim_prompts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error setting verbatim prompts:",
      err.message,
    );
    return { error: err.message };
  }
};

export const getChatImageModel = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/chat_image_model`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting chat image model:",
      err.message,
    );
    return { error: err.message };
  }
};

/** Stills / cast-train / max-quality registry (Ollama-picker style for media). */
export const getMediaModels = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/media_models`);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error getting media models:", err.message);
    return { error: err.message };
  }
};

/**
 * @param {{ stills_model?: string, cast_train_base?: string, max_quality_model?: string }} patch
 */
export const setMediaModels = async (patch) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/media_models`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch || {}),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting media models:", err.message);
    return { error: err.message };
  }
};

export const setChatImageModel = async (model) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/chat_image_model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: model || "auto" }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error setting chat image model:",
      err.message,
    );
    return { error: err.message };
  }
};

export const getBehaviorLearning = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/behavior_learning`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting behavior learning setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setBehaviorLearning = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/behavior_learning`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ behavior_learning_enabled: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error setting behavior learning:",
      err.message,
    );
    return { error: err.message };
  }
};

export const getChatThinkingDefault = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/chat_thinking_default`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting chat thinking default:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setChatThinkingDefault = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/chat_thinking_default`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_thinking_default: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error setting chat thinking default:",
      err.message,
    );
    return { error: err.message };
  }
};

export const getRulesEnabled = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/rules_enabled`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting rules_enabled setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setRulesEnabled = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/rules_enabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules_enabled: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error setting rules_enabled:",
      err.message,
    );
    return { error: err.message };
  }
};

export const purgeBehaviorLearning = async () => {
  try {
    const response = await fetch(`${BASE_URL}/rules/learned`, {
      method: "DELETE",
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error purging learned rules:", err.message);
    throw err;
  }
};

export const getBranding = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/branding`);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error getting branding:", err.message);
    return { error: err.message };
  }
};

export const updateBranding = async (formData) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/branding`, {
      method: "POST",
      body: formData,
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error updating branding:", err.message);
    return { error: err.message };
  }
};

export const triggerReboot = async () => {
  try {
    const response = await fetch(`${BASE_URL}/reboot`, { method: "POST" });
    if (response.status === 202) {
      return { message: "Reboot initiated." };
    }
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    // Refused (this device needs the API key): nothing restarted, and the
    // caller shows the advice handleResponse put in the message.
    if (err.authRefused) throw err;
    console.warn(
      "settingsService: Error triggering reboot (might be expected if server restarted):",
      err.message,
    );
    return {
      warning: "Reboot initiated, connection may have been lost as expected.",
      error: err.message,
    };
  }
};

export const getConfineToolPaths = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/confine_tool_paths`);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error getting tool path limit:", err.message);
    return { error: err.message };
  }
};

export const setConfineToolPaths = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/confine_tool_paths`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confine_tool_paths: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting tool path limit:", err.message);
    return { error: err.message };
  }
};

export const getLlmDebug = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/llm_debug`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting LLM debug setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setLlmDebug = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/llm_debug`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_debug: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting LLM debug:", err.message);
    return { error: err.message };
  }
};

export const getRagFeatures = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/rag-features`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting RAG features:",
      err.message,
    );
    return { error: err.message };
  }
};

export const updateRagFeatures = async (features) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/rag-features`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(features),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error updating RAG features:", err.message);
    return { error: err.message };
  }
};

export const clearBehaviorLog = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/clear_behavior_log`, {
      method: "POST",
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error clearing behavior log:", err.message);
    return { error: err.message };
  }
};

export const getMusicDirectory = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/music_directory`);
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error getting music directory:", err.message);
    return { error: err.message };
  }
};

export const setMusicDirectory = async (path) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/music_directory`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ music_directory: path }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting music directory:", err.message);
    return { error: err.message };
  }
};

/** Keep ready: { enabled, state, detail, model, env_writable }. */
export const getChatKeepReady = async () => {
  const response = await fetch(`${BASE_URL}/settings/chat_keep_ready`);
  return await handleResponse(response);
};

/** Turn Keep ready on or off for this machine; applies at once. */
export const setChatKeepReady = async (enabled) => {
  const response = await fetch(`${BASE_URL}/settings/chat_keep_ready`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const data = await handleResponse(response);
  if (typeof data === "object" && data !== null && data.error) throw new Error(data.error);
  return data;
};
