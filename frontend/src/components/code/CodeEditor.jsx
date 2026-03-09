// frontend/src/components/code/CodeEditor.jsx
// Monaco Editor wrapper component for Cursor-like coding experience
// Integrates with existing LLM009.2 infrastructure

import React, { useRef, useCallback, useEffect, useState } from "react";
import { Box, Paper, Typography, IconButton, Tooltip, Alert } from "@mui/material";
import {
  Save as SaveIcon,
  PlayArrow as RunIcon,
  BugReport as DebugIcon,
  Psychology as AiIcon,
} from "@mui/icons-material";
import Editor from "@monaco-editor/react";
import { useTheme } from "@mui/material/styles";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";

// Language mappings for Monaco Editor
const LANGUAGE_MAPPINGS = {
  ".js": "javascript",
  ".jsx": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".py": "python",
  ".java": "java",
  ".c": "c",
  ".cpp": "cpp",
  ".cs": "csharp",
  ".go": "go",
  ".rs": "rust",
  ".php": "php",
  ".rb": "ruby",
  ".swift": "swift",
  ".kt": "kotlin",
  ".scala": "scala",
  ".html": "html",
  ".css": "css",
  ".scss": "scss",
  ".json": "json",
  ".xml": "xml",
  ".yaml": "yaml",
  ".yml": "yaml",
  ".md": "markdown",
  ".sql": "sql",
  ".sh": "shell",
  ".dockerfile": "dockerfile",
};

// Default file templates
const FILE_TEMPLATES = {
  javascript: `// JavaScript file
console.log("Hello, World!");
`,
  python: `# Python file
print("Hello, World!")
`,
  typescript: `// TypeScript file
console.log("Hello, World!");
`,
  java: `// Java file
public class Main {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
    }
}
`,
  cpp: `// C++ file
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
`,
};

const CodeEditor = ({
  filePath = null,
  initialContent = "",
  language = "javascript",
  readOnly = false,
  onContentChange = null,
  onSave = null,
  onAiAssist = null,
  height = "400px",
  showToolbar = true,
  fontSize = 14,
  wordWrap = "on",
  minimap = true,
}) => {
  const theme = useTheme();
  const editorRef = useRef(null);
  const monacoRef = useRef(null);
  const [content, setContent] = useState(initialContent);
  const [isModified, setIsModified] = useState(false);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  const { startProcess, updateProcess, completeProcess, errorProcess } = useUnifiedProgress();

  // Detect language from file path
  const getLanguageFromPath = useCallback((path) => {
    if (!path) return language;
    const extension = path.substring(path.lastIndexOf("."));
    return LANGUAGE_MAPPINGS[extension] || "plaintext";
  }, [language]);

  const [detectedLanguage, setDetectedLanguage] = useState(() =>
    getLanguageFromPath(filePath) || language
  );

  // Monaco Editor configuration
  const editorOptions = {
    fontSize,
    wordWrap,
    minimap: { enabled: minimap },
    scrollBeyondLastLine: false,
    automaticLayout: true,
    readOnly,
    theme: theme.palette.mode === "dark" ? "vs-dark" : "vs-light",
    bracketPairColorization: { enabled: true },
    guides: {
      bracketPairs: true,
      bracketPairsHorizontal: true,
      highlightActiveBracketPair: true,
    },
    suggest: {
      showKeywords: true,
      showSnippets: true,
      showFunctions: true,
      showVariables: true,
    },
    quickSuggestions: true,
    suggestOnTriggerCharacters: true,
    acceptSuggestionOnEnter: "on",
    tabCompletion: "on",
    renderWhitespace: "selection",
    renderControlCharacters: true,
    cursorStyle: "line",
    cursorBlinking: "blink",
    smoothScrolling: true,
    mouseWheelZoom: true,
  };

  // Handle editor mount
  const handleEditorDidMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;

    // Configure code completion and suggestions
    monaco.languages.registerCompletionItemProvider(detectedLanguage, {
      provideCompletionItems: (model, position) => {
        // Future: Integrate with Ollama for AI-powered completions
        const suggestions = [];

        // Basic keyword suggestions for now
        const keywords = {
          javascript: ["const", "let", "var", "function", "class", "import", "export"],
          python: ["def", "class", "import", "from", "if", "elif", "else", "for", "while"],
          typescript: ["interface", "type", "enum", "namespace", "const", "let", "function"],
        };

        const langKeywords = keywords[detectedLanguage] || [];
        langKeywords.forEach((keyword) => {
          suggestions.push({
            label: keyword,
            kind: monaco.languages.CompletionItemKind.Keyword,
            insertText: keyword,
          });
        });

        return { suggestions };
      },
    });

    // Add keyboard shortcuts
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      handleSave();
    });

    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, () => {
      handleAiAssist();
    });

    // Focus editor
    editor.focus();
  }, [detectedLanguage]);

  // Handle content changes
  const handleEditorChange = useCallback((value) => {
    setContent(value || "");
    setIsModified(value !== initialContent);

    if (onContentChange) {
      onContentChange(value || "");
    }
  }, [initialContent, onContentChange]); // Fix: Dependencies are correct, no stale closure issue here

  // Handle save action
  const handleSave = useCallback(async () => {
    if (!onSave || !isModified) return;

    try {
      setIsLoading(true);
      const processId = startProcess("file-save", "Saving file...", "file_generation");

      await onSave(content, filePath);

      setIsModified(false);
      setError(null);
      completeProcess(processId, "File saved successfully");
    } catch (err) {
      setError(err.message || "Failed to save file");
      errorProcess("file-save", err.message || "Save failed");
    } finally {
      setIsLoading(false);
    }
  }, [content, filePath, isModified, onSave, startProcess, completeProcess, errorProcess]);

  // Handle AI assist action
  const handleAiAssist = useCallback(async () => {
    if (!onAiAssist || !editorRef.current) return;

    try {
      setIsLoading(true);
      const processId = startProcess("ai-assist", "Getting AI assistance...", "llm_processing");

      const selection = editorRef.current.getSelection();
      const selectedText = editorRef.current.getModel().getValueInRange(selection);
      const context = {
        filePath,
        language: detectedLanguage,
        selectedText,
        fullContent: content,
        cursorPosition: editorRef.current.getPosition(),
      };

      await onAiAssist(context);
      completeProcess(processId, "AI assistance completed");
    } catch (err) {
      setError(err.message || "AI assistance failed");
      errorProcess("ai-assist", err.message || "AI assistance failed");
    } finally {
      setIsLoading(false);
    }
  }, [content, detectedLanguage, filePath, onAiAssist, startProcess, completeProcess, errorProcess]);

  // Handle run/debug action
  const handleRun = useCallback(() => {
    // Future: Integrate with code execution service
    console.log("Run code:", { filePath, language: detectedLanguage, content });
  }, [content, detectedLanguage, filePath]);

  // Update language when file path changes
  useEffect(() => {
    setDetectedLanguage(getLanguageFromPath(filePath) || language);
  }, [filePath, language, getLanguageFromPath]);

  // Set default content for new files
  useEffect(() => {
    if (!initialContent && FILE_TEMPLATES[detectedLanguage]) {
      setContent(FILE_TEMPLATES[detectedLanguage]);
      if (onContentChange) {
        onContentChange(FILE_TEMPLATES[detectedLanguage]);
      }
    }
  }, [detectedLanguage, initialContent, onContentChange]);

  return (
    <Paper elevation={2} sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Toolbar */}
      {showToolbar && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            px: 2,
            py: 1,
            borderBottom: 1,
            borderColor: "divider",
            minHeight: 48,
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <Typography variant="body2" color="text.secondary">
              {filePath || "Untitled"}
            </Typography>
            {isModified && (
              <Typography variant="body2" color="warning.main">
                •
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary">
              {detectedLanguage.toUpperCase()}
            </Typography>
          </Box>

          <Box sx={{ display: "flex", gap: 1 }}>
            <Tooltip title="AI Assist (Ctrl+K)">
              <span>
                <IconButton
                  size="small"
                  onClick={handleAiAssist}
                  disabled={isLoading || !onAiAssist}
                  color="primary"
                >
                  <AiIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>

            <Tooltip title="Run Code">
              <span>
                <IconButton
                  size="small"
                  onClick={handleRun}
                  disabled={isLoading}
                  color="success"
                >
                  <RunIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>

            <Tooltip title="Save (Ctrl+S)">
              <span>
                <IconButton
                  size="small"
                  onClick={handleSave}
                  disabled={!isModified || isLoading || !onSave}
                  color={isModified ? "primary" : "default"}
                >
                  <SaveIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        </Box>
      )}

      {/* Error Alert */}
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ m: 1 }}>
          {error}
        </Alert>
      )}

      {/* Monaco Editor */}
      <Box sx={{ flexGrow: 1, position: "relative" }}>
        <Editor
          height={height}
          language={detectedLanguage}
          value={content}
          options={editorOptions}
          onMount={handleEditorDidMount}
          onChange={handleEditorChange}
          loading={
            <Box
              sx={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                height: "100%",
              }}
            >
              <Typography color="text.secondary">Loading editor...</Typography>
            </Box>
          }
        />
      </Box>
    </Paper>
  );
};

export default CodeEditor;