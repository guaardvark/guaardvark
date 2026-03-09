/**
 * Smart Context Builder
 *
 * Builds focused, relevant context for LLM requests by:
 * 1. Parsing code structure (fast, no LLM)
 * 2. Identifying relevant sections based on user query
 * 3. Assembling minimal context that answers the question
 *
 * This prevents timeouts on large files by sending only what's needed.
 */

import {
  parseCodeStructure,
  extractCodeSection,
  findRelevantElements,
  generateStructureSummary
} from './codeStructureParser';

// Maximum context size to send to LLM (in characters)
const MAX_CONTEXT_SIZE = 8000;
const IMPORTS_BUDGET = 1500;      // Reserve for imports/setup
const RELEVANT_BUDGET = 5000;     // Reserve for relevant sections
const STRUCTURE_BUDGET = 1000;    // Reserve for structure summary

/**
 * Build smart context for an LLM request
 * @param {string} code - Full source code
 * @param {string} language - Programming language
 * @param {string} userQuery - User's question/request
 * @param {object} options - Additional options
 * @returns {object} Smart context object
 */
export const buildSmartContext = (code, language, userQuery, options = {}) => {
  const {
    includeFullFile = false,  // Force include full file (for small files)
    maxSize = MAX_CONTEXT_SIZE,
    selectedText = null,      // User's selection takes priority
  } = options;

  // For small files, just return the full content
  if (!code || code.length <= maxSize * 0.6) {
    return {
      type: 'full',
      context: code || '',
      structure: null,
      relevantSections: [],
      truncated: false,
      stats: {
        originalSize: code?.length || 0,
        contextSize: code?.length || 0,
        compressionRatio: 1
      }
    };
  }

  // Parse the code structure
  const structure = parseCodeStructure(code, language);
  const lines = code.split('\n');

  // Build context parts
  const contextParts = [];
  let currentSize = 0;

  // 1. If there's selected text, prioritize it
  if (selectedText && selectedText.length > 0) {
    contextParts.push({
      type: 'selection',
      label: 'SELECTED CODE',
      content: selectedText,
      priority: 100
    });
    currentSize += selectedText.length;
  }

  // 2. Always include imports/setup (first ~50 lines or until first function/class)
  const firstCodeLine = Math.min(
    structure.functions?.[0]?.line || 999,
    structure.classes?.[0]?.line || 999,
    structure.components?.[0]?.line || 999,
    50
  );
  const importsSection = lines.slice(0, firstCodeLine - 1).join('\n');
  if (importsSection.length <= IMPORTS_BUDGET) {
    contextParts.push({
      type: 'imports',
      label: 'IMPORTS AND SETUP',
      content: importsSection,
      lines: `1-${firstCodeLine - 1}`,
      priority: 80
    });
    currentSize += importsSection.length;
  }

  // 3. Find relevant elements based on user query
  const relevantElements = findRelevantElements(structure, userQuery);
  const extractedSections = new Set(); // Track what we've already extracted

  relevantElements.slice(0, 5).forEach(element => {
    if (currentSize >= RELEVANT_BUDGET) return;

    const sectionKey = `${element.type}-${element.line}`;
    if (extractedSections.has(sectionKey)) return;

    const extracted = extractCodeSection(code, element.line, language);
    if (extracted.code && extracted.lineCount <= 100) { // Don't extract huge sections
      const label = element.type === 'method'
        ? `${element.className}.${element.name}`
        : `${element.type}: ${element.name}`;

      contextParts.push({
        type: 'relevant',
        label: label.toUpperCase(),
        content: extracted.code,
        lines: `${extracted.startLine}-${extracted.endLine}`,
        score: element.score,
        priority: 60 + element.score
      });
      currentSize += extracted.code.length;
      extractedSections.add(sectionKey);
    }
  });

  // 4. If query mentions specific keywords, search for them
  const queryKeywords = extractKeywords(userQuery);
  queryKeywords.forEach(keyword => {
    if (currentSize >= maxSize * 0.8) return;

    // Find lines containing this keyword
    lines.forEach((line, index) => {
      if (currentSize >= maxSize * 0.8) return;
      if (line.toLowerCase().includes(keyword.toLowerCase())) {
        // Extract surrounding context (5 lines before and after)
        const startLine = Math.max(0, index - 5);
        const endLine = Math.min(lines.length - 1, index + 5);
        const contextLines = lines.slice(startLine, endLine + 1).join('\n');

        const sectionKey = `keyword-${startLine}`;
        if (!extractedSections.has(sectionKey) && contextLines.length < 500) {
          contextParts.push({
            type: 'keyword_match',
            label: `CONTEXT FOR "${keyword}"`,
            content: contextLines,
            lines: `${startLine + 1}-${endLine + 1}`,
            priority: 40
          });
          currentSize += contextLines.length;
          extractedSections.add(sectionKey);
        }
      }
    });
  });

  // 5. Add structure summary
  const structureSummary = generateStructureSummary(structure);
  if (structureSummary && currentSize + structureSummary.length <= maxSize) {
    contextParts.push({
      type: 'structure',
      label: 'FILE STRUCTURE',
      content: structureSummary,
      priority: 30
    });
    currentSize += structureSummary.length;
  }

  // 6. If we still have room and found nothing relevant, include more of the file
  if (relevantElements.length === 0 && currentSize < maxSize * 0.5) {
    // Include first and last portions of the file
    const remainingBudget = maxSize - currentSize;
    const firstPortion = Math.floor(remainingBudget * 0.6);
    const lastPortion = Math.floor(remainingBudget * 0.3);

    if (code.length > firstPortion) {
      contextParts.push({
        type: 'beginning',
        label: 'FILE BEGINNING',
        content: code.substring(0, firstPortion) + '\n... [middle of file omitted] ...',
        priority: 20
      });

      contextParts.push({
        type: 'ending',
        label: 'FILE ENDING',
        content: '... [continuing from end of file] ...\n' + code.substring(code.length - lastPortion),
        priority: 10
      });
    }
  }

  // Sort by priority and build final context
  contextParts.sort((a, b) => b.priority - a.priority);

  // Assemble the context string
  const assembledContext = contextParts
    .map(part => {
      if (part.lines) {
        return `### ${part.label} (lines ${part.lines}):\n\`\`\`${language}\n${part.content}\n\`\`\``;
      }
      return `### ${part.label}:\n${part.content}`;
    })
    .join('\n\n');

  return {
    type: 'smart',
    context: assembledContext,
    structure: structure,
    relevantSections: relevantElements.slice(0, 5),
    contextParts: contextParts,
    truncated: true,
    stats: {
      originalSize: code.length,
      contextSize: assembledContext.length,
      compressionRatio: (assembledContext.length / code.length).toFixed(2),
      sectionsIncluded: contextParts.length,
      relevantElementsFound: relevantElements.length
    }
  };
};

/**
 * Extract important keywords from a user query
 */
const extractKeywords = (query) => {
  if (!query) return [];

  // Remove common words and extract meaningful terms
  const stopWords = new Set([
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'can', 'this', 'that', 'these',
    'those', 'what', 'which', 'who', 'whom', 'whose', 'where', 'when',
    'why', 'how', 'all', 'each', 'every', 'both', 'few', 'more', 'most',
    'some', 'any', 'no', 'not', 'only', 'own', 'same', 'so', 'than',
    'too', 'very', 'just', 'also', 'now', 'here', 'there', 'then',
    'once', 'always', 'never', 'sometimes', 'usually', 'often',
    'please', 'help', 'explain', 'show', 'tell', 'give', 'make',
    'code', 'function', 'file', 'line', 'error', 'bug', 'fix', 'add',
    'remove', 'change', 'update', 'modify', 'create', 'delete'
  ]);

  const words = query.toLowerCase()
    .replace(/[^\w\s]/g, ' ')
    .split(/\s+/)
    .filter(word => word.length > 2 && !stopWords.has(word));

  // Also extract camelCase/PascalCase identifiers
  const identifiers = query.match(/[a-zA-Z][a-zA-Z0-9]*(?:[A-Z][a-z0-9]*)*/g) || [];
  const validIdentifiers = identifiers.filter(id => id.length > 3 && !stopWords.has(id.toLowerCase()));

  return [...new Set([...words, ...validIdentifiers])].slice(0, 5);
};

/**
 * Build context for a chat message with smart truncation
 * @param {object} currentTab - Current editor tab
 * @param {string} userMessage - User's message
 * @param {object} codeContext - Editor context (selection, cursor, etc.)
 * @param {array} openTabs - Other open tabs
 * @returns {object} Formatted context for LLM
 */
export const buildChatContext = (currentTab, userMessage, codeContext = {}, openTabs = []) => {
  const language = currentTab?.language || 'javascript';
  const code = currentTab?.content || '';
  const selectedText = codeContext?.selectedText || null;

  // Build smart context for current file
  const smartCtx = buildSmartContext(code, language, userMessage, {
    selectedText,
    maxSize: 6000 // Leave room for other context
  });

  // Build prompt parts
  const parts = [];

  // Current file info
  parts.push(`## Current File: ${currentTab?.filePath || 'untitled'} (${language})`);
  parts.push(`Total lines: ${code.split('\n').length}, Size: ${code.length} chars`);

  // If we have a selection, highlight it
  if (selectedText) {
    parts.push(`\n## Selected Code (${selectedText.length} chars):`);
    parts.push(`\`\`\`${language}\n${selectedText}\n\`\`\``);
  }

  // Smart context from current file
  if (smartCtx.type === 'smart') {
    parts.push(`\n## Relevant Context (smart-extracted from ${smartCtx.stats.originalSize} chars → ${smartCtx.stats.contextSize} chars):`);
    parts.push(smartCtx.context);
  } else {
    parts.push(`\n## Full File Content:`);
    parts.push(`\`\`\`${language}\n${code}\n\`\`\``);
  }

  // Other open tabs (brief summary)
  const otherTabs = (openTabs || []).filter(tab =>
    tab.filePath !== currentTab?.filePath && tab.content
  );

  if (otherTabs.length > 0) {
    parts.push(`\n## Other Open Files (${otherTabs.length} tabs):`);
    otherTabs.slice(0, 3).forEach(tab => {
      const tabStructure = parseCodeStructure(tab.content, tab.language || 'javascript');
      const summary = generateStructureSummary(tabStructure);
      parts.push(`- **${tab.filePath || 'untitled'}** (${tab.language || 'unknown'}): ${summary.split('\n')[0]}`);
    });
  }

  return {
    formattedContext: parts.join('\n'),
    smartContext: smartCtx,
    hasSelection: Boolean(selectedText),
    otherTabsCount: otherTabs.length
  };
};

/**
 * Quick check if we should use smart context vs full context
 */
export const shouldUseSmartContext = (code, threshold = 5000) => {
  return code && code.length > threshold;
};

export default {
  buildSmartContext,
  buildChatContext,
  shouldUseSmartContext
};
