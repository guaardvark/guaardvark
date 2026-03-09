// frontend/src/api/taskService.js
// Version 1.1: Added default task model management functions.
import { BASE_URL, handleResponse } from "./apiClient";

export const getTasks = async (
  projectId = null,
  statusFilter = null,
  typeFilter = null,
) => {
  try {
    const params = new URLSearchParams();
    if (projectId !== null && projectId !== undefined && projectId !== "") {
      params.append("project_id", projectId.toString());
    }
    if (statusFilter) {
      params.append("status", statusFilter);
    }
    if (typeFilter) {
      params.append("type", typeFilter);
    }
    const queryString = params.toString();
    const url = `${BASE_URL}/tasks${queryString ? `?${queryString}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("taskService: Error getting tasks:", err.message);
    return { error: err.message || "Failed to get tasks." };
  }
};

export const createTask = async (taskData) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(taskData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("taskService: Error creating task:", err.message);
    throw err;
  }
};

export const updateTask = async (taskId, taskData) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(taskData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("taskService: Error updating task:", err.message);
    throw err;
  }
};

export const deleteTask = async (taskId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("taskService: Error deleting task:", err.message);
    throw err;
  }
};

export const processTaskQueue = async () => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/process-queue`, {
      method: "POST",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("taskService: Error processing task queue:", err.message);
    throw err;
  }
};

export const getDefaultTaskModel = async () => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/default-model`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data.default_model;
  } catch (err) {
    console.error("taskService: Error getting default task model:", err.message);
    return null;
  }
};

export const setDefaultTaskModel = async (modelName) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/default-model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelName }),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("taskService: Error setting default task model:", err.message);
    throw err;
  }
};

export const reprocessTask = async (taskId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}/reprocess`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(`taskService: Error reprocessing task ${taskId}:`, err.message);
    throw err;
  }
};

export const duplicateTask = async (taskId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}/duplicate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(`taskService: Error duplicating task ${taskId}:`, err.message);
    throw err;
  }
};

export const startTask = async (taskId) => {
  try {
    const response = await fetch(`${BASE_URL}/tasks/${taskId}/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(`taskService: Error starting task ${taskId}:`, err.message);
    throw err;
  }
};
