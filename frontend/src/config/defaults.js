/**
 * Configuration defaults for the content generation pipeline
 * Centralized configuration to avoid magic constants
 */

import { DELIMITERS } from '../utils/csv.js';

// CSV Configuration Defaults
export const CSV_DEFAULTS = {
  delimiter: DELIMITERS.COMMA,
  structuredHtml: false,
  includeH1: true,
  encoding: 'utf-8'
};

// Brand Tone Options
export const BRAND_TONE_OPTIONS = [
  { value: 'neutral', label: 'Neutral', description: 'Professional and balanced tone' },
  { value: 'friendly', label: 'Friendly', description: 'Warm and approachable tone' },
  { value: 'authoritative', label: 'Authoritative', description: 'Expert and confident tone' },
  { value: 'playful', label: 'Playful', description: 'Creative and engaging tone' },
  { value: 'luxury', label: 'Luxury', description: 'Premium and sophisticated tone' }
];

// Delimiter Options for UI
export const DELIMITER_OPTIONS = [
  { value: DELIMITERS.COMMA, label: 'Comma (,)', description: 'Standard CSV format' },
  { value: DELIMITERS.SEMICOLON, label: 'Semicolon (;)', description: 'European standard' },
  { value: DELIMITERS.TAB, label: 'Tab', description: 'Tab-separated values' }
];

// Validation Limits
export const VALIDATION_LIMITS = {
  title: {
    min: 10,
    max: 200
  },
  content: {
    min: 100,
    max: 50000
  },
  excerpt: {
    min: 0,
    max: 500
  },
  slug: {
    min: 1,
    max: 500
  },
  category: {
    max: 255
  },
  companyName: {
    min: 1,
    max: 255
  },
  primaryService: {
    min: 1,
    max: 255
  },
  secondaryService: {
    max: 255
  },
  clientLocation: {
    min: 1,
    max: 255
  },
  hours: {
    max: 500
  }
};

// Default Site Metadata
export const SITE_META_DEFAULTS = {
  companyName: '',
  phone: '',
  contactUrl: '',
  clientLocation: '',
  primaryService: '',
  secondaryService: '',
  brandTone: 'neutral',
  hours: '',
  socialLinks: []
};

// Social Platform Options
export const SOCIAL_PLATFORMS = [
  'Facebook',
  'Twitter',
  'LinkedIn',
  'Instagram',
  'YouTube',
  'TikTok',
  'Pinterest',
  'Snapchat',
  'Reddit',
  'Discord',
  'WhatsApp',
  'Telegram'
];

// HTML Structure Options
export const HTML_STRUCTURE_DEFAULTS = {
  includeH1: true,
  useSemanticTags: true,
  allowedTags: ['h1', 'h2', 'h3', 'strong', 'em', 'ul', 'li', 'p', 'a', 'br'],
  selfClosingTags: ['br', 'hr', 'img']
};

// Generation Defaults
export const GENERATION_DEFAULTS = {
  pageCount: 10,
  targetWordCount: 500,
  concurrentWorkers: 5,
  batchSize: 25,
  retryAttempts: 3,
  timeout: 120000 // 2 minutes
};

// File Handling
export const FILE_DEFAULTS = {
  maxFileSize: 50 * 1024 * 1024, // 50MB
  allowedExtensions: ['.csv', '.json', '.txt'],
  defaultEncoding: 'utf-8'
};

// API Configuration
export const API_DEFAULTS = {
  timeout: 30000, // 30 seconds
  retryAttempts: 3,
  retryDelay: 1000 // 1 second
};

// UI Configuration
export const UI_DEFAULTS = {
  pageSize: 25,
  maxRecentItems: 10,
  autoSaveInterval: 30000, // 30 seconds
  debounceDelay: 300 // 300ms
};

// Preflight Validation Thresholds
export const VALIDATION_THRESHOLDS = {
  warnings: {
    contentMinLength: 300,
    excerptMinLength: 100,
    maxEmptyRows: 5,
    maxInconsistentColumns: 10
  },
  errors: {
    maxValidationErrors: 50,
    maxTotalErrors: 100
  }
};

// Export all as a consolidated config object
export const CONFIG = {
  csv: CSV_DEFAULTS,
  brandTone: BRAND_TONE_OPTIONS,
  delimiters: DELIMITER_OPTIONS,
  validation: VALIDATION_LIMITS,
  siteMeta: SITE_META_DEFAULTS,
  socialPlatforms: SOCIAL_PLATFORMS,
  html: HTML_STRUCTURE_DEFAULTS,
  generation: GENERATION_DEFAULTS,
  files: FILE_DEFAULTS,
  api: API_DEFAULTS,
  ui: UI_DEFAULTS,
  thresholds: VALIDATION_THRESHOLDS
};

export default CONFIG;