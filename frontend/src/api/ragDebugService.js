// frontend/src/api/ragDebugService.js
// RAG Debug API service wrapper for performance monitoring and debugging

import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Get overall system health metrics including health score, uptime, and error rates
 */
export const getSystemHealth = async () => {
  try {
    const response = await fetch(`${BASE_URL}/rag-debug/system-health`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    return await handleResponse(response);
  } catch (error) {
    console.error("ragDebugService: Error fetching system health:", error);
    throw error;
  }
};

/**
 * Get performance summary and metrics
 */
export const getPerformanceMetrics = async (hours = 24) => {
  try {
    const response = await fetch(`${BASE_URL}/rag-debug/performance?hours=${hours}`, {
      method: "GET", 
      headers: { "Content-Type": "application/json" },
    });
    return await handleResponse(response);
  } catch (error) {
    console.error("ragDebugService: Error fetching performance metrics:", error);
    throw error;
  }
};

/**
 * Get query patterns and analysis
 */
export const getQueryPatterns = async () => {
  try {
    const response = await fetch(`${BASE_URL}/rag-debug/query-patterns`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
    });
    return await handleResponse(response);
  } catch (error) {
    console.error("ragDebugService: Error fetching query patterns:", error);
    throw error;
  }
};

/**
 * Test a retrieval operation for debugging
 */
export const testRetrieval = async (query, projectId = null, topK = 5) => {
  try {
    const payload = {
      query,
      top_k: topK,
      ...(projectId && { project_id: projectId }),
    };
    
    const response = await fetch(`${BASE_URL}/rag-debug/retrieve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return await handleResponse(response);
  } catch (error) {
    console.error("ragDebugService: Error testing retrieval:", error);
    throw error;
  }
};

/**
 * Analyze context quality for a session
 */
export const analyzeContextQuality = async (sessionId, contextChunks = [], query = "") => {
  try {
    const payload = {
      session_id: sessionId,
      context_chunks: contextChunks,
      query,
    };
    
    const response = await fetch(`${BASE_URL}/rag-debug/context-quality`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return await handleResponse(response);
  } catch (error) {
    console.error("ragDebugService: Error analyzing context quality:", error);
    throw error;
  }
};

/**
 * Format uptime seconds into human-readable string
 */
export const formatUptime = (seconds) => {
  if (!seconds || seconds < 0) return "Unknown";
  
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  
  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`;
  } else if (hours > 0) {
    return `${hours}h ${minutes}m`;
  } else {
    return `${minutes}m`;
  }
};

/**
 * Format bytes into human-readable string
 */
export const formatBytes = (bytes) => {
  if (!bytes || bytes === 0) return "0 B";
  
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
};

/**
 * Get health status color based on health level
 */
export const getHealthColor = (healthLevel) => {
  switch (healthLevel?.toLowerCase()) {
    case "excellent":
      return "success";
    case "good":
      return "info";
    case "fair":
      return "warning";
    case "poor":
      return "error";
    default:
      return "default";
  }
};

/**
 * Get health score percentage for display
 */
export const getHealthPercentage = (healthScore) => {
  return Math.round((healthScore || 0) * 100);
};
