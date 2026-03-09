// frontend/src/api/websiteService.js
// Version 1.0: Service for website-related API calls.
import { BASE_URL, handleResponse } from "./apiClient";

export const getWebsites = async (queryParams = {}) => {
  try {
    const params = new URLSearchParams(queryParams);
    const queryString = params.toString();
    const url = `${BASE_URL}/websites${queryString ? `?${queryString}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("websiteService: Error getting websites:", err.message);
    return { error: err.message || "Failed to get websites." };
  }
};

export const createWebsite = async (websiteData) => {
  if (!websiteData || !websiteData.url || !websiteData.project_id) {
    return { error: "URL and Project ID are required for creating a website." };
  }
  try {
    const response = await fetch(`${BASE_URL}/websites/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(websiteData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("websiteService: Error creating website:", err.message);
    throw err;
  }
};

export const updateWebsite = async (websiteId, websiteData) => {
  if (!websiteId) return { error: "Invalid Website ID provided for update." };
  try {
    const response = await fetch(`${BASE_URL}/websites/${websiteId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(websiteData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `websiteService: Error updating website ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};

export const deleteWebsite = async (websiteId) => {
  if (!websiteId) return { error: "Invalid Website ID provided for delete." };
  try {
    const response = await fetch(`${BASE_URL}/websites/${websiteId}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `websiteService: Error deleting website ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};

export const scrapeWebsite = async (websiteId, scrapeData = {}) => {
  if (!websiteId) return { error: "Website ID is required for scraping." };
  try {
    const response = await fetch(`${BASE_URL}/websites/${websiteId}/scrape`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scrapeData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `websiteService: Error scraping website ${websiteId}:`,
      err.message,
    );
    throw err;
  }
};
