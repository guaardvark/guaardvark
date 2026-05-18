// frontend/src/pages/DashboardPage.jsx
// Version 5.0: 4 layout presets, one-shot placement, free movement, click-to-top z-index
// - 4-way layout toggle: Normal → Compact → Compact Layered → Collapsed
// - Each toggle is a ONE-SHOT placement action — positions cards then leaves them fully free
// - All modes: draggable, resizable, overlappable, no snapping (compactType=null always)
// - Compact Layered: real DashboardCardWrapper bars with card colors, indicators, expand-on-double-click
// - Collapsed: simple text-only Paper bars
// - Clicking any card brings it to the top z-layer
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
  Layers,
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
import GpuStatusCard from "../components/dashboard/GpuStatusCard";
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
  gpu: GpuStatusCard,
};

// Layout mode cycle: normal -> compact -> layered -> modex -> normal
const LAYOUT_MODES = ["normal", "compact", "layered", "modex"];
const LAYOUT_MODE_LABELS = {
  normal: "Normal",
  compact: "Compact",
  layered: "Compact Layered",
  modex: "Mode-X",
};
const LAYOUT_MODE_ICONS = {
  normal: ViewModule,
  compact: ViewComfy,
  layered: Layers,
  modex: Layers,
};

const DashboardPage = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { gridSettings } = useLayout();
  const dashboardWidth = useDashboardWidth();

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
  const [minimizedCards, setMinimizedCards] = useState({});
  const [originalDimensions, setOriginalDimensions] = useState({});
  const [cardZIndex, setCardZIndex] = useState({});
  const [maxZIndex, setMaxZIndex] = useState(0);
  const [layoutMode, setLayoutMode] = useState("normal");
  const [layoutKey, setLayoutKey] = useState(0); // Increments on every toggle to force RGL remount
  const gridContainerRef = useRef(null);
  const [gridWidth, setGridWidth] = useState(dashboardWidth);

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

  // Normal layout: full-size cards in grid
  const defaultFixedLayout = useMemo(() => {
    const { cardGridW, cardGridH, cardMinGridW } = gridSettings;
    const items = [
      { i: "project", x: 0, y: 0, w: cardGridW, h: cardGridH },
      { i: "website", x: cardGridW, y: 0, w: cardGridW, h: cardGridH },
      { i: "tasks", x: cardGridW * 2, y: 0, w: cardGridW, h: cardGridH },
      { i: "chat", x: cardGridW * 3, y: 0, w: cardGridW, h: cardGridH },
      { i: "clients", x: cardGridW * 4, y: 0, w: cardGridW, h: cardGridH },
      { i: "csvgen", x: 0, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "codegen", x: cardGridW, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "imggen", x: cardGridW * 2, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "files", x: cardGridW * 3, y: cardGridH, w: cardGridW * 2, h: cardGridH * 1.5 },
      { i: "family", x: 0, y: cardGridH * 2, w: cardGridW * 2, h: cardGridH },
      { i: "autoresearch", x: cardGridW * 2, y: cardGridH * 2, w: cardGridW, h: cardGridH },
      { i: "gpu", x: cardGridW * 3, y: cardGridH * 2, w: cardGridW, h: cardGridH },
    ];
    items.forEach((it) => {
      it.minW = cardMinGridW;
      it.isDraggable = true;
      it.isResizable = true;
    });
    return items;
  }, [gridSettings]);

  // Compact layout: smaller cards arranged in rows filling available width
  const compactLayout = useMemo(() => {
    const { cardGridW, cardGridH } = gridSettings;
    const compactW = Math.round(cardGridW * 0.71);
    const compactH = Math.round(cardGridH * 0.71);
    const cardIds = Object.keys(cardComponents);
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
      isResizable: true,
    }));
  }, [gridSettings, gridWidth, COLS_COUNT, cardMinGridW]);

  // Layered layout: stacked bars on right side (same geometry as collapsed,
  // but renders real minimized DashboardCardWrapper components)
  const layeredLayout = useMemo(() => {
    const cardIds = Object.keys(cardComponents);
    const colWidthPx = gridWidth / COLS_COUNT;
    const barW = Math.round(300 / colWidthPx);
    const barH = Math.round(50 / ROW_HEIGHT_PX);
    const barX = Math.max(0, COLS_COUNT - barW);
    return cardIds.map((id, idx) => ({
      i: id,
      x: barX,
      y: idx * barH,
      w: barW,
      h: barH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: true,
    }));
  }, [gridWidth, COLS_COUNT, ROW_HEIGHT_PX, cardMinGridW]);

  const [layout, setLayout] = useState(defaultFixedLayout);
  // Store the user's normal-mode layout separately so switching modes doesn't lose it
  const normalLayoutRef = useRef(null);
  const { activeModel, isLoadingModel, modelError } = useStatus();

  // Load saved dashboard state (layout, minimized states, colors, originalDimensions, layoutMode)
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
            setOriginalDimensions({});
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
            setLayout(validatedLayout);
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

          // Load original dimensions
          if (
            savedState.originalDimensions &&
            typeof savedState.originalDimensions === "object"
          ) {
            setOriginalDimensions(savedState.originalDimensions);
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
        setOriginalDimensions({});
        setLayoutMode("normal");
      }
      setInitialStateLoaded(true);
    };
    fetchDashboardState();
  }, [defaultFixedLayout, cardMinGridW, cardMinGridH]);

  // One-time effect: apply mode-specific layout after initial state is loaded
  // Does NOT re-fire on layoutMode changes — handleCycleLayoutMode applies layout directly
  useEffect(() => {
    if (!initialStateLoaded) return;
    if (layoutMode === "compact") {
      setLayout(compactLayout);
    } else if (layoutMode === "layered") {
      setLayout(layeredLayout);
      // Ensure all cards minimized in layered mode with standard expand sizes
      const allMinimized = {};
      const dims = {};
      Object.keys(cardComponents).forEach((id) => {
        allMinimized[id] = true;
        const item = defaultFixedLayout.find((l) => l.i === id);
        if (item) dims[id] = { w: item.w, h: item.h };
      });
      setMinimizedCards(allMinimized);
      setOriginalDimensions(dims);
    } else if (layoutMode === "modex") {
      // Content-visible bars: taller than layered so content is readable
      const mxCardIds = Object.keys(cardComponents);
      const mxColWidthPx = gridWidth / COLS_COUNT;
      const mxBarW = Math.round(300 / mxColWidthPx);
      const mxBarH = Math.round(150 / ROW_HEIGHT_PX);
      const mxBarX = Math.max(0, COLS_COUNT - mxBarW);
      setLayout(mxCardIds.map((id, idx) => ({
        i: id,
        x: mxBarX,
        y: idx * mxBarH,
        w: mxBarW,
        h: mxBarH,
        minW: cardMinGridW,
        isDraggable: true,
        isResizable: true,
      })));
      setMinimizedCards({});
      setOriginalDimensions({});
    }
    // normal: already loaded from saved state
  }, [initialStateLoaded]);

  // Apply z-indices to grid item elements after renders.
  useEffect(() => {
    Object.entries(cardZIndex).forEach(([cardId, zIdx]) => {
      const el = document.querySelector(`[data-card-id="${cardId}"]`);
      const gridItem = el?.closest(".react-grid-item") || el;
      if (gridItem) gridItem.style.zIndex = zIdx;
    });
  }, [cardZIndex, layout, layoutKey]);

  // Save dashboard state (layout, minimized states, colors, originalDimensions, layoutMode)
  const saveDashboardState = useCallback(
    async (newLayout, newCardColors, newMinimizedCards, newLayoutMode, newOriginalDimensions) => {
      try {
        const stateToSave = {
          layout: normalLayoutRef.current || newLayout || layout,
          cardColors: newCardColors || cardColors,
          minimizedCards: newMinimizedCards || minimizedCards,
          originalDimensions: newOriginalDimensions !== undefined ? newOriginalDimensions : originalDimensions,
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
    [layout, cardColors, minimizedCards, originalDimensions, layoutMode],
  );

  // Apply a single card's z-index to its react-grid-item wrapper.
  const applyZIndexToDOM = useCallback((cardId, zIdx) => {
    const el = document.querySelector(`[data-card-id="${cardId}"]`);
    const gridItem = el?.closest(".react-grid-item") || el;
    if (gridItem) gridItem.style.zIndex = zIdx;
  }, []);

  // Re-apply ALL z-indices (used after drag ends when RGL resets styles)
  const applyAllZIndices = useCallback(() => {
    Object.entries(cardZIndex).forEach(([id, z]) => applyZIndexToDOM(id, z));
  }, [cardZIndex, applyZIndexToDOM]);

  const handleCardClick = useCallback((cardId) => {
    const newMaxZIndex = maxZIndex + 1;
    setMaxZIndex(newMaxZIndex);
    setCardZIndex(prev => ({
      ...prev,
      [cardId]: newMaxZIndex
    }));
    applyZIndexToDOM(cardId, newMaxZIndex);
  }, [maxZIndex, applyZIndexToDOM]);

  // Use onDragStop/onResizeStop instead of onLayoutChange.
  // onLayoutChange fires on EVERY layout change including programmatic ones (toggle),
  // which overwrites preset positions. onDragStop/onResizeStop only fire on USER actions.
  const onUserLayoutChange = useCallback(
    (newLayout) => {
      const validLayout = newLayout.filter((item) => item !== undefined);
      // Only persist to normalLayoutRef in normal mode — dragging in
      // compact/layered modes must NOT pollute the normal layout.
      if (layoutMode === "normal") {
        normalLayoutRef.current = validLayout;
      }
      setLayout(validLayout);
      saveDashboardState(validLayout, cardColors, minimizedCards);
      // Re-apply z-indices after RGL finishes — drag end resets inline styles
      requestAnimationFrame(() => applyAllZIndices());
    },
    [cardColors, minimizedCards, saveDashboardState, layoutMode, applyAllZIndices],
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
      // Allow minimize/expand in all modes (not just normal)
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
      // Only update normalLayoutRef in normal mode — dragging/minimizing in
      // compact/layered/modex modes must NOT pollute the normal layout.
      if (layoutMode === "normal") {
        normalLayoutRef.current = adjustedLayout;
      }
      saveDashboardState(adjustedLayout, cardColors, newMinimizedCards, undefined, newOriginalDimensions);
    },
    [minimizedCards, layout, cardColors, saveDashboardState, cardMinGridH, originalDimensions],
  );

  const _handleResetLayout = useCallback(() => {
    normalLayoutRef.current = defaultFixedLayout;
    setLayout(defaultFixedLayout);
    setCardColors({});
    setMinimizedCards({});
    setOriginalDimensions({});
    setCardZIndex({});
    setMaxZIndex(0);
    setLayoutMode("normal");
    saveDashboardState(defaultFixedLayout, {}, {}, "normal", {});
  }, [defaultFixedLayout, saveDashboardState]);

  // 4-way layout mode toggle: one-shot placement actions
  // Each toggle applies layout + minimize state directly — no useEffect dependency
  const handleCycleLayoutMode = useCallback(() => {
    const currentIdx = LAYOUT_MODES.indexOf(layoutMode);
    const nextMode = LAYOUT_MODES[(currentIdx + 1) % LAYOUT_MODES.length];

    // When leaving normal mode, save the current layout
    if (layoutMode === "normal") {
      normalLayoutRef.current = layout;
    }

    let newLayout;
    let newMinimizedCards = minimizedCards;
    let newOriginalDimensions = originalDimensions;

    switch (nextMode) {
      case "normal":
        // Restore saved normal layout, clear minimized
        newLayout = normalLayoutRef.current || defaultFixedLayout;
        newMinimizedCards = {};
        newOriginalDimensions = {};
        break;
      case "compact":
        // Apply compact grid, clear minimized
        newLayout = compactLayout;
        newMinimizedCards = {};
        newOriginalDimensions = {};
        break;
      case "layered": {
        // Apply layered bars: all cards minimized, stacked on the right.
        // Compute positions inline to guarantee fresh gridWidth values.
        const cardIds = Object.keys(cardComponents);
        const colWidthPx = gridWidth / COLS_COUNT;
        const barW = Math.round(300 / colWidthPx);
        const barH = Math.round(50 / ROW_HEIGHT_PX);
        const barX = Math.max(0, COLS_COUNT - barW);
        newLayout = cardIds.map((id, idx) => ({
          i: id,
          x: barX,
          y: idx * barH,
          w: barW,
          h: barH,
          minW: cardMinGridW,
          isDraggable: true,
          isResizable: true,
        }));
        const allMinimized = {};
        const dims = {};
        Object.keys(cardComponents).forEach((id) => {
          allMinimized[id] = true;
          const item = defaultFixedLayout.find((l) => l.i === id);
          if (item) dims[id] = { w: item.w, h: item.h };
        });
        newMinimizedCards = allMinimized;
        newOriginalDimensions = dims;
        break;
      }
      case "modex": {
        // Content-visible bars: taller than layered so card content is readable
        const mxCardIds = Object.keys(cardComponents);
        const mxColWidthPx = gridWidth / COLS_COUNT;
        const mxBarW = Math.round(300 / mxColWidthPx);
        const mxBarH = Math.round(150 / ROW_HEIGHT_PX);
        const mxBarX = Math.max(0, COLS_COUNT - mxBarW);
        newLayout = mxCardIds.map((id, idx) => ({
          i: id,
          x: mxBarX,
          y: idx * mxBarH,
          w: mxBarW,
          h: mxBarH,
          minW: cardMinGridW,
          isDraggable: true,
          isResizable: true,
        }));
        // All cards NOT minimized — content visible inside the taller bars
        newMinimizedCards = {};
        newOriginalDimensions = {};
        break;
      }
    }

    setLayoutMode(nextMode);
    setLayoutKey(k => k + 1); // Force ReactGridLayout remount to apply preset positions
    setLayout(newLayout);
    setMinimizedCards(newMinimizedCards);
    setOriginalDimensions(newOriginalDimensions);
    saveDashboardState(
      normalLayoutRef.current || newLayout,
      cardColors,
      newMinimizedCards,
      nextMode,
      newOriginalDimensions,
    );

  }, [layoutMode, layout, cardColors, minimizedCards, originalDimensions, defaultFixedLayout, compactLayout, layeredLayout, saveDashboardState, gridWidth, COLS_COUNT, ROW_HEIGHT_PX, cardMinGridW]);

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
                opacity: 0.15,
                background: "transparent",
                border: `1px dashed ${theme.palette.primary.main}`,
                borderRadius: "4px",
              },
              "&.react-draggable-dragging": {
                transition: "none !important",
                opacity: 0.9,
                // Outline the child div, not the grid item — so minimized
                // cards show a bar-sized outline instead of the full grid cell
                "& > div": {
                  outline: `2px solid ${theme.palette.primary.main}`,
                  borderRadius: "4px",
                },
              },
            },
          }}
        >
          <ReactGridLayout
            key={layoutKey}
            className="layout"
            layout={layout}
            style={{
              transition: "all 0.2s ease-out",
            }}
            cols={COLS_COUNT}
            rowHeight={ROW_HEIGHT_PX}
            width={gridWidth}
            containerPadding={[CONTAINER_PADDING_PX, CONTAINER_PADDING_PX]}
            margin={[CARD_MARGIN_PX, CARD_MARGIN_PX]}
            isDraggable={true}
            isResizable={true}
            compactType={null}
            preventCollision={false}
            useCSSTransforms={false}
            allowOverlap={true}
            draggableHandle=".card-header-buttons"
            draggableCancel="button, input, textarea, select, option, .non-draggable"
            onDragStart={(layout, oldItem) => handleCardClick(oldItem.i)}
            onDragStop={onUserLayoutChange}
            onResizeStop={onUserLayoutChange}
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
                      transition: "transform 0.2s ease-out, box-shadow 0.2s ease-out",
                      height: isMinimized ? "auto" : "100%",
                      maxHeight: "100%",
                      overflow: "hidden",
                    }}
                    onMouseDown={() => handleCardClick(cardId)}
                    onMouseUp={() => { applyZIndexToDOM(cardId, cardZIndex[cardId] || 0); applyAllZIndices(); }}
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
