// frontend/src/api/fileOperationsService.js
// File operations service for CodeEditorPage

import { BASE_URL, handleResponse } from "./apiClient";

const FILE_OPS_BASE = "/api/files";

/**
 * Read file content
 * @param {string} filePath - Path to the file
 * @returns {Promise<Object>} File content and metadata
 */
export const readFile = async (filePath) => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/read`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ filePath }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      content: result.content || "",
      filePath: result.filePath || filePath,
      size: result.size || 0,
      lastModified: result.lastModified || null,
      language: result.language || "text"
    };
  } catch (error) {
    console.error("File read failed:", error);
    return {
      success: false,
      error: error.message || "Failed to read file",
      content: ""
    };
  }
};

/**
 * Write file content
 * @param {string} filePath - Path to the file
 * @param {string} content - Content to write
 * @returns {Promise<Object>} Write result
 */
export const writeFile = async (filePath, content) => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/write`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ filePath, content }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      filePath: result.filePath || filePath,
      size: result.size || content.length,
      message: "File saved successfully"
    };
  } catch (error) {
    console.error("File write failed:", error);
    return {
      success: false,
      error: error.message || "Failed to write file"
    };
  }
};

/**
 * Create new file
 * @param {string} filePath - Path for the new file
 * @param {string} content - Initial content
 * @returns {Promise<Object>} Creation result
 */
export const createFile = async (filePath, content = "") => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/create`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ filePath, content }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      filePath: result.filePath || filePath,
      message: "File created successfully"
    };
  } catch (error) {
    console.error("File creation failed:", error);
    return {
      success: false,
      error: error.message || "Failed to create file"
    };
  }
};

/**
 * Delete file
 * @param {string} filePath - Path to the file to delete
 * @returns {Promise<Object>} Deletion result
 */
export const deleteFile = async (filePath) => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/delete`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ filePath }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      message: "File deleted successfully"
    };
  } catch (error) {
    console.error("File deletion failed:", error);
    return {
      success: false,
      error: error.message || "Failed to delete file"
    };
  }
};

/**
 * List directory contents
 * @param {string} dirPath - Directory path
 * @returns {Promise<Object>} Directory contents
 */
export const listDirectory = async (dirPath) => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/list`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ dirPath }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      files: result.files || [],
      directories: result.directories || [],
      path: result.path || dirPath
    };
  } catch (error) {
    console.error("Directory listing failed:", error);
    return {
      success: false,
      error: error.message || "Failed to list directory",
      files: [],
      directories: []
    };
  }
};

/**
 * Create directory
 * @param {string} dirPath - Path for the new directory
 * @returns {Promise<Object>} Creation result
 */
export const createDirectory = async (dirPath) => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/mkdir`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ dirPath }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      message: "Directory created successfully"
    };
  } catch (error) {
    console.error("Directory creation failed:", error);
    return {
      success: false,
      error: error.message || "Failed to create directory"
    };
  }
};

/**
 * Rename file or directory
 * @param {string} oldPath - Current path
 * @param {string} newPath - New path
 * @returns {Promise<Object>} Rename result
 */
export const renameFile = async (oldPath, newPath) => {
  try {
    const response = await fetch(`${BASE_URL}${FILE_OPS_BASE}/rename`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ oldPath, newPath }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      oldPath,
      newPath: result.newPath || newPath,
      message: "File renamed successfully"
    };
  } catch (error) {
    console.error("File rename failed:", error);
    return {
      success: false,
      error: error.message || "Failed to rename file"
    };
  }
};
