// frontend/src/api/pluginsService.js
/**
 * Plugin management API service
 */

import { BASE_URL as API_BASE_URL, handleResponse } from "./apiClient";

const BASE_URL = `${API_BASE_URL}/plugins`;

/**
 * List all registered plugins
 */
export const listPlugins = async () => {
  const response = await fetch(BASE_URL, { method: "GET" });
  return handleResponse(response);
};

/**
 * Get detailed info about a specific plugin
 */
export const getPlugin = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}`, { method: "GET" });
  return handleResponse(response);
};

/**
 * Get plugin health status
 */
export const getPluginHealth = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/health`, {
    method: "GET",
  });
  return handleResponse(response);
};

/**
 * Start a plugin
 */
export const startPlugin = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/start`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * Stop a plugin
 */
export const stopPlugin = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/stop`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * Restart a plugin
 */
export const restartPlugin = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/restart`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * Enable a plugin
 */
export const enablePlugin = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/enable`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * Disable a plugin
 */
export const disablePlugin = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/disable`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * Get plugin configuration
 */
export const getPluginConfig = async (pluginId) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/config`, {
    method: "GET",
  });
  return handleResponse(response);
};

/**
 * Update plugin configuration
 */
export const updatePluginConfig = async (pluginId, config) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  return handleResponse(response);
};

/**
 * Refresh plugin registry
 */
export const refreshPlugins = async () => {
  const response = await fetch(`${BASE_URL}/refresh`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * Get plugin logs
 */
export const getPluginLogs = async (pluginId, lines = 100) => {
  const response = await fetch(`${BASE_URL}/${pluginId}/logs?lines=${lines}`, {
    method: "GET",
  });
  return handleResponse(response);
};

/**
 * Get live GPU stats (nvidia-smi)
 */
export const getLiveGpuStats = async () => {
  const response = await fetch(`${BASE_URL}/stats/gpu`, { method: "GET" });
  return handleResponse(response);
};

/**
 * Get status of all plugins
 */
export const getAllPluginStatus = async () => {
  const response = await fetch(`${BASE_URL}/status`, { method: "GET" });
  return handleResponse(response);
};

export default {
  listPlugins,
  getPlugin,
  getPluginHealth,
  startPlugin,
  stopPlugin,
  restartPlugin,
  enablePlugin,
  disablePlugin,
  getPluginConfig,
  updatePluginConfig,
  refreshPlugins,
  getPluginLogs,
  getLiveGpuStats,
  getAllPluginStatus,
};
