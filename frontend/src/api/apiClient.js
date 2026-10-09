// frontend/src/api/apiClient.js
// Version 1.0: Centralized API client logic.

import { announceRefusal, authRefusalCode, describeAuthRefusal, isBackendUrl } from "./apiAuth";
import { isQueueUnreachable, QUEUE_UNREACHABLE_MESSAGE } from "./taskQueue";

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

export const handleResponse = async (response, options = {}) => {
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
    // The backend's standard error body nests the text: {"error": {"code",
    // "message"}}. Handing that object to Error() stringifies it to
    // "[object Object]", which is what the client box showed when its master
    // was unreachable. Older endpoints still send a bare string under "error".
    const nested = errorData?.error;
    // A protected route refused this browser: say what to do here (Settings →
    // API key) rather than the server's wording, which also addresses the CLI.
    const refusal =
      (response.status === 401 || response.status === 403) &&
      (!response.url || isBackendUrl(response.url))
        ? authRefusalCode(errorData)
        : null;
    const rejected = Boolean(refusal && errorData.credential_rejected);
    // Background work that Redis did not take: say so in the UI's words
    // (taskQueue.js); the server's text stays in error.data.
    const queueDown = response.status === 503 && isQueueUnreachable(errorData);
    const errorMessage =
      (refusal && describeAuthRefusal(refusal, rejected, errorData.machine)) ||
      (queueDown && QUEUE_UNREACHABLE_MESSAGE) ||
      (typeof nested === "string" && nested) ||
      (typeof nested?.message === "string" && nested.message) ||
      (typeof errorData?.message === "string" && errorData.message) ||
      `HTTP error! Status: ${response.status}`;
    const error = new Error(errorMessage);
    error.status = response.status;
    error.data = errorData;
    if (queueDown) error.queueUnreachable = true;
    if (refusal) {
      error.authRefused = refusal;
      // ApiKeyRefusalNotice tells pages that only log their errors.
      announceRefusal({ code: refusal, rejected, url: response.url });
    }
    // The Vite proxy returns 502 (and {"error":"backend_offline"}) when the Flask
    // backend is unreachable; 504 is a proxy timeout. Surface a single flag so
    // callers (StatusContext, VoiceContext, HealthContext) can distinguish
    // "backend is down" from a genuine application error and recover gracefully.
    if (
      response.status === 502 ||
      response.status === 504 ||
      errorData?.error === "backend_offline"
    ) {
      error.backendOffline = true;
    }
    if (!options.quiet) {
      console.error(
        `apiClient: handleResponse throwing error for ${response.url}:`,
        error.message,
        error.data || "",
      );
    }
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
