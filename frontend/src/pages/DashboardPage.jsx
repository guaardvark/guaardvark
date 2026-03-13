// frontend/src/pages/DashboardPage.jsx
// Version 4.0: Responsive width + 3-way layout mode toggle
// - Dashboard width adapts to window size (no hardcoded 1750px)
// - 3-way layout toggle: Normal (grid), Compact (tight grid), Collapsed (sidebar bars)
// - Cards can be minimized by double-clicking the header
// - Minimized state is persisted with other dashboard state
// - Minimized cards become compact bars but remain draggable
// - Enhanced session saving for placements, sizes, colors, minimized states, and layout mode
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  Paper,
  Typography,
  Tooltip,
  useTheme,
  IconButton,
} from "@mui/material";
import ReactGridLayout from "react-grid-layout";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

// MUI Icons
import {
  ViewModule,
  ViewComfy,
  ViewList,
  StickyNote2,
  Dashboard as DashboardIcon,
} from "@mui/icons-material";

import { useNavigate } from "react-router-dom";
import { useStatus } from "../contexts/StatusContext";
import { useAppStore } from "../stores/useAppStore";
import PageLayout from "../components/layout/PageLayout";

import ProjectManagerCard from "../components/dashboard/ProjectManagerCard";
import WebsiteDataCard from "../components/dashboard/WebsiteDataCard";
import TaskManagerCard from "../components/dashboard/TaskManagerCard";
import SemanticSearchCard from "../components/dashboard/SemanticSearchCard";
import ClientsDashboardCard from "../components/dashboard/ClientsDashboardCard";
import CSVGenerationCard from "../components/dashboard/CSVGenerationCard";
import CodeGenerationCard from "../components/dashboard/CodeGenerationCard";
import ImageGenerationCard from "../components/dashboard/ImageGenerationCard";
import FileManagerCard from "../components/dashboard/FileManagerCard";
import FamilySelfImprovementCard from "../components/dashboard/FamilySelfImprovementCard";
import RAGAutoresearchCard from "../components/dashboard/RAGAutoresearchCard";
import { useLayout, useDashboardWidth } from "../contexts/LayoutContext";
import { ContextualLoader } from "../components/common/LoadingStates";

const cardComponents = {
  project: ProjectManagerCard,
  website: WebsiteDataCard,
  tasks: TaskManagerCard,
  chat: SemanticSearchCard,
  clients: ClientsDashboardCard,
  csvgen: CSVGenerationCard,
  codegen: CodeGenerationCard,
  imggen: ImageGenerationCard,
  files: FileManagerCard,
  family: FamilySelfImprovementCard,
  autoresearch: RAGAutoresearchCard,
};

// Card titles for collapsed mode
const cardTitles = {
  project: "Project Manager",
  website: "Website Data",
  tasks: "Tasks",
  chat: "Chat",
  clients: "Clients",
  csvgen: "CSV Generation",
  codegen: "Code Generation",
  imggen: "Image Generation",
  files: "File Manager",
  family: "Family & Self-Improvement",
  autoresearch: "RAG Autoresearch",
};

// Layout mode cycle: normal -> compact -> collapsed -> normal
const LAYOUT_MODES = ["normal", "compact", "collapsed"];
const LAYOUT_MODE_LABELS = {
  normal: "Normal",
  compact: "Compact",
  collapsed: "Collapsed",
};
const LAYOUT_MODE_ICONS = {
  normal: ViewModule,
  compact: ViewComfy,
  collapsed: ViewList,
};

const DashboardPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { gridSettings } = useLayout();
  const dashboardWidth = useDashboardWidth();

  // Destructure grid settings early to avoid lexical declaration errors
  const {
    CONTAINER_PADDING_PX,
    CARD_MARGIN_PX,
    COLS_COUNT,
    ROW_HEIGHT_PX,
    cardMinGridW,
    cardMinGridH,
  } = gridSettings;

  const systemName = useAppStore((state) => state.systemName);
  const [initialStateLoaded, setInitialStateLoaded] = useState(false);
  const [layoutError, setLayoutError] = useState(null);
  const [cardColors, setCardColors] = useState({});
  const [minimizedCards, setMinimizedCards] = useState({}); // Track which cards are minimized
  const [originalDimensions, setOriginalDimensions] = useState({}); // Store original dimensions before minimizing
  const [cardZIndex, setCardZIndex] = useState({}); // Track z-index for each card
  const [maxZIndex, setMaxZIndex] = useState(0); // Start from 0 like desktop windows
  const [layoutMode, setLayoutMode] = useState("normal"); // 'normal' | 'compact' | 'collapsed'
  const gridContainerRef = useRef(null);
  const [gridWidth, setGridWidth] = useState(dashboardWidth);
  const isTogglingRef = useRef(false); // Skip onLayoutChange during programmatic minimize/expand

  // Keep gridWidth in sync with the responsive dashboardWidth
  useEffect(() => {
    if (dashboardWidth > 0) setGridWidth(dashboardWidth);
  }, [dashboardWidth]);

  // Also measure actual container width with ResizeObserver as a fallback
  useEffect(() => {
    const el = gridContainerRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setGridWidth(w);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const defaultFixedLayout = useMemo(() => {
    const { cardGridW, cardGridH, cardMinGridW, cardMinGridH } = gridSettings;
    const items = [
      { i: "project", x: 0, y: 0, w: cardGridW, h: cardGridH },
      { i: "website", x: cardGridW, y: 0, w: cardGridW, h: cardGridH },
      { i: "tasks", x: cardGridW * 2, y: 0, w: cardGridW, h: cardGridH },
      { i: "chat", x: cardGridW * 3, y: 0, w: cardGridW, h: cardGridH },
      { i: "clients", x: cardGridW * 4, y: 0, w: cardGridW, h: cardGridH },
      { i: "csvgen", x: 0, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "codegen", x: cardGridW, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "imggen", x: cardGridW * 2, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "files", x: cardGridW * 3, y: cardGridH, w: cardGridW * 2, h: cardGridH * 1.5 }, // FileManager needs more space
      { i: "family", x: 0, y: cardGridH * 2, w: cardGridW * 2, h: cardGridH },
    ];
    items.forEach((it) => {
      it.minW = cardMinGridW;
      // Remove minH to avoid conflicts - let the grid handle sizing automatically
      it.isDraggable = true;
      it.isResizable = true;
    });
    return items;
  }, [gridSettings]);

  // Compute compact layout: smaller cards arranged in rows filling available width
  const compactLayout = useMemo(() => {
    const { cardGridW, cardGridH } = gridSettings;
    // Compact cards are ~71% of normal size (250/350)
    const compactW = Math.round(cardGridW * 0.71);
    const compactH = Math.round(cardGridH * 0.71);
    const cardIds = Object.keys(cardComponents);

    // Calculate how many cards fit per row based on current grid width
    const colWidthPx = gridWidth / COLS_COUNT;
    const cardPixelW = compactW * colWidthPx;
    const cardsPerRow = Math.max(1, Math.floor(gridWidth / cardPixelW));

    return cardIds.map((id, idx) => ({
      i: id,
      x: (idx % cardsPerRow) * compactW,
      y: Math.floor(idx / cardsPerRow) * compactH,
      w: compactW,
      h: compactH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: false,
    }));
  }, [gridSettings, gridWidth, COLS_COUNT, cardMinGridW]);

  // Compute collapsed layout: thin horizontal bars stacked vertically on the right
  const collapsedLayout = useMemo(() => {
    const cardIds = Object.keys(cardComponents);
    // Each bar: ~300px wide, ~50px tall
    const colWidthPx = gridWidth / COLS_COUNT;
    const barW = Math.round(300 / colWidthPx);
    const barH = Math.round(50 / ROW_HEIGHT_PX);
    // Position on the right side
    const barX = Math.max(0, COLS_COUNT - barW);

    return cardIds.map((id, idx) => ({
      i: id,
      x: barX,
      y: idx * barH,
      w: barW,
      h: barH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: false,
    }));
  }, [gridWidth, COLS_COUNT, ROW_HEIGHT_PX, cardMinGridW]);

  const [layout, setLayout] = useState(defaultFixedLayout);
  // Store the user's normal-mode layout separately so switching modes doesn't lose it
  const normalLayoutRef = useRef(null);
  const { activeModel, isLoadingModel, modelError } = useStatus();

  // Load saved dashboard state (layout, minimized states, colors, layoutMode)
  useEffect(() => {
    const fetchDashboardState = async () => {
      setLayoutError(null);
      try {
        const res = await fetch("/api/state/dashboard");
        if (!res.ok) {
          if (res.status === 404) {
            console.warn("Dashboard: No saved state found. Using defaults.");
            setLayout(defaultFixedLayout);
            normalLayoutRef.current = defaultFixedLayout;
            setCardColors({});
            setMinimizedCards({});
            setLayoutMode("normal");
          } else {
            throw new Error(
              `Failed to fetch dashboard state: ${res.statusText} (Status: ${res.status})`,
            );
          }
        } else {
          const savedState = await res.json();

          // Load layout mode
          if (savedState.layoutMode && LAYOUT_MODES.includes(savedState.layoutMode)) {
            setLayoutMode(savedState.layoutMode);
          }

          // Load layout
          let layoutToApply = null;
          if (
            Array.isArray(savedState.layout) &&
            savedState.layout.length > 0
          ) {
            layoutToApply = savedState.layout;
          } else if (Array.isArray(savedState) && savedState.length > 0) {
            layoutToApply = savedState;
          }

          if (layoutToApply) {
            const validatedLayout = defaultFixedLayout.map((defaultItem) => {
              const savedItem = layoutToApply.find(
                (item) => item.i === defaultItem.i,
              );
              return {
                ...defaultItem,
                x:
                  savedItem && savedItem.x !== undefined
                    ? savedItem.x
                    : defaultItem.x,
                y:
                  savedItem && savedItem.y !== undefined
                    ? savedItem.y
                    : defaultItem.y,
                w:
                  savedItem && savedItem.w !== undefined
                    ? savedItem.w
                    : defaultItem.w,
                h:
                  savedItem && savedItem.h !== undefined
                    ? savedItem.h
                    : defaultItem.h,
                static:
                  savedItem && savedItem.static !== undefined
                    ? savedItem.static
                    : defaultItem.static,
              };
            });
            layoutToApply.forEach((savedItem) => {
              if (!validatedLayout.some((item) => item.i === savedItem.i)) {
                console.warn(
                  `Saved layout item "${savedItem.i}" not in default config, adding.`,
                );
                validatedLayout.push({
                  minW: cardMinGridW,
                  minH: cardMinGridH,
                  isDraggable: true,
                  isResizable: true,
                  ...savedItem,
                });
              }
            });
            normalLayoutRef.current = validatedLayout;

            // Apply mode-specific layout
            const savedMode = savedState.layoutMode || "normal";
            if (savedMode === "compact") {
              // compactLayout is derived from useMemo so just use the ref
              setLayout(validatedLayout); // will be overridden by effect below
            } else if (savedMode === "collapsed") {
              setLayout(validatedLayout); // will be overridden by effect below
            } else {
              setLayout(validatedLayout);
            }
          } else {
            normalLayoutRef.current = defaultFixedLayout;
            setLayout(defaultFixedLayout);
          }

          // Load card colors
          if (
            savedState.cardColors &&
            typeof savedState.cardColors === "object"
          ) {
            setCardColors(savedState.cardColors);
          }

          // Load minimized states
          if (
            savedState.minimizedCards &&
            typeof savedState.minimizedCards === "object"
          ) {
            setMinimizedCards(savedState.minimizedCards);
          }
        }
      } catch (e) {
        console.error("Dashboard: Error fetching or processing state:", e);
        setLayoutError(
          `Failed to load dashboard state: ${e.message}. Using defaults.`,
        );
        normalLayoutRef.current = defaultFixedLayout;
        setLayout(defaultFixedLayout);
        setCardColors({});
        setMinimizedCards({});
        setLayoutMode("normal");
      }
      setInitialStateLoaded(true);
    };
    fetchDashboardState();
  }, [defaultFixedLayout, cardMinGridW, cardMinGridH]);

  // Apply the correct layout whenever layoutMode changes (after initial load)
  useEffect(() => {
    if (!initialStateLoaded) return;
    isTogglingRef.current = true;
    if (layoutMode === "compact") {
      setLayout(compactLayout);
    } else if (layoutMode === "collapsed") {
      setLayout(collapsedLayout);
    } else {
      // Restore normal layout
      setLayout(normalLayoutRef.current || defaultFixedLayout);
    }
    requestAnimationFrame(() => {
      isTogglingRef.current = false;
    });
  }, [layoutMode, initialStateLoaded, compactLayout, collapsedLayout, defaultFixedLayout]);

  // Save dashboard state (layout, minimized states, colors, layoutMode)
  const saveDashboardState = useCallback(
    async (newLayout, newCardColors, newMinimizedCards, newLayoutMode) => {
      try {
        const stateToSave = {
          layout: normalLayoutRef.current || newLayout || layout,
          cardColors: newCardColors || cardColors,
          minimizedCards: newMinimizedCards || minimizedCards,
          layoutMode: newLayoutMode !== undefined ? newLayoutMode : layoutMode,
          lastSaved: new Date().toISOString(),
        };

        const res = await fetch("/api/state/dashboard", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(stateToSave),
        });

        if (!res.ok) {
          throw new Error(`Failed to save dashboard state (${res.status})`);
        }
        setLayoutError(null);
      } catch (err) {
        console.error("Failed to save dashboard state:", err);
        setLayoutError("Failed to save dashboard state changes.");
      }
    },
    [layout, cardColors, minimizedCards, layoutMode],
  );

  const onLayoutChange = useCallback(
    (newLayout) => {
      // Skip when we just programmatically toggled minimize/expand or switched mode
      if (isTogglingRef.current) return;

      const validLayout = newLayout.filter((item) => item !== undefined);

      // Only update normal layout ref in normal mode
      if (layoutMode === "normal") {
        normalLayoutRef.current = validLayout;
      }

      setLayout(validLayout);
      saveDashboardState(validLayout, cardColors, minimizedCards);
    },
    [cardColors, minimizedCards, saveDashboardState, layoutMode],
  );

  const handleCardColorChange = useCallback(
    (cardId, color) => {
      const newCardColors = {
        ...cardColors,
        [cardId]: color,
      };
      setCardColors(newCardColors);
      saveDashboardState(layout, newCardColors, minimizedCards);
    },
    [cardColors, layout, minimizedCards, saveDashboardState],
  );

  const handleToggleMinimize = useCallback(
    (cardId) => {
      // Only allow minimize in normal mode
      if (layoutMode !== "normal") return;

      // Flag to prevent onLayoutChange from overwriting our programmatic layout update
      isTogglingRef.current = true;

      const newMinimizedCards = {
        ...minimizedCards,
        [cardId]: !minimizedCards[cardId],
      };
      setMinimizedCards(newMinimizedCards);

      // Store original dimensions when minimizing, restore when expanding
      const newOriginalDimensions = { ...originalDimensions };
      const adjustedLayout = layout.map((item) => {
        if (item.i === cardId) {
          if (newMinimizedCards[cardId]) {
            // Minimizing: store original dimensions, keep x/y and w
            newOriginalDimensions[cardId] = { w: item.w, h: item.h };
            return {
              ...item,
              h: cardMinGridH, // Only shrink height
            };
          } else {
            // Expanding: restore original height, keep current x/y position
            const original = newOriginalDimensions[cardId];
            if (original) {
              delete newOriginalDimensions[cardId];
              return {
                ...item,
                w: original.w,
                h: original.h,
              };
            }
            return item;
          }
        }
        return item;
      });

      setOriginalDimensions(newOriginalDimensions);
      setLayout(adjustedLayout);
      normalLayoutRef.current = adjustedLayout;
      saveDashboardState(adjustedLayout, cardColors, newMinimizedCards);

      // Allow onLayoutChange again after React processes the state updates
      requestAnimationFrame(() => {
        isTogglingRef.current = false;
      });
    },
    [minimizedCards, layout, cardColors, saveDashboardState, cardMinGridH, originalDimensions, layoutMode],
  );

  const handleCardClick = useCallback((cardId) => {
    const newMaxZIndex = maxZIndex + 1;
    setMaxZIndex(newMaxZIndex);
    setCardZIndex(prev => ({
      ...prev,
      [cardId]: newMaxZIndex
    }));

    // Also directly set the z-index on the DOM element for immediate effect
    const cardElement = document.querySelector(`[data-card-id="${cardId}"]`);

    if (cardElement) {
      cardElement.style.setProperty('z-index', newMaxZIndex, 'important');

      // Also set z-index on the Paper component inside
      const paperElement = cardElement.querySelector('.MuiPaper-root');
      if (paperElement) {
        paperElement.style.setProperty('z-index', newMaxZIndex, 'important');
      }
    }
  }, [maxZIndex]);

  const handleResetLayout = useCallback(() => {
    normalLayoutRef.current = defaultFixedLayout;
    setLayout(defaultFixedLayout);
    setCardColors({});
    setMinimizedCards({});
    setOriginalDimensions({});
    setCardZIndex({});
    setMaxZIndex(0);
    setLayoutMode("normal");
    saveDashboardState(defaultFixedLayout, {}, {}, "normal");
  }, [defaultFixedLayout, saveDashboardState]);

  // 3-way layout mode toggle: normal -> compact -> collapsed -> normal
  const handleCycleLayoutMode = useCallback(() => {
    const currentIdx = LAYOUT_MODES.indexOf(layoutMode);
    const nextMode = LAYOUT_MODES[(currentIdx + 1) % LAYOUT_MODES.length];

    // When leaving normal mode, save the current layout
    if (layoutMode === "normal") {
      normalLayoutRef.current = layout;
    }

    setLayoutMode(nextMode);
    saveDashboardState(normalLayoutRef.current || layout, cardColors, minimizedCards, nextMode);
  }, [layoutMode, layout, cardColors, minimizedCards, saveDashboardState]);

  // Helper: get contrast text color for a background hex color (YIQ formula)
  const getContrastTextColor = useCallback((bgColor) => {
    if (!bgColor) return theme.palette.text.primary;
    let hex = bgColor.replace("#", "");
    if (hex.length === 3) hex = hex.split("").map(h => h + h).join("");
    const r = parseInt(hex.substring(0, 2), 16);
    const g = parseInt(hex.substring(2, 4), 16);
    const b = parseInt(hex.substring(4, 6), 16);
    const yiq = (r * 299 + g * 587 + b * 114) / 1000;
    return yiq > 186 ? 'rgba(0, 0, 0, 0.87)' : 'rgba(255, 255, 255, 0.95)';
  }, [theme.palette.text.primary]);

  const LayoutModeIcon = LAYOUT_MODE_ICONS[layoutMode];

  if (!initialStateLoaded) {
    return (
      <PageLayout title={systemName || "Dashboard"} variant="grid">
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flex: 1,
          }}
        >
          <ContextualLoader loading message="Loading dashboard..." showProgress={false} inline />
        </Box>
      </PageLayout>
    );
  }

  // All modes use ReactGridLayout (normal, compact, collapsed)
  const isCompact = layoutMode === "compact";
  const isCollapsed = layoutMode === "collapsed";

  return (
    <PageLayout
      title={systemName || "Dashboard"}
      variant="grid"
      actions={
        <>
          <Tooltip title="Dashboard Cards">
            <IconButton size="small" sx={{ color: "primary.main" }}>
              <DashboardIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Sticky Notes">
            <IconButton onClick={() => navigate("/notes")} size="small" sx={{ opacity: 0.5 }}>
              <StickyNote2 fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={`Layout: ${LAYOUT_MODE_LABELS[layoutMode]} (click to cycle)`}>
            <IconButton onClick={handleCycleLayoutMode} size="small" sx={{ opacity: 0.6 }}>
              <LayoutModeIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >

        <Box
          sx={{
            flex: 1,
            overflow: "auto",
            p: 0.5,
            display: "flex",
            flexDirection: "column",
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
          ref={gridContainerRef}
          sx={{
            width: "100%",
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
          <ReactGridLayout
            className="layout"
            layout={layout}
            style={{
              transition: "all 0.2s ease-out",
            }}
            cols={COLS_COUNT}
            rowHeight={ROW_HEIGHT_PX}
            width={gridWidth}
            containerPadding={[CONTAINER_PADDING_PX, CONTAINER_PADDING_PX]}
            margin={[CARD_MARGIN_PX / 20, CARD_MARGIN_PX / 20]}
            isDraggable={true}
            isResizable={!isCompact && !isCollapsed}
            compactType={isCompact ? "horizontal" : isCollapsed ? "vertical" : null}
            preventCollision={false}
            useCSSTransforms={false}
            allowOverlap={!isCompact && !isCollapsed}
            draggableHandle=".card-header-buttons"
            draggableCancel="button, input, textarea, select, option, .non-draggable"
            onLayoutChange={onLayoutChange}
            resizeHandles={!isCompact && !isCollapsed ? ["s", "w", "e", "n", "sw", "nw", "se", "ne"] : []}
          >
            {layout.map((layoutItem) => {
              const cardId = layoutItem.i;
              const CardComponent = cardComponents[cardId];
              const isMinimized = minimizedCards[cardId] || false;

                return (
                  <div
                    key={cardId}
                    data-card-id={cardId}
                    style={{
                      zIndex: cardZIndex[cardId] || 0,
                      transition: "transform 0.2s ease-out, box-shadow 0.2s ease-out",
                      position: "relative",
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (isCollapsed) {
                        // Switch to normal mode to "expand" the bar
                        setLayoutMode("normal");
                        saveDashboardState(normalLayoutRef.current || layout, cardColors, minimizedCards, "normal");
                      } else {
                        handleCardClick(cardId);
                      }
                    }}
                  >
                  {isCollapsed ? (
                    <Paper
                      elevation={1}
                      className="card-header-buttons"
                      sx={{
                        display: "flex",
                        alignItems: "center",
                        height: "100%",
                        px: 2,
                        cursor: "grab",
                        userSelect: "none",
                        borderRadius: 1,
                        transition: "background-color 0.15s ease, box-shadow 0.15s ease",
                        ...(cardColors[cardId] && { backgroundColor: cardColors[cardId] }),
                        "&:hover": {
                          boxShadow: theme.shadows[4],
                          backgroundColor: cardColors[cardId] || theme.palette.action.hover,
                        },
                        "&:active": { cursor: "grabbing" },
                      }}
                    >
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: 500,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          color: getContrastTextColor(cardColors[cardId]),
                          pointerEvents: "none",
                        }}
                      >
                        {cardTitles[cardId] || cardId}
                      </Typography>
                    </Paper>
                  ) : CardComponent ? (
                    <CardComponent
                      id={cardId}
                      cardColor={cardColors[cardId]}
                      onCardColorChange={(color) =>
                        handleCardColorChange(cardId, color)
                      }
                      isMinimized={isMinimized}
                      onToggleMinimize={() => handleToggleMinimize(cardId)}
                    />
                  ) : (
                    <Paper
                      sx={{
                        p: 1,
                        height: "50%",
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
          </ReactGridLayout>
        </Box>
      </Box>
    </PageLayout>
  );
};

export default DashboardPage;
