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
  CircularProgress,
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
  SmartToy as SmartToyIcon,
} from "@mui/icons-material";
import Editor, { useMonaco } from "../../utils/monaco";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import * as codeIntelligenceService from "../../api/codeIntelligenceService";
import * as fileOperationsService from "../../api/fileOperationsService";
import * as documentService from "../../api/documentService";
import { getLanguageFromFilename } from "../../utils/languageDetector";
import { revealEditorLine } from "./codeEditorNav";
import { createAiHoverProvider, readAiHoverPreference, writeAiHoverPreference } from "./aiHover";
import { saveTargetFor, saveRefusal, loadedTab, tabDisplayName, TAB_LOAD } from "./tabOrigin";
import { bindEditorActions, modelPathFor, disposeTabModels } from "./editorBindings";
import { readDroppedCodeFiles, dropNotice as noticeForDrop } from "./droppedCode";
import { dragCarriesFiles, snapshotDrop } from "../../utils/droppedFiles";
import { useUiTranslation } from "../../i18n";

// Monaco command behind the lightbulb actions (Fix with AI, Explain, Optimize):
// each sends the lines under the marker or selection to the chat card.
const CODE_ACTION_COMMAND = "guaardvark.codeAction";

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
      onRun,
      revealRequest,
      onRevealDone,
      ...props
    },
    ref
  ) => {
    const { t } = useUiTranslation();
    const editorRef = useRef(null);
    const [editorMounts, setEditorMounts] = useState(0);
    const contextUpdateTimeoutRef = useRef(null);
    const [editorError, setEditorError] = useState(null);
    const [contextMenu, setContextMenu] = useState(null);
    const [renameDialog, setRenameDialog] = useState({ open: false, tabIndex: null });
    const [renameValue, setRenameValue] = useState("");
    const renameInputRef = useRef(null);
    const [previewDialogOpen, setPreviewDialogOpen] = useState(false);
    // A file dragged in from the computer: the overlay while it hovers, the
    // line saying what opened (or did not) after it lands.
    const [fileDragActive, setFileDragActive] = useState(false);
    const [dropNotice, setDropNotice] = useState(null);
    const dragDepthRef = useRef(0);
    const { startProcess, completeProcess, errorProcess } = useUnifiedProgress();
    const theme = useTheme();
    const monaco = useMonaco();

    // AI hover explanations are opt-in: each one is a model request.
    const [aiHoverEnabled, setAiHoverEnabled] = useState(readAiHoverPreference);
    const aiHoverEnabledRef = useRef(aiHoverEnabled);
    aiHoverEnabledRef.current = aiHoverEnabled;
    const hoverCacheRef = useRef(new Map());
    const currentTabRef = useRef(null);
    const onChatActionRef = useRef(onChatAction);
    onChatActionRef.current = onChatAction;

    const toggleAiHover = useCallback(() => {
      setAiHoverEnabled((on) => {
        writeAiHoverPreference(!on);
        return !on;
      });
      setContextMenu(null);
    }, []);

    // The lightbulb actions run through one command so they reach the chat
    // card with the real text instead of inserting a placeholder comment.
    useEffect(() => {
      if (!monaco) return undefined;
      const disposable = monaco.editor.registerCommand(CODE_ACTION_COMMAND, (_accessor, action, model, range, problems) => {
        if (!model || !range || !onChatActionRef.current) return;
        const text = model.getValueInRange({
          startLineNumber: range.startLineNumber,
          startColumn: 1,
          endLineNumber: range.endLineNumber,
          endColumn: model.getLineMaxColumn(range.endLineNumber),
        });
        if (!text.trim()) return;
        onChatActionRef.current(action, text, currentTabRef.current?.filePath, { problems: problems || [] });
      });
      return () => disposable.dispose();
    }, [monaco]);

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
    currentTabRef.current = currentTab;

    // Monaco's onMount runs once per editor instance; Ctrl+S reads this ref so
    // it saves the tab showing now, not the one open when the editor mounted.
    const handleSaveRef = useRef(null);

    // Each tab has its own Monaco model (the Editor's `path` prop); models of
    // closed tabs are disposed here, and all of them when the card unmounts.
    const monacoRef = useRef(null);
    const knownTabsRef = useRef([]);
    useEffect(() => {
      const current = new Set((openTabs || []).map((tab) => tab.id));
      const gone = knownTabsRef.current.filter((tab) => !current.has(tab.id));
      knownTabsRef.current = openTabs || [];
      if (gone.length && monacoRef.current) disposeTabModels(monacoRef.current, gone);
    }, [openTabs]);
    useEffect(() => () => {
      if (monacoRef.current) disposeTabModels(monacoRef.current, knownTabsRef.current);
    }, []);

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
      monacoRef.current = monaco;
      setEditorMounts((n) => n + 1);

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

      // Hover provider for AI explanations: only when the tab menu's "AI hover
      // explanations" is on, one request per word (see aiHover.js).
      monaco.languages.registerHoverProvider(currentTabRef.current?.language || 'javascript', {
        provideHover: createAiHoverProvider({
          isEnabled: () => aiHoverEnabledRef.current,
          explain: codeIntelligenceService.analyzeCodeIntelligent,
          getTab: () => currentTabRef.current,
          cache: hoverCacheRef.current,
        }),
      });

      // Lightbulb actions: each hands the lines in range to the chat card.
      monaco.languages.registerCodeActionProvider(currentTabRef.current?.language || 'javascript', {
        provideCodeActions: async (model, range, context) => {
          const actions = [];
          const problems = (context.markers || []).map((m) => m.message);

          if (problems.length > 0) {
            actions.push({
              title: t("codeEditor.card.fixWithAi"),
              kind: 'quickfix',
              command: { id: CODE_ACTION_COMMAND, title: t("codeEditor.card.fixWithAi"), arguments: ['fix', model, range, problems] }
            });
          }

          actions.push({
            title: t("codeEditor.card.explainCode"),
            kind: 'refactor',
            command: { id: CODE_ACTION_COMMAND, title: t("codeEditor.card.explainCode"), arguments: ['explain', model, range] }
          });

          actions.push({
            title: t("codeEditor.card.optimizeCode"),
            kind: 'refactor',
            command: { id: CODE_ACTION_COMMAND, title: t("codeEditor.card.optimizeCode"), arguments: ['optimize', model, range] }
          });

          return { actions, dispose: () => {} };
        }
      });

      // Ctrl+S and the right-click chat actions read the active tab and their
      // handlers through refs: onMount runs once per editor while tabs change.
      bindEditorActions(editor, monaco, {
        getTab: () => currentTabRef.current,
        save: () => handleSaveRef.current?.(),
        chatAction: (action, text, filePath) => onChatActionRef.current?.(action, text, filePath),
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
    }, [updateEditorContext, onEditorContextChange, t]);

    // Not bound to a key: the completion it fetches is never shown, so Ctrl+Space
    // stays Monaco's own suggestion list.
    const _handleAIAssist = useCallback(async () => {
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
        return { success: false, error: t("codeEditor.card.editorUnavailable") };
      },
      getEditor: () => editorRef.current,
      getSelection: () => editorRef.current?.getSelection(),
      getPosition: () => editorRef.current?.getPosition()
    }), [t]);

    // A jump asked for by the page (Find Symbol) runs once its tab is showing
    // and Monaco has mounted; the Editor child updates its model before this runs.
    useEffect(() => {
      if (!revealRequest || !currentTab || currentTab.id !== revealRequest.tabId) return;
      if (revealEditorLine(editorRef.current, revealRequest.line)) onRevealDone?.();
    }, [revealRequest, currentTab, editorMounts, onRevealDone]);

    const handleContentChange = useCallback((value) => {
      if (!currentTab || activeTabIndex < 0 || activeTabIndex >= openTabs.length) return;
      if (currentTab.readOnly || currentTab.source === 'live_repo') return;
      if (currentTab.loadState && currentTab.loadState !== TAB_LOAD.LOADED) return;

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
        // The tab's origin tag decides the save path; a name is never enough,
        // since a library document and a repo file can share one.
        const target = saveTargetFor(currentTab);
        if (target.kind === 'readonly' || target.kind === 'error') {
          throw new Error(target.message);
        }
        // A tab that is still loading, failed to load, or holds exactly what
        // was loaded is never written: that is how a placeholder or an empty
        // string would replace the stored document.
        const refusal = saveRefusal(currentTab);
        if (refusal && refusal.reason === 'clean') {
          completeProcess(startProcess("save-file", t("codeEditor.card.saving"), "file_generation"), refusal.message);
          return;
        }
        if (refusal) {
          throw new Error(refusal.message);
        }
        const processId = startProcess("save-file", t("codeEditor.card.saving"), "file_generation");

        // Validate file content
        if (currentTab.content === undefined || currentTab.content === null) {
          throw new Error(t("codeEditor.card.undefinedContent"));
        }
        // The text this save writes, and the tab it belongs to: the user can
        // type, switch or close tabs while the write is in flight.
        const sent = currentTab.content;
        const savedId = currentTab.id;
        const savedIndex = activeTabIndex;

        // A tab with no name yet gets one; a new tab renamed before its first
        // save keeps the name it was given.
        let actualFilePath = currentTab.filePath;
        if (!actualFilePath) {
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
          throw new Error(t("codeEditor.card.invalidPath"));
        }

        if (target.kind === 'document') {
          // A library document is saved by id through the documents API, the
          // same route the Files page's code viewer uses. /api/files/write
          // resolves bare names under the repo root and must not see it.
          const result = await documentService.updateDocument(target.documentId, { content: sent });
          if (result && result.error) {
            throw new Error(result.error);
          }
        } else if (target.kind === 'repo') {
          // A repo file keeps its repo-relative path and the guarded write route.
          const result = await fileOperationsService.writeFile(target.path, sent);
          if (!result.success) {
            throw new Error(result.error || t("codeEditor.card.saveFailed"));
          }
        } else {
          // Save to localStorage with error handling
          const fileData = {
            path: actualFilePath,
            content: sent,
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
              throw new Error(t("codeEditor.card.storageFull"));
            }
            throw new Error(t("codeEditor.card.localSaveFailed", { error: storageError.message }));
          }
        }

        // Mark the saved tab by id, not by the index it had when Save started,
        // and only the text that was sent as saved.
        setOpenTabs(prev => prev.map((tab, index) =>
          (savedId !== undefined ? tab.id === savedId : index === savedIndex)
            ? {
                ...tab,
                filePath: actualFilePath,
                savedContent: sent,
                loadState: TAB_LOAD.LOADED,
                loadError: null,
                isModified: tab.content !== sent,
                isNew: false,
              }
            : tab
        ));

        completeProcess(processId, t("codeEditor.card.savedAs", { path: actualFilePath }));
      } catch (err) {
        errorProcess("save-file", err.message);
      }
    }, [currentTab, activeTabIndex, setOpenTabs, startProcess, completeProcess, errorProcess, t]);
    handleSaveRef.current = handleSave;

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
          tabName: tabToClose.filePath ? tabToClose.filePath.split('/').pop() : null
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
      const newTab = loadedTab({
        id: Math.random().toString(36).slice(2, 11),
        filePath: null,
        language: "javascript",
        isNew: true,
      }, "");
      setOpenTabs(prev => {
        const newTabs = [...prev, newTab];
        setActiveTabIndex(newTabs.length - 1);
        return newTabs;
      });
    }, [setOpenTabs, setActiveTabIndex]);

    const openDroppedFiles = useCallback(async (snapshot) => {
      const result = await readDroppedCodeFiles(snapshot);
      if (result.tabs.length) {
        setOpenTabs(prev => {
          const newTabs = [...prev, ...result.tabs];
          setActiveTabIndex(newTabs.length - 1);
          return newTabs;
        });
      }
      setDropNotice(noticeForDrop(result));
    }, [setOpenTabs, setActiveTabIndex]);

    // File drags are handled in the capture phase, before Monaco sees them,
    // so a dropped file opens in its own tab instead of landing in the text.
    const handleFileDragEnter = useCallback((event) => {
      if (!dragCarriesFiles(event)) return;
      event.preventDefault();
      event.stopPropagation();
      dragDepthRef.current += 1;
      setFileDragActive(true);
    }, []);

    const handleFileDragOver = useCallback((event) => {
      if (!dragCarriesFiles(event)) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = 'copy';
    }, []);

    const handleFileDragLeave = useCallback((event) => {
      if (!dragCarriesFiles(event)) return;
      event.stopPropagation();
      dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
      if (dragDepthRef.current === 0) setFileDragActive(false);
    }, []);

    const handleFileDrop = useCallback((event) => {
      if (!dragCarriesFiles(event)) return;
      event.preventDefault();
      event.stopPropagation();
      dragDepthRef.current = 0;
      setFileDragActive(false);
      // The browser empties dataTransfer once this handler returns.
      openDroppedFiles(snapshotDrop(event.dataTransfer));
    }, [openDroppedFiles]);

    // An "opened" notice clears itself; one that left files out stays until closed.
    useEffect(() => {
      if (dropNotice?.severity !== 'info') return undefined;
      const timer = setTimeout(() => setDropNotice(null), 10000);
      return () => clearTimeout(timer);
    }, [dropNotice]);

    // The page owns Run (it also drives the header button and the Output card).
    const handleRunCode = useCallback(() => {
      if (currentTab?.content && onRun) onRun();
    }, [currentTab, onRun]);

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
          alert(t("codeEditor.rename.invalidChars"));
          return;
        }

        if (trimmedName.length > 255) {
          alert(t("codeEditor.rename.tooLong"));
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
        alert(t("codeEditor.rename.failed"));
      }
    }, [renameDialog.tabIndex, renameValue, openTabs, setOpenTabs, t]);

    return (
      <DashboardCardWrapper
        ref={ref}
        title={t("codeEditor.card.title")}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        {...props}
      >
        <Box
          data-testid="code-editor-drop-zone"
          sx={{ height: '100%', display: 'flex', flexDirection: 'column', position: 'relative' }}
          onDragEnterCapture={handleFileDragEnter}
          onDragOverCapture={handleFileDragOver}
          onDragLeaveCapture={handleFileDragLeave}
          onDropCapture={handleFileDrop}
        >
          {fileDragActive && (
            <Box
              data-testid="code-editor-drop-overlay"
              sx={{
                position: 'absolute',
                inset: 4,
                zIndex: 5,
                pointerEvents: 'none',
                border: '2px dashed',
                borderColor: 'primary.main',
                borderRadius: 1,
                bgcolor: 'background.default',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 0.5,
              }}
            >
              <Typography variant="h6">{t("codeEditor.card.dropTitle")}</Typography>
              <Typography variant="body2" color="text.secondary">
                {t("codeEditor.card.dropHint")}
              </Typography>
            </Box>
          )}
          {dropNotice && (
            <Box sx={{ position: 'absolute', left: 8, right: 8, bottom: 8, zIndex: 4 }}>
              <Alert
                severity={dropNotice.severity}
                data-testid="code-editor-drop-notice"
                onClose={() => setDropNotice(null)}
              >
                {dropNotice.text}
              </Alert>
            </Box>
          )}
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
                        {(tab.filePath || t("codeEditor.tabs.untitled")).split('/').pop()}
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
              <Tooltip title={t("codeEditor.card.newFile")}>
                <IconButton size="small" onClick={createNewTab}>
                  <AddIcon fontSize="small" />
                </IconButton>
              </Tooltip>
              {/* Preview button - only show for WordPress HTML pages */}
              {currentTab && currentTab.source === "wordpress" && currentTab.language === "html" && (
                <Tooltip title={t("codeEditor.card.previewHtml")}>
                  <IconButton size="small" onClick={() => setPreviewDialogOpen(true)}>
                    <PreviewIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              )}
              <Tooltip title={t("codeEditor.card.save")}>
                <span>
                  <IconButton size="small" onClick={handleSave} disabled={!currentTab}>
                    <SaveIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              <Tooltip title={t("codeEditor.card.runCode")}>
                <span>
                  <IconButton size="small" onClick={handleRunCode} disabled={!currentTab?.content || !onRun}>
                    <PlayArrowIcon fontSize="small" />
                  </IconButton>
                </span>
              </Tooltip>
              {currentTab && (
                <Tooltip title={t("codeEditor.card.tabOptions")}>
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
                  {t("codeEditor.card.editorFailed", { error: editorError.message })}
                </Alert>
                <IconButton onClick={() => setEditorError(null)} color="primary">
                  <RefreshIcon />
                </IconButton>
                <Typography variant="body2" color="text.secondary">
                  {t("codeEditor.card.clickToRetry")}
                </Typography>
              </Box>
            ) : currentTab ? (
              <>
              {/* Load state lives beside the editor, never in its text */}
              {currentTab.loadState === TAB_LOAD.LOADING && (
                <Box
                  data-testid="tab-loading"
                  sx={{ position: 'absolute', inset: 0, zIndex: 2, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 1, bgcolor: 'rgba(0,0,0,0.55)' }}
                >
                  <CircularProgress size={20} />
                  <Typography variant="body2">{t("codeEditor.card.loadingTab", { name: tabDisplayName(currentTab) })}</Typography>
                </Box>
              )}
              {currentTab.loadState === TAB_LOAD.FAILED && (
                <Alert severity="error" data-testid="tab-failed" sx={{ position: 'absolute', top: 8, left: 8, right: 8, zIndex: 2 }}>
                  {t("codeEditor.card.tabFailed", { name: tabDisplayName(currentTab), error: currentTab.loadError })}
                </Alert>
              )}
              <Editor
                height="100%"
                path={modelPathFor(currentTab)}
                language={currentTab.language}
                value={currentTab.content}
                onChange={currentTab.readOnly || currentTab.source === 'live_repo' ? undefined : handleContentChange}
                onMount={handleEditorDidMount}
                theme="vs-dark"
                options={{
                  readOnly: currentTab.readOnly || currentTab.source === 'live_repo'
                    || (Boolean(currentTab.loadState) && currentTab.loadState !== TAB_LOAD.LOADED),
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
              </>
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
                  {t("codeEditor.card.noFiles")}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center' }}>
                  {t("codeEditor.card.emptyHint")}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ mb: 2, textAlign: 'center', maxWidth: 420 }}>
                  {t("codeEditor.card.dropNote")}
                </Typography>
                <Tooltip title={t("codeEditor.card.createNewFile")}>
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
            <ListItemText>{t("codeEditor.menu.renameTab")}</ListItemText>
          </MenuItem>
          <MenuItem onClick={toggleAiHover}>
            <ListItemIcon>
              <SmartToyIcon fontSize="small" color={aiHoverEnabled ? "primary" : "inherit"} />
            </ListItemIcon>
            <ListItemText
              primary={aiHoverEnabled ? t("codeEditor.menu.aiHoverOn") : t("codeEditor.menu.aiHoverOff")}
              secondary={t("codeEditor.menu.aiHoverHint")}
            />
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
            {t("codeEditor.preview.title", {
              title: currentTab?.wordpressPageTitle || currentTab?.filePath || t("codeEditor.tabs.untitled"),
            })}
          </DialogTitle>
          <DialogContent sx={{ p: 0 }}>
            <Box
              component="iframe"
              srcDoc={getPreviewContent()}
              // Scripts, dialogs and forms work, on an opaque origin: the previewed
              // page cannot read the app, its storage or call its API as the app.
              sandbox="allow-scripts allow-modals allow-forms"
              sx={{
                width: '100%',
                height: '70vh',
                border: 'none',
                display: 'block',
              }}
              title={t("codeEditor.preview.frameTitle")}
              key={currentTab?.content} // Force refresh when content changes
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setPreviewDialogOpen(false)}>
              {t("codeEditor.actions.close")}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Rename Dialog */}
        <Dialog
          open={renameDialog.open}
          onClose={() => setRenameDialog({ open: false, tabIndex: null })}
          // autoFocus alone loses to the tab menu, which hands focus back to
          // where it was as it closes; focus and select once the dialog is in.
          TransitionProps={{
            onEntered: () => {
              renameInputRef.current?.focus();
              renameInputRef.current?.select();
            },
          }}
        >
          <DialogTitle>
            {t("codeEditor.rename.title")}
          </DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              inputRef={renameInputRef}
              margin="dense"
              label={t("codeEditor.rename.label")}
              fullWidth
              variant="outlined"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  handleRenameConfirm();
                }
              }}
              helperText={t("codeEditor.rename.helper")}
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setRenameDialog({ open: false, tabIndex: null })}>
              {t("codeEditor.actions.cancel")}
            </Button>
            <Button onClick={handleRenameConfirm} variant="contained">
              {t("codeEditor.rename.confirm")}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Unsaved Changes Confirmation Dialog */}
        <Dialog open={closeConfirmDialog.open} onClose={handleCloseConfirmCancel}>
          <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <WarningIcon color="warning" />
            {t("codeEditor.unsaved.title")}
          </DialogTitle>
          <DialogContent>
            <Typography>
              {t("codeEditor.unsaved.message", {
                name: closeConfirmDialog.tabName ?? t("codeEditor.tabs.untitled"),
              })}
            </Typography>
          </DialogContent>
          <DialogActions>
            <Button onClick={handleCloseConfirmCancel}>
              {t("codeEditor.actions.cancel")}
            </Button>
            <Button onClick={handleCloseConfirmDiscard} color="error" variant="contained">
              {t("codeEditor.unsaved.discard")}
            </Button>
          </DialogActions>
        </Dialog>
      </DashboardCardWrapper>
    );
  }
);

CodeEditorCard.displayName = "CodeEditorCard";

export default CodeEditorCard;