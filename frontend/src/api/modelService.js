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

// --- Cloud Models (LLM provider: local Ollama vs hosted Mistral) ---
// Backed by /api/llm/* — GET /llm/provider is the single source of truth.
// Some endpoints wrap their payload in a `data` envelope; unwrap defensively.
const unwrapProvider = (data) =>
  data?.data !== undefined ? data.data : data;

// GET /api/llm/provider -> single source of truth for the Cloud Models UI.
// { cloud_models_enabled, provider, cloud_active, mistral_model, providers: [...] }
export const getLlmProvider = async () => {
  const response = await fetch(`${BASE_URL}/llm/provider`);
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  return unwrapProvider(data);
};

// POST /api/llm/cloud-enabled -> master switch. Returns new provider_state.
export const setCloudModelsEnabled = async (enabled) => {
  const response = await fetch(`${BASE_URL}/llm/cloud-enabled`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  return unwrapProvider(data);
};

// POST /api/llm/provider -> set active chat provider. Returns new provider_state.
// 400 if cloud disabled or the provider's key is missing.
export const setLlmProvider = async (provider) => {
  const response = await fetch(`${BASE_URL}/llm/provider`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  return unwrapProvider(data);
};

// GET /api/llm/provider/models?provider=mistral -> [{ name, id }]
export const getProviderModels = async (provider = "mistral") => {
  const response = await fetch(
    `${BASE_URL}/llm/provider/models?provider=${encodeURIComponent(provider)}`,
  );
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  const unwrapped = unwrapProvider(data);
  return Array.isArray(unwrapped?.models) ? unwrapped.models : [];
};

// POST /api/llm/provider/mistral-model -> set active Mistral model.
export const setMistralModel = async (model) => {
  const response = await fetch(`${BASE_URL}/llm/provider/mistral-model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  return unwrapProvider(data);
};

// POST /api/llm/provider/openai-model -> set active OpenAI-compatible model.
export const setOpenaiModel = async (model) => {
  const response = await fetch(`${BASE_URL}/llm/provider/openai-model`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  return unwrapProvider(data);
};

// POST /api/llm/provider/test -> live round-trip.
// { connected, response, model } or an error.
export const testProviderConnection = async () => {
  const response = await fetch(`${BASE_URL}/llm/provider/test`, {
    method: "POST",
  });
  const data = await handleResponse(response);
  if (data?.error) throw new Error(data?.error?.message || data.error);
  return unwrapProvider(data);
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
