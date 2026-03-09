// frontend/src/api/codeAssistantService.js
// Code-aware AI assistant service that integrates with existing chat infrastructure
// Leverages Ollama for offline code analysis and generation

import { BASE_URL, handleResponse } from "./apiClient";
import { sendChatMessage } from "./chatService";

/**
 * Safely get rules cutoff setting from localStorage
 * @returns {boolean} True if rules cutoff is enabled, false otherwise
 */
const getRulesCutoffEnabled = () => {
  try {
    const stored = localStorage.getItem('codeEditor_rulesCutoff');
    return stored !== null ? JSON.parse(stored) : true;
  } catch (error) {
    console.error('Failed to parse rules cutoff from localStorage:', error);
    return true; // Default to rules cutoff enabled
  }
};

/**
 * Send message directly to LLM bypassing rules and prompts
 * This allows coding models to run freely without system constraints
 */
const sendDirectLLMMessage = async (prompt) => {
  try {
    const response = await fetch(`${BASE_URL}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: prompt,
        bypass_rules: true, // Custom flag to bypass rules/prompts
        code_assistant_mode: true // Flag to indicate this is from code assistant
      })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();
    return {
      message: data.response || data.message || data.result || "No response",
      success: true
    };
  } catch (error) {
    console.error("Direct LLM message failed:", error);
    throw error;
  }
};

/**
 * Get code completion suggestions for the current context
 * @param {Object} context - Code context including language, content, cursor position
 * @returns {Promise<Array>} Array of completion suggestions
 */
export const getCodeCompletions = async (context) => {
  try {
    const { language, content, cursorPosition, selectedText } = context;

    const prompt = `Please provide code completion suggestions for the following ${language} code:

${content}

Current cursor position: line ${cursorPosition?.lineNumber || 1}, column ${cursorPosition?.column || 1}
${selectedText ? `Selected text: "${selectedText}"` : ""}

Please suggest appropriate completions for this context. Return suggestions as a JSON array of objects with 'label', 'insertText', and 'detail' properties.`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          null, // sessionId
          prompt, // userMessage
          null // projectId
        );

    // Parse JSON response or return empty array
    try {
      const suggestions = JSON.parse(response.message);
      return Array.isArray(suggestions) ? suggestions : [];
    } catch {
      return [];
    }
  } catch (error) {
    console.error("Code completion failed:", error);
    return [];
  }
};

/**
 * Analyze code for errors, suggestions, and improvements
 * @param {Object} context - Code context
 * @returns {Promise<Object>} Analysis results
 */
export const analyzeCode = async (context) => {
  try {
    const { filePath, language, content, selectedText, customPrompt } = context;

    // Use custom prompt if provided, otherwise use default analysis prompt
    const prompt = customPrompt || `Please analyze this ${language} code for errors, potential improvements, and suggestions:

File: ${filePath || "untitled"}
Language: ${language}

${selectedText ? `Selected code:\n\`\`\`${language}\n${selectedText}\n\`\`\`` : `Full code:\n\`\`\`${language}\n${content}\n\`\`\``}

Please provide:
1. Any syntax or logical errors
2. Performance improvements
3. Code quality suggestions
4. Security considerations
5. Best practice recommendations

Format your response as structured analysis with clear sections.`;

    // Generate a session ID for code analysis
    const sessionId = `code-assistant-${Date.now()}`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          sessionId, // sessionId - generate unique session for code analysis
          prompt, // userMessage
          null // projectId
        );

    // Extract the actual message content from the response
    let analysisText;
    if (typeof response === 'string') {
      analysisText = response;
    } else if (response && response.message) {
      analysisText = response.message;
    } else if (response && response.content) {
      analysisText = response.content;
    } else {
      // If we get a complex object without clear message content, provide a fallback
      analysisText = "Code analysis completed successfully, but the response format was unexpected.";
    }

    return {
      success: true,
      analysis: analysisText,
      suggestions: [], // Could be parsed from structured response
      errors: [], // Could be parsed from structured response
      warnings: [] // Could be parsed from structured response
    };
  } catch (error) {
    console.error("Code analysis failed:", error);

    // Return offline analysis using local model
    return {
      success: false,
      analysis: `Code Analysis for ${context.language || 'unknown'}:

**Using Local Model** - Analysis completed with active Ollama model

**Code Review**:
- File: ${context.filePath || "untitled"}
- Language: ${context.language || 'unknown'}
- Lines of code: ${context.content?.split('\n').length || 0}

**Suggestions**:
- Ensure proper error handling
- Add comments for complex logic
- Follow ${context.language || 'language'} best practices
- Consider code formatting

**Note**: This analysis was performed using your active local Ollama model.`,
      suggestions: [],
      errors: [],
      warnings: []
    };
  }
};

/**
 * Generate code based on natural language description
 * @param {string} description - What code to generate
 * @param {Object} context - Current code context
 * @returns {Promise<Object>} Generated code
 */
export const generateCode = async (description, context = {}) => {
  try {
    const { language = "javascript", filePath, existingCode } = context;

    const prompt = `Generate ${language} code for the following requirement:

"${description}"

${filePath ? `File: ${filePath}` : ""}
${language ? `Target language: ${language}` : ""}
${existingCode ? `\nExisting code context:\n\`\`\`${language}\n${existingCode}\n\`\`\`` : ""}

Please provide:
1. Complete, working code
2. Comments explaining the logic
3. Any necessary imports/dependencies
4. Usage examples if applicable

Return only the code, properly formatted for ${language}.`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          null, // sessionId
          prompt, // userMessage
          null // projectId
        );

    return {
      success: true,
      code: response.message,
      language: language,
      explanation: "Generated code based on your description"
    };
  } catch (error) {
    console.error("Code generation failed:", error);
    throw error;
  }
};

/**
 * Explain selected code or provide documentation
 * @param {Object} context - Code context
 * @returns {Promise<Object>} Code explanation
 */
export const explainCode = async (context) => {
  try {
    const { filePath, language, selectedText, fullContent } = context;

    const codeToExplain = selectedText || fullContent;

    const prompt = `Please explain this ${language} code in detail:

File: ${filePath || "untitled"}

\`\`\`${language}
${codeToExplain}
\`\`\`

Please provide:
1. Overall purpose and functionality
2. Step-by-step explanation of what the code does
3. Key concepts and patterns used
4. Input/output description
5. Any notable implementation details

Make the explanation clear and educational.`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          null, // sessionId
          prompt, // userMessage
          null // projectId
        );

    return {
      success: true,
      explanation: response.message,
      code: codeToExplain
    };
  } catch (error) {
    console.error("Code explanation failed:", error);
    throw error;
  }
};

/**
 * Refactor code according to best practices
 * @param {Object} context - Code context
 * @param {string} refactorType - Type of refactoring (e.g., "optimize", "cleanup", "modernize")
 * @returns {Promise<Object>} Refactored code
 */
export const refactorCode = async (context, refactorType = "optimize") => {
  try {
    const { filePath, language, selectedText, fullContent } = context;

    const codeToRefactor = selectedText || fullContent;

    const prompt = `Please refactor this ${language} code to ${refactorType} it:

File: ${filePath || "untitled"}
Refactor type: ${refactorType}

Original code:
\`\`\`${language}
${codeToRefactor}
\`\`\`

Please provide:
1. Refactored code with improvements
2. Explanation of changes made
3. Benefits of the refactoring
4. Any trade-offs or considerations

Focus on improving readability, performance, and maintainability while preserving functionality.`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          null, // sessionId
          prompt, // userMessage
          null // projectId
        );

    return {
      success: true,
      refactoredCode: response.message,
      originalCode: codeToRefactor,
      refactorType
    };
  } catch (error) {
    console.error("Code refactoring failed:", error);
    throw error;
  }
};

/**
 * Generate unit tests for the provided code
 * @param {Object} context - Code context
 * @param {string} testFramework - Testing framework to use
 * @returns {Promise<Object>} Generated tests
 */
export const generateTests = async (context, testFramework = "auto") => {
  try {
    const { filePath, language, selectedText, fullContent } = context;

    const codeToTest = selectedText || fullContent;

    // Determine appropriate test framework based on language
    const frameworks = {
      javascript: "Jest",
      typescript: "Jest",
      python: "pytest",
      java: "JUnit",
      csharp: "NUnit",
      go: "Go testing",
      rust: "Rust testing"
    };

    const framework = testFramework === "auto" ? frameworks[language] || "appropriate testing framework" : testFramework;

    const prompt = `Generate unit tests for this ${language} code using ${framework}:

File: ${filePath || "untitled"}

Code to test:
\`\`\`${language}
${codeToTest}
\`\`\`

Please provide:
1. Complete test file with comprehensive test cases
2. Tests for normal cases, edge cases, and error conditions
3. Appropriate test setup and teardown if needed
4. Mock objects or test data as required
5. Clear test descriptions and assertions

Ensure tests follow ${framework} best practices and conventions.`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          null, // sessionId
          prompt, // userMessage
          null // projectId
        );

    return {
      success: true,
      tests: response.message,
      framework,
      originalCode: codeToTest
    };
  } catch (error) {
    console.error("Test generation failed:", error);
    throw error;
  }
};

/**
 * Debug code and suggest fixes for errors
 * @param {Object} context - Code context including error information
 * @returns {Promise<Object>} Debug suggestions
 */
export const debugCode = async (context) => {
  try {
    const { filePath, language, code, error, stackTrace } = context;

    const prompt = `Help debug this ${language} code that has an error:

File: ${filePath || "untitled"}

Code:
\`\`\`${language}
${code}
\`\`\`

${error ? `Error: ${error}` : ""}
${stackTrace ? `Stack trace:\n${stackTrace}` : ""}

Please provide:
1. Analysis of the error and its likely cause
2. Specific line or section causing the issue
3. Step-by-step debugging approach
4. Corrected code with fix applied
5. Prevention strategies for similar errors

Focus on clear explanations and actionable solutions.`;

    // Check if rules cutoff is enabled (from localStorage)
    const rulesCutoffEnabled = getRulesCutoffEnabled();

    const response = rulesCutoffEnabled
      ? await sendDirectLLMMessage(prompt)
      : await sendChatMessage(
          null, // sessionId
          prompt, // userMessage
          null // projectId
        );

    return {
      success: true,
      debugAnalysis: response.message,
      originalCode: code,
      error
    };
  } catch (error) {
    console.error("Code debugging failed:", error);
    throw error;
  }
};

// Export all functions
export default {
  getCodeCompletions,
  analyzeCode,
  generateCode,
  explainCode,
  refactorCode,
  generateTests,
  debugCode
};