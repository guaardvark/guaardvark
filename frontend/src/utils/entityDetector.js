/**
 * Entity Detector - Automatically detect and extract entity mentions from user queries
 *
 * Detects mentions of:
 * - Clients (company names, business references)
 * - Projects (project names, initiatives)
 * - Websites (URLs, domain names)
 * - Documents (file names, document references)
 * - Tasks (task identifiers, work items)
 */

/**
 * Entity types enum
 */
export const EntityType = {
  CLIENT: 'client',
  PROJECT: 'project',
  WEBSITE: 'website',
  DOCUMENT: 'document',
  TASK: 'task',
  UNKNOWN: 'unknown'
};

/**
 * Extract website/URL entities
 */
function extractWebsiteEntities(queryText) {
  const entities = [];

  // Match various URL formats
  const urlPatterns = [
    // Full URLs with protocol
    /(https?:\/\/)?(www\.)?([a-zA-Z0-9\-]+\.(com|org|net|io|ai|co|uk|edu|gov))(\/[^\s]*)?/gi,
    // Domain-only mentions
    /\b([a-zA-Z0-9\-]+\.(com|org|net|io|ai|co|uk))\b/gi
  ];

  urlPatterns.forEach(pattern => {
    let match;
    while ((match = pattern.exec(queryText)) !== null) {
      const fullMatch = match[0];
      // Extract clean domain (remove protocol and www)
      const domain = fullMatch.replace(/^(https?:\/\/)?(www\.)?/, '').split('/')[0];

      entities.push({
        type: EntityType.WEBSITE,
        value: domain,
        rawValue: fullMatch,
        confidence: 0.95,
        startIndex: match.index,
        endIndex: match.index + fullMatch.length
      });
    }
  });

  return entities;
}

/**
 * Extract client entities (company names)
 */
function extractClientEntities(queryText) {
  const entities = [];

  // Patterns for client references
  const clientPatterns = [
    // Explicit client mentions
    /\b(client|company)\s+(?:called\s+|named\s+)?['"]?([A-Z][a-zA-Z0-9\s&\-]+?)['"]?\b/gi,
    // Possessive form
    /\b([A-Z][a-zA-Z0-9\s&\-]+?)'s?\s+(website|project|content|account)/gi,
    // "for [Company]" pattern
    /\bfor\s+([A-Z][A-Z\s&]+)\b/g
  ];

  clientPatterns.forEach(pattern => {
    let match;
    while ((match = pattern.exec(queryText)) !== null) {
      const clientName = match[2] || match[1];
      if (clientName && clientName.length > 2 && clientName.length < 50) {
        entities.push({
          type: EntityType.CLIENT,
          value: clientName.trim(),
          rawValue: match[0],
          confidence: 0.75,
          startIndex: match.index,
          endIndex: match.index + match[0].length
        });
      }
    }
  });

  return entities;
}

/**
 * Extract project entities
 */
function extractProjectEntities(queryText) {
  const entities = [];

  // Patterns for project references
  const projectPatterns = [
    // Explicit project mentions
    /\b(project|initiative)\s+(?:called\s+|named\s+)?['"]?([A-Z][a-zA-Z0-9\s\-]+?)['"]?\b/gi,
    // Project code patterns (e.g., PROJ-123, PRJ-2024-001)
    /\b([A-Z]{3,5}-\d{2,6})\b/g
  ];

  projectPatterns.forEach(pattern => {
    let match;
    while ((match = pattern.exec(queryText)) !== null) {
      const projectName = match[2] || match[1];
      if (projectName && projectName.length > 2) {
        entities.push({
          type: EntityType.PROJECT,
          value: projectName.trim(),
          rawValue: match[0],
          confidence: 0.70,
          startIndex: match.index,
          endIndex: match.index + match[0].length
        });
      }
    }
  });

  return entities;
}

/**
 * Extract document/file entities
 */
function extractDocumentEntities(queryText) {
  const entities = [];

  // Patterns for document references
  const documentPatterns = [
    // File with extension
    /\b([a-zA-Z0-9_\-]+\.(pdf|doc|docx|txt|csv|xlsx|json|xml|md))\b/gi,
    // Explicit document mentions
    /\b(document|file)\s+['"]?([a-zA-Z0-9_\-\s]+?)['"]?\b/gi,
    // Uploaded file references
    /\b(uploaded|attached)\s+(file|document)\s+['"]?([a-zA-Z0-9_\-\s]+?)['"]?\b/gi
  ];

  documentPatterns.forEach(pattern => {
    let match;
    while ((match = pattern.exec(queryText)) !== null) {
      const docName = match[1] || match[2] || match[3];
      if (docName && docName.length > 2) {
        entities.push({
          type: EntityType.DOCUMENT,
          value: docName.trim(),
          rawValue: match[0],
          confidence: 0.80,
          startIndex: match.index,
          endIndex: match.index + match[0].length
        });
      }
    }
  });

  return entities;
}

/**
 * Extract task entities
 */
function extractTaskEntities(queryText) {
  const entities = [];

  // Patterns for task references
  const taskPatterns = [
    // Task IDs (TASK-123, T-456, etc.)
    /\b(TASK|T|TSK)-(\d{1,6})\b/gi,
    // Explicit task mentions
    /\b(task|ticket|issue)\s+#?(\d{1,6})\b/gi,
    /\b(task|ticket|issue)\s+['"]?([a-zA-Z0-9\s\-]+?)['"]?\b/gi
  ];

  taskPatterns.forEach(pattern => {
    let match;
    while ((match = pattern.exec(queryText)) !== null) {
      const taskId = match[2] || match[1];
      if (taskId) {
        entities.push({
          type: EntityType.TASK,
          value: taskId.trim(),
          rawValue: match[0],
          confidence: 0.75,
          startIndex: match.index,
          endIndex: match.index + match[0].length
        });
      }
    }
  });

  return entities;
}

/**
 * Remove duplicate entities (keep highest confidence)
 */
function deduplicateEntities(entities) {
  const seen = new Map();

  entities.forEach(entity => {
    const key = `${entity.type}:${entity.value.toLowerCase()}`;
    const existing = seen.get(key);

    if (!existing || entity.confidence > existing.confidence) {
      seen.set(key, entity);
    }
  });

  return Array.from(seen.values());
}

/**
 * Main entity extraction function
 *
 * @param {string} queryText - The user's query text
 * @returns {Array} Array of detected entities with metadata
 */
export function extractEntities(queryText) {
  if (!queryText || typeof queryText !== 'string') {
    return [];
  }

  const trimmedQuery = queryText.trim();
  if (trimmedQuery.length < 3) {
    return [];
  }

  // Extract entities of all types
  const allEntities = [
    ...extractWebsiteEntities(trimmedQuery),
    ...extractClientEntities(trimmedQuery),
    ...extractProjectEntities(trimmedQuery),
    ...extractDocumentEntities(trimmedQuery),
    ...extractTaskEntities(trimmedQuery)
  ];

  // Remove duplicates and sort by confidence
  const uniqueEntities = deduplicateEntities(allEntities);
  uniqueEntities.sort((a, b) => b.confidence - a.confidence);

  return uniqueEntities;
}

/**
 * Get entities grouped by type
 *
 * @param {string} queryText - The user's query text
 * @returns {Object} Entities grouped by type
 */
export function extractEntitiesByType(queryText) {
  const entities = extractEntities(queryText);

  const byType = {
    [EntityType.WEBSITE]: [],
    [EntityType.CLIENT]: [],
    [EntityType.PROJECT]: [],
    [EntityType.DOCUMENT]: [],
    [EntityType.TASK]: []
  };

  entities.forEach(entity => {
    if (byType[entity.type]) {
      byType[entity.type].push(entity);
    }
  });

  return byType;
}

/**
 * Check if query contains any entities
 *
 * @param {string} queryText - The user's query text
 * @returns {boolean} True if entities detected
 */
export function hasEntities(queryText) {
  const entities = extractEntities(queryText);
  return entities.length > 0;
}

/**
 * Get primary entity (highest confidence)
 *
 * @param {string} queryText - The user's query text
 * @returns {Object|null} Primary entity or null
 */
export function getPrimaryEntity(queryText) {
  const entities = extractEntities(queryText);
  return entities.length > 0 ? entities[0] : null;
}

/**
 * Format entities for display/logging
 *
 * @param {Array} entities - Array of entities
 * @returns {string} Formatted entity list
 */
export function formatEntities(entities) {
  if (!entities || entities.length === 0) {
    return 'No entities detected';
  }

  return entities.map(e =>
    `${e.type}: "${e.value}" (${(e.confidence * 100).toFixed(0)}%)`
  ).join(', ');
}

/**
 * Create entity context string for API requests
 *
 * @param {Array} entities - Array of entities
 * @returns {string} Formatted context for backend
 */
export function createEntityContext(entities) {
  if (!entities || entities.length === 0) {
    return '';
  }

  const contextParts = [];

  // Group by type
  const byType = {};
  entities.forEach(entity => {
    if (!byType[entity.type]) {
      byType[entity.type] = [];
    }
    byType[entity.type].push(entity.value);
  });

  // Format for backend
  Object.entries(byType).forEach(([type, values]) => {
    if (values.length > 0) {
      contextParts.push(`${type}s: ${values.join(', ')}`);
    }
  });

  return contextParts.join(' | ');
}

/**
 * Validate entity format
 *
 * @param {Object} entity - Entity to validate
 * @returns {boolean} True if valid
 */
export function isValidEntity(entity) {
  return entity &&
    typeof entity === 'object' &&
    entity.type &&
    entity.value &&
    entity.confidence >= 0 &&
    entity.confidence <= 1;
}

export default {
  EntityType,
  extractEntities,
  extractEntitiesByType,
  hasEntities,
  getPrimaryEntity,
  formatEntities,
  createEntityContext,
  isValidEntity
};
