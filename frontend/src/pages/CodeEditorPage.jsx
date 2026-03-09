
import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  CircularProgress,
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
} from "@mui/material";
import ReactGridLayout, { WidthProvider } from "react-grid-layout";
import { useParams } from "react-router-dom";

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
  Visibility,
  VisibilityOff,
  BlockOutlined,
  CheckCircleOutlined,
  SmartToy,
} from "@mui/icons-material";

import PageLayout from "../components/layout/PageLayout";
import { useStatus } from "../contexts/StatusContext";
import { useAppStore } from "../stores/useAppStore";
import { useUnifiedProgress } from "../contexts/UnifiedProgressContext";
import { useLayout } from "../contexts/LayoutContext";
import * as apiService from "../api";
import * as fileOperationsService from "../api/fileOperationsService";
import * as codeExecutionService from "../api/codeExecutionService";

import FileTreeCard from "../components/codeeditor/FileTreeCard";
import CodeEditorCard from "../components/codeeditor/CodeEditorCard";
import ChatAssistantCard from "../components/codeeditor/ChatAssistantCard";
import SearchCard from "../components/codeeditor/SearchCard";
import OutputCard from "../components/codeeditor/OutputCard";
import WordPressPagesCard from "../components/codeeditor/WordPressPagesCard";

const FixedGridLayout = WidthProvider(ReactGridLayout);

const cardComponents = {
  filetree: FileTreeCard,
  editor: CodeEditorCard,
  chat: ChatAssistantCard,
  search: SearchCard,
  output: OutputCard,
  wordpress: WordPressPagesCard,
};

const CodeEditorPage = () => {
  const theme = useTheme();
  const { projectId } = useParams();
  const { gridSettings } = useLayout();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const systemName = useAppStore((state) => state.systemName);
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
  
  const [editorContext, setEditorContext] = useState(null);
  const editorRef = useRef(null);

  const [rulesCutoffEnabled, setRulesCutoffEnabled] = useState(true);
  const [availableModels, setAvailableModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [isModelLoading, setIsModelLoading] = useState(false);

  const [showFloatingActions, setShowFloatingActions] = useState(false);
  const [showFloatingMetrics, setShowFloatingMetrics] = useState(false);

  const { startProcess, completeProcess, errorProcess } = useUnifiedProgress();
  const gridContainerRef = useRef(null);

  useEffect(() => {
    const loadModels = async () => {
      try {
        const models = await apiService.getAvailableModels();
        setAvailableModels(models || []);
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

  const handleModelChange = async (modelName) => {
    if (!modelName || modelName === activeModel) return;

    if (!availableModels || !Array.isArray(availableModels) || !availableModels.some(m => m.name === modelName)) {
      errorProcess("model-change", `Model "${modelName}" not found in available models`);
      return;
    }

    setIsModelLoading(true);
    try {
      await apiService.setModel(modelName);
      setSelectedModel(modelName);
    } catch (err) {
      console.error('Failed to set model:', err);
      errorProcess("model-change", `Failed to change model: ${err.message}`);
      setSelectedModel(activeModel || "");
    } finally {
      setIsModelLoading(false);
    }
  };

  const {
    RGL_WIDTH_PROP_PX,
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

  const outerBoxWidth = useMemo(() => {
    if (containerSize.width && containerSize.width > 0) {
      return containerSize.width;
    }
    return RGL_WIDTH_PROP_PX + 2 * CONTAINER_PADDING_PX;
  }, [containerSize.width, RGL_WIDTH_PROP_PX, CONTAINER_PADDING_PX]);

  const gridWidth = useMemo(() => {
    if (containerSize.width && containerSize.width > 0) {
      return Math.max(containerSize.width - 2 * CONTAINER_PADDING_PX, 1);
    }
    return RGL_WIDTH_PROP_PX;
  }, [containerSize.width, CONTAINER_PADDING_PX, RGL_WIDTH_PROP_PX]);

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

  const handleQuickAction = useCallback(async (action) => {
    if (!currentTab?.content) {
      console.warn('No active tab or content to perform action on');
      return;
    }

    const processId = `code-action-${action}-${Date.now()}`;
    
    try {
      switch (action) {
        case 'run':
          startProcess(processId, 'Code Execution', 'Running code...');
          
          const language = currentTab.language || 'javascript';
          let result;
          
          if (language === 'python') {
            result = await codeExecutionService.executePythonCode(currentTab.content);
          } else if (language === 'javascript' || language === 'typescript') {
            result = await codeExecutionService.executeJavaScriptCode(currentTab.content);
          } else {
            throw new Error(`Execution not supported for language: ${language}`);
          }
          
          if (result.success) {
            completeProcess(processId, 'Code executed successfully');
            setSearchResults(prev => [...prev, {
              type: 'output',
              message: result.output,
              timestamp: new Date(),
              language: language
            }]);
          } else {
            errorProcess(processId, result.error || 'Code execution failed');
            setSearchResults(prev => [...prev, {
              type: 'error',
              message: result.error || result.stderr,
              timestamp: new Date(),
              language: language
            }]);
          }
          break;

        case 'format':
          startProcess(processId, 'Code Formatting', 'Formatting code...');
          
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
            
            completeProcess(processId, 'Code formatted successfully');
          } else {
            errorProcess(processId, formatResult.error || 'Code formatting failed');
          }
          break;

        case 'debug':
          startProcess(processId, 'Code Debugging', 'Analyzing code...');
          
          const lintResult = await codeExecutionService.lintCode(
            currentTab.content,
            currentTab.language || 'javascript'
          );
          
          if (lintResult.success) {
            completeProcess(processId, `Code analysis complete - Score: ${lintResult.score}`);
            
            const issues = [...lintResult.errors, ...lintResult.warnings];
            issues.forEach(issue => {
              setSearchResults(prev => [...prev, {
                type: issue.severity === 2 ? 'error' : 'warning',
                message: `Line ${issue.line}: ${issue.message}`,
                timestamp: new Date(),
                language: currentTab.language
              }]);
            });
          } else {
            errorProcess(processId, lintResult.error || 'Code analysis failed');
          }
          break;

        case 'build':
          startProcess(processId, 'Project Build', 'Building project...');
          
          const buildPath = projectId ? `/home/llamax1/dev/guaardvark` : '/tmp';
          const buildResult = await codeExecutionService.buildProject(buildPath);
          
          if (buildResult.success) {
            completeProcess(processId, `Build completed in ${buildResult.buildTime.toFixed(2)}s`);
            
            setSearchResults(prev => [...prev, {
              type: 'output',
              message: buildResult.output,
              timestamp: new Date(),
              language: 'build'
            }]);
          } else {
            errorProcess(processId, buildResult.error || 'Build failed');
            
            setSearchResults(prev => [...prev, {
              type: 'error',
              message: buildResult.error,
              timestamp: new Date(),
              language: 'build'
            }]);
          }
          break;

        default:
          console.warn('Unknown action:', action);
          break;
      }
    } catch (error) {
      console.error(`Failed to execute ${action}:`, error);
      errorProcess(processId, `Failed to execute ${action}: ${error.message}`);
    }
  }, [currentTab, openTabs, activeTabIndex, setOpenTabs, setSearchResults, startProcess, completeProcess, errorProcess, projectId]);

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
        tooltip: "Balanced layout (20% / 60% / 20%)",
        layout: [
          { i: "filetree", x: 0, y: 0, w: leftCols, h: halfRows },
          { i: "search", x: 0, y: halfRows, w: leftCols, h: halfRows },
          { i: "editor", x: leftCols, y: 0, w: centerCols, h: totalRows },
          { i: "chat", x: leftCols + centerCols, y: 0, w: rightCols, h: halfRows },
          { i: "output", x: leftCols + centerCols, y: halfRows, w: rightCols, h: halfRows },
        ]
      }
    };
  }, [COLS_COUNT]);

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
        setLayoutError(
          "Failed to load saved code editor state. Using default layout.",
        );
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
        setLayoutError("Failed to save code editor state changes.");
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
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey) {
        const cardMap = {
          '1': 'filetree',
          '2': 'editor',
          '3': 'chat',
          '4': 'search',
          '5': 'output'
        };

        const cardId = cardMap[event.key];
        if (cardId) {
          event.preventDefault();

          const cardElement = document.querySelector(`[data-grid*='"i":"${cardId}"']`);
          if (cardElement) {
            cardElement.scrollIntoView({ behavior: 'smooth', block: 'center' });

            cardElement.style.transition = 'box-shadow 0.3s';
            cardElement.style.boxShadow = '0 0 20px rgba(25, 118, 210, 0.6)';
            setTimeout(() => {
              cardElement.style.boxShadow = '';
            }, 1000);
          }
        }
      }

      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key === 's') {
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
          <CircularProgress />
        </Box>
      </PageLayout>
    );
  }

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
          <Typography variant="h6" sx={{ fontSize: '0.9rem', fontWeight: 'medium', display: 'flex', alignItems: 'center', gap: 0.75 }}>
            <CodeIcon sx={{ fontSize: '1.1rem' }} />
            Code Editor {projectId && `- Project: ${projectId}`}
          </Typography>

          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
            {}
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Tooltip title={rulesCutoffEnabled ? "Rules/Prompts are DISABLED for coding - AI models run freely" : "Rules/Prompts are ACTIVE - may constrain AI responses"}>
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
                        {rulesCutoffEnabled ? "Rules OFF" : "Rules ON"}
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
                <InputLabel id="code-model-select-label" sx={{ fontSize: '0.7rem' }}>AI Model</InputLabel>
                <Select
                  labelId="code-model-select-label"
                  value={selectedModel || ""}
                  label="AI Model"
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
                      <em style={{ fontSize: '0.7rem' }}>{isLoadingModel ? "Loading..." : "No models"}</em>
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
                  {metrics.lines}L • {metrics.characters}C • {metrics.functions}F
                </Typography>
              </Box>
            )}

            {}
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
              <Tooltip title="Run Code (Ctrl+R)">
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

              <Tooltip title="Format Code (Ctrl+Shift+F)">
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

              <Tooltip title="Debug (F5)">
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

              <Tooltip title="Build (Ctrl+B)">
                <IconButton
                  onClick={() => handleQuickAction('build')}
                  size="small"
                  sx={{ p: 0.5 }}
                >
                  <Build sx={{ fontSize: '1rem' }} />
                </IconButton>
              </Tooltip>
            </Box>

            {}
            <Box sx={{ display: 'flex', gap: 0.25, borderLeft: 1, borderColor: 'divider', pl: 1 }}>
              {defaultLayoutProfile ? (
                <Tooltip title={defaultLayoutProfile.tooltip}>
                  <IconButton
                    onClick={() => applyLayoutProfile("default")}
                    size="small"
                    aria-label="Apply default layout"
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
              <Tooltip title="Reset Layout">
                <IconButton onClick={handleResetLayout} size="small" sx={{ p: 0.5 }}>
                  <FormatAlignJustify sx={{ fontSize: '1rem' }} />
                </IconButton>
              </Tooltip>
            </Box>

            {}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5, px: 1, borderLeft: 1, borderColor: 'divider' }}>
              {isSaving ? (
                <Chip
                  label="Saving..."
                  size="small"
                  color="primary"
                  sx={{
                    fontSize: '0.6rem',
                    height: 18,
                    '& .MuiChip-label': { px: 0.75, py: 0 }
                  }}
                />
              ) : lastSaveTime ? (
                <Tooltip title={`Last saved: ${lastSaveTime.toLocaleTimeString()}`}>
                  <Chip
                    label="Saved"
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
              title={`Active Model: ${isLoadingModel ? "Loading..." : modelError ? "Error fetching model" : activeModel || "N/A"}`}
            >
              <span>
                <Typography variant="caption" sx={{ color: "text.secondary", fontSize: '0.7rem', ml: 1 }}>
                  Model:{" "}
                  {isLoadingModel
                    ? "Loading..."
                    : modelError
                      ? "Error"
                      : activeModel || "Default"}
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
            {layoutError}
          </MuiAlert>
        )}

        <Box
          sx={{
            width: outerBoxWidth ? `${outerBoxWidth}px` : "100%",
            minWidth: outerBoxWidth ? `${outerBoxWidth}px` : "100%",
            overflow: "none",
            "& .react-grid-item": {
              transition: "transform 0.2s ease-out !important",
              "&.react-grid-placeholder": {
                transition: "all 0.2s ease-out !important",
                opacity: 0.3,
              },
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
            width={gridWidth}
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
                <div key={cardId} data-grid={adjustedLayoutItem}>
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
                      })}
                      {...(cardId === 'output' && {
                        currentTab: currentTab
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
                        Missing Card: {cardId}
                      </Typography>
                    </Paper>
                  )}
                </div>
              );
            })}
          </FixedGridLayout>
        </Box>
      </Box>
      </Box>
    </PageLayout>
  );
};

export default CodeEditorPage;
