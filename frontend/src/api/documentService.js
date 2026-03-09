// frontend/src/api/documentService.js
// Version 1.1: Added document usage and context analytics endpoints.
import { BASE_URL, handleResponse } from "./apiClient";
import { API_TIMEOUT_GENERATION } from "../config/constants";

export const getDocuments = async (queryParams = {}) => {
  const { projectId, page = 1, perPage = 10, ...otherFilters } = queryParams;
  try {
    const params = new URLSearchParams({
      page: page.toString(),
      per_page: perPage.toString(),
      ...otherFilters,
    });
    if (projectId !== null && projectId !== undefined) {
      params.append("project_id", projectId.toString());
    }
    const queryString = params.toString();
    const url = `${BASE_URL}/docs${queryString ? `?${queryString}` : ""}`;
    const response = await fetch(url);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("documentService: Error getting documents:", err.message);
    return { error: err.message || "Failed to get documents." };
  }
};

export const getDocumentStatus = async (documentId) => {
  if (!documentId)
    return { error: "Invalid Document ID provided for status check." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${documentId}/status`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `documentService: Error fetching status for document ${documentId}:`,
      err.message,
    );
    throw err;
  }
};

export const getDocumentUsage = async (documentId) => {
  if (!documentId)
    return { error: "Invalid Document ID provided for usage check." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${documentId}/usage`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `documentService: Error fetching usage for document ${documentId}:`,
      err.message,
    );
    throw err;
  }
};

export const getDocumentContext = async (documentId, options = {}) => {
  if (!documentId)
    return { error: "Invalid Document ID provided for context check." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${documentId}/context`, {
      signal: options.signal
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    // Don't log AbortError as it's expected when requests are cancelled
    if (err.name !== 'AbortError') {
      console.error(
        `documentService: Error fetching context for document ${documentId}:`,
        err.message,
      );
    }
    throw err;
  }
};

export const deleteDocument = async (documentId) => {
  if (!documentId) return { error: "Invalid Document ID provided for delete." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${documentId}`, {
      method: "DELETE",
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `documentService: Error deleting document ${documentId}:`,
      err.message,
    );
    throw err;
  }
};

export const updateDocument = async (documentId, documentData) => {
  if (!documentId) return { error: "Invalid Document ID for update." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${documentId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(documentData),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error(
      `documentService: Error updating document ${documentId}:`,
      err.message,
    );
    throw err;
  }
};

export const getDocumentSources = async (docId) => {
  if (!docId) return { error: "Document ID is required." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${docId}/sources`);
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return Array.isArray(data) ? data : [];
  } catch (error) {
    console.error(
      `documentService: Error fetching sources for document ${docId}:`,
      error.message,
    );
    throw error;
  }
};

export const getDocumentContent = async (documentId) => {
  if (!documentId)
    return { error: "Invalid Document ID provided for content fetch." };
  try {
    const response = await fetch(`${BASE_URL}/docs/${documentId}/content/raw`);
    if (!response.ok) {
      if (response.status === 404) {
        throw new Error("Document not found");
      } else if (response.status === 400) {
        throw new Error("Invalid document ID");
      } else {
        throw new Error(`Failed to fetch document content (${response.status})`);
      }
    }

    // Get content as text since we're using the /raw endpoint
    const content = await response.text();
    return { content };
  } catch (err) {
    console.error(
      `documentService: Error fetching content for document ${documentId}:`,
      err.message,
    );
    return { error: err.message || "Failed to fetch document content." };
  }
};

export const uploadFile = async (
  file,
  projectId = null,
  tags = null,
  metadata = {},
  signal = null,
  onProgress = null,
) => {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    if (
      projectId !== null &&
      projectId !== undefined &&
      String(projectId).trim() !== ""
    ) {
      formData.append("project_id", String(projectId));
    }
    if (tags !== null && tags !== undefined && String(tags).trim() !== "") {
      formData.append("tags", String(tags).trim());
    }
    formData.append("file", file);
    formData.append("metadata", JSON.stringify(metadata));

    const xhr = new XMLHttpRequest();

    // Handle upload progress
    if (onProgress && xhr.upload) {
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          const percentComplete = (event.loaded / event.total) * 100;
          const progressData = {
            loaded: event.loaded,
            total: event.total,
            percentage: Math.round(percentComplete),
            speed: calculateUploadSpeed(event.loaded),
            eta: calculateETA(event.loaded, event.total),
          };
          onProgress(progressData);
        }
      });
    }

    // Handle response
    xhr.onload = async () => {
      try {
        if (xhr.status >= 200 && xhr.status < 300) {
          const responseText = xhr.responseText;
          let data;
          try {
            data = JSON.parse(responseText);
          } catch (parseError) {
            data = { message: responseText };
          }
          
          if (typeof data === "object" && data !== null && data.error) {
            throw new Error(data.error);
          }
          resolve(data);
        } else {
          const errorText = xhr.responseText || `HTTP ${xhr.status}`;
          let errorData;
          try {
            errorData = JSON.parse(errorText);
            throw new Error(errorData.error || errorData.message || errorText);
          } catch (parseError) {
            throw new Error(errorText);
          }
        }
      } catch (err) {
        console.error("documentService: Error uploading file:", err.message);
        reject(err);
      }
    };

    // Handle network errors
    xhr.onerror = () => {
      const error = new Error("Network error during file upload");
      console.error("documentService: Network error uploading file");
      reject(error);
    };

    // Handle request abortion
    xhr.onabort = () => {
      const error = new Error("Upload cancelled");
      reject(error);
    };

    // Handle timeout
    xhr.ontimeout = () => {
      const error = new Error("Upload timeout");
      console.error("documentService: Upload timeout");
      reject(error);
    };

    // Configure request
    xhr.open("POST", `${BASE_URL}/docs/upload`);
    xhr.timeout = API_TIMEOUT_GENERATION;

    // Handle request cancellation via AbortSignal
    if (signal) {
      signal.addEventListener("abort", () => {
        xhr.abort();
      });
    }

    // Send request
    xhr.send(formData);
  });
};

// Helper functions for upload progress calculations
let uploadStartTime = null;
let lastLoaded = 0;

const calculateUploadSpeed = (loaded) => {
  const now = Date.now();
  if (!uploadStartTime) {
    uploadStartTime = now;
    lastLoaded = loaded;
    return 0;
  }
  
  const timeDiff = (now - uploadStartTime) / 1000; // seconds
  const loadedDiff = loaded - lastLoaded;
  
  if (timeDiff > 0) {
    return Math.round(loadedDiff / timeDiff); // bytes per second
  }
  return 0;
};

const calculateETA = (loaded, total) => {
  if (!uploadStartTime || loaded === 0) return null;
  
  const now = Date.now();
  const elapsed = (now - uploadStartTime) / 1000; // seconds
  const rate = loaded / elapsed; // bytes per second
  
  if (rate > 0) {
    const remaining = total - loaded;
    return Math.round(remaining / rate); // seconds
  }
  return null;
};
