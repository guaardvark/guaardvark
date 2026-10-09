// frontend/src/api/modelService.js
// Version 1.0: Service for model-related API calls.
import { BASE_URL, handleResponse } from "./apiClient";

export const getAvailableModels = async () => {
  try {
    const response = await fetch(`${BASE_URL}/model/list`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    // Standard envelope: the payload is under data. Older backends put it
    // under message (the arguments were reversed), so accept both.
    const payload = data?.data?.models ? data.data : data?.message?.models ? data.message : null;
    if (data?.success && payload) {
      return Array.isArray(payload.models) ? payload.models : [];
    }
    // Handle old format for backward compatibility
    return Array.isArray(data?.models) ? data.models : [];
  } catch (err) {
    console.error(
      "modelService: Error fetching available models:",
      err.message,
    );
    return { error: err.message || "Failed to fetch available models." };
  }
};

/**
 * The chat model list for Settings, and whether Ollama answered at all.
 * { models, ollamaOffline } or { error }.
 */
export const getChatModelList = async () => {
  try {
    // Fresh, not the backend's 30 s cache, so a stopped Ollama shows at once.
    const response = await fetch(`${BASE_URL}/model/list?refresh=true`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error) throw new Error(data.error);
    const payload = data?.data || data?.message || data || {};
    return {
      models: Array.isArray(payload.models) ? payload.models : [],
      ollamaOffline: Boolean(payload.ollama_offline),
    };
  } catch (err) {
    return { error: err.message || "Failed to fetch available models." };
  }
};

export const getCurrentModel = async () => {
  try {
    const response = await fetch(`${BASE_URL}/model`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    // Standard envelope with an older-backend fallback (see getAvailableModels).
    if (data?.success && data?.message?.model) {
      return data.message.model;
    }
    // Handle data wrapper format (e.g. { data: { active_model: "..." } })
    if (data?.data?.active_model) {
      return data.data.active_model;
    }
    if (data?.data?.model) {
      return data.data.model;
    }
    // Handle direct active_model key (e.g. from active_model.json format)
    if (data?.active_model) {
      return data.active_model;
    }
    // Handle old format for backward compatibility
    return data?.model ?? null;
  } catch (err) {
    console.error("modelService: Error fetching current model:", err.message);
    throw err;
  }
};

export const setModel = async (modelName) => {
  try {
    const response = await fetch(`${BASE_URL}/model/set`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelName }),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("modelService: Error setting model:", err.message);
    throw err;
  }
};

export const getModelStatus = async () => {
  try {
    const response = await fetch(`${BASE_URL}/model/status`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("modelService: Error fetching model status:", err.message);
    throw err;
  }
};
