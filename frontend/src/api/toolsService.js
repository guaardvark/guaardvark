// frontend/src/api/toolsService.js
// Tools API Service - Interface to the agent tool system
// Version 1.0
/* eslint-env browser */

import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Get all registered tools with their schemas
 * @returns {Promise<{success: boolean, tools: Array, count: number}>}
 */
export const getTools = async () => {
  const response = await fetch(`${BASE_URL}/tools`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

/**
 * Get detailed schema for a specific tool
 * @param {string} toolName - Name of the tool
 * @returns {Promise<{success: boolean, tool: Object}>}
 */
export const getToolSchema = async (toolName) => {
  const response = await fetch(`${BASE_URL}/tools/${toolName}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

/**
 * Execute a tool with given parameters
 * @param {string} toolName - Name of the tool to execute
 * @param {Object} parameters - Tool parameters
 * @returns {Promise<{success: boolean, result: Object}>}
 */
export const executeTool = async (toolName, parameters = {}) => {
  const response = await fetch(`${BASE_URL}/tools/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tool_name: toolName,
      parameters,
    }),
  });
  return handleResponse(response);
};

/**
 * Get all tool schemas in a specific format
 * @param {string} format - 'xml' or 'json'
 * @returns {Promise<{success: boolean, format: string, schemas: string, tool_count: number}>}
 */
export const getToolSchemas = async (format = "xml") => {
  const response = await fetch(`${BASE_URL}/tools/schemas?format=${format}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

/**
 * Get tools organized by category
 * @returns {Promise<{success: boolean, categories: Object}>}
 */
export const getToolCategories = async () => {
  const response = await fetch(`${BASE_URL}/tools/categories`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

/**
 * Route a message to determine appropriate tool/handling
 * @param {string} message - User message to route
 * @param {Object} context - Optional context (client, project_id, etc.)
 * @returns {Promise<{success: boolean, route: Object}>}
 */
export const routeMessage = async (message, context = {}) => {
  const response = await fetch(`${BASE_URL}/tools/route`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, context }),
  });
  return handleResponse(response);
};

/**
 * Route a message and execute the appropriate action
 * @param {string} message - User message
 * @param {Object} context - Optional context
 * @returns {Promise<{success: boolean, result: Object}>}
 */
export const routeAndExecute = async (message, context = {}) => {
  const response = await fetch(`${BASE_URL}/tools/route-and-execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, context }),
  });
  return handleResponse(response);
};

export default {
  getTools,
  getToolSchema,
  executeTool,
  getToolSchemas,
  getToolCategories,
  routeMessage,
  routeAndExecute,
};
