// frontend/src/api/ruleService.js
// Version 1.0: Service for rule/prompt related API calls.
import { BASE_URL, handleResponse } from "./apiClient";

export const getRules = async (filters = {}) => {
  try {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") {
        if (key === "target_models" && Array.isArray(value)) {
          value.forEach((model) => params.append(key, model));
        } else {
          params.append(key, String(value));
        }
      }
    });
    const queryString = params.toString();
    const url = `${BASE_URL}/rules${queryString ? `?${queryString}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("ruleService: Error fetching rules/prompts:", err.message);
    return { error: err.message || "Failed to fetch rules/prompts." };
  }
};

export const createRule = async (ruleData) => {
  try {
    const response = await fetch(`${BASE_URL}/rules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ruleData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("ruleService: Error creating rule/prompt:", err.message);
    throw err;
  }
};

export const updateRule = async (ruleId, updateData) => {
  if (!ruleId) return { error: "Invalid ID for update." };
  try {
    const response = await fetch(`${BASE_URL}/rules/${ruleId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updateData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `ruleService: Error updating rule/prompt ${ruleId}:`,
      err.message,
    );
    throw err;
  }
};

export const deleteRule = async (ruleId) => {
  if (!ruleId) return { error: "Invalid ID for delete." };
  try {
    const response = await fetch(`${BASE_URL}/rules/${ruleId}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `ruleService: Error deleting rule/prompt ${ruleId}:`,
      err.message,
    );
    throw err;
  }
};

export const linkRuleToProject = async (projectId, ruleId) => {
  if (!projectId || !ruleId)
    return { error: "Project ID and Rule ID are required." };
  try {
    const url = `${BASE_URL}/projects/${projectId}/rules/${ruleId}/link`;
    const response = await fetch(url, { method: "POST" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `ruleService: Error linking rule ${ruleId} to project ${projectId}:`,
      err.message,
    );
    throw err;
  }
};

export const unlinkRuleFromProject = async (projectId, ruleId) => {
  if (!projectId || !ruleId)
    return { error: "Project ID and Rule ID are required." };
  try {
    const url = `${BASE_URL}/projects/${projectId}/rules/${ruleId}/unlink`;
    const response = await fetch(url, { method: "DELETE" });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `ruleService: Error unlinking rule ${ruleId} from project ${projectId}:`,
      err.message,
    );
    throw err;
  }
};

export const exportRules = async () => {
  try {
    const response = await fetch(`${BASE_URL}/meta/rules/export`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("ruleService: Error exporting rules:", err.message);
    throw err;
  }
};

export const importRules = async (rulesPayload) => {
  try {
    const response = await fetch(`${BASE_URL}/meta/rules/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rulesPayload),
    });
    const data = await handleResponse(response);
    if (
      typeof data === "object" &&
      data !== null &&
      data.error &&
      response.status >= 400
    )
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("ruleService: Error importing rules:", err.message);
    throw err;
  }
};
