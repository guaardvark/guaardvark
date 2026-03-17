/**
 * Query Classifier - Intelligent query type detection for smart routing
 *
 * Classifies user queries into types for optimized handling:
 * - greeting: Simple greetings/acknowledgments (fast path, minimal tokens)
 * - factual: Questions requiring current/factual information (web search)
 * - entity: Mentions of clients/projects/websites (entity context injection)
 * - file_gen: File generation requests (generation templates)
 * - code: Code-related queries (code assistance)
 * - general: General questions (standard RAG)
 */

/**
 * Query types enum
 */
export const QueryType = {
  GREETING: 'greeting',
  FACTUAL: 'factual',
  ENTITY: 'entity',
  FILE_GEN: 'file_gen',
  CODE: 'code',
  WEB_SEARCH: 'web_search',
  GENERAL: 'general'
};

/**
 * Greeting patterns - simple interactions requiring minimal processing
 */
const GREETING_PATTERNS = [
  /^(hi|hello|hey|greetings|howdy|sup|yo)[\s!.]*$/i,
  /^good\s+(morning|afternoon|evening|day)[\s!.]*$/i,
  /^(thanks|thank you|thx|ty|appreciate it)[\s!.]*$/i,
  /^(ok|okay|got it|understood|fine|sure|alright)[\s!.]*$/i,
  /^(bye|goodbye|see you|later|cya)[\s!.]*$/i,
  /^(yes|yeah|yep|yup|no|nope|nah)[\s!.]*$/i,
  /^how are you[\s?]*$/i,
  /^what'?s up[\s?]*$/i
];

/**
 * Factual query patterns - require current/real-time information
 */
const FACTUAL_PATTERNS = [
  // Current information
  /\b(what is|what's|whats)\s+(the\s+)?(current|latest|today's|todays)\b/i,
  /\b(current|latest|today's|todays|recent)\s+(price|temperature|weather|status|news)\b/i,

  // Time-sensitive queries
  /\b(when (is|was|will)|what time|date of|schedule for)\b/i,
  /\b(today|tomorrow|yesterday|this (week|month|year))\b/i,

  // Statistical/data queries
  /\b(how (many|much)|statistics|data|numbers|count)\b/i,

  // Weather
  /\b(weather|temperature|forecast|rain|snow|climate)\s+(in|at|for)?\b/i,

  // News/events
  /\b(news|announcement|event|happened|occurred)\b/i,

  // Comparisons requiring current data
  /\b(compare|vs|versus|difference between)\b.*\b(price|cost|feature|performance)\b/i
];

/**
 * Web search trigger patterns - explicit web/URL mentions
 */
const WEB_SEARCH_PATTERNS = [
  // Direct web references
  /\b(search|google|look up|find (on|in) (web|internet|online))\b/i,
  /\b(website|webpage|url|link|site)\b/i,

  // Analyze/scrape patterns
  /\b(analyze|check|review|scrape|extract (from|data from))\s+.*\.(com|org|net|io|ai|co)/i,
  /\b(what('?s| is) (on|at))\s+.*\.(com|org|net|io|ai|co)/i
];

/**
 * Entity patterns - mentions of clients, projects, websites
 */
const ENTITY_PATTERNS = [
  // Direct entity actions
  /\b(client|project|website|task|document)\s+[A-Z]/,
  /\b(analyze|review|show|display|get)\s+(client|project|website|task|document)/i,

  // Website URLs
  /(https?:\/\/|www\.)[\w\-.]+(\.com|\.org|\.net|\.io|\.ai|\.co)/i,

  // Specific entity references
  /\b(techcorp|acme|smith & jones)\b/i,  // Examples - would be dynamic from DB

  // Entity queries
  /\b(status of|details about|information on)\s+[A-Z]/,
  /\b(client|project|website)\s+called\b/i
];

/**
 * File generation patterns
 */
const FILE_GEN_PATTERNS = [
  // Explicit generation
  /\b(generate|create|make|build|produce|export|output)\s+(a\s+)?(csv|file|document|spreadsheet|excel)/i,
  /\b(csv|spreadsheet|excel|file)\s+(generation|creation|export)/i,

  // Bulk operations
  /\b(bulk|batch|multiple|mass|many)\s+(generate|create|make)/i,
  /\b(generate|create)\s+\d+\s+(pages|files|entries|items|records)/i,

  // File-specific actions
  /\b(save|download|export)\s+(as|to)\s+.*\.(csv|xlsx|json|xml)/i,

  // WordPress/CMS generation
  /\b(wordpress|cms)\s+(content|pages|posts)/i
];

/**
 * Code-related patterns
 */
const CODE_PATTERNS = [
  // Programming languages
  /\b(python|javascript|react|java|typescript|css|html|sql|bash)\b/i,

  // Code actions
  /\b(code|script|function|class|component|module|api)\b/i,
  /\b(debug|refactor|optimize|fix|implement|write)\s+(code|function|script)/i,

  // Code file extensions
  /\.(py|js|jsx|ts|tsx|css|html|java|cpp|rb|go|rs|php)\b/i,

  // Development terms
  /\b(algorithm|syntax|compile|execute|run|test)\b/i
];

/**
 * Extract entities from query text
 */
function extractEntities(queryText) {
  const entities = [];

  // Extract URLs (websites)
  const urlPattern = /(https?:\/\/|www\.)?([\w-]+\.(com|org|net|io|ai|co|uk))/gi;
  const urlMatches = queryText.match(urlPattern);
  if (urlMatches) {
    urlMatches.forEach(url => {
      entities.push({
        type: 'website',
        value: url.replace(/^(https?:\/\/|www\.)/, ''),
        confidence: 0.95
      });
    });
  }

  // Extract potential client/project names (capitalized words after entity keywords)
  const entityKeywords = /\b(client|project|website|company)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)/g;
  let match;
  while ((match = entityKeywords.exec(queryText)) !== null) {
    entities.push({
      type: match[1].toLowerCase(),
      value: match[2],
      confidence: 0.75
    });
  }

  // Extract file references
  const filePattern = /\b([a-zA-Z0-9_-]+\.(csv|xlsx|json|xml|pdf|doc|txt))\b/gi;
  const fileMatches = queryText.match(filePattern);
  if (fileMatches) {
    fileMatches.forEach(file => {
      entities.push({
        type: 'file',
        value: file,
        confidence: 0.85
      });
    });
  }

  return entities;
}

/**
 * Calculate pattern match confidence
 * FIXED: Better confidence scoring that doesn't dilute single strong matches
 */
function calculatePatternConfidence(queryText, patterns) {
  let matchCount = 0;
  let totalWeight = 0;
  let bestMatchIndex = -1;

  patterns.forEach((pattern, index) => {
    if (pattern.test(queryText)) {
      matchCount++;
      // Earlier patterns are more important
      const weight = patterns.length - index;
      totalWeight += weight;
      if (bestMatchIndex === -1) {
        bestMatchIndex = index;
      }
    }
  });

  if (matchCount === 0) return 0;

  // FIXED: Give high confidence to early patterns (greetings, explicit triggers)
  // First 3 patterns = 0.9-1.0 confidence (strong signals)
  // Middle patterns = 0.6-0.9 confidence
  // Later patterns = 0.3-0.6 confidence
  if (bestMatchIndex === 0) return 1.0;  // First pattern = perfect match
  if (bestMatchIndex <= 2) return 0.9;   // Top 3 patterns = very high confidence
  if (bestMatchIndex <= 5) return 0.75;  // Top 6 patterns = high confidence

  // For later patterns, use weighted calculation
  const maxPossibleWeight = patterns.length * (patterns.length + 1) / 2;
  const baseConfidence = totalWeight / maxPossibleWeight;

  // Boost confidence for multiple matches
  const matchBoost = Math.min(matchCount * 0.1, 0.3);

  return Math.min(baseConfidence + matchBoost, 1.0);
}

/**
 * Classify a user query into a type with confidence score
 *
 * @param {string} queryText - The user's query text
 * @returns {Object} Classification result with type, confidence, and entities
 */
export function classifyQuery(queryText) {
  if (!queryText || typeof queryText !== 'string') {
    return {
      type: QueryType.GENERAL,
      confidence: 0,
      entities: [],
      reasoning: 'Empty or invalid query'
    };
  }

  const trimmedQuery = queryText.trim();

  // Quick check for very short queries (likely greetings)
  if (trimmedQuery.length < 3) {
    return {
      type: QueryType.GENERAL,
      confidence: 0.3,
      entities: [],
      reasoning: 'Query too short'
    };
  }

  // Extract entities first (useful for multiple classifications)
  const entities = extractEntities(trimmedQuery);

  // Calculate confidence for each type
  const scores = {
    greeting: calculatePatternConfidence(trimmedQuery, GREETING_PATTERNS),
    factual: calculatePatternConfidence(trimmedQuery, FACTUAL_PATTERNS),
    web_search: calculatePatternConfidence(trimmedQuery, WEB_SEARCH_PATTERNS),
    entity: calculatePatternConfidence(trimmedQuery, ENTITY_PATTERNS) + (entities.length > 0 ? 0.3 : 0),
    file_gen: calculatePatternConfidence(trimmedQuery, FILE_GEN_PATTERNS),
    code: calculatePatternConfidence(trimmedQuery, CODE_PATTERNS)
  };

  // Boost entity score if we found entities
  if (entities.length > 0) {
    scores.entity = Math.min(scores.entity, 1.0);
  }

  // Find highest scoring type
  let maxScore = 0;
  let maxType = QueryType.GENERAL;
  let reasoning = 'No strong patterns detected';

  Object.entries(scores).forEach(([type, score]) => {
    if (score > maxScore) {
      maxScore = score;
      maxType = type;
      reasoning = `Matched ${type} patterns with confidence ${(score * 100).toFixed(1)}%`;
    }
  });

  // Require minimum confidence threshold
  const MIN_CONFIDENCE = 0.3;
  if (maxScore < MIN_CONFIDENCE) {
    maxType = QueryType.GENERAL;
    reasoning = `Confidence below threshold (${(maxScore * 100).toFixed(1)}% < ${(MIN_CONFIDENCE * 100).toFixed(1)}%)`;
  }

  // Handle hybrid queries (multiple high-scoring types)
  const hybridTypes = Object.entries(scores)
    .filter(([type, score]) => score > 0.5 && type !== maxType)
    .map(([type]) => type);

  if (hybridTypes.length > 0) {
    reasoning += ` (also matches: ${hybridTypes.join(', ')})`;
  }

  const result = {
    type: maxType,
    confidence: maxScore,
    entities: entities,
    hybridTypes: hybridTypes,
    reasoning: reasoning,
    scores: scores  // Include all scores for debugging
  };

  // DEBUG: Log classification for troubleshooting
  console.log(`CLASSIFIER_DEBUG: "${trimmedQuery}" → Type: ${maxType}, Confidence: ${(maxScore * 100).toFixed(1)}%`, scores);

  return result;
}

/**
 * Determine if query should use simple mode (fast path, minimal tokens)
 * FIXED: Lower threshold since greetings should always be simple
 */
export function shouldUseSimpleMode(classification) {
  return classification.type === QueryType.GREETING && classification.confidence > 0.5;
}

/**
 * Determine if query should trigger web search
 */
export function shouldUseWebSearch(classification) {
  return (classification.type === QueryType.FACTUAL && classification.confidence > 0.5) ||
         (classification.type === QueryType.WEB_SEARCH && classification.confidence > 0.6);
}

/**
 * Determine if query needs entity context injection
 */
export function needsEntityContext(classification) {
  return (classification.type === QueryType.ENTITY && classification.confidence > 0.4) ||
         classification.entities.length > 0;
}

/**
 * Determine if query is file generation request
 */
export function isFileGeneration(classification) {
  return classification.type === QueryType.FILE_GEN && classification.confidence > 0.5;
}

/**
 * Get suggested rules for query type
 */
export function getSuggestedRules(classification) {
  const ruleMap = {
    [QueryType.GREETING]: [], // No rules needed - simple mode
    [QueryType.FACTUAL]: [6], // qa_default only
    [QueryType.WEB_SEARCH]: [6], // qa_default with web search context
    [QueryType.ENTITY]: [6], // qa_default with entity context
    [QueryType.FILE_GEN]: [19, 22], // Generation templates only
    [QueryType.CODE]: [6, 17], // qa_default + CodeGen
    [QueryType.GENERAL]: [6] // qa_default
  };

  return ruleMap[classification.type] || [6];
}

/**
 * Format classification for logging/debugging
 */
export function formatClassification(classification) {
  return `Type: ${classification.type} (${(classification.confidence * 100).toFixed(1)}% confidence)
Entities: ${classification.entities.length > 0 ? classification.entities.map(e => `${e.type}:${e.value}`).join(', ') : 'none'}
Reasoning: ${classification.reasoning}`;
}

export default {
  QueryType,
  classifyQuery,
  shouldUseSimpleMode,
  shouldUseWebSearch,
  needsEntityContext,
  isFileGeneration,
  getSuggestedRules,
  formatClassification
};
