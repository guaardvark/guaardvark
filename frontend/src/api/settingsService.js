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
      localStorage.removeItem("llamax_chat_session_id");

      // Clear per-project session keys
      const lsKeysToRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith("llamax_chat_session_id_")) {
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
    const response = await fetch(`${BASE_URL}/meta/test-llm`);
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

export const getRagDebug = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/rag_debug`);
    return await handleResponse(response);
  } catch (err) {
    console.error(
      "settingsService: Error getting RAG debug setting:",
      err.message,
    );
    return { error: err.message };
  }
};

export const setRagDebug = async (enabled) => {
  try {
    const response = await fetch(`${BASE_URL}/settings/rag_debug`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rag_debug_enabled: !!enabled }),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("settingsService: Error setting RAG debug:", err.message);
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
