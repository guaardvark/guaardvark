// frontend/src/config/logoConfig.js
// Version 1.0: Centralized logo path configuration for consistent logo handling

import { BASE_URL } from '../api/apiClient';
const API_BASE_URL = BASE_URL;

// Centralized logo path configuration
export const LOGO_CONFIG = {
  // Base URL for accessing logos - matches system logo pattern using /uploads
  BASE_URL: `${API_BASE_URL}/uploads`,

  // Default path when no logo is available
  DEFAULT_LOGO: null,

  // Allowed file extensions for logos
  ALLOWED_EXTENSIONS: ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'],

  // Maximum file size (in bytes) - 5MB
  MAX_FILE_SIZE: 5 * 1024 * 1024,
};

/**
 * Get the full URL for a logo given its path
 * Matches system logo pattern: logoPath is relative (e.g., "logos/logo.png" or "system/logo.png")
 * @param {string} logoPath - The relative path to the logo (e.g., "logos/logo.png")
 * @returns {string} - The full URL to access the logo
 */
export const getLogoUrl = (logoPath) => {
  if (!logoPath) {
    return LOGO_CONFIG.DEFAULT_LOGO;
  }

  // logoPath is a relative path like "logos/logo.png" or "system/logo.png"
  // Use BASE_URL (/api/uploads) to construct full path
  return `${LOGO_CONFIG.BASE_URL}/${logoPath}`;
};

/**
 * Validate if a file is a valid logo file
 * @param {File} file - The file to validate
 * @returns {Object} - { isValid: boolean, error?: string }
 */
export const validateLogoFile = (file) => {
  if (!file) {
    return { isValid: false, error: "No file provided" };
  }

  // Check file size
  if (file.size > LOGO_CONFIG.MAX_FILE_SIZE) {
    return {
      isValid: false,
      error: `File size too large. Maximum size is ${LOGO_CONFIG.MAX_FILE_SIZE / (1024 * 1024)}MB`
    };
  }

  // Check file extension
  const extension = '.' + file.name.split('.').pop().toLowerCase();
  if (!LOGO_CONFIG.ALLOWED_EXTENSIONS.includes(extension)) {
    return {
      isValid: false,
      error: `Invalid file type. Allowed types: ${LOGO_CONFIG.ALLOWED_EXTENSIONS.join(', ')}`
    };
  }

  return { isValid: true };
};