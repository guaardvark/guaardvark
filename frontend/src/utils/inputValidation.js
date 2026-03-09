// frontend/src/utils/inputValidation.js
// Comprehensive Input Validation and Sanitization Utilities
// Provides security-focused validation for user inputs

import DOMPurify from 'dompurify';

// Configuration constants
const VALIDATION_LIMITS = {
  // Text input limits
  MAX_PROMPT_LENGTH: 50000,          // Maximum characters for prompts
  MAX_TAG_LENGTH: 100,               // Maximum characters per tag
  MAX_TAGS_COUNT: 20,                // Maximum number of tags
  MAX_FILENAME_LENGTH: 255,          // Maximum filename length
  MAX_PROJECT_NAME_LENGTH: 100,      // Maximum project name length
  MAX_COMMENT_LENGTH: 2000,          // Maximum comment length

  // Code input limits
  MAX_CODE_LENGTH: 1000000,          // Maximum characters for code (1MB)
  MAX_FILE_SIZE: 10485760,           // Maximum file size (10MB)

  // API limits
  MAX_REQUEST_SIZE: 52428800,        // Maximum request size (50MB)
  REQUEST_TIMEOUT: 300000,           // Request timeout (5 minutes)

  // Security limits
  MAX_NESTED_OBJECTS: 10,            // Maximum object nesting depth
  MAX_ARRAY_LENGTH: 1000,            // Maximum array length
};

// Common regex patterns
const PATTERNS = {
  // Basic patterns
  EMAIL: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
  UUID: /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  ALPHANUMERIC: /^[a-zA-Z0-9]+$/,
  SAFE_FILENAME: /^[a-zA-Z0-9._-]+$/,

  // Code-related patterns
  PROGRAMMING_LANGUAGE: /^(javascript|typescript|python|java|cpp|c|csharp|php|ruby|go|rust|swift|kotlin|scala|html|css|sql|json|xml|yaml|markdown|text|plain)$/i,
  FILE_EXTENSION: /^\.[a-zA-Z0-9]+$/,

  // Security patterns (things to watch for)
  SUSPICIOUS_SCRIPT: /<script[\s\S]*?>[\s\S]*?<\/script>/gi,
  SUSPICIOUS_EVAL: /\b(eval|setTimeout|setInterval|Function|document\.write)\s*\(/gi,
  SUSPICIOUS_URL: /javascript:|data:|vbscript:|file:|about:/gi,
  SQL_INJECTION: /(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|UNION)\b)/gi,

  // XSS patterns
  XSS_PATTERNS: [
    /<script[\s\S]*?>[\s\S]*?<\/script>/gi,
    /javascript:/gi,
    /on\w+\s*=/gi,
    /<iframe[\s\S]*?>/gi,
    /<object[\s\S]*?>/gi,
    /<embed[\s\S]*?>/gi,
  ]
};

// Custom validation errors
export class ValidationError extends Error {
  constructor(message, field = null, code = null) {
    super(message);
    this.name = 'ValidationError';
    this.field = field;
    this.code = code;
  }
}

// Input sanitization functions
export const sanitizeText = (input, options = {}) => {
  if (typeof input !== 'string') {
    throw new ValidationError('Input must be a string', 'input', 'INVALID_TYPE');
  }

  const {
    maxLength = VALIDATION_LIMITS.MAX_PROMPT_LENGTH,
    allowHtml = false,
    stripHtml = true,
    preserveNewlines = true
  } = options;

  // Check length
  if (input.length > maxLength) {
    throw new ValidationError(
      `Input exceeds maximum length of ${maxLength} characters`,
      'input',
      'MAX_LENGTH_EXCEEDED'
    );
  }

  let sanitized = input;

  // Basic sanitization
  if (stripHtml && !allowHtml) {
    // Remove HTML tags but preserve content
    sanitized = sanitized.replace(/<[^>]*>/g, '');
  } else if (allowHtml) {
    // Sanitize HTML using DOMPurify
    sanitized = DOMPurify.sanitize(sanitized, {
      ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'code', 'pre', 'ul', 'ol', 'li', 'blockquote'],
      ALLOWED_ATTR: []
    });
  }

  // Preserve or normalize newlines
  if (preserveNewlines) {
    sanitized = sanitized.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  }

  // Remove null bytes and other dangerous characters
  sanitized = sanitized.replace(/\0/g, '').replace(/\uFEFF/g, '');

  // Trim excessive whitespace
  sanitized = sanitized.trim();

  return sanitized;
};

export const sanitizeCode = (code, language = 'javascript') => {
  if (typeof code !== 'string') {
    throw new ValidationError('Code must be a string', 'code', 'INVALID_TYPE');
  }

  if (code.length > VALIDATION_LIMITS.MAX_CODE_LENGTH) {
    throw new ValidationError(
      `Code exceeds maximum length of ${VALIDATION_LIMITS.MAX_CODE_LENGTH} characters`,
      'code',
      'MAX_LENGTH_EXCEEDED'
    );
  }

  // Validate programming language
  if (!PATTERNS.PROGRAMMING_LANGUAGE.test(language)) {
    throw new ValidationError(
      `Invalid programming language: ${language}`,
      'language',
      'INVALID_LANGUAGE'
    );
  }

  // Remove null bytes and BOM
  let sanitized = code.replace(/\0/g, '').replace(/\uFEFF/g, '');

  // Normalize line endings
  sanitized = sanitized.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  return sanitized;
};

export const sanitizeArray = (array, itemValidator, options = {}) => {
  if (!Array.isArray(array)) {
    throw new ValidationError('Input must be an array', 'array', 'INVALID_TYPE');
  }

  const { maxLength = VALIDATION_LIMITS.MAX_ARRAY_LENGTH } = options;

  if (array.length > maxLength) {
    throw new ValidationError(
      `Array exceeds maximum length of ${maxLength} items`,
      'array',
      'MAX_LENGTH_EXCEEDED'
    );
  }

  return array.map((item, index) => {
    try {
      return itemValidator(item);
    } catch (error) {
      throw new ValidationError(
        `Invalid item at index ${index}: ${error.message}`,
        `array[${index}]`,
        'INVALID_ITEM'
      );
    }
  });
};

// Specific validation functions
export const validatePrompt = (prompt) => {
  return sanitizeText(prompt, {
    maxLength: VALIDATION_LIMITS.MAX_PROMPT_LENGTH,
    stripHtml: true,
    preserveNewlines: true
  });
};

export const validateTags = (tags) => {
  if (!Array.isArray(tags)) {
    return [];
  }

  return sanitizeArray(tags, (tag) => {
    return sanitizeText(tag, {
      maxLength: VALIDATION_LIMITS.MAX_TAG_LENGTH,
      stripHtml: true,
      preserveNewlines: false
    });
  }, { maxLength: VALIDATION_LIMITS.MAX_TAGS_COUNT });
};

export const validateFilename = (filename) => {
  if (!filename || typeof filename !== 'string') {
    throw new ValidationError('Filename is required and must be a string', 'filename', 'REQUIRED');
  }

  if (filename.length > VALIDATION_LIMITS.MAX_FILENAME_LENGTH) {
    throw new ValidationError(
      `Filename exceeds maximum length of ${VALIDATION_LIMITS.MAX_FILENAME_LENGTH} characters`,
      'filename',
      'MAX_LENGTH_EXCEEDED'
    );
  }

  // Check for safe filename characters
  if (!PATTERNS.SAFE_FILENAME.test(filename.replace(/\s/g, '_'))) {
    throw new ValidationError(
      'Filename contains invalid characters. Only letters, numbers, dots, hyphens, and underscores are allowed.',
      'filename',
      'INVALID_CHARACTERS'
    );
  }

  // Prevent path traversal
  if (filename.includes('..') || filename.includes('/') || filename.includes('\\')) {
    throw new ValidationError(
      'Filename cannot contain path traversal characters',
      'filename',
      'PATH_TRAVERSAL'
    );
  }

  return filename.trim();
};

export const validateId = (id, type = 'ID') => {
  if (!id) {
    return null; // Allow null/undefined IDs
  }

  if (typeof id !== 'string') {
    throw new ValidationError(`${type} must be a string`, 'id', 'INVALID_TYPE');
  }

  // Check if it's a valid UUID or alphanumeric string
  if (!PATTERNS.UUID.test(id) && !PATTERNS.ALPHANUMERIC.test(id)) {
    throw new ValidationError(
      `${type} must be a valid UUID or alphanumeric string`,
      'id',
      'INVALID_FORMAT'
    );
  }

  return id;
};

// Security check functions
export const detectXSS = (input) => {
  if (typeof input !== 'string') {
    return false;
  }

  return PATTERNS.XSS_PATTERNS.some(pattern => pattern.test(input));
};

export const detectSQLInjection = (input) => {
  if (typeof input !== 'string') {
    return false;
  }

  return PATTERNS.SQL_INJECTION.test(input);
};

export const detectSuspiciousCode = (input) => {
  if (typeof input !== 'string') {
    return false;
  }

  return (
    PATTERNS.SUSPICIOUS_SCRIPT.test(input) ||
    PATTERNS.SUSPICIOUS_EVAL.test(input) ||
    PATTERNS.SUSPICIOUS_URL.test(input)
  );
};

// Comprehensive validation function for chat inputs
// Set skipSecurityChecks to true when validating code content (e.g., code editor prompts)
// which legitimately contains patterns that look like XSS or SQL injection
export const validateChatInput = (data, options = {}) => {
  const { skipSecurityChecks = false } = options;
  const errors = [];
  const sanitized = {};

  try {
    // Validate prompt (required)
    if (!data.prompt) {
      throw new ValidationError('Prompt is required', 'prompt', 'REQUIRED');
    }
    sanitized.prompt = validatePrompt(data.prompt);

    // Security checks on prompt (skip for code content which legitimately contains these patterns)
    if (!skipSecurityChecks) {
      if (detectXSS(data.prompt)) {
        throw new ValidationError('Potential XSS detected in prompt', 'prompt', 'SECURITY_VIOLATION');
      }
      if (detectSQLInjection(data.prompt)) {
        throw new ValidationError('Potential SQL injection detected in prompt', 'prompt', 'SECURITY_VIOLATION');
      }
    }

  } catch (error) {
    errors.push(error);
  }

  try {
    // Validate tags (optional)
    sanitized.tags = validateTags(data.tags || []);
  } catch (error) {
    errors.push(error);
  }

  try {
    // Validate IDs (optional)
    sanitized.project_id = validateId(data.project_id, 'Project ID');
    sanitized.session_id = validateId(data.session_id, 'Session ID');
  } catch (error) {
    errors.push(error);
  }

  // Return results
  if (errors.length > 0) {
    const validationError = new ValidationError('Validation failed');
    validationError.errors = errors;
    throw validationError;
  }

  return sanitized;
};

// Comprehensive validation function for code inputs
export const validateCodeInput = (data) => {
  const errors = [];
  const sanitized = {};

  try {
    // Validate code content
    if (data.content) {
      sanitized.content = sanitizeCode(data.content, data.language);
    }
  } catch (error) {
    errors.push(error);
  }

  try {
    // Validate filename
    if (data.filePath) {
      sanitized.filePath = validateFilename(data.filePath);
    }
  } catch (error) {
    errors.push(error);
  }

  try {
    // Validate language
    if (data.language && !PATTERNS.PROGRAMMING_LANGUAGE.test(data.language)) {
      throw new ValidationError(`Invalid programming language: ${data.language}`, 'language', 'INVALID_LANGUAGE');
    }
    sanitized.language = data.language || 'javascript';
  } catch (error) {
    errors.push(error);
  }

  // Return results
  if (errors.length > 0) {
    const validationError = new ValidationError('Code validation failed');
    validationError.errors = errors;
    throw validationError;
  }

  return sanitized;
};

// Rate limiting helper (simple implementation)
const rateLimitMap = new Map();
export const checkRateLimit = (identifier, maxRequests = 100, windowMs = 60000) => {
  const now = Date.now();
  const windowStart = now - windowMs;

  if (!rateLimitMap.has(identifier)) {
    rateLimitMap.set(identifier, []);
  }

  const requests = rateLimitMap.get(identifier);

  // Remove old requests outside the window
  const recentRequests = requests.filter(time => time > windowStart);

  if (recentRequests.length >= maxRequests) {
    throw new ValidationError(
      `Rate limit exceeded. Maximum ${maxRequests} requests per ${windowMs / 1000} seconds.`,
      'rate_limit',
      'RATE_LIMIT_EXCEEDED'
    );
  }

  // Add current request
  recentRequests.push(now);
  rateLimitMap.set(identifier, recentRequests);

  return true;
};

export default {
  sanitizeText,
  sanitizeCode,
  sanitizeArray,
  validatePrompt,
  validateTags,
  validateFilename,
  validateId,
  validateChatInput,
  validateCodeInput,
  detectXSS,
  detectSQLInjection,
  detectSuspiciousCode,
  checkRateLimit,
  ValidationError,
  VALIDATION_LIMITS,
  PATTERNS
};