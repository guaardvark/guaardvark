// frontend/src/api/projectService.js
// Version 1.0: Service for all project-related API calls.
import { BASE_URL, handleResponse } from "./apiClient";

export const getProjects = async (queryParams = {}) => {
  try {
    const params = new URLSearchParams(queryParams);
    const queryString = params.toString();
    const url = `${BASE_URL}/projects${queryString ? `?${queryString}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("projectService: Error getting projects:", err.message);
    return { error: err.message || "Failed to get projects." };
  }
};

export const getProjectsForClient = async (clientId) => {
  if (!clientId || String(clientId).trim() === "") {
    console.error(
      "projectService: getProjectsForClient requires a valid clientId",
    );
    return [];
  }
  try {
    const url = `${BASE_URL}/clients/${encodeURIComponent(clientId)}/projects`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error(
      `projectService: Error getting projects for client ${clientId}:`,
      err.message,
    );
    return [];
  }
};

export const getProject = async (projectId) => {
  if (!projectId) return { error: "Invalid Project ID provided." };
  try {
    const response = await fetch(`${BASE_URL}/projects/${projectId}`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `projectService: Error getting project ${projectId}:`,
      err.message,
    );
    return { error: err.message || `Failed to get project ${projectId}.` };
  }
};

export const createProject = async (projectData) => {
  try {
    const response = await fetch(`${BASE_URL}/projects/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(projectData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("projectService: Error creating project:", err.message);
    throw err;
  }
};

export const updateProject = async (projectId, projectData) => {
  if (!projectId) return { error: "Invalid Project ID provided for update." };
  try {
    const response = await fetch(`${BASE_URL}/projects/${projectId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(projectData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `projectService: Error updating project ${projectId}:`,
      err.message,
    );
    throw err;
  }
};

export const deleteProject = async (projectId) => {
  if (!projectId) return { error: "Invalid Project ID provided for delete." };
  try {
    const response = await fetch(`${BASE_URL}/projects/${projectId}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `projectService: Error deleting project ${projectId}:`,
      err.message,
    );
    throw err;
  }
};
