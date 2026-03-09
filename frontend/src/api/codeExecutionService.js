// frontend/src/api/codeExecutionService.js
// Code execution service for CodeEditorPage

import { BASE_URL, handleResponse } from "./apiClient";

const CODE_EXEC_BASE = "/api/code-execution";

/**
 * Execute Python code
 * @param {string} code - Python code to execute
 * @param {Object} options - Execution options
 * @returns {Promise<Object>} Execution result
 */
export const executePythonCode = async (code, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_EXEC_BASE}/python`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        code,
        timeout: options.timeout || 30,
        input: options.input || "",
        environment: options.environment || "default"
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      output: result.output || "",
      error: result.error || "",
      executionTime: result.executionTime || 0,
      exitCode: result.exitCode || 0,
      stdout: result.stdout || "",
      stderr: result.stderr || ""
    };
  } catch (error) {
    console.error("Python code execution failed:", error);
    return {
      success: false,
      error: error.message || "Failed to execute Python code",
      output: "",
      stdout: "",
      stderr: error.message || ""
    };
  }
};

/**
 * Execute JavaScript code
 * @param {string} code - JavaScript code to execute
 * @param {Object} options - Execution options
 * @returns {Promise<Object>} Execution result
 */
export const executeJavaScriptCode = async (code, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_EXEC_BASE}/javascript`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        code,
        timeout: options.timeout || 30,
        input: options.input || "",
        environment: options.environment || "node"
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      output: result.output || "",
      error: result.error || "",
      executionTime: result.executionTime || 0,
      stdout: result.stdout || "",
      stderr: result.stderr || ""
    };
  } catch (error) {
    console.error("JavaScript code execution failed:", error);
    return {
      success: false,
      error: error.message || "Failed to execute JavaScript code",
      output: "",
      stdout: "",
      stderr: error.message || ""
    };
  }
};

/**
 * Execute shell command
 * @param {string} command - Shell command to execute
 * @param {Object} options - Execution options
 * @returns {Promise<Object>} Execution result
 */
export const executeShellCommand = async (command, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_EXEC_BASE}/shell`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        command,
        timeout: options.timeout || 30,
        workingDirectory: options.workingDirectory || "/tmp",
        environment: options.environment || {}
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      output: result.output || "",
      error: result.error || "",
      executionTime: result.executionTime || 0,
      exitCode: result.exitCode || 0,
      stdout: result.stdout || "",
      stderr: result.stderr || ""
    };
  } catch (error) {
    console.error("Shell command execution failed:", error);
    return {
      success: false,
      error: error.message || "Failed to execute shell command",
      output: "",
      stdout: "",
      stderr: error.message || ""
    };
  }
};

/**
 * Format code using appropriate formatter
 * @param {string} code - Code to format
 * @param {string} language - Programming language
 * @param {Object} options - Formatting options
 * @returns {Promise<Object>} Formatting result
 */
export const formatCode = async (code, language, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_EXEC_BASE}/format`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        code,
        language,
        options: {
          indentSize: options.indentSize || 2,
          useTabs: options.useTabs || false,
          maxLineLength: options.maxLineLength || 80,
          ...options
        }
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      formattedCode: result.formattedCode || code,
      originalCode: code,
      language: result.language || language,
      changes: result.changes || []
    };
  } catch (error) {
    console.error("Code formatting failed:", error);
    return {
      success: false,
      error: error.message || "Failed to format code",
      formattedCode: code,
      originalCode: code
    };
  }
};

/**
 * Lint code for errors and warnings
 * @param {string} code - Code to lint
 * @param {string} language - Programming language
 * @param {Object} options - Linting options
 * @returns {Promise<Object>} Linting result
 */
export const lintCode = async (code, language, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_EXEC_BASE}/lint`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        code,
        language,
        options: {
          strict: options.strict || false,
          rules: options.rules || {},
          ...options
        }
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      errors: result.errors || [],
      warnings: result.warnings || [],
      suggestions: result.suggestions || [],
      score: result.score || 0,
      language: result.language || language
    };
  } catch (error) {
    console.error("Code linting failed:", error);
    return {
      success: false,
      error: error.message || "Failed to lint code",
      errors: [],
      warnings: [],
      suggestions: []
    };
  }
};

/**
 * Build project
 * @param {string} projectPath - Path to project
 * @param {Object} options - Build options
 * @returns {Promise<Object>} Build result
 */
export const buildProject = async (projectPath, options = {}) => {
  try {
    const response = await fetch(`${BASE_URL}${CODE_EXEC_BASE}/build`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        projectPath,
        options: {
          clean: options.clean || false,
          verbose: options.verbose || false,
          target: options.target || "default",
          ...options
        }
      }),
    });

    const result = await handleResponse(response);
    return {
      success: true,
      output: result.output || "",
      error: result.error || "",
      buildTime: result.buildTime || 0,
      artifacts: result.artifacts || [],
      exitCode: result.exitCode || 0
    };
  } catch (error) {
    console.error("Project build failed:", error);
    return {
      success: false,
      error: error.message || "Failed to build project",
      output: "",
      artifacts: []
    };
  }
};
