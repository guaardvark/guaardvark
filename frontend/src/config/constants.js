// frontend/src/config/constants.js
// Centralized configuration constants

// API Timeouts (milliseconds)
export const API_TIMEOUT_DEFAULT = 30000;       // 30s — general API calls
export const API_TIMEOUT_GENERATION = 300000;    // 5min — file/content generation
export const API_TIMEOUT_BULK_XML = 600000;      // 10min — XML bulk generation
export const API_TIMEOUT_PROVEN_JOB = 900000;    // 15min — proven job re-runs
export const API_TIMEOUT_CODE_INTEL = 120000;    // 2min — code intelligence

// Chat/Session
export const CHAT_HISTORY_LIMIT = 50;
export const CHAT_CONTEXT_WINDOW = 50;           // messages kept in context
export const SESSION_MAX_AGE_MS = 86400000;      // 24 hours

// Rate Limiting
export const RATE_LIMIT_MAX_REQUESTS = 50;
export const RATE_LIMIT_WINDOW_MS = 60000;       // 1 minute

// Generation
export const BATCH_SIZE_MAX = 50;
export const DEFAULT_WORD_COUNT = 500;
