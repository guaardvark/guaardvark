
import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  Paper,
  Typography,
  Tooltip,
  useTheme,
  IconButton,
  Switch,
  FormControlLabel,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  TextField,
  List,
  ListItemButton,
  ListItemText,
  CircularProgress,
} from "@mui/material";
import ReactGridLayout, { WidthProvider } from "react-grid-layout/legacy";
import { useParams, useNavigate, useLocation } from "react-router-dom";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import {
  FormatAlignJustify,
  Code as CodeIcon,
  PlayArrow,
  FormatAlignLeft,
  BugReport,
  Build,
  Dashboard,
  Assessment,
  BlockOutlined,
  CheckCircleOutlined,
  SmartToy,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
} from "@mui/icons-material";

import PageLayout from "../components/layout/PageLayout";
import { useStatus } from "../contexts/StatusContext";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import { useLayout } from "../contexts/LayoutContext";
import * as apiService from "../api";
import * as codeExecutionService from "../api/codeExecutionService";

import FileTreeCard from "../components/codeeditor/FileTreeCard";
import CodeEditorCard from "../components/codeeditor/CodeEditorCard";
import ChatAssistantCard from "../components/codeeditor/ChatAssistantCard";
import SearchCard from "../components/codeeditor/SearchCard";
import OutputCard from "../components/codeeditor/OutputCard";
import WordPressPagesCard from "../components/codeeditor/WordPressPagesCard";
import {
  cardForShortcut,
  editorActionForShortcut,
  findCardElement,
  findDocumentTab,
  isFindSymbolShortcut,
  isSaveSessionShortcut,
} from "../components/codeeditor/codeEditorNav";
import { ContextualLoader } from "../components/common/LoadingStates";
import { useUiTranslation, translate, uiLocale } from "../i18n";

const FixedGridLayout = WidthProvider(ReactGridLayout);

const buildUnavailableReason = () => translate("code.page.buildUnavailable");

const cardComponents = {
  filetree: FileTreeCard,
  editor: CodeEditorCard,
  chat: ChatAssistantCard,
  search: SearchCard,
  output: OutputCard,
  wordpress: WordPressPagesCard,
};

const CodeEditorPage = () => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { projectId } = useParams();
  const codeNavHistory = useNavigate();
  const location = useLocation();
  const { gridSettings } = useLayout();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const [initialStateLoaded, setInitialStateLoaded] = useState(false);
  const [layoutError, setLayoutError] = useState(null);
  const [cardColors, setCardColors] = useState({});
  const [minimizedCards, setMinimizedCards] = useState({});
  const [originalDimensions, setOriginalDimensions] = useState({});
  const [containerSize, setContainerSize] = useState({ width: null, height: null });

  const [openTabs, setOpenTabs] = useState([]);
  const [activeTabIndex, setActiveTabIndex] = useState(0);
  const [fileTree, setFileTree] = useState([]);
  const [chatMessages, setChatMessages] = useState([]);
  const [searchResults, setSearchResults] = useState([]);
  // Run, Debug and Build results, shown on the Output card's Output tab.
  const [runOutput, setRunOutput] = useState([]);
  const appendOutput = useCallback((entry) => {
    setRunOutput((prev) => [...prev, { timestamp: new Date().toISOString(), ...entry }]);
  }, []);

  const [editorContext, setEditorContext] = useState(null);
  const editorRef = useRef(null);

  const [rulesCutoffEnabled, setRulesCutoffEnabled] = useState(true);
  const [availableModels, setAvailableModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [isModelLoading, setIsModelLoading] = useState(false);

  const [symbolSearchOpen, setSymbolSearchOpen] = useState(false);
  const [symbolQuery, setSymbolQuery] = useState('');
  const [symbolResults, setSymbolResults] = useState([]);
  const [symbolLoading, setSymbolLoading] = useState(false);
  const [relatedFiles, setRelatedFiles] = useState([]);
  // {tabId, line}: the editor card moves to that line once the tab is showing.
  const [revealRequest, setRevealRequest] = useState(null);
  const openTabsRef = useRef(openTabs);
  openTabsRef.current = openTabs;

  const { startProcess, completeProcess, errorProcess } = useUnifiedProgress();
  const gridContainerRef = useRef(null);

  /**
   * Open a document in a tab, or switch to the tab already showing it, and
   * optionally move the cursor to a 1-based line.
   */
  const openDocumentTab = useCallback(async (doc, { line } = {}) => {
    const existing = findDocumentTab(openTabsRef.current, doc);
    if (existing !== -1) {
      setActiveTabIndex(existing);
      if (line) setRevealRequest({ tabId: openTabsRef.current[existing].id, line });
      return;
    }
    let content = doc.content;
    // Fetch content if not provided. A failed fetch leaves the tab in the
    // failed state with nothing in it; an empty string must never pass for
    // the document's content, since Save would write it over the document.
    let loadError = null;
    if (content === null || content === undefined) {
      try {
        const { getDocumentContent, getRepoFileContent } = await import("../api/documentService");
        const result = doc.source === 'live_repo'
          ? await getRepoFileContent(doc.relativePath || doc.filePath || "")
          : await getDocumentContent(doc.id);
        if (result && typeof result === "object" && result.error) {
          loadError = result.error;
        } else {
          content = typeof result === "string" ? result : result.content ?? result.data ?? "";
        }
      } catch (err) {
        loadError = err?.message || t("code.page.fetchFailed");
      }
    }
    const { getLanguageFromFilename } = await import("../utils/languageDetector");
    const { loadedTab, failedTab } = await import("../components/codeeditor/tabOrigin");
    const baseTab = {
      id: `doc-${doc.id}-${Date.now()}`,
      filePath: doc.filePath || doc.filename,
      language: getLanguageFromFilename(doc.filename),
      source: doc.source || 'document',
      documentId: doc.source === 'live_repo' ? null : doc.id,
      readOnly: doc.source === 'live_repo',
    };
    const newTab = loadError ? failedTab(baseTab, loadError) : loadedTab(baseTab, content);
    setOpenTabs(prev => {
      const updated = [...prev, newTab];
      setActiveTabIndex(updated.length - 1);
      return updated;
    });
    if (line) setRevealRequest({ tabId: newTab.id, line });
  }, [t]);

  // Handle file opened from Documents page via router state
  useEffect(() => {
    const incoming = location.state?.openFile;
    if (!incoming) return;
    // Clear the state so refreshing doesn't re-open
    window.history.replaceState({}, document.title);
    // Delay slightly to let state restoration finish
    setTimeout(() => openDocumentTab(incoming), 500);
  }, [location.state, openDocumentTab]);

  useEffect(() => {
    const loadModels = async () => {
      try {
        const models = await apiService.getAvailableModels();
        // getAvailableModels returns { error } (a non-array) on failure, so guard
        // explicitly — `models || []` would store that object and crash .map().
        setAvailableModels(Array.isArray(models) ? models : []);
      } catch (err) {
        console.error('Failed to load available models:', err);
        setAvailableModels([]);
      }
    };
    loadModels();
  }, []);

  useEffect(() => {
    if (activeModel && activeModel !== "N/A" && availableModels.length > 0) {
      const modelExists = availableModels.some((model) => model.name === activeModel);
      if (modelExists) {
        setSelectedModel(activeModel);
      }
    }
  }, [activeModel, availableModels]);

  useEffect(() => {
    const handleSymbolShortcut = (e) => {
      if (isFindSymbolShortcut(e)) {
        e.preventDefault();
        setSymbolSearchOpen(true);
      }
    };
    window.addEventListener('keydown', handleSymbolShortcut);
    return () => window.removeEventListener('keydown', handleSymbolShortcut);
  }, []);

  const handleSymbolSearch = useCallback(async (query) => {
    if (!query || query.length < 2) {
      setSymbolResults([]);
      return;
    }
    setSymbolLoading(true);
    try {
      const { searchSymbols } = await import('../api/indexingService');
      const data = await searchSymbols(query);
      setSymbolResults(data.symbols || []);
    } catch (err) {
      console.error('Symbol search failed:', err);
      setSymbolResults([]);
    } finally {
      setSymbolLoading(false);
    }
  }, []);

  const _fetchRelatedFiles = useCallback(async (folderId) => {
    if (!folderId) {
      setRelatedFiles([]);
      return;
    }
    try {
      const { searchFiles } = await import('../api/indexingService');
      const data = await searchFiles('', { folderId });
      setRelatedFiles((data.files || []).filter(f => f.is_code_file).slice(0, 10));
    } catch (err) {
      console.error('Failed to fetch related files:', err);
      setRelatedFiles([]);
    }
  }, []);

  const handleModelChange = async (modelName) => {
    if (!modelName || modelName === activeModel) return;

    if (!availableModels || !Array.isArray(availableModels) || !availableModels.some(m => m.name === modelName)) {
      errorProcess("model-change", t("code.page.modelNotFound", { model: modelName }));
      return;
    }

    setIsModelLoading(true);
    try {
      await apiService.setModel(modelName);
      setSelectedModel(modelName);
    } catch (err) {
      console.error('Failed to set model:', err);
      errorProcess("model-change", t("code.page.modelChangeFailed", { error: err.message }));
      setSelectedModel(activeModel || "");
    } finally {
      setIsModelLoading(false);
    }
  };

  const {
    CONTAINER_PADDING_PX,
    CARD_MARGIN_PX,
    COLS_COUNT,
    ROW_HEIGHT_PX,
    cardMinGridW,
    cardMinGridH,
  } = gridSettings;

  const measureContainer = useCallback(() => {
    const element = gridContainerRef.current?.parentElement || gridContainerRef.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    setContainerSize({
      width: rect?.width && rect.width > 0 ? rect.width : null,
      height: rect?.height && rect.height > 0 ? rect.height : null,
    });
  }, []);

  useEffect(() => {
    measureContainer();
    const handleResize = () => measureContainer();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [measureContainer]);

  const maxRows = useMemo(() => {
    if (!containerSize.height) return null;
    const usableHeight = Math.max(containerSize.height - 2 * CONTAINER_PADDING_PX, 0);
    return Math.max(1, Math.floor(usableHeight / ROW_HEIGHT_PX));
  }, [containerSize.height, CONTAINER_PADDING_PX, ROW_HEIGHT_PX]);

  const currentTab = openTabs && activeTabIndex >= 0 && activeTabIndex < openTabs.length
    ? openTabs[activeTabIndex]
    : null;

  const handleEditorContextChange = useCallback((context) => {
    setEditorContext(context);
  }, []);

  // Handle right-click and lightbulb chat actions from the editor (Ask Chat,
  // Fix This, Explain, Optimize, Add to Chat). `extra.problems` carries the
  // editor's own marker messages when a quick fix asked for the help.
  const handleEditorChatAction = useCallback((action, selectedText, filePath, extra = {}) => {
    if (!selectedText?.trim()) return;

    const codeBlock = "```" + (currentTab?.language || '') + "\n" + selectedText + "\n```";
    const problems = Array.isArray(extra?.problems) && extra.problems.length > 0
      ? `\n\nThe editor reports:\n${extra.problems.map((p) => `- ${p}`).join("\n")}`
      : "";

    switch (action) {
      case 'ask': {
        const msg = {
          id: Date.now().toString(),
          role: 'user',
          content: `Regarding this code from ${filePath || 'the editor'}:\n\n${codeBlock}\n\nWhat does this do and are there any issues?`,
          timestamp: new Date().toISOString(),
          pending: true,
          skipRag: true,
        };
        setChatMessages(prev => [...prev, msg]);
        break;
      }
      case 'fix': {
        const msg = {
          id: Date.now().toString(),
          role: 'user',
          content: `Fix any issues in this code from ${filePath || 'the editor'}:\n\n${codeBlock}${problems}`,
          timestamp: new Date().toISOString(),
          pending: true,
          skipRag: true,
        };
        setChatMessages(prev => [...prev, msg]);
        break;
      }
      case 'optimize': {
        const msg = {
          id: Date.now().toString(),
          role: 'user',
          content: `Optimize this code from ${filePath || 'the editor'} and explain what changed:\n\n${codeBlock}`,
          timestamp: new Date().toISOString(),
          pending: true,
          skipRag: true,
        };
        setChatMessages(prev => [...prev, msg]);
        break;
      }
      case 'explain': {
        const msg = {
          id: Date.now().toString(),
          role: 'user',
          content: `Explain this code from ${filePath || 'the editor'}:\n\n${codeBlock}`,
          timestamp: new Date().toISOString(),
          pending: true,
          skipRag: true,
        };
        setChatMessages(prev => [...prev, msg]);
        break;
      }
      case 'add': {
        // Just add the code as context without asking a question
        const msg = {
          id: Date.now().toString(),
          role: 'user',
          content: `[Added code from ${filePath || 'the editor'}]\n\n${codeBlock}`,
          timestamp: new Date().toISOString(),
          context: true,
        };
        setChatMessages(prev => [...prev, msg]);
        break;
      }
      default:
        break;
    }
  }, [currentTab?.language, setChatMessages]);

  const metrics = useMemo(() => {
    if (!currentTab?.content) {
      return {
        lines: 0,
        characters: 0,
        words: 0,
        functions: 0,
        imports: 0,
      };
    }

    const content = currentTab.content || '';
    const lines = content.split('\n');

    return {
      lines: lines.length,
      characters: content.length,
      words: content.split(/\s+/).filter(word => word.length > 0).length,
      functions: (content.match(/function\s+\w+|const\s+\w+\s*=\s*\(/g) || []).length,
      imports: (content.match(/import\s+.*from|const\s+.*=\s*require/g) || []).length,
    };
  }, [currentTab?.content, currentTab?.id]);

  // A folder the build API may run in. Projects carry no folder yet, so this
  // is null and Build stays disabled with buildUnavailableReason().
  const buildTarget = null;

  const handleQuickAction = useCallback(async (action) => {
    if (action !== 'build' && !currentTab?.content) {
      console.warn('No active tab or content to perform action on');
      return;
    }

    const processId = `code-action-${action}-${Date.now()}`;
    
    try {
      /* eslint-disable no-case-declarations -- each case body is self-contained; wrapping every case in braces is noisier than this scoped suppression */
      switch (action) {
        case 'run':
          startProcess(processId, t("code.page.running"), 'code');
          
          const language = currentTab.language || 'javascript';
          let result;
          
          if (language === 'python') {
            result = await codeExecutionService.executePythonCode(currentTab.content);
          } else if (language === 'javascript' || language === 'typescript') {
            result = await codeExecutionService.executeJavaScriptCode(currentTab.content);
          } else {
            throw new Error(t("code.page.runUnsupported", { language }));
          }
          
          if (result.success) {
            completeProcess(processId, t("code.page.runDone"));
            appendOutput({
              type: result.exitCode ? 'error' : 'output',
              label: t("code.page.runLabel", { language }),
              message: result.output || result.error || t("code.page.noOutput"),
            });
          } else {
            errorProcess(processId, result.error || t("code.page.runFailed"));
            appendOutput({ type: 'error', label: t("code.page.runLabel", { language }), message: result.error || result.stderr });
          }
          break;

        case 'format':
          startProcess(processId, t("code.page.formatting"), 'code');
          
          const formatResult = await codeExecutionService.formatCode(
            currentTab.content,
            currentTab.language || 'javascript'
          );
          
          if (formatResult.success) {
            const updatedTabs = [...openTabs];
            updatedTabs[activeTabIndex] = {
              ...updatedTabs[activeTabIndex],
              content: formatResult.formattedCode,
              isModified: true
            };
            setOpenTabs(updatedTabs);
            
            completeProcess(processId, t("code.page.formatDone"));
          } else {
            errorProcess(processId, formatResult.error || t("code.page.formatFailed"));
          }
          break;

        case 'debug':
          startProcess(processId, t("code.page.analyzing"), 'code');
          
          const lintResult = await codeExecutionService.lintCode(
            currentTab.content,
            currentTab.language || 'javascript'
          );
          
          if (lintResult.success) {
            completeProcess(processId, lintResult.tool
              ? t("code.page.analysisDoneTool", { tool: lintResult.tool, score: lintResult.score })
              : t("code.page.analysisDone", { score: lintResult.score }));

            const issues = [
              ...lintResult.errors.map((issue) => ({ ...issue, kind: 'error' })),
              ...lintResult.warnings.map((issue) => ({ ...issue, kind: 'warning' })),
            ];
            const lines = issues.map((issue) => `${issue.kind === 'error' ? 'error' : 'warning'} line ${issue.line}:${issue.column || 1} ${issue.code ? `[${issue.code}] ` : ''}${issue.message}`);
            if (lintResult.note) lines.push(lintResult.note);
            appendOutput({
              type: issues.some((issue) => issue.kind === 'error') ? 'error' : issues.length ? 'warning' : 'output',
              label: lintResult.tool ? t("code.page.debugLabelTool", { tool: lintResult.tool }) : t("code.page.debugLabel"),
              message: lines.length ? lines.join('\n') : t("code.page.noProblems"),
            });
          } else {
            errorProcess(processId, lintResult.error || t("code.page.analysisFailed"));
            appendOutput({ type: 'error', label: t("code.page.debugLabel"), message: lintResult.error || t("code.page.analysisFailed") });
          }
          break;

        case 'build': {
          // Projects have no folder on disk yet, so there is nothing to build;
          // the toolbar keeps the button disabled with that reason.
          if (!buildTarget) {
            errorProcess(processId, buildUnavailableReason());
            break;
          }
          startProcess(processId, t("code.page.building"), 'code');
          const buildResult = await codeExecutionService.buildProject(buildTarget);

          if (buildResult.success) {
            completeProcess(processId, t("code.page.buildDone", { seconds: buildResult.buildTime.toFixed(2) }));
            appendOutput({ type: 'output', label: t("code.page.buildLabel"), message: buildResult.output || t("code.page.noOutput") });
          } else {
            errorProcess(processId, buildResult.error || t("code.page.buildFailed"));
            appendOutput({ type: 'error', label: t("code.page.buildLabel"), message: buildResult.error || t("code.page.buildFailed") });
          }
          break;
        }

        default:
          console.warn('Unknown action:', action);
          break;
      }
    } catch (error) {
      console.error(`Failed to execute ${action}:`, error);
      errorProcess(processId, t("code.page.actionFailed", { action, error: error.message }));
    }
  }, [currentTab, openTabs, activeTabIndex, setOpenTabs, appendOutput, startProcess, completeProcess, errorProcess, buildTarget, t]);

  // Keyboard shortcuts for the toolbar actions, listened for on the editor
  // card only, so Ctrl+R anywhere else on the page still reloads the browser.
  const handleEditorShortcut = useCallback((event) => {
    const action = editorActionForShortcut(event);
    if (!action) return;
    if (action === 'build' ? !buildTarget : !currentTab?.content) return;
    event.preventDefault();
    handleQuickAction(action);
  }, [buildTarget, currentTab?.content, handleQuickAction]);

  const layoutProfiles = useMemo(() => {
    const leftCols = Math.round(COLS_COUNT * 0.2);
    const centerCols = Math.round(COLS_COUNT * 0.6);
    const rightCols = Math.max(COLS_COUNT - leftCols - centerCols, 1);
    const totalRows = 74;
    const halfRows = Math.floor(totalRows / 2);

    return {
      default: {
        name: "Default",
        icon: Dashboard,
        tooltip: () => t("code.page.layoutDefaultTooltip"),
        layout: [
          { i: "filetree", x: 0, y: 0, w: leftCols, h: halfRows },
          { i: "search", x: 0, y: halfRows, w: leftCols, h: halfRows },
          { i: "editor", x: leftCols, y: 0, w: centerCols, h: totalRows },
          { i: "chat", x: leftCols + centerCols, y: 0, w: rightCols, h: halfRows },
          { i: "output", x: leftCols + centerCols, y: halfRows, w: rightCols, h: halfRows },
        ]
      }
    };
  }, [COLS_COUNT, t]);

  const defaultLayoutProfile = layoutProfiles.default;
  const DefaultLayoutIcon = defaultLayoutProfile?.icon || Dashboard;

  const defaultCodeEditorLayout = useMemo(() => {
    const items = [...layoutProfiles.default.layout];
    items.forEach((it) => {
      it.minW = cardMinGridW;
      it.minH = cardMinGridH;
      it.isDraggable = true;
      it.isResizable = true;
    });
    return items;
  }, [layoutProfiles, cardMinGridW, cardMinGridH]);

  const [layout, setLayout] = useState(defaultCodeEditorLayout);

  useEffect(() => {
    try {
      const savedCutoff = localStorage.getItem('codeEditor_rulesCutoff');
      if (savedCutoff !== null) {
        setRulesCutoffEnabled(JSON.parse(savedCutoff));
      }
    } catch (error) {
      console.error('Failed to parse rules cutoff preference from localStorage:', error);
      setRulesCutoffEnabled(true);
    }
  }, []);

  const handleRulesCutoffChange = (enabled) => {
    setRulesCutoffEnabled(enabled);
    localStorage.setItem('codeEditor_rulesCutoff', JSON.stringify(enabled));
  };

  useEffect(() => {
    const fetchCodeEditorState = async () => {
      setLayoutError(null);
      try {
        const res = await fetch("/api/state/code-editor");
        if (!res.ok) {
          if (res.status === 404) {
            console.warn("CodeEditor: No saved state found. Using defaults.");
            setLayout(defaultCodeEditorLayout);
            setCardColors({});
            setMinimizedCards({});
            setOriginalDimensions({});
          } else {
            console.error(`Failed to fetch code editor state: ${res.statusText} (Status: ${res.status})`);
            setLayout(defaultCodeEditorLayout);
            setCardColors({});
            setMinimizedCards({});
            setOriginalDimensions({});
          }
        } else {
          let savedState;
          try {
            savedState = await res.json();
          } catch (jsonError) {
            console.error("Failed to parse saved state JSON:", jsonError);
            setLayout(defaultCodeEditorLayout);
            setCardColors({});
            setMinimizedCards({});
            setOriginalDimensions({});
            setInitialStateLoaded(true);
            return;
          }

          let layoutToApply = null;
          if (
            Array.isArray(savedState.layout) &&
            savedState.layout.length > 0
          ) {
            layoutToApply = savedState.layout;
          }

          let validatedLayout = null; // Declare outside the if block so it's accessible later
          if (layoutToApply) {
            if (process.env.NODE_ENV === 'development') {
              console.log("CodeEditor: Found saved layout. Applying...");
            }
            
            validatedLayout = defaultCodeEditorLayout.map((defaultItem) => {
              const savedItem = layoutToApply.find(
                (item) => item.i === defaultItem.i,
              );
              
              if (savedItem) {
                const savedH = typeof savedItem.h === 'number' ? savedItem.h : defaultItem.h;
                const savedMinH = typeof savedItem.minH === 'number' ? savedItem.minH : defaultItem.minH;
                const savedMaxH = savedItem.maxH !== undefined ? savedItem.maxH : undefined;
                
                let validMaxH = savedMaxH;
                if (validMaxH !== undefined) {
                  if (validMaxH < savedH || validMaxH < savedMinH) {
                    validMaxH = undefined;
                  }
                }
                
                return {
                  ...defaultItem,
                  x: typeof savedItem.x === 'number' ? savedItem.x : defaultItem.x,
                  y: typeof savedItem.y === 'number' ? savedItem.y : defaultItem.y,
                  w: typeof savedItem.w === 'number' ? savedItem.w : defaultItem.w,
                  h: savedH,
                  minW: typeof savedItem.minW === 'number' ? savedItem.minW : defaultItem.minW,
                  minH: savedMinH,
                  maxW: savedItem.maxW !== undefined ? savedItem.maxW : defaultItem.maxW,
                  maxH: validMaxH,
                  isDraggable: savedItem.isDraggable !== undefined ? savedItem.isDraggable : defaultItem.isDraggable,
                  isResizable: savedItem.isResizable !== undefined ? savedItem.isResizable : defaultItem.isResizable,
                };
              }
              return defaultItem;
            });
            
            const savedCardIds = new Set(validatedLayout.map(item => item.i));
            const newCards = layoutToApply.filter(item => !savedCardIds.has(item.i));
            
            if (newCards.length > 0) {
              const { cardMinGridW, cardMinGridH } = gridSettings;
              newCards.forEach(savedItem => {
                validatedLayout.push({
                  i: savedItem.i,
                  x: typeof savedItem.x === 'number' ? savedItem.x : 0,
                  y: typeof savedItem.y === 'number' ? savedItem.y : 0,
                  w: typeof savedItem.w === 'number' ? savedItem.w : cardMinGridW,
                  h: typeof savedItem.h === 'number' ? savedItem.h : cardMinGridH,
                  minW: typeof savedItem.minW === 'number' ? savedItem.minW : cardMinGridW,
                  minH: typeof savedItem.minH === 'number' ? savedItem.minH : cardMinGridH,
                  maxW: savedItem.maxW,
                  maxH: savedItem.maxH,
                  isDraggable: savedItem.isDraggable !== undefined ? savedItem.isDraggable : true,
                  isResizable: savedItem.isResizable !== undefined ? savedItem.isResizable : true,
                });
              });
            }
          } else {
            setLayout(defaultCodeEditorLayout);
          }

          if (savedState.cardColors) {
            setCardColors(savedState.cardColors);
          }

          if (savedState.minimizedCards) {
            setMinimizedCards(savedState.minimizedCards);
          }

          if (savedState.originalDimensions && typeof savedState.originalDimensions === 'object') {
            setOriginalDimensions(savedState.originalDimensions);
          }

          if (layoutToApply && validatedLayout) {
            const minimizedHeight = Math.max(1, Math.round(48 / ROW_HEIGHT_PX));
            const adjustedLayout = validatedLayout.map((item) => {
              if (savedState.minimizedCards && savedState.minimizedCards[item.i]) {
                return {
                  ...item,
                  h: minimizedHeight,
                  minH: minimizedHeight,
                  maxH: minimizedHeight,
                };
              }
              return item;
            });
            setLayout(adjustedLayout);
          }
        }

        try {
          const sessionRes = await fetch("/api/state/code-editor/session");
          if (sessionRes.ok) {
            const sessionData = await sessionRes.json();
            if (process.env.NODE_ENV === 'development') {
              console.log("CodeEditor: Found saved session. Restoring tabs and data...");
            }

            if (Array.isArray(sessionData.openTabs)) {
              setOpenTabs(sessionData.openTabs);
              if (typeof sessionData.activeTabIndex === 'number') {
                const validIndex = Math.max(0, Math.min(sessionData.activeTabIndex, sessionData.openTabs.length - 1));
                setActiveTabIndex(validIndex);
              }
            } else if (typeof sessionData.activeTabIndex === 'number') {
              setActiveTabIndex(0);
            }
            if (Array.isArray(sessionData.chatMessages)) {
              setChatMessages(sessionData.chatMessages);
            }
            if (Array.isArray(sessionData.fileTree)) {
              setFileTree(sessionData.fileTree);
            }
            if (Array.isArray(sessionData.searchResults)) {
              setSearchResults(sessionData.searchResults);
            }
          } else if (sessionRes.status !== 404) {
            console.warn("Failed to load session data:", sessionRes.statusText);
          }
        } catch (sessionErr) {
          console.warn("Failed to load session data:", sessionErr);
        }

      } catch (err) {
        console.error("Failed to load code editor state:", err);
        setLayoutError("LOAD_FAILED");
        setLayout(defaultCodeEditorLayout);
        setCardColors({});
        setMinimizedCards({});
        setOriginalDimensions({});
      }
      setInitialStateLoaded(true);
    };
    fetchCodeEditorState();
  }, [defaultCodeEditorLayout, ROW_HEIGHT_PX]);

  const saveCodeEditorState = useCallback(
    async (newLayout, newCardColors, newMinimizedCards, newOriginalDimensions) => {
      if (!initialStateLoaded) {
        if (process.env.NODE_ENV === 'development') {
          console.log("CodeEditor: Skipping saveCodeEditorState during initial load");
        }
        return;
      }

      try {
        const stateToSave = {
          layout: newLayout ?? layout,
          cardColors: newCardColors ?? cardColors,
          minimizedCards: newMinimizedCards ?? minimizedCards,
          originalDimensions: newOriginalDimensions ?? originalDimensions,
          lastSaved: new Date().toISOString(),
        };

        if (process.env.NODE_ENV === 'development') {
          console.log("CodeEditor: Saving state to backend", {
            layoutItems: stateToSave.layout?.length || 0,
            minimizedCards: Object.keys(stateToSave.minimizedCards || {}).length,
            cardColors: Object.keys(stateToSave.cardColors || {}).length,
          });
        }

        const res = await fetch("/api/state/code-editor", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(stateToSave),
        });

        if (!res.ok) {
          throw new Error(`Failed to save code editor state (${res.status})`);
        }
        setLayoutError(null);
        if (process.env.NODE_ENV === 'development') {
          console.log("CodeEditor: State saved successfully");
        }
      } catch (err) {
        console.error("Failed to save code editor state:", err);
        setLayoutError("SAVE_FAILED");
      }
    },
    [layout, cardColors, minimizedCards, originalDimensions, initialStateLoaded],
  );

  const lastSavedSession = useRef(null);
  const [isSaving, setIsSaving] = useState(false);
  const [lastSaveTime, setLastSaveTime] = useState(null);

  const saveCodeEditorSession = useCallback(
    async () => {
      const sessionToSave = {
        openTabs,
        activeTabIndex,
        chatMessages,
        fileTree,
        searchResults,
        lastSaved: new Date().toISOString(),
      };

      const sessionString = JSON.stringify({
        openTabs: openTabs.map(tab => ({
          id: tab.id,
          filePath: tab.filePath,
          content: tab.content?.substring(0, 100),
          language: tab.language,
          isModified: tab.isModified,
        })),
        activeTabIndex,
        chatMessagesCount: chatMessages.length,
        chatMessagesLastId: chatMessages.length > 0 ? chatMessages[chatMessages.length - 1]?.id : null,
        fileTreeCount: fileTree.length,
        searchResultsCount: searchResults.length,
      });

      if (lastSavedSession.current === sessionString) {
        return;
      }

      setIsSaving(true);
      try {
        const res = await fetch("/api/state/code-editor/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sessionToSave),
        });

        if (!res.ok) {
          throw new Error(`Failed to save code editor session (${res.status})`);
        }

        lastSavedSession.current = sessionString;
        setLastSaveTime(new Date());

        if (process.env.NODE_ENV === 'development') {
          console.log("CodeEditor: Session saved successfully");
        }
      } catch (err) {
        console.error("Failed to save code editor session:", err);
      } finally {
        setIsSaving(false);
      }
    },
    [openTabs, activeTabIndex, chatMessages, fileTree, searchResults],
  );

  const onLayoutChange = useCallback(
    (newLayout) => {
      if (!initialStateLoaded) {
        if (process.env.NODE_ENV === 'development') {
          console.log("CodeEditor: Skipping onLayoutChange during initial load");
        }
        return;
      }

      if (process.env.NODE_ENV === 'development') {
        console.log("CodeEditor: onLayoutChange triggered", newLayout.length, "items");
      }

      const validLayout = newLayout.filter((item) => 
        item !== undefined && 
        item !== null &&
        typeof item === 'object' &&
        typeof item.i === 'string' &&
        typeof item.x === 'number' &&
        typeof item.y === 'number' &&
        typeof item.w === 'number' &&
        typeof item.h === 'number'
      ).map(item => ({
        i: item.i,
        x: item.x,
        y: item.y,
        w: item.w,
        h: item.h,
        minW: item.minW,
        minH: item.minH,
        maxW: item.maxW,
        maxH: item.maxH,
        isDraggable: item.isDraggable,
        isResizable: item.isResizable,
      }));
      
      const minimizedHeight = Math.max(1, Math.round(48 / ROW_HEIGHT_PX));
      
      const adjustedLayout = validLayout.map((item) => {
        if (minimizedCards[item.i]) {
          return {
            ...item,
            w: item.w,
            h: minimizedHeight,
            minH: minimizedHeight,
            maxH: minimizedHeight,
          };
        }
        const cleanedItem = { ...item };
        if (cleanedItem.maxH === minimizedHeight && cleanedItem.h > minimizedHeight) {
          delete cleanedItem.maxH;
        }
        return cleanedItem;
      });

      const boundedLayout = adjustedLayout.map((item) => {
        const boundedW = Math.min(item.w, COLS_COUNT);
        const maxX = Math.max(0, COLS_COUNT - boundedW);
        const boundedX = Math.min(Math.max(item.x, 0), maxX);
        let boundedH = item.h;
        let boundedY = Math.max(item.y, 0);

        if (maxRows) {
          boundedH = Math.min(boundedH, maxRows);
          const maxY = Math.max(0, maxRows - boundedH);
          boundedY = Math.min(boundedY, maxY);
        }

        return {
          ...item,
          x: boundedX,
          y: boundedY,
          w: boundedW,
          h: boundedH,
        };
      });
      
      setLayout(boundedLayout);
      if (process.env.NODE_ENV === 'development') {
        console.log("CodeEditor: Saving layout state", boundedLayout.length, "items");
      }
      saveCodeEditorState(boundedLayout, cardColors, minimizedCards, originalDimensions);
    },
    [cardColors, minimizedCards, originalDimensions, saveCodeEditorState, ROW_HEIGHT_PX, initialStateLoaded, COLS_COUNT, maxRows],
  );

  const handleCardColorChange = useCallback(
    (cardId, color) => {
      const newCardColors = { ...cardColors, [cardId]: color };
      setCardColors(newCardColors);
      saveCodeEditorState(layout, newCardColors, minimizedCards, originalDimensions);
    },
    [cardColors, layout, minimizedCards, originalDimensions, saveCodeEditorState],
  );

  const handleToggleMinimize = useCallback(
    (cardId) => {
      const newMinimizedCards = {
        ...minimizedCards,
        [cardId]: !minimizedCards[cardId],
      };
      setMinimizedCards(newMinimizedCards);
      
      const minimizedHeight = Math.max(1, Math.round(48 / ROW_HEIGHT_PX));
      
      const newOriginalDimensions = { ...originalDimensions };
      const adjustedLayout = layout.map((item) => {
        if (item.i === cardId) {
          if (newMinimizedCards[cardId]) {
            newOriginalDimensions[cardId] = { w: item.w, h: item.h };
            return {
              ...item,
              w: item.w,
              h: minimizedHeight,
              minH: minimizedHeight,
              maxH: minimizedHeight,
            };
          } else {
            const original = newOriginalDimensions[cardId];
            if (original) {
              delete newOriginalDimensions[cardId];
              const restored = {
                ...item,
                w: original.w,
                h: original.h,
              };
              if (restored.maxH === minimizedHeight) {
                delete restored.maxH;
              }
              return restored;
            }
            return item;
          }
        }
        return item;
      });
      
      setOriginalDimensions(newOriginalDimensions);
      setLayout(adjustedLayout);
      saveCodeEditorState(adjustedLayout, cardColors, newMinimizedCards, newOriginalDimensions);
    },
    [minimizedCards, layout, cardColors, saveCodeEditorState, ROW_HEIGHT_PX, originalDimensions],
  );

  const handleResetLayout = useCallback(() => {
    setLayout(defaultCodeEditorLayout);
    setCardColors({});
    setMinimizedCards({});
    setOriginalDimensions({});
    saveCodeEditorState(defaultCodeEditorLayout, {}, {}, {});
  }, [defaultCodeEditorLayout, saveCodeEditorState]);

  const applyLayoutProfile = useCallback((profileKey) => {
    const profile = layoutProfiles[profileKey];
    if (!profile) return;

    const newLayout = profile.layout.map(item => ({
      ...item,
      minW: cardMinGridW,
      minH: cardMinGridH,
      isDraggable: true,
      isResizable: true,
    }));

    setLayout(newLayout);
    saveCodeEditorState(newLayout, cardColors, minimizedCards, originalDimensions);
  }, [layoutProfiles, cardMinGridW, cardMinGridH, cardColors, minimizedCards, originalDimensions, saveCodeEditorState]);

  useEffect(() => {
    if (initialStateLoaded) {
      const timeoutId = setTimeout(() => {
        saveCodeEditorSession();
      }, 3000);

      return () => clearTimeout(timeoutId);
    }
  }, [openTabs, activeTabIndex, chatMessages, fileTree, searchResults, initialStateLoaded, saveCodeEditorSession]);

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (initialStateLoaded) {
        const sessionString = JSON.stringify({
          openTabs: openTabs.map(tab => ({
            id: tab.id,
            filePath: tab.filePath,
            content: tab.content?.substring(0, 100),
            language: tab.language,
            isModified: tab.isModified,
          })),
          activeTabIndex,
          chatMessagesCount: chatMessages.length,
          chatMessagesLastId: chatMessages.length > 0 ? chatMessages[chatMessages.length - 1]?.id : null,
          fileTreeCount: fileTree.length,
          searchResultsCount: searchResults.length,
        });

        if (lastSavedSession.current !== sessionString && navigator.sendBeacon) {
          const blob = new Blob([JSON.stringify({
            openTabs,
            activeTabIndex,
            chatMessages,
            fileTree,
            searchResults,
            lastSaved: new Date().toISOString(),
          })], { type: 'application/json' });
          navigator.sendBeacon('/api/state/code-editor/session', blob);
        }
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [openTabs, activeTabIndex, chatMessages, fileTree, searchResults, initialStateLoaded]);

  useEffect(() => {
    const handleKeyDown = (event) => {
      const cardId = cardForShortcut(event);
      if (cardId) {
        event.preventDefault();

        const cardElement = findCardElement(gridContainerRef.current || document, cardId);
        if (cardElement) {
          cardElement.scrollIntoView({ behavior: 'smooth', block: 'center' });

          cardElement.style.transition = 'box-shadow 0.3s';
          cardElement.style.boxShadow = '0 0 20px rgba(25, 118, 210, 0.6)';
          setTimeout(() => {
            cardElement.style.boxShadow = '';
          }, 1000);
        }
      }

      if (isSaveSessionShortcut(event)) {
        event.preventDefault();
        saveCodeEditorSession();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [saveCodeEditorSession]);

  if (!initialStateLoaded) {
    return (
      <PageLayout variant="fullscreen" noPadding>
        <Box
          sx={{
            display: "flex",
            flex: 1,
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <ContextualLoader loading message={t("code.page.loading")} showProgress={false} inline />
        </Box>
      </PageLayout>
    );
  }

  // The layout error holds LOAD_FAILED or SAVE_FAILED, worded here.
  const layoutErrorText =
    layoutError === "LOAD_FAILED" ? t("code.page.layoutLoadFailed")
      : layoutError === "SAVE_FAILED" ? t("code.page.layoutSaveFailed")
        : layoutError;

  return (
    <PageLayout variant="fullscreen" noPadding>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          bgcolor: theme.palette.background.default,
        }}
      >
      <Paper
        elevation={2}
        square
        sx={{
          borderBottom: 1,
          borderColor: "divider",
          flexShrink: 0,
          backgroundImage:
            theme.components?.MuiAppBar?.styleOverrides?.root?.backgroundImage,
        }}
      >
        <Box
          sx={{
            px: 2,
            py: 1,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
            <IconButton size="small" onClick={() => codeNavHistory(-1)} sx={{ opacity: 0.5, "&:hover": { opacity: 1 } }}>
              <ChevronLeftIcon fontSize="small" />
            </IconButton>
            <IconButton size="small" onClick={() => codeNavHistory(1)} sx={{ opacity: 0.5, "&:hover": { opacity: 1 }, mr: 1.5 }}>
              <ChevronRightIcon fontSize="small" />
            </IconButton>
            <Typography variant="h6" sx={{ fontSize: '0.9rem', fontWeight: 'medium', display: 'flex', alignItems: 'center', gap: 0.75 }}>
              <CodeIcon sx={{ fontSize: '1.1rem' }} />
              {t("code.page.title")} {projectId && `- ${t("code.page.project", { id: projectId })}`}
            </Typography>
          </Box>

          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
            {}
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Tooltip title={rulesCutoffEnabled ? t("code.page.rulesOffTooltip") : t("code.page.rulesOnTooltip")}>
                <FormControlLabel
                  control={
                    <Switch
                      checked={rulesCutoffEnabled}
                      onChange={(e) => handleRulesCutoffChange(e.target.checked)}
                      size="small"
                    />
                  }
                  label={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                      {rulesCutoffEnabled ? (
                        <BlockOutlined sx={{ fontSize: '0.9rem', color: "error.main" }} />
                      ) : (
                        <CheckCircleOutlined sx={{ fontSize: '0.9rem', color: "success.main" }} />
                      )}
                      <Typography variant="caption" sx={{ fontSize: "0.7rem", fontWeight: 'medium' }}>
                        {rulesCutoffEnabled ? t("code.page.rulesOff") : t("code.page.rulesOn")}
                      </Typography>
                    </Box>
                  }
                  sx={{
                    mr: 0,
                    '& .MuiFormControlLabel-label': { fontSize: '0.7rem' }
                  }}
                />
              </Tooltip>
            </Box>

            {}
            <Box sx={{ minWidth: 130 }}>
              <FormControl size="small" fullWidth>
                <InputLabel id="code-model-select-label" sx={{ fontSize: '0.7rem' }}>{t("code.page.aiModel")}</InputLabel>
                <Select
                  labelId="code-model-select-label"
                  value={selectedModel || ""}
                  label={t("code.page.aiModel")}
                  onChange={(e) => handleModelChange(e.target.value)}
                  disabled={isModelLoading || isLoadingModel}
                  sx={{
                    fontSize: '0.7rem',
                    height: 32,
                    '& .MuiSelect-select': { fontSize: '0.7rem', py: 0.5 }
                  }}
                >
                  {availableModels.length === 0 ? (
                    <MenuItem value="" disabled>
                      <em style={{ fontSize: '0.7rem' }}>{isLoadingModel ? t("code.page.loadingShort") : t("code.page.noModels")}</em>
                    </MenuItem>
                  ) : (
                    availableModels.map((model) => (
                      <MenuItem key={model.name} value={model.name} sx={{ fontSize: '0.7rem' }}>
                        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                          <SmartToy sx={{ fontSize: '0.9rem' }} />
                          <Typography variant="body2" sx={{ fontSize: '0.7rem' }}>{model.name}</Typography>
                        </Box>
                      </MenuItem>
                    ))
                  )}
                </Select>
              </FormControl>
            </Box>

            {}
            {currentTab && (
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, px: 1.5, borderLeft: 1, borderRight: 1, borderColor: "divider" }}>
                <Assessment sx={{ fontSize: '0.9rem', color: "text.secondary" }} />
                <Typography variant="caption" sx={{ color: "text.secondary", fontSize: '0.7rem' }}>
                  {t("code.page.metrics", { lines: metrics.lines, characters: metrics.characters, functions: metrics.functions })}
                </Typography>
              </Box>
            )}

            {}
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
              <Tooltip title={t("code.page.runTooltip")}>
                <span>
                  <IconButton
                    onClick={() => handleQuickAction('run')}
                    size="small"
                    disabled={!currentTab?.content}
                    color="primary"
                    sx={{ p: 0.5 }}
                  >
                    <PlayArrow sx={{ fontSize: '1rem' }} />
                  </IconButton>
                </span>
              </Tooltip>

              <Tooltip title={t("code.page.formatTooltip")}>
                <span>
                  <IconButton
                    onClick={() => handleQuickAction('format')}
                    size="small"
                    disabled={!currentTab?.content}
                    sx={{ p: 0.5 }}
                  >
                    <FormatAlignLeft sx={{ fontSize: '1rem' }} />
                  </IconButton>
                </span>
              </Tooltip>

              <Tooltip title={t("code.page.debugTooltip")}>
                <span>
                  <IconButton
                    onClick={() => handleQuickAction('debug')}
                    size="small"
                    disabled={!currentTab?.content}
                    sx={{ p: 0.5 }}
                  >
                    <BugReport sx={{ fontSize: '1rem' }} />
                  </IconButton>
                </span>
              </Tooltip>

              <Tooltip title={buildTarget ? t("code.page.buildTooltip") : buildUnavailableReason()}>
                <span>
                  <IconButton
                    onClick={() => handleQuickAction('build')}
                    size="small"
                    disabled={!buildTarget}
                    aria-label={t("code.page.buildLabel")}
                    sx={{ p: 0.5 }}
                  >
                    <Build sx={{ fontSize: '1rem' }} />
                  </IconButton>
                </span>
              </Tooltip>
            </Box>

            {}
            <Box sx={{ display: 'flex', gap: 0.25, borderLeft: 1, borderColor: 'divider', pl: 1 }}>
              {defaultLayoutProfile ? (
                <Tooltip title={defaultLayoutProfile.tooltip()}>
                  <IconButton
                    onClick={() => applyLayoutProfile("default")}
                    size="small"
                    aria-label={t("code.page.applyDefaultLayout")}
                    sx={{
                      p: 0.5,
                      opacity: 0.7,
                      '&:hover': {
                        opacity: 1,
                        backgroundColor: 'action.hover',
                      }
                    }}
                  >
                    <DefaultLayoutIcon sx={{ fontSize: '1rem' }} />
                  </IconButton>
                </Tooltip>
              ) : null}
              <Tooltip title={t("code.page.resetLayout")}>
                <IconButton onClick={handleResetLayout} size="small" sx={{ p: 0.5 }}>
                  <FormatAlignJustify sx={{ fontSize: '1rem' }} />
                </IconButton>
              </Tooltip>
            </Box>

            {}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, px: 1, borderLeft: 1, borderColor: 'divider' }}>
              {isSaving ? (
                <Chip
                  label={t("code.page.saving")}
                  size="small"
                  color="primary"
                  sx={{
                    fontSize: '0.6rem',
                    height: 18,
                    '& .MuiChip-label': { px: 0.75, py: 0 }
                  }}
                />
              ) : lastSaveTime ? (
                <Tooltip title={t("code.page.lastSaved", { time: lastSaveTime.toLocaleTimeString(uiLocale()) })}>
                  <Chip
                    label={t("code.page.saved")}
                    size="small"
                    color="success"
                    sx={{
                      fontSize: '0.6rem',
                      height: 18,
                      '& .MuiChip-label': { px: 0.75, py: 0 }
                    }}
                  />
                </Tooltip>
              ) : null}
            </Box>

            {}
            <Tooltip
              title={t("code.page.activeModel", {
                model: isLoadingModel
                  ? t("code.page.loadingShort")
                  : modelError ? t("code.page.modelFetchError") : activeModel || t("code.page.notAvailable"),
              })}
            >
              <span>
                <Typography variant="caption" sx={{ color: "text.secondary", fontSize: '0.7rem', ml: 1 }}>
                  {t("code.page.model", {
                    model: isLoadingModel
                      ? t("code.page.loadingShort")
                      : modelError
                        ? t("code.page.modelError")
                        : activeModel || t("code.page.defaultModel"),
                  })}
                </Typography>
              </span>
            </Tooltip>
          </Box>
        </Box>
      </Paper>

      <Box
        ref={gridContainerRef}
        sx={{
          flex: 1,
          overflow: "auto",
          p: 0.5,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        {layoutError && (
          <MuiAlert
            severity="warning"
            sx={{ mb: 1 }}
            onClose={() => setLayoutError(null)}
          >
            {layoutErrorText}
          </MuiAlert>
        )}

        {/* The grid takes the width it is given: WidthProvider measures this
            box (sidebar toggles included) and layouts are in columns, so
            saved layouts scale. A pixel min-width here would hold the page
            at that width and the measurement with it. */}
        <Box
          sx={{
            width: "100%",
            overflow: "none",
            "& .react-grid-item": {
              transition: "transform 0.2s ease-out !important",
              "&.react-draggable-dragging": {
                transition: "none !important",
                zIndex: 1000,
              },
              "&[style*='z-index']": {
                zIndex: "inherit !important",
              }
            },
            "& .react-grid-item .MuiPaper-root": {
              zIndex: "inherit !important",
            }
          }}
        >
          <FixedGridLayout
            className="layout"
            layout={layout}
            style={{
              transition: "all 0.2s ease-out",
            }}
            cols={COLS_COUNT}
            rowHeight={ROW_HEIGHT_PX}
            containerPadding={[CONTAINER_PADDING_PX / 10, CONTAINER_PADDING_PX / 10]}
            margin={[CARD_MARGIN_PX / 20, CARD_MARGIN_PX / 20]}
            isDraggable
            isResizable
            compactType={null}
            preventCollision={false}
            useCSSTransforms={false}
            allowOverlap={true}
            maxRows={maxRows || undefined}
            draggableHandle=".card-header-buttons"
            draggableCancel="button, input, textarea, select, option, .non-draggable"
            onLayoutChange={onLayoutChange}
            resizeHandles={["s", "w", "e", "n", "sw", "nw", "se", "ne"]}
          >
            {layout.map((layoutItem) => {
              const cardId = layoutItem.i;
              const CardComponent = cardComponents[cardId];
              const isMinimized = minimizedCards[cardId] || false;

              const minimizedHeight = Math.max(1, Math.round(48 / ROW_HEIGHT_PX));
              
              const adjustedLayoutItem = isMinimized ? {
                ...layoutItem,
                h: minimizedHeight,
                minH: minimizedHeight,
                maxH: minimizedHeight,
                isResizable: false,
              } : {
                ...layoutItem,
                isResizable: true,
              };

              return (
                <div
                  key={cardId}
                  data-grid={adjustedLayoutItem}
                  data-card-id={cardId}
                  onKeyDown={cardId === 'editor' ? handleEditorShortcut : undefined}
                >
                  {CardComponent ? (
                    <CardComponent
                      id={cardId}
                      ref={cardId === 'editor' ? editorRef : null}
                      cardColor={cardColors[cardId]}
                      onCardColorChange={(color) =>
                        handleCardColorChange(cardId, color)
                      }
                      isMinimized={minimizedCards[cardId] || false}
                      onToggleMinimize={() => handleToggleMinimize(cardId)}
                      projectId={projectId}
                      openTabs={openTabs}
                      setOpenTabs={setOpenTabs}
                      activeTabIndex={activeTabIndex}
                      setActiveTabIndex={setActiveTabIndex}
                      fileTree={fileTree}
                      setFileTree={setFileTree}
                      chatMessages={chatMessages}
                      setChatMessages={setChatMessages}
                      searchResults={searchResults}
                      setSearchResults={setSearchResults}
                      {...(cardId === 'chat' && {
                        rulesCutoffEnabled,
                        currentTab: currentTab,
                        editorContext: editorContext,
                        editorRef: editorRef
                      })}
                      {...(cardId === 'editor' && {
                        onEditorContextChange: handleEditorContextChange,
                        onChatAction: handleEditorChatAction,
                        onRun: () => handleQuickAction('run'),
                        revealRequest,
                        onRevealDone: () => setRevealRequest(null),
                      })}
                      {...(cardId === 'output' && {
                        currentTab: currentTab,
                        outputEntries: runOutput,
                        onClearOutput: () => setRunOutput([]),
                      })}
                      {...(cardId === 'wordpress' && {
                        openTabs: openTabs,
                        setOpenTabs: setOpenTabs,
                        setActiveTabIndex: setActiveTabIndex
                      })}
                    />
                  ) : (
                    <Paper
                      sx={{
                        p: 1,
                        height: "100%",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        bgcolor: "warning.dark",
                        backgroundImage: 'none',
                      }}
                    >
                      <Typography color="warning.contrastText">
                        {t("code.page.missingCard", { card: cardId })}
                      </Typography>
                    </Paper>
                  )}
                </div>
              );
            })}
          </FixedGridLayout>
        </Box>
      </Box>
      {relatedFiles.length > 0 && (
        <Box sx={{ p: 1, borderTop: '1px solid', borderColor: 'divider' }}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold', display: 'block', mb: 0.5 }}>
            {t("code.page.relatedFiles")}
          </Typography>
          <List dense disablePadding sx={{ maxHeight: 200, overflow: 'auto' }}>
            {relatedFiles.map(f => (
              <ListItemButton key={f.id} dense sx={{ py: 0.25 }}>
                <ListItemText
                  primary={f.filename}
                  secondary={f.path}
                  primaryTypographyProps={{ variant: 'caption' }}
                  secondaryTypographyProps={{ variant: 'caption', noWrap: true }}
                />
              </ListItemButton>
            ))}
          </List>
        </Box>
      )}
      </Box>
      <Dialog
        open={symbolSearchOpen}
        onClose={() => { setSymbolSearchOpen(false); setSymbolQuery(''); setSymbolResults([]); }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{t("code.page.findSymbol")}</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            placeholder={t("code.page.symbolPlaceholder")}
            value={symbolQuery}
            onChange={(e) => {
              setSymbolQuery(e.target.value);
              handleSymbolSearch(e.target.value);
            }}
            sx={{ mb: 2, mt: 1 }}
            size="small"
          />
          {symbolLoading && <CircularProgress size={20} sx={{ display: 'block', mx: 'auto', mb: 1 }} />}
          <List dense sx={{ maxHeight: 400, overflow: 'auto' }}>
            {symbolResults.slice(0, 20).map((sym, i) => (
              <ListItemButton
                key={`${sym.document_id}-${sym.name}-${i}`}
                onClick={() => {
                  setSymbolSearchOpen(false);
                  setSymbolQuery('');
                  setSymbolResults([]);
                  openDocumentTab(
                    {
                      id: sym.document_id,
                      filename: sym.filename,
                      filePath: sym.file_path || sym.filename,
                      source: 'document',
                    },
                    { line: sym.line },
                  );
                }}
              >
                <ListItemText
                  primary={`${sym.type}: ${sym.name}`}
                  secondary={`${sym.file_path || sym.filename}${sym.line ? `:${sym.line}` : ''}`}
                />
              </ListItemButton>
            ))}
            {symbolResults.length === 0 && symbolQuery.length >= 2 && !symbolLoading && (
              <ListItemText secondary={t("code.page.noSymbols")} sx={{ textAlign: 'center', py: 2 }} />
            )}
          </List>
        </DialogContent>
      </Dialog>
    </PageLayout>
  );
};

export default CodeEditorPage;
