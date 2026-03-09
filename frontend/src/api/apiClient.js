// frontend/src/api/apiClient.js
// Version 1.0: Centralized API client logic.

// Default to '/api' so requests use the Vite proxy during development
// Strip any trailing slash to avoid double slashes or Flask 405 errors
export const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "/api").replace(
  /\/$/,
  "",
);

// Absolute backend origin for URLs that can't use relative paths
// (audio playback, Socket.IO connections, GraphQL)
// Uses window.location.origin so Vite proxy handles routing in dev,
// and same-origin works in production. Override with VITE_SOCKET_URL if needed.
export const BACKEND_URL = (
  import.meta.env.VITE_SOCKET_URL || window.location.origin
).replace(/\/$/, "");

// Socket.IO connection URL (same as BACKEND_URL)
export const SOCKET_URL = BACKEND_URL;

export const handleResponse = async (response) => {
  if (response.status === 204) {
    return { success: true, status: 204 };
  }
  const contentType = response.headers.get("content-type");
  const isJson = contentType && contentType.includes("application/json");
  let responseText = "";
  try {
    responseText = await response.text();
  } catch (e) {
    console.warn(
      `apiClient: handleResponse could not get text (Status: ${response.status})`,
      e,
    );
  }

  if (!response.ok) {
    let errorData = {
      message: `HTTP error ${response.status} - ${response.statusText || "Unknown Error"}`,
    };
    if (isJson && responseText) {
      try {
        errorData = { ...errorData, ...JSON.parse(responseText) };
      } catch (e) {
        errorData.rawError = responseText;
      }
    } else if (responseText) {
      errorData.rawError = responseText;
    }
    const errorMessage =
      errorData?.error ||
      errorData?.message ||
      `HTTP error! Status: ${response.status}`;
    const error = new Error(errorMessage);
    error.status = response.status;
    error.data = errorData;
    console.error(
      `apiClient: handleResponse throwing error for ${response.url}:`,
      error.message,
      error.data || "",
    );
    throw error;
  }

  if (isJson) {
    if (!responseText) return { success: true, status: response.status };
    try {
      return JSON.parse(responseText);
    } catch (e) {
      console.error(
        "apiClient: handleResponse failed to parse success JSON:",
        e,
        "Raw:",
        responseText,
      );
      throw new Error("Failed to parse JSON response from server.");
    }
  } else if (contentType && contentType.includes("text/plain")) {
    return responseText;
  } else {
    return {
      success: true,
      status: response.status,
      contentType: contentType,
      body: responseText,
    };
  }
};
