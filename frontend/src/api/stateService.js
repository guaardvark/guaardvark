// frontend/src/api/stateService.js
// Version 1.0: Service for dashboard layout and system metrics.
import { BASE_URL, handleResponse } from "./apiClient";

export const getDashboardLayout = async () => {
  try {
    const response = await fetch(`${BASE_URL}/state/layout`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      "stateService: Error fetching dashboard layout:",
      err.message,
    );
    throw err;
  }
};

export const saveDashboardLayout = async (layout) => {
  try {
    const response = await fetch(`${BASE_URL}/state/layout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ layout: layout }),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("stateService: Error saving dashboard layout:", err.message);
    throw err;
  }
};

export const getSystemMetrics = async () => {
  try {
    const response = await fetch(`${BASE_URL}/meta/metrics`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("stateService: Error fetching system metrics:", err.message);
    return { error: err.message };
  }
};
