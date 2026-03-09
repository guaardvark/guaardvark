// frontend/src/api/clientService.js
// Version 1.0: Service for client-related API calls.
import { BASE_URL, handleResponse } from "./apiClient";

export const getClients = async () => {
  try {
    const response = await fetch(`${BASE_URL}/clients`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("clientService: Error getting clients:", err.message);
    return { error: err.message || "Failed to get clients." };
  }
};

export const createClient = async (clientData) => {
  if (!clientData || !clientData.name || !clientData.name.trim()) {
    return { error: "Client name is required." };
  }
  try {
    const response = await fetch(`${BASE_URL}/clients/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(clientData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("clientService: Error creating client:", err.message);
    throw err;
  }
};

export const updateClient = async (clientId, clientData) => {
  if (!clientId) return { error: "Invalid Client ID for update." };
  try {
    const response = await fetch(`${BASE_URL}/clients/${clientId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(clientData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `clientService: Error updating client ${clientId}:`,
      err.message,
    );
    throw err;
  }
};

export const uploadClientLogo = async (clientId, file) => {
  if (!clientId || !file || (file instanceof File && file.size === 0)) {
    const errorMsg =
      "Client ID and a non-empty file are required for logo upload.";
    console.error(`clientService: ${errorMsg}`);
    return { error: errorMsg };
  }
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await fetch(`${BASE_URL}/clients/${clientId}/logo`, {
      method: "POST",
      body: formData,
      // Don't set Content-Type header - browser sets it automatically for FormData
      // Include credentials for cross-origin requests
      credentials: "include",
      mode: "cors",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `clientService: Error uploading logo for client ${clientId}:`,
      err.message,
    );
    throw err;
  }
};

export const deleteClient = async (clientId) => {
  if (!clientId) return { error: "Invalid Client ID provided for delete." };
  try {
    const response = await fetch(`${BASE_URL}/clients/${clientId}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `clientService: Error deleting client ${clientId}:`,
      err.message,
    );
    throw err;
  }
};

export const getProjectsForClient = async (clientId) => {
  if (!clientId || String(clientId).trim() === "") {
    const errorMsg =
      "Client ID is required and cannot be empty for getProjectsForClient.";
    console.error(`clientService: ${errorMsg}`);
    return { error: errorMsg };
  }
  try {
    const response = await fetch(
      `${BASE_URL}/clients/${encodeURIComponent(clientId)}/projects`,
    );
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (error) {
    console.error(
      `clientService: Error fetching projects for client ${clientId}:`,
      error.message,
    );
    throw error;
  }
};
