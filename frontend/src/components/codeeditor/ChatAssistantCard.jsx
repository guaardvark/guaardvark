// frontend/src/components/codeeditor/ChatAssistantCard.jsx
// Next-Generation Context-Aware AI Assistant for Code Editor
// Provides intelligent code assistance with real-time context awareness

import React, { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { EnhancedLinearProgress } from "../common/LoadingStates";
import {
  Box,
  TextField,
  IconButton,
  Chip,
  Stack,
  Typography,
  Tooltip,
  Alert,
  Paper,
  Button,
  Badge,
  List,
  ListItem,
  ListItemText,
  ListItemIcon,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Switch,
  FormControlLabel,
  Menu,
  MenuItem,
  Grid,
  Card,
  CardContent,
  CardActions,
} from "@mui/material";
import {
  Send,
  Edit as EditIcon,
  PlayArrow as ApplyIcon,
  Help as ExplainIcon,
  Speed as OptimizeIcon,
  Build as RefactorIcon,
  BugReport as TestIcon,
  Security as SecurityIcon,
  Code as CodeIcon,
  AutoFixHigh as FixIcon,
  Close as CloseIcon,
  Add as AddIcon,
  Description as DocumentIcon,
  LightbulbOutlined as SuggestionIcon,
  ErrorOutline as ErrorIcon,
  WarningAmber as WarningIcon,
  CheckCircle as SuccessIcon,
  ExpandMore as ExpandMoreIcon,
  Psychology as ContextIcon,
  Apps as GridMenuIcon,
  LocationOn as LocationIcon,
  AutoFixHigh as AutoFixIcon,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import MessageList from "../chat/MessageList";
import CodeDiffViewer from "./CodeDiffViewer";
import AICommandPalette from "./AICommandPalette";
import { extractApplyableCode } from "./applyableCode";
import * as codeIntelligenceService from "../../api/codeIntelligenceService";
import * as chatService from "../../api/chatService";
import { CODE_EDITOR_SESSION_ID } from "../../api/chatService";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";
import { buildSmartContext } from "../../utils/smartContextBuilder";
import { parseCodeStructure, generateStructureSummary } from "../../utils/codeStructureParser";
import { useUiTranslation } from "../../i18n";

const ChatAssistantCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      chatMessages,
      setChatMessages,
      currentTab,
      setOpenTabs,
      activeTabIndex,
      openTabs,
      rulesCutoffEnabled,
      editorContext,
      editorRef,
      projectId,  // Add projectId for RAG context search
      ...props
    },
    ref
  ) => {
    const { t, language } = useUiTranslation();
    // Core state
    const [message, setMessage] = useState("");
    const [pendingCodeEdit, setPendingCodeEdit] = useState(null);
    const [showCommandPalette, setShowCommandPalette] = useState(false);
    const [showDiffViewer, setShowDiffViewer] = useState(false);
    const [gridMenuAnchor, setGridMenuAnchor] = useState(null);

    // Advanced context awareness state
    const [codeContext, setCodeContext] = useState({
      selectedText: "",
      cursorPosition: { line: 0, column: 0 },
      selectionRange: null,
      syntaxErrors: [],
      suggestions: [],
      lastAnalysis: null,
    });

    // Real-time analysis state. Off by default: when on, every pause in typing
    // sends the file to the model (one chat request per pause) and runs a
    // syntax check; the switch's tooltip says so.
    const [realTimeAnalysis, setRealTimeAnalysis] = useState({
      enabled: false,
      errors: [],
      warnings: [],
      suggestions: [],
      performance: { score: 0, issues: [] },
      security: { score: 0, vulnerabilities: [] },
      lastUpdate: null,
    });

    // Recommendations state for structured recommendations from AI
    const [structuredRecommendations, setStructuredRecommendations] = useState([]);

    // UI state
    const [contextExpanded, setContextExpanded] = useState(false);

    // Enhanced loading states
    const [loadingState, setLoadingState] = useState({
      isLoading: false,
      message: '',
      progress: null,
      estimatedTime: null,
      stage: null
    });

    // Loading state helpers
    const startLoading = useCallback((message = t('codeAssistant.loading.processing'), estimatedTime = null) => {
      setLoadingState({
        isLoading: true,
        message,
        progress: null,
        estimatedTime,
        stage: null
      });
    }, []);

    const updateLoadingProgress = useCallback((progress, message = null) => {
      setLoadingState(prev => ({
        ...prev,
        progress,
        message: message || prev.message
      }));
    }, []);

    const stopLoading = useCallback(() => {
      setLoadingState({
        isLoading: false,
        message: '',
        progress: null,
        estimatedTime: null,
        stage: null
      });
    }, []);

    const isLoading = loadingState.isLoading;

    const { startProcess, completeProcess, errorProcess } = useUnifiedProgress();
    const analysisTimeoutRef = useRef(null);

    // Enhanced command system with context awareness
    const AI_COMMANDS = useMemo(() => ({
      explain: {
        label: t('codeAssistant.commands.explain.label'),
        icon: ExplainIcon,
        description: t('codeAssistant.commands.explain.description'),
        color: 'info',
        contextual: true
      },
      edit: {
        label: t('codeAssistant.commands.edit.label'),
        icon: EditIcon,
        description: t('codeAssistant.commands.edit.description'),
        color: 'primary',
        contextual: true
      },
      optimize: {
        label: t('codeAssistant.commands.optimize.label'),
        icon: OptimizeIcon,
        description: t('codeAssistant.commands.optimize.description'),
        color: 'success',
        contextual: true
      },
      refactor: {
        label: t('codeAssistant.commands.refactor.label'),
        icon: RefactorIcon,
        description: t('codeAssistant.commands.refactor.description'),
        color: 'warning',
        contextual: true
      },
      test: {
        label: t('codeAssistant.commands.test.label'),
        icon: TestIcon,
        description: t('codeAssistant.commands.test.description'),
        color: 'secondary',
        contextual: true
      },
      fix: {
        label: t('codeAssistant.commands.fix.label'),
        icon: FixIcon,
        description: t('codeAssistant.commands.fix.description'),
        color: 'error',
        contextual: true
      },
      document: {
        label: t('codeAssistant.commands.document.label'),
        icon: DocumentIcon,
        description: t('codeAssistant.commands.document.description'),
        color: 'info',
        contextual: true
      },
      secure: {
        label: t('codeAssistant.commands.secure.label'),
        icon: SecurityIcon,
        description: t('codeAssistant.commands.secure.description'),
        color: 'error',
        contextual: true
      },
      suggest: {
        label: t('codeAssistant.commands.suggest.label'),
        icon: SuggestionIcon,
        description: t('codeAssistant.commands.suggest.description'),
        color: 'primary',
        contextual: true
      }
    }), [language]); // the labels are translated text

    // Memoized context for stable dependencies to prevent unnecessary re-renders
    const stableContext = useMemo(() => ({
      content: currentTab?.content,
      filePath: currentTab?.filePath,
      language: currentTab?.language,
      selectedText: codeContext.selectedText,
      cursorPosition: codeContext.cursorPosition,
      selectionRange: codeContext.selectionRange
    }), [currentTab?.content, currentTab?.filePath, currentTab?.language, codeContext.selectedText, codeContext.cursorPosition, codeContext.selectionRange]);

    // Memoized related files detection for context panel display
    const relatedFiles = useMemo(() => {
      if (!currentTab?.content) return [];
      const relatedFilePaths = codeIntelligenceService.detectRelatedFiles(
        currentTab.content, 
        currentTab.language || 'javascript', 
        openTabs || []
      );
      return openTabs?.filter(tab => 
        relatedFilePaths.includes(tab.filePath) || 
        (tab.filePath !== currentTab?.filePath && tab.content?.trim())
      ).map(tab => ({
        filePath: tab.filePath || 'untitled',
        content: tab.content || '',
        language: tab.language || 'javascript'
      })) || [];
    }, [currentTab?.content, currentTab?.language, currentTab?.filePath, openTabs]);

    // Update codeContext when editorContext prop changes (real-time Monaco editor integration)
    useEffect(() => {
      if (editorContext) {
        setCodeContext(prev => ({
          ...prev,
          selectedText: editorContext.selectedText || "",
          cursorPosition: editorContext.cursorPosition || { line: 0, column: 0 },
          selectionRange: editorContext.selectionRange || null,
        }));
      }
    }, [editorContext]);

    // Real-time code analysis function — depends only on content/file, not cursor
    const performRealTimeAnalysis = useCallback(async () => {
      const content = currentTab?.content;
      if (!content?.trim() || !realTimeAnalysis.enabled) return;

      // Skip automatic analysis for large files to prevent timeouts
      // Users can still request analysis manually via chat
      const MAX_AUTO_ANALYSIS_SIZE = 5000; // ~100-150 lines
      if (content.length > MAX_AUTO_ANALYSIS_SIZE) {
        return;
      }

      try {
        const context = {
          filePath: currentTab?.filePath || "untitled",
          content: content,
          language: currentTab?.language || "javascript",
        };

        // Perform comprehensive analysis
        const [analysisResult, validationResult] = await Promise.all([
          codeIntelligenceService.analyzeCodeIntelligent(context, "Perform a comprehensive analysis including errors, warnings, performance issues, and security concerns", rulesCutoffEnabled),
          codeIntelligenceService.validateCodeIntelligent(context, rulesCutoffEnabled)
        ]);

        if (analysisResult.success && validationResult.success) {
          // /validate is a real syntax check; when it could not parse the
          // language it says so, and that note is shown instead of "no issues".
          const unchecked = validationResult.mode === "unchecked" && validationResult.validation
            ? [{ message: validationResult.validation }]
            : [];
          setRealTimeAnalysis(prev => ({
            ...prev,
            errors: validationResult.errors || [],
            warnings: [...(validationResult.warnings || []), ...unchecked],
            suggestions: analysisResult.suggestions || [],
            lastUpdate: new Date(),
          }));
          
          // Update structured recommendations if available
          if (analysisResult.suggestions && Array.isArray(analysisResult.suggestions) && analysisResult.suggestions.length > 0) {
            // Check if suggestions are structured recommendations
            const isStructured = analysisResult.suggestions[0]?.type !== undefined && 
                                 analysisResult.suggestions[0]?.filePath !== undefined;
            if (isStructured) {
              setStructuredRecommendations(analysisResult.suggestions);
            }
          }
        } else {
          // Reset on partial failure
          setRealTimeAnalysis(prev => ({
            ...prev,
            errors: analysisResult.success ? [] : prev.errors,
            warnings: validationResult.success ? [] : prev.warnings,
            lastUpdate: new Date(),
          }));
        }
      } catch (error) {
        console.error("Real-time analysis failed:", error);
        // Reset analysis state on error to prevent stale data
        setRealTimeAnalysis(prev => ({
          ...prev,
          errors: [],
          warnings: [],
          suggestions: [],
          lastUpdate: new Date(),
        }));
      }
    }, [currentTab?.content, currentTab?.filePath, currentTab?.language, realTimeAnalysis.enabled]);

    // Debounced analysis trigger
    useEffect(() => {
      if (analysisTimeoutRef.current) {
        clearTimeout(analysisTimeoutRef.current);
      }

      if (realTimeAnalysis?.enabled && currentTab?.content?.trim()) {
        analysisTimeoutRef.current = setTimeout(() => {
          performRealTimeAnalysis();
        }, 2000); // Analyze 2 seconds after code changes
      }

      return () => {
        if (analysisTimeoutRef.current) {
          clearTimeout(analysisTimeoutRef.current);
        }
      };
    }, [stableContext.content, performRealTimeAnalysis, realTimeAnalysis?.enabled]);

    // Cleanup on unmount
    useEffect(() => {
      return () => {
        if (analysisTimeoutRef.current) {
          clearTimeout(analysisTimeoutRef.current);
        }
      };
    }, []);

    // Enhanced context detection
    const _updateCodeContext = useCallback((selection = {}) => {
      setCodeContext(prev => {
        // Only update if something actually changed - optimized comparison
        const newText = selection.text || "";
        const newPosition = selection.position || { line: 0, column: 0 };
        const newRange = selection.range || null;

        // Fast shallow comparison without expensive JSON.stringify
        const textChanged = prev.selectedText !== newText;
        const positionChanged = prev.cursorPosition?.line !== newPosition.line ||
                               prev.cursorPosition?.column !== newPosition.column;
        const rangeChanged = (prev.selectionRange === null) !== (newRange === null) ||
                            (prev.selectionRange && newRange &&
                             (prev.selectionRange.start !== newRange.start ||
                              prev.selectionRange.end !== newRange.end));

        if (!textChanged && !positionChanged && !rangeChanged) {
          return prev; // No change, don't update
        }

        return {
          ...prev,
          selectedText: newText,
          cursorPosition: newPosition,
          selectionRange: newRange,
          lastUpdate: Date.now(),
        };
      });
    }, []);

    // Real-time editor context integration - editorContext prop provides real Monaco editor data
    // No simulation needed - editorContext comes from CodeEditorCard's Monaco editor events

    const handleSendMessage = useCallback(async (userMessage, options = {}) => {
      if (!userMessage.trim() || isLoading) return;
      const { skipRag = false } = options;

      const userMsg = {
        id: `user-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
        role: "user",
        content: userMessage,
        timestamp: new Date(),
        context: {
          hasSelection: Boolean(codeContext.selectedText),
          selectionLength: codeContext.selectedText?.length || 0,
          cursorPosition: codeContext.cursorPosition,
          fileName: currentTab?.filePath || "untitled",
          language: currentTab?.language || "javascript"
        }
      };
      setChatMessages(prev => [...prev, userMsg]);
      setMessage("");
      startLoading(t("codeAssistant.loading.thinking"), 15);

      try {
        const processId = startProcess("ai-chat", t("codeAssistant.progress.processingWithContext"), "llm_processing");

        // Enhanced context with selection and real-time analysis
        // Build multi-file context including related files (reuse memoized relatedFiles)
        const dependencies = currentTab?.content 
          ? codeIntelligenceService.detectRelatedFiles(currentTab.content, currentTab.language || 'javascript', openTabs || [])
          : [];
        
        const enhancedContext = {
          filePath: currentTab?.filePath || "untitled",
          content: currentTab?.content || "",
          language: currentTab?.language || "javascript",
          selectedText: codeContext.selectedText,
          cursorPosition: codeContext.cursorPosition,
          selectionRange: codeContext.selectionRange,
          realtimeAnalysis: realTimeAnalysis,
          rulesCutoff: rulesCutoffEnabled,
          relatedFiles: relatedFiles, // Multi-file context (memoized)
          dependencies: dependencies, // Import/require relationships
          projectContext: {
            openFiles: openTabs?.map(tab => ({
              name: tab.filePath,
              language: tab.language,
              content: tab.content // Include content for better context
            })) || [],
            activeFile: currentTab?.filePath,
            totalFiles: openTabs?.length || 0,
            relatedFilesCount: relatedFiles.length
          }
        };

        let response;
        const lowerMessage = userMessage.toLowerCase();

        // Intelligent message routing with context awareness
        if (codeContext.selectedText && !lowerMessage.startsWith('/')) {
          updateLoadingProgress(25, t("codeAssistant.loading.analyzingSelection"));

          // Use Smart Context Builder for intelligent context extraction
          const smartCtx = buildSmartContext(
            currentTab?.content || '',
            currentTab?.language || 'javascript',
            userMessage,
            { selectedText: codeContext.selectedText }
          );

          // Build context from other open tabs for better understanding
          const otherTabsInfo = (openTabs || [])
            .filter((tab, idx) => idx !== activeTabIndex && tab.content)
            .map(tab => {
              const tabStructure = parseCodeStructure(tab.content, tab.language || 'javascript');
              const summary = generateStructureSummary(tabStructure).split('\n')[0];
              return `- ${tab.filePath || 'untitled'} (${tab.language || 'plaintext'}): ${summary}`;
            })
            .join('\n');

          // Build prompt with smart context
          const selectionPrompt = `You are an AI coding assistant. The user has selected specific code and is asking about it.

CONTEXT:
- File: ${currentTab?.filePath || 'untitled'} (${currentTab?.language || 'javascript'})
- File size: ${currentTab?.content?.length || 0} chars (${smartCtx.type === 'smart' ? 'smart context extracted' : 'full file'})
- Open Files: ${(openTabs || []).length} tabs
${otherTabsInfo ? `Other open files:\n${otherTabsInfo}` : ''}

SELECTED CODE (${codeContext.selectedText.length} chars):
\`\`\`${currentTab?.language || 'javascript'}
${codeContext.selectedText}
\`\`\`

${smartCtx.type === 'smart' ? `RELEVANT FILE CONTEXT (extracted ${smartCtx.stats.sectionsIncluded} sections, ${smartCtx.stats.relevantElementsFound} relevant elements found):
${smartCtx.context}` : `FULL FILE CONTENT:
\`\`\`${currentTab?.language || 'javascript'}
${currentTab?.content || ''}
\`\`\``}

USER QUESTION: ${userMessage}

Please provide a helpful, specific response about the selected code. Analyze the code in context and answer the user's question in detail.`;

          updateLoadingProgress(50, t("codeAssistant.loading.sending"));
          const chatResult = await chatService.sendQuery(selectionPrompt, ['coding', 'selection', 'analysis', currentTab?.language || 'javascript'], null, CODE_EDITOR_SESSION_ID, rulesCutoffEnabled);
          updateLoadingProgress(75, t("codeAssistant.loading.processingResponse"));

          if (!chatResult) {
            throw new Error(t('codeAssistant.errors.noSelectionResponse'));
          }

          if (chatResult && !chatResult.error) {
            // Handle different response formats consistently
            let responseText = '';
            if (typeof chatResult === 'string') {
              responseText = chatResult;
            } else if (chatResult.data?.response) {
              responseText = chatResult.data.response;
            } else if (chatResult.response) {
              responseText = chatResult.response;
            } else if (chatResult.data) {
              responseText = typeof chatResult.data === 'string' ? chatResult.data : JSON.stringify(chatResult.data);
            } else {
              responseText = JSON.stringify(chatResult);
            }

            response = {
              message: responseText,
              success: true,
              type: "selection_analysis"
            };
          } else {
            // Handle specific error types
            let errorMessage = t('codeAssistant.replies.analysisFailed');

            if (chatResult?.validationErrors) {
              errorMessage = t('codeAssistant.replies.validationFailed', { errors: chatResult.validationErrors.map(e => e.message).join(', ') });
            } else if (chatResult?.code === 'RATE_LIMIT_EXCEEDED') {
              errorMessage = t('codeAssistant.replies.rateLimited');
            } else if (chatResult?.error) {
              errorMessage = t('codeAssistant.replies.analysisFailedWithError', { error: chatResult.error });
            }

            // Fallback to code intelligence
            const analysisResult = await codeIntelligenceService.analyzeCodeIntelligent(enhancedContext, selectionPrompt, rulesCutoffEnabled);
            response = {
              message: analysisResult.success ? analysisResult.analysis : errorMessage,
              success: analysisResult.success,
              type: "selection_analysis"
            };
          }
        }

        // Enhanced command processing with context
        else if (lowerMessage.startsWith('/explain')) {
          const target = codeContext.selectedText || enhancedContext.content;
          const explainResult = await codeIntelligenceService.explainCodeIntelligent({
            ...enhancedContext,
            content: target,
            selectedText: codeContext.selectedText
          }, rulesCutoffEnabled);
          response = {
            message: explainResult.success ? explainResult.explanation : t('codeAssistant.replies.explanationFailed', { error: explainResult.error }),
            success: explainResult.success,
            type: "explanation"
          };
        } else if (lowerMessage.startsWith('/edit')) {
          const instructions = userMessage.replace(/^\/edit\s*/, '').trim();
          if (!instructions) {
            response = {
              message: t("codeAssistant.replies.editHelp"),
              success: true,
              type: "help"
            };
          } else {
            const target = codeContext.selectedText || enhancedContext.content;
            const editResult = await codeIntelligenceService.editCodeIntelligent(target, instructions, enhancedContext, rulesCutoffEnabled);
            if (editResult.success) {
              // The reply is prose with code in it; only a fenced block may be
              // applied. No block means the reply is shown and nothing is offered.
              const code = extractApplyableCode(editResult.editedCode, currentTab?.language);
              if (code) {
                const isSelection = Boolean(codeContext.selectedText);
                setPendingCodeEdit({
                  newCode: code,
                  description: isSelection
                    ? t('codeAssistant.replies.editDescriptionSelection', { instructions })
                    : t('codeAssistant.replies.editDescriptionFile', { instructions }),
                  command: 'edit',
                  targetText: codeContext.selectedText,
                  isSelection: Boolean(codeContext.selectedText),
                  editRange: codeContext.selectionRange // Include selection range for precise editing
                });
                response = {
                  message: isSelection
                    ? t('codeAssistant.replies.editReadySelection')
                    : t('codeAssistant.replies.editReadyFile'),
                  success: true,
                  type: "edit_ready"
                };
              } else {
                response = {
                  message: editResult.editedCode || t("codeAssistant.replies.noReply"),
                  success: true,
                  type: "edit_response"
                };
              }
            } else {
              response = {
                message: t('codeAssistant.replies.editFailed', { error: editResult.error }),
                success: false,
                type: "error"
              };
            }
          }
        } else if (lowerMessage.startsWith('/fix')) {
          const fixPrompt = realTimeAnalysis.errors.length > 0
            ? `Fix these specific errors: ${realTimeAnalysis.errors.map(e => e.message || e).join(', ')}`
            : "Find and fix all errors, bugs, and issues in this code";

          const analysisResult = await codeIntelligenceService.analyzeCodeIntelligent(enhancedContext, fixPrompt, rulesCutoffEnabled);
          response = {
            message: analysisResult.success
              ? t('codeAssistant.replies.fixAnalysis', { analysis: analysisResult.analysis })
              : t('codeAssistant.replies.fixFailed', { error: analysisResult.error }),
            success: analysisResult.success,
            type: "fix_analysis"
          };
        } else if (lowerMessage.startsWith('/optimize') || lowerMessage.startsWith('/refactor') || lowerMessage.startsWith('/test') || lowerMessage.startsWith('/document') || lowerMessage.startsWith('/secure')) {
          // Handle other commands with enhanced context
          const commandType = lowerMessage.split(' ')[0].substring(1);
          let result;

          /* eslint-disable no-case-declarations -- each case body is self-contained; scoped suppression beats wrapping every case in braces */
          switch (commandType) {
            case 'optimize':
              result = await codeIntelligenceService.refactorCodeIntelligent(enhancedContext, 'optimize', rulesCutoffEnabled);
              break;
            case 'refactor':
              const refactorType = userMessage.replace(/^\/refactor\s*/, '').trim() || 'structure';
              result = await codeIntelligenceService.refactorCodeIntelligent(enhancedContext, refactorType, rulesCutoffEnabled);
              break;
            case 'test':
              const testFramework = userMessage.replace(/^\/test\s*/, '').trim() || 'auto';
              result = await codeIntelligenceService.generateTestsIntelligent(enhancedContext, testFramework, rulesCutoffEnabled);
              break;
            case 'document':
              result = await codeIntelligenceService.refactorCodeIntelligent(enhancedContext, 'document', rulesCutoffEnabled);
              break;
            case 'secure':
              result = await codeIntelligenceService.analyzeCodeIntelligent(enhancedContext, "Perform comprehensive security analysis", rulesCutoffEnabled);
              response = {
                message: result.success
                  ? t('codeAssistant.replies.securityAnalysis', { analysis: result.analysis })
                  : t('codeAssistant.replies.securityFailed', { error: result.error }),
                success: result.success,
                type: "security_analysis"
              };
              break;
          }

          if (commandType !== 'secure' && result) {
            const commandNames = {
              optimize: t('codeAssistant.replies.commandNames.optimize'),
              refactor: t('codeAssistant.replies.commandNames.refactor'),
              test: t('codeAssistant.replies.commandNames.test'),
              document: t('codeAssistant.replies.commandNames.document'),
            };
            const label = commandNames[commandType];
            const reply = result.refactoredCode || result.tests || '';
            // These commands send the whole file and Apply replaces the whole
            // file, so only a fenced code block from the reply may be applied.
            const code = result.success ? extractApplyableCode(reply, currentTab?.language) : null;
            if (code) {
              setPendingCodeEdit({
                newCode: code,
                description: t('codeAssistant.replies.commandDescription', { command: label }),
                command: commandType
              });
              response = {
                message: t('codeAssistant.replies.commandReady', { command: label }),
                success: true,
                type: "command_ready"
              };
            } else if (result.success) {
              response = {
                message: reply || t('codeAssistant.replies.commandNoReply', { command: label }),
                success: true,
                type: "command_response"
              };
            } else {
              response = {
                message: t('codeAssistant.replies.commandFailed', { command: label, error: result.error }),
                success: false,
                type: "error"
              };
            }
          }
        } else {
          // General conversation with enhanced context using proper chat service
          if (currentTab && currentTab.content?.trim()) {
            // Search for relevant code context from indexed project documents (RAG)
            // Skip RAG for right-click editor actions (code already provided inline)
            let projectContextText = '';
            if (projectId && !skipRag) {
              updateLoadingProgress(20, t("codeAssistant.loading.searchingProject"));
              const ragResults = await codeIntelligenceService.searchProjectCode(projectId, userMessage, 5);
              if (ragResults.success && ragResults.sources && ragResults.sources.length > 0) {
                projectContextText = '\n\nRELEVANT CODE FROM PROJECT (indexed files):\n';
                ragResults.sources.forEach((source, idx) => {
                  const filename = source.filename || source.file_name || `File ${idx + 1}`;
                  const content = source.content || source.text || '';
                  if (content) {
                    projectContextText += `\n### ${filename}:\n\`\`\`\n${content.substring(0, 1500)}\n\`\`\`\n`;
                  }
                });
              }
            }

            updateLoadingProgress(40, t("codeAssistant.loading.buildingContext"));

            const smartCtx = buildSmartContext(
              currentTab.content,
              currentTab.language || 'javascript',
              userMessage,
              { selectedText: codeContext.selectedText }
            );

            let openTabsContext = '';
            const otherTabs = (openTabs || []).filter((tab, idx) =>
              idx !== activeTabIndex && tab.content && tab.content.trim()
            );
            if (otherTabs.length > 0) {
              openTabsContext = '\n\nOTHER OPEN FILES:\n';
              otherTabs.slice(0, 3).forEach((tab) => {
                const tabName = tab.filePath || tab.name || 'untitled';
                const tabLang = tab.language || 'plaintext';
                const tabStructure = parseCodeStructure(tab.content, tabLang);
                const summary = generateStructureSummary(tabStructure);
                openTabsContext += `- **${tabName}** (${tabLang}): ${summary.split('\n').slice(0, 2).join('; ')}\n`;
              });
            }

            const contextPrompt = `You are an AI coding assistant with access to the user's current code context.

CURRENT CONTEXT:
- Active File: ${currentTab.filePath || 'untitled'} (${currentTab.language || 'javascript'})
- File size: ${currentTab.content.length} chars, ${currentTab.content.split('\n').length} lines
- Context mode: ${smartCtx.type === 'smart' ? `Smart (${smartCtx.stats.sectionsIncluded} sections extracted)` : 'Full file'}
- Open Files: ${enhancedContext.projectContext.totalFiles} tabs
${codeContext.selectedText ? `- Selected Code (${codeContext.selectedText.length} chars):\n\`\`\`${currentTab.language}\n${codeContext.selectedText}\n\`\`\`` : ''}
${realTimeAnalysis.errors.length > 0 ? `- Detected Errors: ${realTimeAnalysis.errors.map(e => e.message || e).join(', ')}` : ''}
${projectContextText}
${openTabsContext}

${smartCtx.type === 'smart'
  ? `ACTIVE FILE - RELEVANT SECTIONS (from ${smartCtx.stats.originalSize} chars):
${smartCtx.context}`
  : `ACTIVE FILE CONTENT:
\`\`\`${currentTab.language}
${currentTab.content}
\`\`\``}

USER QUESTION: ${userMessage}

Provide a helpful, specific response. Reference actual code when relevant. If modifications are requested, generate complete, working code.`;

            updateLoadingProgress(60, t("codeAssistant.loading.sending"));
            const chatResult = await chatService.sendQuery(contextPrompt, ['coding', 'assistance', currentTab.language || 'javascript'], null, CODE_EDITOR_SESSION_ID, rulesCutoffEnabled);

            if (!chatResult) {
              throw new Error(t('codeAssistant.errors.noResponse'));
            }

            if (chatResult && !chatResult.error) {
              // Handle different response formats from chat service
              let responseText = '';
              if (typeof chatResult === 'string') {
                responseText = chatResult;
              } else if (chatResult.data?.response) {
                responseText = chatResult.data.response;
              } else if (chatResult.response) {
                responseText = chatResult.response;
              } else if (chatResult.data) {
                responseText = typeof chatResult.data === 'string' ? chatResult.data : JSON.stringify(chatResult.data);
              } else {
                responseText = JSON.stringify(chatResult);
              }

              response = {
                message: responseText,
                success: true,
                type: "contextual_chat"
              };
            } else {
              // Fallback to code intelligence if chat service fails
              const analysisResult = await codeIntelligenceService.analyzeCodeIntelligent(enhancedContext, `Please help with: ${userMessage}`, rulesCutoffEnabled);
              
              // Extract structured recommendations if available
              if (analysisResult.success && analysisResult.suggestions && Array.isArray(analysisResult.suggestions) && analysisResult.suggestions.length > 0) {
                const isStructured = analysisResult.suggestions[0]?.type !== undefined && 
                                     analysisResult.suggestions[0]?.filePath !== undefined;
                if (isStructured) {
                  setStructuredRecommendations(analysisResult.suggestions);
                }
              }
              
              response = {
                message: analysisResult.success
                  ? analysisResult.analysis
                  : t('codeAssistant.replies.requestTrouble', { error: chatResult?.error || analysisResult.error }),
                success: analysisResult.success,
                type: "contextual_chat"
              };
            }
          } else {
            const otherTabsWithContent = (openTabs || []).filter(tab => tab.content && tab.content.trim());

            let noFilePrompt;
            if (otherTabsWithContent.length > 0) {
              let otherFilesContext = 'OPEN FILES:\n';
              otherTabsWithContent.slice(0, 3).forEach((tab) => {
                const tabName = tab.filePath || 'untitled';
                const tabLang = tab.language || 'plaintext';
                const smartCtx = buildSmartContext(tab.content, tabLang, userMessage);
                if (smartCtx.type === 'smart') {
                  otherFilesContext += `\n### ${tabName} (${tabLang}):\n${smartCtx.context}\n`;
                } else {
                  otherFilesContext += `\n### ${tabName} (${tabLang}):\n\`\`\`${tabLang}\n${tab.content}\n\`\`\`\n`;
                }
              });
              noFilePrompt = `You are an AI coding assistant. The user's current tab is empty, but they have ${otherTabsWithContent.length} other file(s) open:\n\n${otherFilesContext}\n\nUSER QUESTION: ${userMessage}\n\nPlease help based on the available context.`;
            } else {
              noFilePrompt = `You are an AI coding assistant. ${userMessage}`;
            }

            const chatResult = await chatService.sendQuery(noFilePrompt, ['coding', 'general'], null, CODE_EDITOR_SESSION_ID, rulesCutoffEnabled);

            if (!chatResult) {
              throw new Error(t('codeAssistant.errors.noGeneralResponse'));
            }

            if (chatResult && !chatResult.error) {
              // Handle different response formats consistently
              let responseText = '';
              if (typeof chatResult === 'string') {
                responseText = chatResult;
              } else if (chatResult.data?.response) {
                responseText = chatResult.data.response;
              } else if (chatResult.response) {
                responseText = chatResult.response;
              } else if (chatResult.data) {
                responseText = typeof chatResult.data === 'string' ? chatResult.data : JSON.stringify(chatResult.data);
              } else {
                responseText = JSON.stringify(chatResult);
              }

              response = {
                message: responseText,
                success: true,
                type: "general_chat"
              };
            } else {
              response = {
                message: "",
                success: true,
                type: "welcome"
              };
            }
          }
        }

        const assistantMsg = {
          id: `assistant-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: response.message,
          timestamp: new Date(),
          metadata: {
            type: response.type || (response.success ? "assistant" : "error"),
            hasCode: pendingCodeEdit !== null,
            success: response.success
          }
        };

        setChatMessages(prev => [...prev, assistantMsg]);
        completeProcess(processId, t("codeAssistant.progress.completed"));

      } catch (error) {
        console.error("Chat error:", error);
        const errorMsg = {
          id: `error-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: t('codeAssistant.replies.error', { message: error.message }),
          timestamp: new Date(),
          metadata: { type: "error" }
        };
        setChatMessages(prev => [...prev, errorMsg]);
        errorProcess("ai-chat", error.message);
      } finally {
        stopLoading();
      }
    }, [currentTab, setChatMessages, startProcess, completeProcess, errorProcess, setPendingCodeEdit, codeContext, realTimeAnalysis, rulesCutoffEnabled, openTabs, projectId]);

    // Auto-send messages marked as pending (from editor right-click → Ask Chat / Fix / Explain)
    const pendingProcessed = useRef(new Set());
    useEffect(() => {
      if (!chatMessages?.length || isLoading) return;
      const last = chatMessages[chatMessages.length - 1];
      if (last?.pending && last?.role === 'user' && !pendingProcessed.current.has(last.id)) {
        pendingProcessed.current.add(last.id);
        const msgSkipRag = last.skipRag || false;
        // handleSendMessage adds the user message itself, so the pending copy
        // goes; keeping it showed the question twice.
        setChatMessages(prev => prev.filter(m => m.id !== last.id));
        // Send through the chat pipeline — skip RAG for editor right-click actions
        handleSendMessage(last.content, { skipRag: msgSkipRag });
      }
    }, [chatMessages, isLoading, handleSendMessage, setChatMessages]);

    // Function to apply code changes to the current tab with support for selection-based edits
    const applyCodeToCurrentTab = useCallback((newCode, description = t("codeAssistant.apply.defaultDescription"), editRange = null) => {
      if (!currentTab || !setOpenTabs) {
        console.error('Cannot apply code: missing required props', { currentTab: !!currentTab, setOpenTabs: !!setOpenTabs });
        return;
      }

      if (!newCode || typeof newCode !== 'string') {
        console.error('Cannot apply code: invalid newCode', { newCode, type: typeof newCode });
        return;
      }

      if (!Array.isArray(openTabs)) {
        console.error('Cannot apply code: openTabs is not an array', { openTabs });
        return;
      }

      if (activeTabIndex < 0 || activeTabIndex >= openTabs.length) {
        console.error('Cannot apply code: invalid activeTabIndex', { activeTabIndex, openTabsLength: openTabs.length });
        return;
      }

      try {
        // If editRange is provided and we have editor context, apply selection-based edit
        if (editRange && codeContext.selectionRange && editorRef?.current) {
          try {
            // Use the selectionRange directly - it's already in Monaco format from CodeEditorCard
            if (!editorRef?.current) {
              throw new Error('Editor reference not available');
            }
            const result = editorRef.current.applyEdit(
              codeContext.selectionRange, 
              newCode, 
              description
            );
            if (result.success) {
              // Update tab content from editor (it will be updated via editor's onChange)
              // Just mark as modified
              if (activeTabIndex >= 0 && activeTabIndex < openTabs.length && setOpenTabs) {
                setOpenTabs(prev => {
                  if (activeTabIndex >= 0 && activeTabIndex < prev.length) {
                    return prev.map((tab, index) =>
                      index === activeTabIndex
                        ? { ...tab, isModified: true }
                        : tab
                    );
                  }
                  return prev;
                });
              }
              
              // Add success message to chat
              const successMsg = {
                id: `success-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                role: "assistant",
                content: t('codeAssistant.apply.appliedToSelection', { description }),
                timestamp: new Date(),
                metadata: { type: "code_applied", editType: "selection" }
              };
              setChatMessages(prev => [...prev, successMsg]);
              return;
            }
          } catch (editError) {
            console.warn('Selection-based edit failed, falling back to full file replacement:', editError);
            // Fall through to full file replacement
          }
        }

        // Full file replacement (existing behavior)
        if (activeTabIndex >= 0 && activeTabIndex < openTabs.length && setOpenTabs) {
          setOpenTabs(prev => {
            if (activeTabIndex >= 0 && activeTabIndex < prev.length) {
              return prev.map((tab, index) =>
                index === activeTabIndex
                  ? { ...tab, content: newCode, isModified: true }
                  : tab
              );
            }
            return prev;
          });
        }

        // Add success message to chat
        const successMsg = {
          id: `success-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: t('codeAssistant.apply.applied', { description }),
          timestamp: new Date(),
          metadata: { type: "code_applied" }
        };
        setChatMessages(prev => [...prev, successMsg]);
      } catch (error) {
        console.error('Failed to apply code changes:', error);
        const errorMsg = {
          id: `apply-error-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: t('codeAssistant.apply.failed', { error: error.message }),
          timestamp: new Date(),
          metadata: { type: "error" }
        };
        setChatMessages(prev => [...prev, errorMsg]);
      }
    }, [currentTab, setOpenTabs, activeTabIndex, openTabs, setChatMessages, codeContext.selectionRange, editorRef]);

    // Function to apply multi-file edits with confirmation dialog
    const _applyMultiFileEdits = useCallback(async (edits) => {
      // edits: [{filePath, newContent, description, editRange?}]
      if (!edits || !Array.isArray(edits) || edits.length === 0) {
        console.error('No edits provided for multi-file application');
        return;
      }

      // Show confirmation dialog (simplified - could be enhanced with MUI Dialog)
      const fileList = edits.map(e => `- ${e.filePath || t('codeAssistant.untitled')}`).join('\n');
      const confirmed = window.confirm(
        t('codeAssistant.multiFile.confirm', { count: edits.length, files: fileList })
      );

      if (!confirmed) {
        return;
      }

      const processId = startProcess("multi-file-edit", t('codeAssistant.multiFile.applying', { count: edits.length }), "llm_processing");
      
      try {
        const results = [];
        let successCount = 0;
        let failureCount = 0;

        for (const edit of edits) {
          try {
            // Find if file is open in a tab
            const tabIndex = openTabs.findIndex(tab => tab.filePath === edit.filePath);
            
            if (tabIndex >= 0) {
              // File is open - update tab
              setOpenTabs(prev => prev.map((tab, idx) =>
                idx === tabIndex
                  ? { ...tab, content: edit.newContent, isModified: true }
                  : tab
              ));
              results.push({ filePath: edit.filePath, success: true });
              successCount++;
            } else {
              // File not open - could open it or save directly
              // For now, just mark as success (file will be updated when opened)
              results.push({ filePath: edit.filePath, success: true, note: "File not open in editor" });
              successCount++;
            }
          } catch (error) {
            console.error(`Failed to apply edit to ${edit.filePath}:`, error);
            results.push({ filePath: edit.filePath, success: false, error: error.message });
            failureCount++;
          }
        }

        completeProcess(processId, t('codeAssistant.multiFile.completedProgress', { succeeded: successCount, failed: failureCount }));

        // Add summary message to chat
        const summaryMsg = {
          id: `multi-file-summary-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: failureCount > 0
            ? t('codeAssistant.multiFile.completeWithFailures', { succeeded: successCount, failed: failureCount })
            : t('codeAssistant.multiFile.complete', { succeeded: successCount }),
          timestamp: new Date(),
          metadata: { type: "multi_file_edit", results }
        };
        setChatMessages(prev => [...prev, summaryMsg]);

      } catch (error) {
        console.error('Multi-file edit failed:', error);
        errorProcess(processId, t('codeAssistant.multiFile.failedProgress', { error: error.message }));
        
        const errorMsg = {
          id: `multi-file-error-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: t('codeAssistant.multiFile.failed', { error: error.message }),
          timestamp: new Date(),
          metadata: { type: "error" }
        };
        setChatMessages(prev => [...prev, errorMsg]);
      }
    }, [openTabs, setOpenTabs, setChatMessages, startProcess, completeProcess, errorProcess]);

    // Enhanced quick action handler for all commands
    const handleQuickAction = useCallback(async (commandKey) => {
      if (!currentTab || !currentTab.content?.trim()) {
        const errorMsg = {
          id: `no-file-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: t("codeAssistant.replies.noActiveFile"),
          timestamp: new Date(),
          metadata: { type: "error" }
        };
        setChatMessages(prev => [...prev, errorMsg]);
        return;
      }

      const command = AI_COMMANDS[commandKey];
      if (!command) return;

      // Set appropriate message for the command
      switch (commandKey) {
        case 'explain':
          await handleSendMessage('/explain');
          break;
        case 'edit':
          setMessage('/edit ');
          break;
        case 'optimize':
          await handleSendMessage('/optimize');
          break;
        case 'refactor':
          await handleSendMessage('/refactor');
          break;
        case 'test':
          await handleSendMessage('/test');
          break;
        case 'fix':
          await handleSendMessage('/fix');
          break;
        case 'document':
          await handleSendMessage('/document');
          break;
        case 'secure':
          await handleSendMessage('/secure');
          break;
        case 'style':
          await handleSendMessage('/style');
          break;
        default:
          setMessage(`/${commandKey} `);
      }
    }, [currentTab, setChatMessages, handleSendMessage, AI_COMMANDS]);

    // Function to show diff viewer
    const handleShowDiff = useCallback(() => {
      if (pendingCodeEdit && currentTab) {
        setShowDiffViewer(true);
      }
    }, [pendingCodeEdit, currentTab]);

    // Function to apply pending code edits directly
    const handleApplyPendingEdit = useCallback(() => {
      if (pendingCodeEdit) {
        applyCodeToCurrentTab(
          pendingCodeEdit.newCode, 
          pendingCodeEdit.description,
          pendingCodeEdit.editRange // Pass edit range for selection-based edits
        );
        setPendingCodeEdit(null);
        setShowDiffViewer(false);
      }
    }, [pendingCodeEdit, applyCodeToCurrentTab]);

    // Function to reject pending code edits
    const handleRejectPendingEdit = useCallback(() => {
      setPendingCodeEdit(null);
      setShowDiffViewer(false);
    }, []);

    const handleKeyPress = useCallback((event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        handleSendMessage(message);
      }
    }, [message, handleSendMessage]);

    // Global keyboard shortcuts
    const handleGlobalKeyDown = useCallback((event) => {
      // Ctrl+K or Cmd+K to open command palette
      if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
        event.preventDefault();
        setShowCommandPalette(true);
      }
      // Ctrl+/ to open command palette (alternative)
      if ((event.ctrlKey || event.metaKey) && event.key === '/') {
        event.preventDefault();
        setShowCommandPalette(true);
      }
    }, []);

    // Add global keyboard event listener
    React.useEffect(() => {
      document.addEventListener('keydown', handleGlobalKeyDown);
      return () => document.removeEventListener('keydown', handleGlobalKeyDown);
    }, [handleGlobalKeyDown]);

    // Grid menu handlers
    const handleGridMenuOpen = useCallback((event) => {
      setGridMenuAnchor(event.currentTarget);
    }, []);

    const handleGridMenuClose = useCallback(() => {
      setGridMenuAnchor(null);
    }, []);

    const handleGridMenuAction = useCallback((commandKey) => {
      handleQuickAction(commandKey);
      handleGridMenuClose();
    }, [handleQuickAction]);

    // Handler for clearing/deleting chat
    const handleDeleteChat = useCallback(() => {
      setChatMessages([]);
      setPendingCodeEdit(null);
      setShowDiffViewer(false);
      setStructuredRecommendations([]);
    }, [setChatMessages]);

    // Handler for starting a new chat
    const handleNewChat = useCallback(() => {
      setChatMessages([]);
      setPendingCodeEdit(null);
      setShowDiffViewer(false);
      setStructuredRecommendations([]);
      setMessage("");
    }, [setChatMessages]);

    // Title bar actions
    const titleBarActions = (
      <>
        <Tooltip title={t("codeAssistant.titleBar.newChat")} arrow>
          <IconButton
            size="small"
            onClick={handleNewChat}
            aria-label={t("codeAssistant.titleBar.startNewChat")}
            sx={{
              color: 'inherit',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.1)',
              }
            }}
          >
            <AddIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t("codeAssistant.titleBar.deleteChat")} arrow>
          <IconButton
            size="small"
            onClick={handleDeleteChat}
            aria-label={t("codeAssistant.titleBar.deleteChatLabel")}
            sx={{
              color: 'inherit',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.1)',
              }
            }}
          >
            <CloseIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t("codeAssistant.titleBar.aiCommands")} arrow>
          <IconButton
            id="grid-menu-button"
            size="small"
            onClick={handleGridMenuOpen}
            aria-label={t("codeAssistant.titleBar.openCommandsMenu")}
            aria-haspopup="menu"
            aria-expanded={Boolean(gridMenuAnchor)}
            sx={{
              color: 'inherit',
              '&:hover': {
                backgroundColor: 'rgba(255, 255, 255, 0.1)',
              }
            }}
          >
            <GridMenuIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </>
    );

    return (
      <>
        <DashboardCardWrapper
          ref={ref}
          title={t("codeAssistant.card.title")}
          cardColor={cardColor}
          onCardColorChange={onCardColorChange}
          isMinimized={isMinimized}
          onToggleMinimize={onToggleMinimize}
          titleBarActions={titleBarActions}
          style={style}
          {...props}
        >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {/* Context Status Bar */}
          <Box sx={{ p: 0.5, borderBottom: 1, borderColor: 'divider', bgcolor: 'action.hover' }}>
            <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
              <Stack direction="row" spacing={1} alignItems="center">
                <Badge badgeContent={realTimeAnalysis.errors.length} color="error" showZero={false}>
                  <ErrorIcon fontSize="small" color={realTimeAnalysis.errors.length > 0 ? "error" : "disabled"} />
                </Badge>
                <Badge badgeContent={realTimeAnalysis.warnings.length} color="warning" showZero={false}>
                  <WarningIcon fontSize="small" color={realTimeAnalysis.warnings.length > 0 ? "warning" : "disabled"} />
                </Badge>
                <Badge badgeContent={realTimeAnalysis.suggestions.length} color="info" showZero={false}>
                  <SuggestionIcon fontSize="small" color={realTimeAnalysis.suggestions.length > 0 ? "info" : "disabled"} />
                </Badge>
                <Typography variant="caption" color="text.secondary">
                  {codeContext.selectedText
                    ? t("codeAssistant.status.selected", { count: codeContext.selectedText.length })
                    : currentTab?.filePath || t("codeAssistant.status.noFile")}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Tooltip title={t("codeAssistant.status.realTimeTooltip")} arrow>
                  <FormControlLabel
                    control={
                      <Switch
                        size="small"
                        checked={realTimeAnalysis.enabled}
                        onChange={(e) => setRealTimeAnalysis(prev => ({ ...prev, enabled: e.target.checked }))}
                        inputProps={{ 'aria-label': t('codeAssistant.status.toggleRealTime') }}
                      />
                    }
                    label=""
                    sx={{ m: 0 }}
                  />
                </Tooltip>
                <Tooltip title={t("codeAssistant.status.contextInfo")} arrow>
                  <IconButton
                    size="small"
                    onClick={() => setContextExpanded(!contextExpanded)}
                    aria-label={t("codeAssistant.status.toggleContext")}
                  >
                    <ContextIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            </Stack>
          </Box>

          {/* Recommendation Panel */}
          {structuredRecommendations.length > 0 && (
            <Box sx={{ p: 1, borderBottom: 1, borderColor: 'divider', maxHeight: '250px', overflow: 'auto' }}>
              <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold', mb: 1, display: 'block' }}>
                {t("codeAssistant.recommendations.title", { count: structuredRecommendations.length })}
              </Typography>
              <Stack spacing={1}>
                {structuredRecommendations.slice(0, 5).map((rec, index) => (
                  <Card key={index} variant="outlined" sx={{ p: 0.5 }}>
                    <CardContent sx={{ p: '8px !important', '&:last-child': { pb: '8px' } }}>
                      <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="space-between">
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mb: 0.5, flexWrap: 'wrap' }}>
                            <Chip
                              label={rec.type || t('codeAssistant.recommendations.defaultType')}
                              size="small"
                              color={
                                rec.type === 'fix' ? 'error' :
                                rec.type === 'optimize' ? 'success' :
                                rec.type === 'security' ? 'warning' :
                                'info'
                              }
                              sx={{ fontSize: '0.65rem', height: '18px' }}
                            />
                            {rec.priority && (
                              <Chip
                                label={rec.priority}
                                size="small"
                                variant="outlined"
                                sx={{ fontSize: '0.65rem', height: '18px' }}
                              />
                            )}
                            {rec.filePath && (
                              <Tooltip title={rec.filePath}>
                                <Chip
                                  icon={<LocationIcon sx={{ fontSize: '0.7rem !important' }} />}
                                  label={rec.filePath.split('/').pop() || rec.filePath}
                                  size="small"
                                  variant="outlined"
                                  sx={{ fontSize: '0.65rem', height: '18px', maxWidth: '150px' }}
                                />
                              </Tooltip>
                            )}
                            {rec.lineRange && (
                              <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                                {t("codeAssistant.recommendations.lines", { start: rec.lineRange.start, end: rec.lineRange.end })}
                              </Typography>
                            )}
                          </Stack>
                          <Typography variant="caption" sx={{ fontSize: '0.7rem', display: 'block' }}>
                            {rec.description || rec.rationale || t('codeAssistant.recommendations.noDescription')}
                          </Typography>
                          {rec.rationale && rec.rationale !== rec.description && (
                            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem', display: 'block', mt: 0.5 }}>
                              {rec.rationale}
                            </Typography>
                          )}
                        </Box>
                        {rec.canAutoApply && rec.suggestedCode && (
                          <CardActions sx={{ p: 0, m: 0 }}>
                            <Button
                              size="small"
                              variant="contained"
                              color="primary"
                              startIcon={<AutoFixIcon />}
                              onClick={() => {
                                // Apply this recommendation
                                setPendingCodeEdit({
                                  newCode: rec.suggestedCode,
                                  description: rec.description || t('codeAssistant.recommendations.applyDescription', { type: rec.type }),
                                  command: rec.type,
                                  editRange: rec.lineRange ? {
                                    startLineNumber: rec.lineRange.start,
                                    startColumn: 1,
                                    endLineNumber: rec.lineRange.end,
                                    endColumn: 999
                                  } : null
                                });
                              }}
                              sx={{ fontSize: '0.65rem', minWidth: 'auto', px: 1 }}
                            >
                              {t("codeAssistant.actions.apply")}
                            </Button>
                          </CardActions>
                        )}
                      </Stack>
                    </CardContent>
                  </Card>
                ))}
                {structuredRecommendations.length > 5 && (
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem', textAlign: 'center', fontStyle: 'italic' }}>
                    {t("codeAssistant.recommendations.more", { count: structuredRecommendations.length - 5 })}
                  </Typography>
                )}
              </Stack>
            </Box>
          )}

          {/* Enhanced Apply Changes Section */}
          {pendingCodeEdit && (
            <Box sx={{ p: 1, borderBottom: 1, borderColor: 'divider' }}>
              <Paper
                variant="outlined"
                sx={{
                  p: 1,
                  mt: 1,
                  bgcolor: 'action.hover',
                  borderColor: 'primary.main'
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                  <Typography variant="caption" color="primary" sx={{ fontWeight: 'bold' }}>
                    {t("codeAssistant.pending.readyToApply", { command: pendingCodeEdit.command?.toUpperCase() || 'EDIT' })}
                  </Typography>
                  <IconButton
                    size="small"
                    onClick={() => setPendingCodeEdit(null)}
                    sx={{ color: 'text.secondary' }}
                  >
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Box>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                  {pendingCodeEdit.description}
                </Typography>
                <Stack direction="row" spacing={1}>
                  <Button
                    startIcon={<ApplyIcon />}
                    onClick={handleApplyPendingEdit}
                    variant="contained"
                    color="primary"
                    size="small"
                    sx={{ fontSize: '0.7rem' }}
                  >
                    {t("codeAssistant.actions.apply")}
                  </Button>
                  <Button
                    startIcon={<CodeIcon />}
                    onClick={handleShowDiff}
                    variant="outlined"
                    color="info"
                    size="small"
                    sx={{ fontSize: '0.7rem' }}
                  >
                    {t("codeAssistant.pending.preview")}
                  </Button>
                  <Button
                    startIcon={<CloseIcon />}
                    onClick={handleRejectPendingEdit}
                    variant="outlined"
                    color="secondary"
                    size="small"
                    sx={{ fontSize: '0.7rem' }}
                  >
                    {t("codeAssistant.pending.discard")}
                  </Button>
                </Stack>
              </Paper>
            </Box>
          )}

          {/* Context Information Panel */}
          {contextExpanded && (
            <Accordion expanded={contextExpanded} onChange={() => setContextExpanded(!contextExpanded)}>
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="caption" sx={{ fontWeight: 'bold' }}>
                  <ContextIcon fontSize="small" sx={{ mr: 1 }} />
                  {t("codeAssistant.context.title")}
                </Typography>
              </AccordionSummary>
              <AccordionDetails sx={{ p: 1 }}>
                <Stack spacing={1}>
                  {/* File Context */}
                  <Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
                      {t("codeAssistant.context.currentFile")}:
                    </Typography>
                    <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                      {currentTab?.filePath || t("codeAssistant.context.noFileOpen")} ({currentTab?.language || t("codeAssistant.context.unknownLanguage")})
                    </Typography>
                  </Box>

                  {/* Selection Context */}
                  {codeContext.selectedText && (
                    <Box>
                      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
                        {t("codeAssistant.context.selectedCode")}:
                      </Typography>
                      <Box sx={{ bgcolor: 'action.hover', p: 0.5, borderRadius: 1, fontSize: '0.7rem', fontFamily: 'monospace', maxHeight: 100, overflow: 'auto' }}>
                        {codeContext.selectedText.substring(0, 200)}{codeContext.selectedText.length > 200 ? '...' : ''}
                      </Box>
                    </Box>
                  )}

                  {/* Real-time Analysis Results */}
                  {realTimeAnalysis.enabled && realTimeAnalysis.lastUpdate && (
                    <Box>
                      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
                        {t("codeAssistant.context.analysisResults")}:
                      </Typography>
                      <List dense sx={{ p: 0 }}>
                        {realTimeAnalysis.errors.map((error, index) => (
                          <ListItem key={`error-${index}`} sx={{ p: 0.5 }}>
                            <ListItemIcon sx={{ minWidth: 20 }}>
                              <ErrorIcon fontSize="small" color="error" />
                            </ListItemIcon>
                            <ListItemText
                              primary={error.message || error}
                              primaryTypographyProps={{ fontSize: '0.7rem' }}
                            />
                          </ListItem>
                        ))}
                        {realTimeAnalysis.warnings.map((warning, index) => (
                          <ListItem key={`warning-${index}`} sx={{ p: 0.5 }}>
                            <ListItemIcon sx={{ minWidth: 20 }}>
                              <WarningIcon fontSize="small" color="warning" />
                            </ListItemIcon>
                            <ListItemText
                              primary={warning.message || warning}
                              primaryTypographyProps={{ fontSize: '0.7rem' }}
                            />
                          </ListItem>
                        ))}
                        {realTimeAnalysis.suggestions.map((suggestion, index) => (
                          <ListItem key={`suggestion-${index}`} sx={{ p: 0.5 }}>
                            <ListItemIcon sx={{ minWidth: 20 }}>
                              <SuggestionIcon fontSize="small" color="info" />
                            </ListItemIcon>
                            <ListItemText
                              primary={suggestion.message || suggestion}
                              primaryTypographyProps={{ fontSize: '0.7rem' }}
                            />
                          </ListItem>
                        ))}
                        {realTimeAnalysis.errors.length === 0 && realTimeAnalysis.warnings.length === 0 && realTimeAnalysis.suggestions.length === 0 && (
                          <ListItem sx={{ p: 0.5 }}>
                            <ListItemIcon sx={{ minWidth: 20 }}>
                              <SuccessIcon fontSize="small" color="success" />
                            </ListItemIcon>
                            <ListItemText
                              primary={t("codeAssistant.context.noIssues")}
                              primaryTypographyProps={{ fontSize: '0.7rem', color: 'success.main' }}
                            />
                          </ListItem>
                        )}
                      </List>
                    </Box>
                  )}

                  {/* Project Context */}
                  <Box>
                    <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
                      {t("codeAssistant.context.project")}:
                    </Typography>
                    <Typography variant="body2" sx={{ fontSize: '0.75rem' }}>
                      {t("codeAssistant.context.filesOpen", { count: openTabs?.length || 0 })}
                      {relatedFiles.length > 0 && (
                        <span> • {t("codeAssistant.context.relatedDetected", { count: relatedFiles.length })}</span>
                      )}
                    </Typography>
                    {relatedFiles.length > 0 && (
                      <Box sx={{ mt: 0.5 }}>
                        <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                          {t("codeAssistant.context.relatedFiles")}:
                        </Typography>
                        {relatedFiles.slice(0, 3).map((rf, idx) => (
                          <Typography key={idx} variant="caption" sx={{ fontSize: '0.65rem', display: 'block', ml: 1 }}>
                            • {rf.filePath || t('codeAssistant.untitled')} ({rf.language})
                          </Typography>
                        ))}
                        {relatedFiles.length > 3 && (
                          <Typography variant="caption" sx={{ fontSize: '0.65rem', ml: 1 }}>
                            {t("codeAssistant.context.more", { count: relatedFiles.length - 3 })}
                          </Typography>
                        )}
                      </Box>
                    )}
                  </Box>
                </Stack>
              </AccordionDetails>
            </Accordion>
          )}

          {/* Enhanced Messages with Real-time Analysis */}
          {realTimeAnalysis.enabled && !contextExpanded && (realTimeAnalysis.errors.length > 0 || realTimeAnalysis.warnings.length > 0) && (
            <Alert
              severity={realTimeAnalysis.errors.length > 0 ? "error" : "warning"}
              action={
                <Button size="small" onClick={() => handleSendMessage("/fix")}>
                  {t("codeAssistant.commands.fix.label")}
                </Button>
              }
              sx={{ m: 1, fontSize: '0.75rem' }}
            >
              {realTimeAnalysis.errors.length > 0
                ? t("codeAssistant.alerts.errorsDetected", { count: realTimeAnalysis.errors.length })
                : t("codeAssistant.alerts.warningsDetected", { count: realTimeAnalysis.warnings.length })
              }
            </Alert>
          )}

          {/* Messages */}
          <Box sx={{
            flex: 1,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column'
          }}>
            <MessageList
              messages={chatMessages}
              isLoading={isLoading}
              sx={{
                flex: 1,
                p: 1,
                overflow: 'auto'
              }}
            />
          </Box>

          {/* Simplified Input */}
          <Box sx={{
            p: 1,
            borderTop: 1,
            borderColor: 'divider',
          }}>
            {/* Loading Progress */}
            {isLoading && (
              <Box sx={{ mb: 0.5 }}>
                <EnhancedLinearProgress
                  progress={loadingState.progress}
                  message={loadingState.message}
                  variant={loadingState.progress !== null ? "determinate" : "indeterminate"}
                  height={2}
                  showPercentage={false}
                />
              </Box>
            )}

            {/* Simplified Input */}
            <Box sx={{ display: 'flex', gap: 0.75, alignItems: 'flex-end' }}>
              <TextField
                fullWidth
                size="small"
                placeholder={t("codeAssistant.input.placeholder")}
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                onKeyPress={handleKeyPress}
                disabled={isLoading}
                multiline
                maxRows={4}
                sx={{
                  '& .MuiInputBase-root': {
                    fontSize: '0.75rem',
                    py: 0.5,
                  },
                  '& .MuiInputBase-input::placeholder': {
                    fontSize: '0.75rem',
                    opacity: 0.6
                  },
                  '& .MuiOutlinedInput-root': {
                    borderRadius: 1.5,
                  }
                }}
              />

              <IconButton
                onClick={() => handleSendMessage(message)}
                disabled={isLoading || !message.trim()}
                color="primary"
                size="small"
                sx={{
                  bgcolor: 'primary.main',
                  color: 'white',
                  p: 0.75,
                  '&:hover': {
                    bgcolor: 'primary.dark',
                  },
                  '&.Mui-disabled': {
                    bgcolor: 'action.disabledBackground',
                    color: 'action.disabled',
                  },
                }}
              >
                <Send sx={{ fontSize: '0.9rem' }} />
              </IconButton>
            </Box>
          </Box>
        </Box>

        </DashboardCardWrapper>

        {/* Grid Menu - Portal to render outside card boundaries */}
        <Menu
          anchorEl={gridMenuAnchor}
          open={Boolean(gridMenuAnchor)}
          onClose={handleGridMenuClose}
          role="menu"
          aria-labelledby="grid-menu-button"
          aria-label={t("codeAssistant.menu.label")}
          PaperProps={{
            sx: {
              mt: 1,
              minWidth: 300,
              maxWidth: 400,
              '& .MuiMenuItem-root': {
                px: 2,
                py: 1,
              }
            }
          }}
          transformOrigin={{ horizontal: 'right', vertical: 'top' }}
          anchorOrigin={{ horizontal: 'right', vertical: 'bottom' }}
        >
          <Box sx={{ p: 1 }}>
            <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 'bold', color: 'text.secondary' }}>
              {t("codeAssistant.titleBar.aiCommands")}
            </Typography>
            <Grid container spacing={1}>
              {Object.entries(AI_COMMANDS).map(([key, command]) => {
                const IconComponent = command.icon;
                return (
                  <Grid item xs={6} key={key}>
                    <MenuItem
                      onClick={() => handleGridMenuAction(key)}
                      disabled={isLoading || !currentTab?.content?.trim()}
                      role="menuitem"
                      aria-label={`${command.label}: ${command.description}`}
                      aria-disabled={isLoading || !currentTab?.content?.trim()}
                      title={`${command.description} ${command.shortcut ? `(${command.shortcut})` : ''}`}
                      sx={{
                        flexDirection: 'column',
                        alignItems: 'center',
                        minHeight: 60,
                        borderRadius: 1,
                        mx: 0.5,
                        '&:hover': {
                          backgroundColor: `${command.color}.50` || 'action.hover',
                        }
                      }}
                    >
                      <IconComponent
                        sx={{
                          fontSize: '1.2rem',
                          mb: 0.5,
                          color: `${command.color}.main`
                        }}
                      />
                      <Typography variant="caption" sx={{ textAlign: 'center', fontSize: '0.7rem' }}>
                        {command.label}
                      </Typography>
                    </MenuItem>
                  </Grid>
                );
              })}
            </Grid>
          </Box>
        </Menu>

        {/* Code Diff Viewer Modal */}
        {showDiffViewer && pendingCodeEdit && currentTab && (
          <CodeDiffViewer
            originalCode={currentTab.content || ''}
            modifiedCode={pendingCodeEdit.newCode || ''}
            language={currentTab.language || 'javascript'}
            description={pendingCodeEdit.description}
            onAccept={handleApplyPendingEdit}
            onReject={handleRejectPendingEdit}
            onClose={() => setShowDiffViewer(false)}
          />
        )}

        {/* AI Command Palette */}
        <AICommandPalette
          commands={AI_COMMANDS}
          isOpen={showCommandPalette}
          onClose={() => setShowCommandPalette(false)}
          onCommandSelect={handleQuickAction}
          currentContext={currentTab ? {
            filePath: currentTab.filePath,
            language: currentTab.language,
            hasContent: Boolean(currentTab.content?.trim())
          } : null}
        />
      </>
    );
  }
);

ChatAssistantCard.displayName = "ChatAssistantCard";

// Memoize component — skip re-render when only content changes (keystrokes).
// Content changes are handled internally via stableContext; the memo only
// needs to track structural changes that affect the card's own rendering.
const MemoizedChatAssistantCard = React.memo(ChatAssistantCard, (prevProps, nextProps) => {
  return (
    prevProps.chatMessages === nextProps.chatMessages &&
    prevProps.currentTab === nextProps.currentTab &&
    prevProps.isMinimized === nextProps.isMinimized &&
    prevProps.rulesCutoffEnabled === nextProps.rulesCutoffEnabled &&
    prevProps.openTabs === nextProps.openTabs &&
    prevProps.editorContext === nextProps.editorContext &&
    prevProps.cardColor === nextProps.cardColor
  );
});

MemoizedChatAssistantCard.displayName = "MemoizedChatAssistantCard";

export default MemoizedChatAssistantCard;