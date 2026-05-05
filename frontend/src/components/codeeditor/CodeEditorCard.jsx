// frontend/src/components/codeeditor/CodeEditorCard.jsx
// Main Monaco editor card with tab support

import React, { useState, useCallback, useRef, useEffect, useImperativeHandle } from "react";
import {
  Box,
  Tabs,
  Tab,
  Typography,
  IconButton,
  Tooltip,
  Alert,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  useTheme,
} from "@mui/material";
import {
  Close as CloseIcon,
  Save as SaveIcon,
  PlayArrow as PlayArrowIcon,
  Refresh as RefreshIcon,
  DriveFileRenameOutline,
  MoreVert,
  Preview as PreviewIcon,
  Add as AddIcon,
  Warning as WarningIcon,
} from "@mui/icons-material";
import Editor from "@monaco-editor/react";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import * as codeIntelligenceService from "../../api/codeIntelligenceService";
import * as fileOperationsService from "../../api/fileOperationsService";
import { getLanguageFromFilename } from "../../utils/languageDetector";

const CodeEditorCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      openTabs,
      setOpenTabs,
      activeTabIndex,
      setActiveTabIndex,
      onEditorContextChange,
      onChatAction,
      ...props
    },
    ref
  ) => {
    const editorRef = useRef(null);
    const contextUpdateTimeoutRef = useRef(null);
    const [editorError, setEditorError] = useState(null);
    const [contextMenu, setContextMenu] = useState(null);
    const [renameDialog, setRenameDialog] = useState({ open: false, tabIndex: null });
    const [renameValue, setRenameValue] = useState("");
    const [previewDialogOpen, setPreviewDialogOpen] = useState(false);
    const { startProcess, completeProcess, errorProcess } = useUnifiedProgress();
    const theme = useTheme();

    // Track initialization but don't force create tabs
    const hasInitialized = useRef(false);
    useEffect(() => {
      if (!hasInitialized.current) {
        hasInitialized.current = true;
        // No longer force create Untitled tab - user can add tabs via + button
      }
    }, []);

    const currentTab = openTabs && activeTabIndex >= 0 && activeTabIndex < openTabs.length
      ? openTabs[activeTabIndex]
      : openTabs && openTabs.length > 0 ? openTabs[0] : null;

    // Create preview HTML with theme styles
    const getPreviewContent = useCallback(() => {
      if (!currentTab?.content) return '';
      
      const bgColor = theme.palette.background.default || theme.palette.background.paper;
      const textColor = theme.palette.text.primary;
      
      return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {
      background-color: ${bgColor};
      color: ${textColor};
      font-family: ${theme.typography.fontFamily || 'inherit'};
      margin: 0;
      padding: 20px;
      line-height: 1.6;
    }
    a {
      color: ${theme.palette.primary.main};
    }
    a:hover {
      color: ${theme.palette.primary.light};
    }
    h1, h2, h3, h4, h5, h6 {
      color: ${textColor};
    }
  </style>
</head>
<body>
${currentTab.content}
</body>
</html>
      `;
    }, [currentTab?.content, theme]);

    // Function to update editor context with debouncing
    const updateEditorContext = useCallback(() => {
      if (!editorRef.current || !onEditorContextChange) return;

      const editor = editorRef.current;
      const model = editor.getModel();
      if (!model) return;

      const selection = editor.getSelection();
      const position = editor.getPosition();
      
      let selectedText = '';
      let selectionRange = null;
      
      if (selection && !selection.isEmpty()) {
        selectedText = model.getValueInRange(selection);
        selectionRange = {
          startLineNumber: selection.startLineNumber,
          startColumn: selection.startColumn,
          endLineNumber: selection.endLineNumber,
          endColumn: selection.endColumn
        };
      }

      const context = {
        selectedText,
        cursorPosition: position ? {
          line: position.lineNumber,
          column: position.column
        } : { line: 0, column: 0 },
        selectionRange,
        modelVersion: model.getVersionId()
      };

      // Debounce context updates to prevent excessive re-renders
      if (contextUpdateTimeoutRef.current) {
        clearTimeout(contextUpdateTimeoutRef.current);
      }
      
      contextUpdateTimeoutRef.current = setTimeout(() => {
        onEditorContextChange(context);
      }, 150); // 150ms debounce
    }, [onEditorContextChange]);

    const handleEditorDidMount = useCallback((editor, monaco) => {
      editorRef.current = editor;

      // Configure editor with AI enhancements
      editor.updateOptions({
        fontSize: 14,
        wordWrap: "on",
        minimap: { enabled: true },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        suggest: {
          showKeywords: true,
          showSnippets: true,
          showColors: true,
          showFiles: true,
          showReferences: true,
          showWords: true,
          showTypeParameters: true,
          showIcons: true,
          showMethods: true,
          showFunctions: true,
          showConstructors: true,
          showFields: true,
          showVariables: true,
          showClasses: true,
          showStructs: true,
          showInterfaces: true,
          showModules: true,
          showProperties: true,
          showEvents: true,
          showOperators: true,
          showUnits: true,
          showValues: true,
          showConstants: true,
          showEnums: true,
          showEnumMembers: true,
          showUser: true,
          showText: true,
        },
        quickSuggestions: {
          other: true,
          comments: false,
          strings: false
        },
        suggestOnTriggerCharacters: true,
        acceptSuggestionOnEnter: "on",
        tabCompletion: "on",
      });

      // Add keyboard shortcuts
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        handleSave();
      });

      // Add AI assistance shortcut
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Space, () => {
        handleAIAssist();
      });

      // Add hover provider for AI explanations
      monaco.languages.registerHoverProvider(currentTab?.language || 'javascript', {
        provideHover: async (model, position) => {
          const word = model.getWordAtPosition(position);
          if (!word) return null;

          const line = model.getLineContent(position.lineNumber);
          const context = {
            content: line,
            language: currentTab?.language || 'javascript',
            filePath: currentTab?.filePath || 'untitled',
            word: word.word,
            position: position
          };

          try {
            const explanation = await codeIntelligenceService.analyzeCodeIntelligent(context, `Explain this ${currentTab?.language || 'javascript'} code: ${word.word}`);
            if (explanation.success) {
              return {
                range: new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn),
                contents: [
                  { value: `**${word.word}**` },
                  { value: explanation.analysis || 'AI explanation not available' }
                ]
              };
            }
          } catch (error) {
            console.error('Hover provider error:', error);
          }

          return null;
        }
      });

      // Add code action provider for AI suggestions
      monaco.languages.registerCodeActionProvider(currentTab?.language || 'javascript', {
        provideCodeActions: async (model, range, context) => {
          const actions = [];

          if (context.markers && context.markers.length > 0) {
            actions.push({
              title: 'Fix with AI',
              kind: 'quickfix',
              edit: {
                edits: [{
                  resource: model.uri,
                  edit: {
                    range: range,
                    text: '// AI fix would be applied here'
                  }
                }]
              }
            });
          }

          actions.push({
            title: 'Explain Code',
            kind: 'refactor',
            command: {
              id: 'ai.explain',
              title: 'Explain Code',
              arguments: [model, range]
            }
          });

          actions.push({
            title: 'Optimize Code',
            kind: 'refactor',
            command: {
              id: 'ai.optimize',
              title: 'Optimize Code',
              arguments: [model, range]
            }
          });

          return { actions, dispose: () => {} };
        }
      });

      // Register Chat context menu actions (appear in Monaco's right-click menu)
      editor.addAction({
        id: 'chat.ask',
        label: 'Ask Chat',
        contextMenuGroupId: '9_chat',
        contextMenuOrder: 1,
        precondition: 'editorHasSelection',
        run: (ed) => {
          const sel = ed.getSelection();
          const text = ed.getModel().getValueInRange(sel);
          if (text && onChatAction) onChatAction('ask', text, currentTab?.filePath);
        }
      });

      editor.addAction({
        id: 'chat.fix',
        label: 'Fix This',
        contextMenuGroupId: '9_chat',
        contextMenuOrder: 2,
        precondition: 'editorHasSelection',
        run: (ed) => {
          const sel = ed.getSelection();
          const text = ed.getModel().getValueInRange(sel);
          if (text && onChatAction) onChatAction('fix', text, currentTab?.filePath);
        }
      });

      editor.addAction({
        id: 'chat.explain',
        label: 'Explain',
        contextMenuGroupId: '9_chat',
        contextMenuOrder: 3,
        keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyMod.Shift | monaco.KeyCode.KeyE],
        precondition: 'editorHasSelection',
        run: (ed) => {
          const sel = ed.getSelection();
          const text = ed.getModel().getValueInRange(sel);
          if (text && onChatAction) onChatAction('explain', text, currentTab?.filePath);
        }
      });

      editor.addAction({
        id: 'chat.addToChat',
        label: 'Add to Chat',
        contextMenuGroupId: '9_chat',
        contextMenuOrder: 4,
        precondition: 'editorHasSelection',
        run: (ed) => {
          const sel = ed.getSelection();
          const text = ed.getModel().getValueInRange(sel);
          if (text && onChatAction) onChatAction('add', text, currentTab?.filePath);
        }
      });

      // Add real-time context tracking listeners
      if (onEditorContextChange) {
        // Track cursor position changes
        editor.onDidChangeCursorPosition(() => {
          updateEditorContext();
        });

        // Track selection changes
        editor.onDidChangeCursorSelection(() => {
          updateEditorContext();
        });

        // Initial context update
        updateEditorContext();
      }

      // Cleanup function
      return () => {
        if (contextUpdateTimeoutRef.current) {
          clearTimeout(contextUpdateTimeoutRef.current);
        }
      };
    }, [currentTab, updateEditorContext, onEditorContextChange]);

    // AI assistance handler
    const handleAIAssist = useCallback(async () => {
      if (!editorRef.current || !currentTab) return;

      const position = editorRef.current.getPosition();
      const model = editorRef.current.getModel();
      const beforeCursor = model.getValueInRange({
        startLineNumber: 1,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column
      });
      const afterCursor = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: position.column,
        endLineNumber: model.getLineCount(),
        endColumn: model.getLineMaxColumn(model.getLineCount())
      });

      try {
        const completion = await codeIntelligenceService.getCodeCompletionIntelligent({
          codeBefore: beforeCursor,
          codeAfter: afterCursor,
          language: currentTab.language,
          filePath: currentTab.filePath,
          cursorPosition: { line: position.lineNumber, column: position.column }
        });

        if (completion.success && completion.suggestions) {
          // Show completion suggestions in editor
          editorRef.current.trigger('ai', 'editor.action.triggerSuggest', {});
        }
      } catch (error) {
        console.error('AI assistance error:', error);
      }
    }, [currentTab]);

    // AI command handler
    const _handleAICommand = useCallback(async (command, selectedText) => {
      if (!currentTab) return;

      const context = {
        content: selectedText || currentTab.content,
        language: currentTab.language,
        filePath: currentTab.filePath
      };

      try {
        let result;
        switch (command) {
          case 'explain':
            result = await codeIntelligenceService.explainCodeIntelligent(context);
            if (result.success) {
              console.log('Code explanation:', result.explanation);
              // Could show in a tooltip or side panel
            }
            break;
          case 'optimize':
            result = await codeIntelligenceService.refactorCodeIntelligent(context, 'optimize');
            if (result.success && result.refactoredCode) {
              // Apply the optimized code
              const selection = editorRef.current.getSelection();
              if (selection && selectedText) {
                editorRef.current.executeEdits('ai-optimize', [{
                  range: selection,
                  text: result.refactoredCode
                }]);
              }
            }
            break;
          default:
            console.warn('Unknown AI command:', command);
        }
      } catch (error) {
        console.error('AI command error:', error);
      }
    }, [currentTab]);

    // Expose editor methods via ref for external control
    useImperativeHandle(ref, () => ({
      applyEdit: (range, newText, _description) => {
        if (editorRef.current && range) {
          try {
            // Handle both Monaco Range objects and plain objects
            let monacoRange;
            if (range.startLineNumber !== undefined) {
              // It's already a Monaco Range-like object
              monacoRange = range;
            } else {
              // Try to get Monaco from window or create a simple range
              const monaco = window.monaco;
              if (monaco && monaco.Range) {
                monacoRange = new monaco.Range(
                  range.startLineNumber || range.start?.lineNumber || 1,
                  range.startColumn || range.start?.column || 1,
                  range.endLineNumber || range.end?.lineNumber || 1,
                  range.endColumn || range.end?.column || 1
                );
              } else {
                // Fallback: create a simple range object
                monacoRange = {
                  startLineNumber: range.startLineNumber || range.start?.lineNumber || 1,
                  startColumn: range.startColumn || range.start?.column || 1,
                  endLineNumber: range.endLineNumber || range.end?.lineNumber || 1,
                  endColumn: range.endColumn || range.end?.column || 1
                };
              }
            }
            
            editorRef.current.executeEdits('ai-edit', [{
              range: monacoRange,
              text: newText
            }]);
            return { success: true };
          } catch (error) {
            console.error('Failed to apply edit:', error);
            return { success: false, error: error.message };
          }
        }
        return { success: false, error: 'Editor not available' };
      },
      getEditor: () => editorRef.current,
      getSelection: () => editorRef.current?.getSelection(),
      getPosition: () => editorRef.current?.getPosition()
    }), []);

    const handleContentChange = useCallback((value) => {
      if (!currentTab || activeTabIndex < 0 || activeTabIndex >= openTabs.length) return;

      setOpenTabs(prev => {
        if (activeTabIndex >= 0 && activeTabIndex < prev.length) {
          return prev.map((tab, index) =>
            index === activeTabIndex
              ? { ...tab, content: value, isModified: true }
              : tab
          );
        }
        return prev;
      });
      
      // Update context after content change
      if (onEditorContextChange) {
        setTimeout(() => updateEditorContext(), 100);
      }
    }, [activeTabIndex, currentTab, openTabs.length, setOpenTabs, updateEditorContext, onEditorContextChange]);

    const handleSave = useCallback(async () => {
      if (!currentTab) return;

      try {
        const processId = startProcess("save-file", "Saving file...", "file_generation");

        // Validate file content
        if (currentTab.content === undefined || currentTab.content === null) {
          throw new Error('Cannot save file with undefined content');
        }

        // Determine filename for new files
        let actualFilePath = currentTab.filePath;
        if (!actualFilePath || currentTab.isNew) {
          const timestamp = Date.now();
          const LANGUAGE_TO_EXT = {
            javascript: '.js', typescript: '.ts', python: '.py',
            html: '.html', css: '.css', scss: '.scss', less: '.less',
            json: '.json', xml: '.xml', yaml: '.yaml', markdown: '.md',
            sql: '.sql', shell: '.sh', rust: '.rs', go: '.go',
            java: '.java', cpp: '.cpp', c: '.c', csharp: '.cs',
            php: '.php', ruby: '.rb', swift: '.swift', kotlin: '.kt',
          };
          const extension = LANGUAGE_TO_EXT[currentTab.language] || '.txt';
          actualFilePath = `untitled_${timestamp}${extension}`;
        }

        // Validate file path
        if (!actualFilePath || actualFilePath.trim() === '') {
          throw new Error('Invalid file path');
        }

        // Handle different save targets based on tab source
        if (currentTab.source === 'document' || currentTab.source === 'filesystem') {
          // Save to backend/filesystem
          const result = await fileOperationsService.writeFile(actualFilePath, currentTab.content);
          if (!result.success) {
            throw new Error(result.error || 'Failed to save file');
          }
        } else {
          // Save to localStorage with error handling
          const fileData = {
            path: actualFilePath,
            content: currentTab.content,
            language: currentTab.language,
            lastModified: new Date().toISOString()
          };

          try {
            const existingFilesData = localStorage.getItem('codeEditorFiles') || '{}';
            let existingFiles;
            try {
              existingFiles = JSON.parse(existingFilesData);
            } catch (parseError) {
              console.warn('Invalid JSON in localStorage, resetting:', parseError);
              existingFiles = {};
            }

            existingFiles[actualFilePath] = fileData;
            localStorage.setItem('codeEditorFiles', JSON.stringify(existingFiles));
          } catch (storageError) {
            if (storageError.name === 'QuotaExceededError') {
              throw new Error('Storage quota exceeded. Please free up some space by deleting unused files.');
            }
            throw new Error(`Failed to save file to local storage: ${storageError.message}`);
          }
        }

        // Update tab state
        setOpenTabs(prev => prev.map((tab, index) =>
          index === activeTabIndex
            ? {
                ...tab,
                filePath: actualFilePath,
                isModified: false,
                isNew: false,
              }
            : tab
        ));

        completeProcess(processId, `File saved as ${actualFilePath}`);
      } catch (err) {
        errorProcess("save-file", err.message);
      }
    }, [currentTab, activeTabIndex, setOpenTabs, startProcess, completeProcess, errorProcess]);

    const handleTabChange = useCallback((event, newValue) => {
      setActiveTabIndex(newValue);
    }, [setActiveTabIndex]);

    // State for unsaved changes dialog
    const [closeConfirmDialog, setCloseConfirmDialog] = useState({
      open: false,
      tabIndex: null,
      tabName: ''
    });

    const closeTab = useCallback((tabIndex, skipConfirm = false) => {
      const tabToClose = openTabs[tabIndex];

      // Check for unsaved changes
      if (!skipConfirm && tabToClose?.isModified) {
        setCloseConfirmDialog({
          open: true,
          tabIndex: tabIndex,
          tabName: (tabToClose.filePath || "Untitled").split('/').pop()
        });
        return;
      }

      // Allow closing all tabs - no forced Untitled tab
      const newTabs = openTabs.filter((_, index) => index !== tabIndex);
      setOpenTabs(newTabs);

      // Adjust active tab index
      if (newTabs.length === 0) {
        setActiveTabIndex(-1); // No active tab
      } else if (tabIndex === activeTabIndex) {
        setActiveTabIndex(Math.max(0, tabIndex - 1));
      } else if (tabIndex < activeTabIndex) {
        setActiveTabIndex(Math.max(0, activeTabIndex - 1));
      }
    }, [openTabs, activeTabIndex, setOpenTabs, setActiveTabIndex]);

    const handleCloseConfirmDiscard = useCallback(() => {
      const { tabIndex } = closeConfirmDialog;
      setCloseConfirmDialog({ open: false, tabIndex: null, tabName: '' });
      closeTab(tabIndex, true); // Force close without confirm
    }, [closeConfirmDialog, closeTab]);

    const handleCloseConfirmCancel = useCallback(() => {
      setCloseConfirmDialog({ open: false, tabIndex: null, tabName: '' });
    }, []);

    const createNewTab = useCallback(() => {
      const newTab = {
        id: Math.random().toString(36).slice(2, 11),
        filePath: null,
        content: "",
        language: "javascript",
        isModified: false,
        isNew: true,
      };
      setOpenTabs(prev => {
        const newTabs = [...prev, newTab];
        setActiveTabIndex(newTabs.length - 1);
        return newTabs;
      });
    }, [setOpenTabs, setActiveTabIndex]);

    const handleRunCode = useCallback(() => {
      if (!currentTab) return;
      console.log("Code execution not implemented yet");
    }, [currentTab]);

    const handleTabContextMenu = useCallback((event, tabIndex) => {
      event.preventDefault();
      event.stopPropagation();
      setContextMenu({
        mouseX: event.clientX - 2,
        mouseY: event.clientY - 4,
        tabIndex
      });
    }, []);

    const handleCloseContextMenu = useCallback(() => {
      setContextMenu(null);
    }, []);

    const handleRenameTab = useCallback(() => {
      if (contextMenu === null) return;
      const tabIndex = contextMenu.tabIndex;
      if (tabIndex < 0 || tabIndex >= openTabs.length) return;
      const tab = openTabs[tabIndex];
      if (!tab) return;

      const fileName = (tab.filePath || "Untitled").split('/').pop();
      setRenameValue(fileName);
      setRenameDialog({ open: true, tabIndex: tabIndex });
      handleCloseContextMenu();
    }, [contextMenu, openTabs, handleCloseContextMenu]);

    const handleRenameConfirm = useCallback(() => {
      if (renameDialog.tabIndex === null || !renameValue.trim()) return;
      
      const tabIndex = renameDialog.tabIndex;
      if (tabIndex < 0 || tabIndex >= openTabs.length) {
        setRenameDialog({ open: false, tabIndex: null });
        setRenameValue("");
        return;
      }

      try {
        const trimmedName = renameValue.trim();
        // eslint-disable-next-line no-control-regex -- intentional: \x00-\x1f matches OS-illegal filename control chars
        const invalidChars = /[<>:"/\\|?*\x00-\x1f]/;
        if (invalidChars.test(trimmedName)) {
          alert('Filename contains invalid characters. Please use only letters, numbers, spaces, hyphens, and underscores.');
          return;
        }

        if (trimmedName.length > 255) {
          alert('Filename is too long. Please use a shorter name.');
          return;
        }

        const tab = openTabs[tabIndex];
        if (!tab) {
          setRenameDialog({ open: false, tabIndex: null });
          setRenameValue("");
          return;
        }
        
        const oldPath = tab.filePath;
        const newPath = trimmedName;

        // Update localStorage if it's a saved file
        if (oldPath && !tab.isNew) {
          const existingFiles = JSON.parse(localStorage.getItem('codeEditorFiles') || '{}');
          if (existingFiles[oldPath]) {
            const fileData = existingFiles[oldPath];
            delete existingFiles[oldPath];
            existingFiles[newPath] = {
              ...fileData,
              path: newPath,
              lastModified: new Date().toISOString()
            };
            localStorage.setItem('codeEditorFiles', JSON.stringify(existingFiles));
          }
        }

        // Update tab (and re-detect language from new filename)
        const newLanguage = getLanguageFromFilename(trimmedName);
        setOpenTabs(prev => {
          if (tabIndex >= 0 && tabIndex < prev.length) {
            return prev.map((tab, index) =>
              index === tabIndex
                ? { ...tab, filePath: newPath, language: newLanguage, isModified: true }
                : tab
            );
          }
          return prev;
        });

        setRenameDialog({ open: false, tabIndex: null });
        setRenameValue("");
      } catch (error) {
        console.error('Error renaming tab:', error);
        alert('Failed to rename file. Please try again.');
      }
    }, [renameDialog.tabIndex, renameValue, openTabs, setOpenTabs]);

    return (
      <DashboardCardWrapper
        ref={ref}
        title="Code Editor"
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          {/* Tabs and Controls */}
          <Box sx={{
            borderBottom: 1,
            borderColor: 'divider',
            display: 'flex',
            alignItems: 'center',
            minHeight: 48
          }}>
            <Tabs
              value={activeTabIndex}
              onChange={handleTabChange}
              variant="scrollable"
              scrollButtons="auto"
              sx={{ flex: 1, minHeight: 48 }}
            >
              {openTabs.map((tab, index) => (
                <Tab
                  key={tab.id}
                  onContextMenu={(e) => handleTabContextMenu(e, index)}
                  label={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography variant="body2">
                        {(tab.filePath || "Untitled").split('/').pop()}
                        {tab.isModified && " •"}
                      </Typography>
                      <Box
                        component="span"
                        onClick={(e) => {
                          e.stopPropagation();
                          closeTab(index);
                        }}
                        sx={{
                          ml: 0.5,
                          p: 0.25,
                          cursor: 'pointer',
                          borderRadius: '50%',
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          // Show close button more consistently
                          opacity: 0.7,
                          '&:hover': {
                            opacity: 1,
                            backgroundColor: 'action.hover'
                          }
                        }}
                      >
                        <CloseIcon fontSize="small" />
                      </Box>
                    </Box>
                  }
                />
              ))}
            </Tabs>

            {/* Action buttons */}
            <Box sx={{ display: 'flex', gap: 0.5, px: 1 }}>
              {/* New file button */}
              <Tooltip title="New File">
                <IconButton size="small" onClick={createNewTab}>
                  <AddIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              {/* Preview button - only show for WordPress HTML pages */}
              {currentTab && currentTab.source === "wordpress" && currentTab.language === "html" && (
                <Tooltip title="Preview HTML">
                  <IconButton size="small" onClick={() => setPreviewDialogOpen(true)}>
                    <PreviewIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
              <Tooltip title="Save (Ctrl+S)">
                <IconButton size="small" onClick={handleSave} disabled={!currentTab}>
                  <SaveIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              <Tooltip title="Code execution not implemented">
                <span>
                  <IconButton size="small" onClick={handleRunCode} disabled={true}>
                    <PlayArrowIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              {currentTab && (
                <Tooltip title="Tab Options">
                  <IconButton
                    size="small"
                    onClick={(e) => handleTabContextMenu(e, activeTabIndex)}
                  >
                    <MoreVert fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
            </Box>
          </Box>

          {/* Monaco Editor */}
          <Box sx={{ flex: 1, position: 'relative' }}>
            {editorError ? (
              <Box sx={{ p: 2, height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                <Alert severity="error" sx={{ mb: 2 }}>
                  Editor failed to load: {editorError.message}
                </Alert>
                <IconButton onClick={() => setEditorError(null)} color="primary">
                  <RefreshIcon />
                </IconButton>
                <Typography variant="body2" color="text.secondary">
                  Click to retry
                </Typography>
              </Box>
            ) : currentTab ? (
              <Editor
                height="100%"
                language={currentTab.language}
                value={currentTab.content}
                onChange={handleContentChange}
                onMount={handleEditorDidMount}
                theme="vs-dark"
                options={{
                  fontSize: 14,
                  wordWrap: "on",
                  minimap: { enabled: true },
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  padding: { top: 10, bottom: 10 },
                }}
                onError={(error) => {
                  console.error('Monaco Editor error:', error);
                  setEditorError(error);
                }}
              />
            ) : (
              /* Empty state - no tabs open */
              <Box
                sx={{
                  height: '100%',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  backgroundColor: 'background.paper',
                  gap: 2,
                }}
              >
                <Typography variant="h6" color="text.secondary">
                  No files open
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                  Open a file from the File Tree or create a new one
                </Typography>
                <Tooltip title="Create New File">
                  <IconButton
                    onClick={createNewTab}
                    sx={{
                      width: 64,
                      height: 64,
                      border: '2px dashed',
                      borderColor: 'divider',
                      '&:hover': {
                        borderColor: 'primary.main',
                        backgroundColor: 'action.hover',
                      },
                    }}
                  >
                    <AddIcon sx={{ fontSize: 32 }} />
                  </IconButton>
                </Tooltip>
              </Box>
            )}
          </Box>
        </Box>

        {/* Context Menu */}
        <Menu
          open={contextMenu !== null}
          onClose={handleCloseContextMenu}
          anchorReference="anchorPosition"
          anchorPosition={
            contextMenu !== null
              ? { top: contextMenu.mouseY, left: contextMenu.mouseX }
              : undefined
          }
        >
          <MenuItem onClick={handleRenameTab}>
            <ListItemIcon>
              <DriveFileRenameOutline fontSize="small" />
            </ListItemIcon>
            <ListItemText>Rename Tab</ListItemText>
          </MenuItem>
        </Menu>


        {/* Preview Dialog */}
        <Dialog 
          open={previewDialogOpen} 
          onClose={() => setPreviewDialogOpen(false)}
          maxWidth="lg"
          fullWidth
        >
          <DialogTitle>
            Preview: {currentTab?.wordpressPageTitle || currentTab?.filePath || 'Untitled'}
          </DialogTitle>
          <DialogContent sx={{ p: 0 }}>
            <Box
              component="iframe"
              srcDoc={getPreviewContent()}
              sx={{
                width: '100%',
                height: '70vh',
                border: 'none',
                display: 'block',
              }}
              title="HTML Preview"
              key={currentTab?.content} // Force refresh when content changes
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setPreviewDialogOpen(false)}>
              Close
            </Button>
          </DialogActions>
        </Dialog>

        {/* Rename Dialog */}
        <Dialog open={renameDialog.open} onClose={() => setRenameDialog({ open: false, tabIndex: null })}>
          <DialogTitle>
            Rename File
          </DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              margin="dense"
              label="New File Name"
              fullWidth
              variant="outlined"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  handleRenameConfirm();
                }
              }}
              helperText="Enter the new name for this file"
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setRenameDialog({ open: false, tabIndex: null })}>
              Cancel
            </Button>
            <Button onClick={handleRenameConfirm} variant="contained">
              Rename
            </Button>
          </DialogActions>
        </Dialog>

        {/* Unsaved Changes Confirmation Dialog */}
        <Dialog open={closeConfirmDialog.open} onClose={handleCloseConfirmCancel}>
          <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <WarningIcon color="warning" />
            Unsaved Changes
          </DialogTitle>
          <DialogContent>
            <Typography>
              "{closeConfirmDialog.tabName}" has unsaved changes. Do you want to discard them?
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={handleCloseConfirmCancel}>
              Cancel
            </Button>
            <Button onClick={handleCloseConfirmDiscard} color="error" variant="contained">
              Discard Changes
            </Button>
          </DialogActions>
        </Dialog>
      </DashboardCardWrapper>
    );
  }
);

CodeEditorCard.displayName = "CodeEditorCard";

export default CodeEditorCard;