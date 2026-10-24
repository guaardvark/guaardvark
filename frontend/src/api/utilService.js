// frontend/src/api/utilService.js
// Version 1.0: Misc helper functions for links and downloads.
import { BASE_URL } from "./apiClient";
import { getProjects, getProjectsForClient } from "./projectService";
import { getClients } from "./clientService";
import { translate } from "../i18n";

export const getDownloadUrl = (filename) => {
  if (!filename) return null;
  const apiPath = "/api";
  let rootUrl = BASE_URL;
  if (BASE_URL.endsWith(apiPath)) rootUrl = BASE_URL.slice(0, -apiPath.length);
  const outputsSegment = "/outputs/";
  const cleanRoot = rootUrl.replace(/\/$/, "");
  const cleanFilename = filename.startsWith("/")
    ? filename.substring(1)
    : filename;
  return `${cleanRoot}${outputsSegment}${cleanFilename}`;
};

export const getLinkableItems = async (
  primaryEntityType,
  primaryEntityId,
  linkableEntityType,
  queryParams = {},
) => {
  try {
    // Use the new comprehensive entity links API
    const searchParams = new URLSearchParams();
    if (queryParams.search) {
      searchParams.append("search", queryParams.search);
    }
    
    const response = await fetch(
      `${BASE_URL}/entity-links/linkable/${linkableEntityType}?${searchParams}`
    );
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    
    if (data.success) {
      return data.entities || [];
    } else {
      throw new Error(data.error || "Failed to get linkable items");
    }
  } catch (error) {
    console.error(`getLinkableItems error for ${linkableEntityType}:`, error);
    // Fallback to original implementation for backwards compatibility
    if (linkableEntityType === "project") return getProjects(queryParams);
    if (linkableEntityType === "client") return getClients();
    return Promise.resolve([]);
  }
};

export const getCurrentlyLinkedItems = async (
  primaryEntityType,
  primaryEntityId,
  linkableEntityType,
) => {
  try {
    // Use the new comprehensive entity links API
    const response = await fetch(
      `${BASE_URL}/entity-links/${primaryEntityType}/${primaryEntityId}/links`
    );
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const data = await response.json();
    
    if (data.success) {
      // Return the specific link type or empty array
      return data.links[linkableEntityType + "s"] || data.links[linkableEntityType] || [];
    } else {
      throw new Error(data.error || "Failed to get current links");
    }
  } catch (error) {
    console.error(`getCurrentlyLinkedItems error for ${primaryEntityType}-${linkableEntityType}:`, error);
    
    // Fallback to original implementation for backwards compatibility
    if (
      primaryEntityType === "client" &&
      linkableEntityType === "project" &&
      primaryEntityId
    ) {
      return getProjectsForClient(primaryEntityId);
    }
    
    return Promise.resolve([]);
  }
};

export const updateEntityLinks = async (
  primaryEntityType,
  primaryEntityId,
  linkableEntityType,
  linkedIds
) => {
  try {
    // Check for unsupported link combinations and skip gracefully
    const unsupportedCombinations = [
      'document-client', 'document-task',
      'task-client', 'task-document', 
      'client-document', 'client-task'
    ];
    
    const combination = `${primaryEntityType}-${linkableEntityType}`;
    if (unsupportedCombinations.includes(combination)) {
      console.warn(`Skipping unsupported entity link combination: ${combination}`);
      return {
        success: true,
        message: `Link type ${combination} not yet implemented - skipped gracefully`
      };
    }
    
    // Use the new comprehensive entity links API
    const response = await fetch(
      `${BASE_URL}/entity-links/${primaryEntityType}/${primaryEntityId}/links/${linkableEntityType}s`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          linked_ids: linkedIds || []
        })
      }
    );
    
    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(
        data.error || `HTTP ${response.status}: ${response.statusText}`
      );
    }

    if (data.success) {
      return {
        success: true,
        message: data.message || translate("messages.links.updated", { entityType: linkableEntityType })
      };
    }
    throw new Error(data.error || translate("messages.links.updateFailed"));
  } catch (error) {
    // Rethrow: a caller that gets a value back must be able to trust it.
    // Returning { success: false } here let the Rules page announce
    // "Links updated successfully!" on a 400.
    console.error(`updateEntityLinks error for ${primaryEntityType}-${linkableEntityType}:`, error);
    throw error instanceof Error ? error : new Error(String(error));
  }
};
