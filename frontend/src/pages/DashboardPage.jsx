// frontend/src/pages/DashboardPage.jsx
// Version 3.0: Added card minimize/maximize functionality
// - Cards can be minimized by double-clicking the header
// - Minimized state is persisted with other dashboard state
// - Minimized cards become compact bars but remain draggable
// - Enhanced session saving for placements, sizes, colors, and minimized states
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).

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
} from "@mui/material";
import ReactGridLayout from "react-grid-layout";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

// MUI Icons
import {
  FormatAlignJustify,
} from "@mui/icons-material";

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
import { useLayout } from "../contexts/LayoutContext";

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
};

const DashboardPage = () => {
  const theme = useTheme();
  const { gridSettings } = useLayout();

  // Destructure grid settings early to avoid lexical declaration errors
  const {
    RGL_WIDTH_PROP_PX,
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
  const gridContainerRef = useRef(null);
  const [gridWidth, setGridWidth] = useState(RGL_WIDTH_PROP_PX);
  const isTogglingRef = useRef(false); // Skip onLayoutChange during programmatic minimize/expand

  // Measure actual container width with ResizeObserver (responds to sidebar collapse, window resize, etc.)
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
    ];
    items.forEach((it) => {
      it.minW = cardMinGridW;
      // Remove minH to avoid conflicts - let the grid handle sizing automatically
      it.isDraggable = true;
      it.isResizable = true;
    });
    return items;
  }, [gridSettings]);

  const [layout, setLayout] = useState(defaultFixedLayout);
  const { activeModel, isLoadingModel, modelError } = useStatus();

  // Load saved dashboard state (layout, minimized states, colors)
  useEffect(() => {
    const fetchDashboardState = async () => {
      setLayoutError(null);
      try {
        const res = await fetch("/api/state/dashboard");
        if (!res.ok) {
          if (res.status === 404) {
            console.warn("Dashboard: No saved state found. Using defaults.");
            setLayout(defaultFixedLayout);
            setCardColors({});
            setMinimizedCards({});
          } else {
            throw new Error(
              `Failed to fetch dashboard state: ${res.statusText} (Status: ${res.status})`,
            );
          }
        } else {
          const savedState = await res.json();

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
            console.log("Dashboard: Found saved layout. Applying...");
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
            setLayout(validatedLayout);
          } else {
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
        setLayout(defaultFixedLayout);
        setCardColors({});
        setMinimizedCards({});
      }
      setInitialStateLoaded(true);
    };
    fetchDashboardState();
  }, [defaultFixedLayout, cardMinGridW, cardMinGridH]);

  // Save dashboard state (layout, minimized states, colors)
  const saveDashboardState = useCallback(
    async (newLayout, newCardColors, newMinimizedCards) => {
      try {
        const stateToSave = {
          layout: newLayout || layout,
          cardColors: newCardColors || cardColors,
          minimizedCards: newMinimizedCards || minimizedCards,
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
    [layout, cardColors, minimizedCards],
  );

  const onLayoutChange = useCallback(
    (newLayout) => {
      // Skip when we just programmatically toggled minimize/expand
      if (isTogglingRef.current) return;

      const validLayout = newLayout.filter((item) => item !== undefined);
      setLayout(validLayout);
      saveDashboardState(validLayout, cardColors, minimizedCards);
    },
    [cardColors, minimizedCards, saveDashboardState],
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
      saveDashboardState(adjustedLayout, cardColors, newMinimizedCards);

      // Allow onLayoutChange again after React processes the state updates
      requestAnimationFrame(() => {
        isTogglingRef.current = false;
      });
    },
    [minimizedCards, layout, cardColors, saveDashboardState, cardMinGridH, originalDimensions],
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
    console.log(`Looking for card element with selector: [data-card-id="${cardId}"]`);
    console.log(`Found element:`, cardElement);
    
    if (cardElement) {
      cardElement.style.setProperty('z-index', newMaxZIndex, 'important');
      
      // Also set z-index on the Paper component inside
      const paperElement = cardElement.querySelector('.MuiPaper-root');
      if (paperElement) {
        paperElement.style.setProperty('z-index', newMaxZIndex, 'important');
        console.log(`Also applied z-index ${newMaxZIndex} to Paper element:`, paperElement);
      }
      
      console.log(`Applied z-index ${newMaxZIndex} to element:`, cardElement);
      console.log(`Element computed z-index:`, window.getComputedStyle(cardElement).zIndex);
    } else {
      console.error(`Could not find card element for ${cardId}`);
    }
    
    console.log(`Card ${cardId} brought to front with z-index: ${newMaxZIndex}`);
  }, [maxZIndex]);

  const handleResetLayout = useCallback(() => {
    setLayout(defaultFixedLayout);
    setCardColors({});
    setMinimizedCards({});
    setOriginalDimensions({});
    setCardZIndex({});
    setMaxZIndex(0);
    saveDashboardState(defaultFixedLayout, {}, {});
  }, [defaultFixedLayout, saveDashboardState]);

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
          <CircularProgress />{" "}
          <Typography component="span" sx={{ ml: 5 }}>
            Loading Dashboard...
          </Typography>
        </Box>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title={systemName || "Dashboard"}
      variant="grid"
      actions={
        <Tooltip title="Straighten and Reset Layout">
          <IconButton onClick={handleResetLayout} size="small">
            <FormatAlignJustify fontSize="small" />
          </IconButton>
        </Tooltip>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >

        <Box
          sx={{
            flex: 1,
            overflow: "auto",
            p: 1.5,
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
            containerPadding={[CONTAINER_PADDING_PX / 10, CONTAINER_PADDING_PX / 10]}
            margin={[CARD_MARGIN_PX / 20, CARD_MARGIN_PX / 20]}
            isDraggable
            isResizable
            compactType={null}
            preventCollision={false}
            useCSSTransforms={false}
            allowOverlap={true}
            draggableHandle=".card-header-buttons"
            draggableCancel="button, input, textarea, select, option, .non-draggable"
            onLayoutChange={onLayoutChange}
            resizeHandles={["s", "w", "e", "n", "sw", "nw", "se", "ne"]}
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
                      // Always bring card to front when clicked
                      e.stopPropagation();
                      handleCardClick(cardId);
                    }}
                  >
                  {CardComponent ? (
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
