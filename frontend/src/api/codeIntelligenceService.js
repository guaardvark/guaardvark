// frontend/src/api/codeIntelligenceService.js
// Enhanced Code Intelligence Service - Direct integration with backend AI capabilities
// Provides code editing, analysis, and generation with Monaco editor integration

import { BASE_URL, handleResponse } from "./apiClient";
import { API_TIMEOUT_CODE_INTEL } from "../config/constants";

const CODE_INTELLIGENCE_BASE = "/code-intelligence";

/**
 * Analyze code for errors, improvements, and suggestions
 * @param {Object} context - Code context including file path, language, content
 * @param {string} customPrompt - Optional custom analysis prompt
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Analysis results with suggestions
 */
export const analyzeCodeIntelligent = async (context, customPrompt = null, rulesCutoff = false) => {
  try {
    if (!context?.content) {
      return {
        success: false,
        error: "No code content provided for analysis",
        analysis: "Please provide code content to analyze."
      };
    }

    const controller = new AbortController();
    // Increased timeout to 120 seconds for complex code analysis
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_CODE_INTEL);

    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/analyze`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        filePath: context.filePath || "untitled",
        language: context.language || "javascript",
        content: context.content || "",
        selectedText: context.selectedText || "",
        customPrompt: customPrompt,
        relatedFiles: context.relatedFiles || [],
        projectStructure: context.projectStructure || "",
        dependencies: context.dependencies || [],
        rulesCutoff: rulesCutoff
      }),
      signal: controller.signal
    });

    clearTimeout(timeoutId);
    const result = await handleResponse(response);
    return {
      success: true,
      analysis: result.data?.analysis || "Analysis completed successfully.",
      suggestions: result.data?.suggestions || [],
      errors: result.data?.errors || [],
      warnings: result.data?.warnings || [],
      filePath: result.data?.file_path || context.filePath,
      language: result.data?.language || context.language
    };
  } catch (error) {
    console.error("Code analysis failed:", error);

    if (error.name === 'AbortError') {
      return {
        success: false,
        error: "Analysis request timed out after 2 minutes",
        analysis: "The analysis request took too long. This may happen with very large files or complex code. Please try again with a smaller code selection or simpler query."
      };
    }

    return {
      success: false,
      error: error.message || "Unknown error occurred",
      analysis: "Analysis failed. Please check the backend connection and try again."
    };
  }
};

/**
 * Generate code based on natural language description
 * @param {string} description - What code to generate
 * @param {Object} context - Current code context
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Generated code with explanation
 */
export const generateCodeIntelligent = async (description, context = {}, rulesCutoff = false) => {
  try {
    if (!description || typeof description !== 'string' || description.trim().length === 0) {
      return {
        success: false,
        error: "No description provided for code generation",
        code: ""
      };
    }

    const controller = new AbortController();
    // Increased timeout to 120 seconds for complex code generation
    const timeoutId = setTimeout(() => controller.abort(), API_TIMEOUT_CODE_INTEL);

    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        description: description.trim(),
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        existingCode: context.existingCode || "",
        rulesCutoff: rulesCutoff
      }),
      signal: controller.signal
    });

    clearTimeout(timeoutId);
    const result = await handleResponse(response);
    return {
      success: true,
      code: result.data?.code || "",
      language: result.data?.language || context.language || "javascript",
      description: result.data?.description || description,
      explanation: result.data?.explanation || "Code generated successfully"
    };
  } catch (error) {
    console.error("Code generation failed:", error);

    if (error.name === 'AbortError') {
      return {
        success: false,
        error: "Code generation request timed out after 2 minutes. Please try a simpler request.",
        code: ""
      };
    }

    return {
      success: false,
      error: error.message || "Unknown error occurred during code generation",
      code: ""
    };
  }
};

/**
 * Edit existing code based on instructions
 * @param {string} originalCode - The code to edit
 * @param {string} editInstructions - Instructions for how to edit the code
 * @param {Object} context - Code context
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Edited code with explanation
 */
export const editCodeIntelligent = async (originalCode, editInstructions, context = {}, rulesCutoff = false) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/edit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        originalCode: originalCode,
        editInstructions: editInstructions,
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        rulesCutoff: rulesCutoff
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      editedCode: result.data.editedCode,
      originalCode: result.data.originalCode,
      instructions: result.data.instructions,
      language: result.data.language
    };
  } catch (error) {
    console.error("Code editing failed:", error);
    return {
      success: false,
      error: error.message,
      editedCode: originalCode // Return original if edit fails
    };
  }
};

/**
 * Explain selected code or provide documentation
 * @param {Object} context - Code context
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Code explanation
 */
export const explainCodeIntelligent = async (context, rulesCutoff = false) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/explain`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        content: context.content || "",
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        rulesCutoff: rulesCutoff
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      explanation: result.data.explanation,
      code: result.data.code,
      language: result.data.language
    };
  } catch (error) {
    console.error("Code explanation failed:", error);
    return {
      success: false,
      error: error.message,
      explanation: "Explanation failed. Please check the backend connection."
    };
  }
};

/**
 * Refactor code according to best practices
 * @param {Object} context - Code context
 * @param {string} refactorType - Type of refactoring (e.g., "optimize", "cleanup", "modernize")
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Refactored code
 */
export const refactorCodeIntelligent = async (context, refactorType = "optimize", rulesCutoff = false) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/refactor`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        content: context.content || "",
        refactorType: refactorType,
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        rulesCutoff: rulesCutoff
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      refactoredCode: result.data.refactoredCode,
      originalCode: result.data.originalCode,
      refactorType: result.data.refactorType,
      language: result.data.language
    };
  } catch (error) {
    console.error("Code refactoring failed:", error);
    return {
      success: false,
      error: error.message,
      refactoredCode: context.content || ""
    };
  }
};

/**
 * Generate unit tests for the provided code
 * @param {Object} context - Code context
 * @param {string} testFramework - Testing framework to use
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Generated tests
 */
export const generateTestsIntelligent = async (context, testFramework = "auto", rulesCutoff = false) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/generate-tests`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        content: context.content || "",
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        testFramework: testFramework,
        rulesCutoff: rulesCutoff
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      tests: result.data.tests,
      framework: result.data.framework,
      originalCode: result.data.originalCode,
      language: result.data.language
    };
  } catch (error) {
    console.error("Test generation failed:", error);
    return {
      success: false,
      error: error.message,
      tests: ""
    };
  }
};

/**
 * Get intelligent code completion suggestions
 * @param {Object} context - Code context with cursor position
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Completion suggestions
 */
export const getCodeCompletionIntelligent = async (context, rulesCutoff = false) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/completion`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        codeBefore: context.codeBefore || "",
        codeAfter: context.codeAfter || "",
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        cursorPosition: context.cursorPosition || {},
        rulesCutoff: rulesCutoff
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      suggestions: result.data.suggestions,
      language: result.data.language,
      position: result.data.position,
      mode: result.data.mode || "ai"
    };
  } catch (error) {
    console.error("Code completion failed:", error);
    return {
      success: false,
      error: error.message,
      suggestions: []
    };
  }
};

/**
 * Validate code for syntax and logical errors
 * @param {Object} context - Code context
 * @param {boolean} rulesCutoff - Whether to bypass rules system
 * @returns {Promise<Object>} Validation results
 */
export const validateCodeIntelligent = async (context, rulesCutoff = false) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/validate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        content: context.content || "",
        language: context.language || "javascript",
        filePath: context.filePath || "untitled",
        rulesCutoff: rulesCutoff
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      validation: result.data.validation,
      errors: result.data.errors || [],
      warnings: result.data.warnings || [],
      language: result.data.language,
      mode: result.data.mode || "ai"
    };
  } catch (error) {
    console.error("Code validation failed:", error);
    return {
      success: false,
      error: error.message,
      validation: "Validation failed",
      errors: [],
      warnings: []
    };
  }
};

/**
 * Read multiple files in parallel
 * @param {string[]} filePaths - Array of file paths to read
 * @returns {Promise<Array>} Array of {filePath, success, content, language, error} objects
 */
export const readMultipleFiles = async (filePaths) => {
  try {
    if (!Array.isArray(filePaths) || filePaths.length === 0) {
      return [];
    }

    const results = await Promise.allSettled(
      filePaths.map(async (path) => {
        const response = await fetch(`${BASE_URL}/api/files/read`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ filePath: path }),
        });

        const result = await handleResponse(response);
        return {
          filePath: path,
          success: true,
          content: result.content || "",
          language: result.language || "text",
          size: result.size || 0,
          lastModified: result.lastModified || null
        };
      })
    );

    return results.map((result, idx) => {
      if (result.status === 'fulfilled') {
        return result.value;
      } else {
        return {
          filePath: filePaths[idx],
          success: false,
          content: "",
          language: "text",
          error: result.reason?.message || "Failed to read file"
        };
      }
    });
  } catch (error) {
    console.error("Multi-file read failed:", error);
    return filePaths.map(path => ({
      filePath: path,
      success: false,
      content: "",
      language: "text",
      error: error.message || "Failed to read files"
    }));
  }
};

/**
 * Detect related files from import/require statements
 * @param {string} content - File content to analyze
 * @param {string} language - Programming language
 * @param {Array} openTabs - Currently open tabs with file paths
 * @returns {string[]} Array of related file paths
 */
export const detectRelatedFiles = (content, language, openTabs = []) => {
  if (!content || typeof content !== 'string') return [];
  
  const relatedPaths = new Set();
  
  // JavaScript/TypeScript imports
  if (language === 'javascript' || language === 'typescript') {
    // Match: import ... from './path' or import ... from '../path'
    const importRegex = /import\s+.*?\s+from\s+['"]([^'"]+)['"]/g;
    // Match: require('./path') or require("../path")
    const requireRegex = /require\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
    
    let match;
    while ((match = importRegex.exec(content)) !== null) {
      const importPath = match[1];
      // Try to resolve relative paths (simplified - could be enhanced)
      openTabs.forEach(tab => {
        if (tab.filePath && (tab.filePath.includes(importPath) || importPath.includes(tab.filePath.split('/').pop()))) {
          relatedPaths.add(tab.filePath);
        }
      });
    }
    
    while ((match = requireRegex.exec(content)) !== null) {
      const requirePath = match[1];
      openTabs.forEach(tab => {
        if (tab.filePath && (tab.filePath.includes(requirePath) || requirePath.includes(tab.filePath.split('/').pop()))) {
          relatedPaths.add(tab.filePath);
        }
      });
    }
  }
  
  // Python imports
  if (language === 'python') {
    const importRegex = /import\s+([^\s]+)|from\s+([^\s]+)\s+import/g;
    let match;
    while ((match = importRegex.exec(content)) !== null) {
      const importPath = match[1] || match[2];
      openTabs.forEach(tab => {
        if (tab.filePath && tab.filePath.includes(importPath.replace('.', '/'))) {
          relatedPaths.add(tab.filePath);
        }
      });
    }
  }
  
  return Array.from(relatedPaths);
};

/**
 * Check if code intelligence service is available
 * @returns {Promise<Object>} Health status
 */
export const checkCodeIntelligenceHealth = async () => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_INTELLIGENCE_BASE}/health`, {
      method: "GET",
    });

    const result = await handleResponse(response);
    return {
      success: true,
      status: result.data.status,
      mode: result.data.mode || "fully_functional_offline",
      activeModel: result.data.active_model,
      llmAvailable: result.data.llm_available,
      chatAvailable: result.data.chat_available,
      timestamp: result.data.timestamp,
      endpoints: result.data.endpoints || [],
      capabilities: result.data.capabilities || []
    };
  } catch (error) {
    console.error("Health check failed:", error);
    return {
      success: false,
      error: error.message,
      status: "unhealthy"
    };
  }
};

/**
 * Search project's indexed code for relevant context (RAG)
 * Used to provide project-wide context to the coding agent
 * @param {string|number} projectId - The project ID to search within
 * @param {string} query - The search query (natural language)
 * @param {number} limit - Maximum number of results (default: 5)
 * @returns {Promise<Object>} Search results with relevant code snippets
 */
export const searchProjectCode = async (projectId, query, limit = 5) => {
  if (!projectId || !query) {
    return {
      success: false,
      sources: [],
      error: !projectId ? "No project ID provided" : "No query provided"
    };
  }

  try {
    // First, get documents for this project
    const docsResponse = await fetch(`${BASE_URL}/docs?project_id=${projectId}&per_page=100`);
    const docsResult = await handleResponse(docsResponse);
    const projectDocs = docsResult?.documents || docsResult?.items || [];

    if (projectDocs.length === 0) {
      return {
        success: true,
        sources: [],
        message: "No indexed documents found for this project"
      };
    }

    // Then perform semantic search
    const searchResponse = await fetch(`${BASE_URL}/search/semantic`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query })
    });
    const searchResult = await handleResponse(searchResponse);

    if (!searchResult || searchResult.error) {
      // Fallback: return raw document content from project
      const projectDocIds = new Set(projectDocs.map(d => d.id));
      return {
        success: true,
        sources: projectDocs.slice(0, limit).map(doc => ({
          filename: doc.filename || doc.name,
          content: doc.content?.substring(0, 2000) || "",
          type: doc.type,
          id: doc.id
        })),
        fallback: true,
        message: "Using document content (semantic search unavailable)"
      };
    }

    // Filter search results to only include documents from this project
    const projectDocIds = new Set(projectDocs.map(d => d.id));
    const filteredSources = (searchResult.sources || [])
      .filter(source => {
        // Check if source belongs to this project
        const sourceId = source.doc_id || source.document_id || source.id;
        return projectDocIds.has(sourceId) || projectDocIds.has(parseInt(sourceId));
      })
      .slice(0, limit);

    // If no filtered results, return top project docs as fallback
    if (filteredSources.length === 0) {
      return {
        success: true,
        sources: projectDocs.slice(0, limit).map(doc => ({
          filename: doc.filename || doc.name,
          content: doc.content?.substring(0, 2000) || "",
          type: doc.type,
          id: doc.id
        })),
        fallback: true,
        answer: searchResult.answer,
        message: "No project-specific matches; showing project files"
      };
    }

    return {
      success: true,
      sources: filteredSources,
      answer: searchResult.answer,
      message: `Found ${filteredSources.length} relevant code snippets`
    };
  } catch (error) {
    console.error("Project code search failed:", error);
    return {
      success: false,
      sources: [],
      error: error.message
    };
  }
};

// Export all functions as default
export default {
  analyzeCodeIntelligent,
  generateCodeIntelligent,
  editCodeIntelligent,
  explainCodeIntelligent,
  refactorCodeIntelligent,
  generateTestsIntelligent,
  getCodeCompletionIntelligent,
  validateCodeIntelligent,
  checkCodeIntelligenceHealth,
  readMultipleFiles,
  detectRelatedFiles,
  searchProjectCode
};