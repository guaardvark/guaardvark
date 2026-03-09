// frontend/src/api/agentsService.js
// Agents API Service - Interface to the agent configuration system
// Version 1.0
/* eslint-env browser */

import { BASE_URL, handleResponse } from "./apiClient";

export const getAgents = async () => {
  const response = await fetch(`${BASE_URL}/agents`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

export const getAgent = async (agentId) => {
  const response = await fetch(`${BASE_URL}/agents/${agentId}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

export const updateAgent = async (agentId, updates) => {
  const response = await fetch(`${BASE_URL}/agents/${agentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates || {}),
  });
  return handleResponse(response);
};

export const toggleAgent = async (agentId) => {
  const response = await fetch(`${BASE_URL}/agents/${agentId}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

export const matchAgent = async (message) => {
  const response = await fetch(`${BASE_URL}/agents/match`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return handleResponse(response);
};

export const executeAgent = async ({ agent_id, message, context } = {}) => {
  const response = await fetch(`${BASE_URL}/agents/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent_id, message, context: context || {} }),
  });
  return handleResponse(response);
};

export const getAgentSettings = async () => {
  const response = await fetch(`${BASE_URL}/agents/settings`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(response);
};

export const updateAgentSettings = async (updates) => {
  const response = await fetch(`${BASE_URL}/agents/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates || {}),
  });
  return handleResponse(response);
};

export default {
  getAgents,
  getAgent,
  updateAgent,
  toggleAgent,
  matchAgent,
  executeAgent,
  getAgentSettings,
  updateAgentSettings,
};
