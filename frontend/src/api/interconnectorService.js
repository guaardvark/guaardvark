// frontend/src/api/interconnectorService.js
// Interconnector Plugin API Service - handles network interconnection between Guaardvark instances

import { BASE_URL, handleResponse } from "./apiClient";

// Default timeout for interconnector API calls (30 seconds)
const DEFAULT_TIMEOUT = 30000;

/**
 * Fetch with timeout wrapper to prevent indefinite hangs
 * @param {string} url - The URL to fetch
 * @param {object} options - Fetch options
 * @param {number} timeout - Timeout in milliseconds
 * @returns {Promise<Response>}
 */
const fetchWithTimeout = async (url, options = {}, timeout = DEFAULT_TIMEOUT) => {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return response;
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      throw new Error(`Request timeout after ${timeout}ms`);
    }
    throw err;
  }
};

/**
 * Format a master URL to ensure it has protocol and port
 * @param {string} url - The URL to format (e.g., "192.168.1.100" or "192.168.1.100:5000")
 * @param {number} defaultPort - Default port if not specified (default: 5000)
 * @returns {string} - Formatted URL with protocol and port
 */
export const formatMasterUrl = (url, defaultPort = 5000) => {
  if (!url) return "";
  
  let formatted = url.trim();
  
  // Add http:// if no protocol specified
  if (!formatted.startsWith("http://") && !formatted.startsWith("https://")) {
    formatted = `http://${formatted}`;
  }
  
  // Check if port is specified
  try {
    const urlObj = new URL(formatted);
    // If no port specified (or default http/https ports), add the default port
    if (!urlObj.port || urlObj.port === "80" || urlObj.port === "443") {
      // Remove any trailing slash from pathname
      const base = `${urlObj.protocol}//${urlObj.hostname}`;
      formatted = `${base}:${defaultPort}${urlObj.pathname.replace(/\/$/, "")}`;
    }
  } catch (e) {
    // If URL parsing fails, just append port if not present
    if (!formatted.match(/:\d+/)) {
      formatted = `${formatted}:${defaultPort}`;
    }
  }
  
  return formatted;
};

/**
 * Get network information for this node (IP, hostname, port)
 */
export const getNetworkInfo = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/network-info`);
    return await handleResponse(response);
  } catch (err) {
    console.error("interconnectorService: Error getting network info:", err);
    return { error: err.message };
  }
};

/**
 * Get current Interconnector configuration
 */
export const getInterconnectorConfig = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/config`);
    const result = await handleResponse(response);
    // Return in format expected by component (response.data contains the actual data)
    return result;
  } catch (err) {
    // Interconnector plugin is not enabled or not available
    const errorMsg = err.message || err.data?.error?.message || '';
    if (errorMsg === 'Not Found' || err.error === 'Not Found' || err.data?.error === 'Not Found') {
      console.warn("interconnectorService: Plugin not enabled");
      return { error: 'Not Found', data: null };
    }
    console.error("interconnectorService: Error fetching config:", err.message);
    return { error: err.message || errorMsg };
  }
};

/**
 * Update Interconnector configuration
 * @param {Object} config - Configuration object
 * @param {boolean} config.is_enabled - Enable/disable the plugin
 * @param {string} config.node_mode - 'master' or 'client'
 * @param {string} config.node_name - Name for this node
 * @param {string} config.master_url - Master server URL (for client mode)
 * @param {string} config.master_api_key - API key for master server (for client mode)
 * @param {boolean} config.auto_sync_enabled - Enable auto-sync
 * @param {number} config.sync_interval_seconds - Sync interval in seconds
 * @param {Array<string>} config.sync_entities - Entity types to sync
 */
export const updateInterconnectorConfig = async (config) => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("interconnectorService: Error updating config:", err.message);
    throw err;
  }
};

/**
 * Generate a new API key for this node
 */
export const generateInterconnectorApiKey = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/config/generate-key`, {
      method: "POST",
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("interconnectorService: Error generating API key:", err.message);
    throw err;
  }
};

/**
 * Get current node status
 */
export const getInterconnectorStatus = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/status`);
    return await handleResponse(response);
  } catch (err) {
    // Interconnector plugin is not enabled or not available
    const errorMsg = err.message || err.data?.message || '';
    if (err.message === 'Not Found' || err.error === 'Not Found' || 
        errorMsg.includes('not enabled') || errorMsg.includes('Not enabled')) {
      return { error: 'Not Found', data: null };
    }
    console.error("interconnectorService: Error fetching status:", err.message);
    return { error: err.message };
  }
};

/**
 * Get list of connected nodes (master mode only)
 */
export const getInterconnectorNodes = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/nodes`);
    return await handleResponse(response);
  } catch (err) {
    // Interconnector plugin is not enabled or not available
    const errorMsg = err.message || err.data?.message || '';
    if (err.message === 'Not Found' || err.error === 'Not Found' || 
        errorMsg.includes('not enabled') || errorMsg.includes('Not enabled')) {
      return { error: 'Not Found', nodes: [] };
    }
    console.error("interconnectorService: Error fetching nodes:", err.message);
    return { error: err.message, nodes: [] };
  }
};

/**
 * Disconnect a specific node (master mode only)
 * @param {string} nodeId - ID of the node to disconnect
 */
export const disconnectInterconnectorNode = async (nodeId) => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/nodes/${nodeId}`, {
      method: "DELETE",
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("interconnectorService: Error disconnecting node:", err.message);
    throw err;
  }
};

/**
 * Trigger a manual sync operation
 * @param {string} direction - 'pull', 'push', or 'bidirectional'
 * @param {Array<string>} entityTypes - Optional list of entity types to sync
 * @param {boolean} syncFiles - Whether to sync code files
 * @param {Array<string>} filePaths - Optional specific file paths to sync
 */
export const triggerManualSync = async (
  direction = "bidirectional", 
  entityTypes = null,
  syncFiles = false,
  filePaths = null,
  options = {}
) => {
  try {
    const payload = {
      direction,
      entities: entityTypes,
    };
    
    if (syncFiles) {
      payload.sync_files = true;
      if (filePaths) {
        payload.file_paths = filePaths;
      }
    }
    if (options.profile || options.profile_name) {
      payload.profile = options.profile || options.profile_name;
    }
    if (options.profile_id) {
      payload.profile_id = options.profile_id;
    }
    if (options.include_patterns) {
      payload.include_patterns = options.include_patterns;
    }
    if (options.exclude_patterns) {
      payload.exclude_patterns = options.exclude_patterns;
    }
    
    console.log("[INTERCONNECTOR API] Sending sync request:", {
      url: `${BASE_URL}/interconnector/sync/manual`,
      payload,
      method: "POST"
    });
    
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/sync/manual`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    
    console.log("[INTERCONNECTOR API] Response status:", response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log("[INTERCONNECTOR API] Response handled:", result);
    return result;
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error triggering manual sync:", err);
    console.error("[INTERCONNECTOR API] Error details:", {
      name: err.name,
      message: err.message,
      stack: err.stack
    });
    throw err;
  }
};

export const getSyncProfiles = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/sync/profiles`);
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error fetching profiles:", err);
    throw err;
  }
};

export const getPendingApprovals = async () => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/approvals/pending`);
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error fetching approvals:", err);
    throw err;
  }
};

export const decideApproval = async (approvalId, payload) => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/approvals/${approvalId}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error deciding approval:", err);
    throw err;
  }
};

export const broadcastPush = async (payload) => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/broadcast/push`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }, 60000);
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error starting broadcast:", err);
    throw err;
  }
};

export const getBroadcastStatus = async (broadcastId) => {
  try {
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/broadcast/status/${broadcastId}`);
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error fetching broadcast status:", err);
    throw err;
  }
};

/**
 * Test all client connections and get sync history (master mode only)
 */
export const testAllClientConnections = async () => {
  try {
    console.log("[INTERCONNECTOR API] Testing all client connections...");
    
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/nodes/test-all`, {
      method: "POST",
    }, 60000); // 60 second timeout for multiple clients
    
    console.log("[INTERCONNECTOR API] Client connections test response status:", response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log("[INTERCONNECTOR API] Client connections test result:", result);
    return result;
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error testing all client connections:", err);
    console.error("[INTERCONNECTOR API] Error details:", {
      name: err.name,
      message: err.message,
      stack: err.stack
    });
    throw err;
  }
};

/**
 * Test connection to a specific client node (master mode only)
 * @param {string} nodeId - ID of the client node to test
 */
export const testClientConnection = async (nodeId) => {
  try {
    console.log(`[INTERCONNECTOR API] Testing connection to client node: ${nodeId}`);
    
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/nodes/${nodeId}/test`, {
      method: "POST",
    }, 15000); // 15 second timeout for connection test
    
    console.log(`[INTERCONNECTOR API] Client connection test response status:`, response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log(`[INTERCONNECTOR API] Client connection test result:`, result);
    return result;
  } catch (err) {
    console.error(`[INTERCONNECTOR API] Error testing client connection:`, err);
    console.error("[INTERCONNECTOR API] Error details:", {
      name: err.name,
      message: err.message,
      stack: err.stack
    });
    throw err;
  }
};

/**
 * Test file scanning on server (master mode only)
 * Tests if files can be scanned and returns file count/stats
 */
export const testFileScanning = async () => {
  try {
    console.log("[INTERCONNECTOR API] Testing file scanning on server...");
    
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/sync/files/test`, {
      method: "GET",
    }, 30000); // Longer timeout for file scan
    
    console.log("[INTERCONNECTOR API] File scan test response status:", response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log("[INTERCONNECTOR API] File scan test result:", result);
    return result;
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error testing file scanning:", err);
    console.error("[INTERCONNECTOR API] Error details:", {
      name: err.name,
      message: err.message,
      stack: err.stack
    });
    throw err;
  }
};

/**
 * Browse output files on the master (metadata only)
 */
export const fetchOutputsIndex = async (limit = 200, path = null) => {
  try {
    const params = new URLSearchParams();
    if (limit) params.append("limit", limit);
    if (path) params.append("path", path);

    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/outputs/index?${params.toString()}`, {
      method: "GET",
    }, 30000);

    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error fetching outputs index:", err);
    return { error: err.message || "Failed to fetch outputs index" };
  }
};

/**
 * Verify files on server (master mode only)
 */
export const verifyFiles = async (files = null) => {
  try {
    console.log("[INTERCONNECTOR API] Verifying files on server...");
    
    const payload = files ? { files } : {};
    
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/sync/files/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }, 30000);
    
    console.log("[INTERCONNECTOR API] File verification response status:", response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log("[INTERCONNECTOR API] File verification result:", result);
    return result;
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error verifying files:", err);
    throw err;
  }
};

/**
 * Test connection to master server (client mode only)
 * Attempts to fetch status from the configured master
 */
export const testMasterConnection = async (masterUrl, apiKey) => {
  try {
    // Ensure masterUrl doesn't end with a slash and construct proper path
    const baseUrl = masterUrl.replace(/\/$/, "");
    // Check if masterUrl already includes /api path
    const apiPath = baseUrl.endsWith("/api") ? "" : "/api";
    const response = await fetchWithTimeout(`${baseUrl}${apiPath}/interconnector/status`, {
      headers: {
        "X-API-Key": apiKey,
      },
    }, 10000); // Shorter timeout for connection test
    return await handleResponse(response);
  } catch (err) {
    console.error("interconnectorService: Error testing master connection:", err.message);
    return { error: err.message, reachable: false };
  }
};

/**
 * Register this client node with the master server
 * @param {Object} registrationData - Registration data
 * @param {string} registrationData.node_name - Name of this node
 * @param {string} registrationData.node_id - Optional node ID (for re-registration)
 * @param {Array<string>} registrationData.sync_entities - Entity types to sync
 * @param {Object} registrationData.capabilities - System capabilities
 */
export const registerWithMaster = async (masterUrl, apiKey, registrationData) => {
  try {
    console.log("[INTERCONNECTOR API] Registering with master:", {
      masterUrl,
      registrationData: {
        ...registrationData,
        node_id: registrationData.node_id || "none"
      }
    });
    
    const baseUrl = masterUrl.replace(/\/$/, "");
    const apiPath = baseUrl.endsWith("/api") ? "" : "/api";
    const registerUrl = `${baseUrl}${apiPath}/interconnector/nodes/register`;
    
    console.log("[INTERCONNECTOR API] Registration URL:", registerUrl);
    
    const response = await fetchWithTimeout(registerUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      body: JSON.stringify(registrationData),
    }, 10000);
    
    console.log("[INTERCONNECTOR API] Registration response status:", response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log("[INTERCONNECTOR API] Registration result:", result);
    return result;
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error registering with master:", err);
    console.error("[INTERCONNECTOR API] Registration error details:", {
      name: err.name,
      message: err.message,
      stack: err.stack
    });
    
    // Provide a more user-friendly error message for CORS issues
    if (err.name === "TypeError" && err.message.includes("NetworkError")) {
      return { 
        error: "Connection failed. This may be a CORS issue - ensure the master node allows connections from this client's origin. The master may need to be restarted after configuration changes.",
        isCorsError: true
      };
    }
    
    return { error: err.message };
  }
};

// =============================================================================
// SIMPLIFIED UPDATE ENDPOINTS (for client machines)
// =============================================================================

/**
 * Check for available code updates from master (lightweight check)
 * @returns {Promise<Object>} - { available, count, summary, master_version, local_version }
 */
export const checkForUpdates = async () => {
  try {
    console.log("[INTERCONNECTOR API] Checking for updates...");
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/updates/check`, {
      method: "GET",
    }, 30000);
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error checking for updates:", err);
    return { error: err.message };
  }
};

/**
 * Get detailed preview of available updates
 * @returns {Promise<Object>} - { files, count, total_size }
 */
export const previewUpdates = async () => {
  try {
    console.log("[INTERCONNECTOR API] Getting update preview...");
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/updates/preview`, {
      method: "GET",
    }, 30000);
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error previewing updates:", err);
    return { error: err.message };
  }
};

/**
 * Apply code updates from master
 * @param {Array<string>} files - Optional list of specific file paths to update (empty = all)
 * @returns {Promise<Object>} - { applied, created, updated, backed_up, errors, details }
 */
export const applyUpdates = async (files = []) => {
  try {
    console.log("[INTERCONNECTOR API] Applying updates...");
    const response = await fetchWithTimeout(`${BASE_URL}/interconnector/updates/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files }),
    }, 120000); // 2 minute timeout for updates
    return await handleResponse(response);
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error applying updates:", err);
    return { error: err.message };
  }
};

/**
 * Send heartbeat to master server
 * @param {string} masterUrl - Master server URL
 * @param {string} apiKey - API key
 * @param {string} nodeId - This node's ID
 */
export const sendHeartbeat = async (masterUrl, apiKey, nodeId) => {
  try {
    console.log(`[INTERCONNECTOR API] Sending heartbeat for node: ${nodeId}`);
    
    const baseUrl = masterUrl.replace(/\/$/, "");
    const apiPath = baseUrl.endsWith("/api") ? "" : "/api";
    const heartbeatUrl = `${baseUrl}${apiPath}/interconnector/nodes/${nodeId}/heartbeat`;
    
    console.log("[INTERCONNECTOR API] Heartbeat URL:", heartbeatUrl);
    
    const response = await fetchWithTimeout(heartbeatUrl, {
      method: "POST",
      headers: {
        "X-API-Key": apiKey,
      },
    }, 5000); // Short timeout for heartbeat
    
    console.log("[INTERCONNECTOR API] Heartbeat response status:", response.status, response.statusText);
    
    const result = await handleResponse(response);
    console.log("[INTERCONNECTOR API] Heartbeat result:", result);
    return result;
  } catch (err) {
    console.error("[INTERCONNECTOR API] Error sending heartbeat:", err);
    console.error("[INTERCONNECTOR API] Heartbeat error details:", {
      name: err.name,
      message: err.message
    });
    return { error: err.message };
  }
};

