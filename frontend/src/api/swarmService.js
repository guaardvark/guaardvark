// frontend/src/api/swarmService.js
// API service for the Swarm Orchestrator plugin

import { BASE_URL as API_BASE_URL, SOCKET_URL, handleResponse } from "./apiClient";
import { io } from "socket.io-client";

const BASE_URL = `${API_BASE_URL}/swarm`;

/**
 * Check if the swarm service is healthy and online
 */
export const getHealth = async () => {
  const response = await fetch(`${BASE_URL}/health`, { method: "GET" });
  return handleResponse(response);
};

/**
 * Launch a swarm from a plan file
 */
export const launchSwarm = async ({
  planPath,
  repoPath,
  flightMode,
  maxAgents,
  autoMerge,
  dryRun = false,
}) => {
  const response = await fetch(`${BASE_URL}/launch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plan_path: planPath,
      repo_path: repoPath || undefined,
      flight_mode: flightMode,
      max_agents: maxAgents,
      auto_merge: autoMerge,
      dry_run: dryRun,
    }),
  });
  return handleResponse(response);
};

/**
 * Get status of all active swarms — dashboard polls this
 */
export const getAllStatus = async () => {
  const response = await fetch(`${BASE_URL}/status`, { method: "GET" });
  return handleResponse(response);
};

/**
 * Get detailed status for a specific swarm
 */
export const getSwarmStatus = async (swarmId) => {
  const response = await fetch(`${BASE_URL}/status/${swarmId}`, {
    method: "GET",
  });
  return handleResponse(response);
};

/**
 * Get logs for a specific agent task
 */
export const getTaskLogs = async (swarmId, taskId, lines = 100) => {
  const response = await fetch(
    `${BASE_URL}/${swarmId}/logs/${taskId}?lines=${lines}`,
    { method: "GET" }
  );
  return handleResponse(response);
};

/**
 * Cancel a running swarm
 */
export const cancelSwarm = async (swarmId) => {
  const response = await fetch(`${BASE_URL}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ swarm_id: swarmId }),
  });
  return handleResponse(response);
};

/**
 * Trigger merge for a completed swarm
 */
export const mergeSwarm = async (swarmId) => {
  const response = await fetch(`${BASE_URL}/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ swarm_id: swarmId }),
  });
  return handleResponse(response);
};

/**
 * Clean up worktrees and branches
 */
export const cleanupSwarm = async (swarmId, { deleteBranches = false, all = false } = {}) => {
  const response = await fetch(`${BASE_URL}/cleanup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      swarm_id: all ? undefined : swarmId,
      delete_branches: deleteBranches,
      all,
    }),
  });
  return handleResponse(response);
};

/**
 * List available swarm templates
 */
export const getTemplates = async () => {
  const response = await fetch(`${BASE_URL}/templates`, { method: "GET" });
  return handleResponse(response);
};

/**
 * Get raw content of a specific template
 */
export const getTemplateContent = async (filename) => {
  const response = await fetch(`${BASE_URL}/templates/${filename}`, {
    method: "GET",
  });
  return handleResponse(response);
};

/**
 * Save a new or existing plan template
 */
export const saveTemplate = async (filename, content) => {
  const response = await fetch(`${BASE_URL}/templates/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, content }),
  });
  return handleResponse(response);
};

/**
 * Check internet connectivity and available backends
 */
export const getConnectivity = async () => {
  const response = await fetch(`${BASE_URL}/connectivity`, { method: "GET" });
  return handleResponse(response);
};

/**
 * Get swarm run history
 */
export const getHistory = async (limit = 20) => {
  const response = await fetch(`${BASE_URL}/history?limit=${limit}`, {
    method: "GET",
  });
  return handleResponse(response);
};

/**
 * Real-time Swarm Service via Socket.IO
 */
class SwarmService {
  constructor() {
    this.socket = null;
    this._listeners = [];
  }

  connect() {
    if (this.socket?.connected) return this.socket;

    this.socket = io(SOCKET_URL, {
      path: "/socket.io",
      transports: ["websocket", "polling"],
      reconnectionAttempts: 5,
    });

    this.socket.on("connect", () => {
      console.log("SwarmService connected to Socket.IO");
      this.socket.emit("subscribe_swarm");
    });

    this.socket.on("connect_error", (err) => {
      console.error("SwarmService connection error:", err);
    });

    return this.socket;
  }

  onEvent(callback) {
    if (!this.socket) this.connect();
    
    // Cleanup existing listener for swarm:event if any
    this.socket.off("swarm:event");
    
    this.socket.on("swarm:event", (event) => {
      console.log("Swarm event received:", event.event_type, event.task_id);
      callback(event);
    });
  }

  disconnect() {
    if (this.socket) {
      this.socket.off("swarm:event");
      this.socket.disconnect();
      this.socket = null;
    }
  }
}

export const swarmService = new SwarmService();

export default {
  getHealth,
  launchSwarm,
  getAllStatus,
  getSwarmStatus,
  getTaskLogs,
  cancelSwarm,
  mergeSwarm,
  cleanupSwarm,
  getTemplates,
  getTemplateContent,
  getConnectivity,
  getHistory,
  swarmService,
};
