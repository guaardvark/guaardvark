// frontend/src/pages/DashboardPage.jsx
// The dashboard is a card board (components/board): the board owns dragging,
// resizing, stacking and minimizing; this page owns which cards exist, the four
// layout modes (Normal, Compact, Compact Layered, Mode-X), hidden cards, card
// colours and where the arrangement is saved.
// - Each mode change is a one-shot placement; the cards are then free to move.
// - Right-click: each card has its own menu (Hide card among them); the empty
//   board has one for layout mode, reset, hidden cards and Sticky Notes.
// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  Paper,
  Typography,
  Tooltip,
  IconButton,
} from "@mui/material";
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
import VideoGenerationCard from "../components/dashboard/VideoGenerationCard";
import ApprovalsCard from "../components/dashboard/ApprovalsCard";
import FileManagerCard from "../components/dashboard/FileManagerCard";
import FamilySelfImprovementCard from "../components/dashboard/FamilySelfImprovementCard";
import RAGAutoresearchCard from "../components/dashboard/RAGAutoresearchCard";
import GpuStatusCard from "../components/dashboard/GpuStatusCard";
import { useLayout, useDashboardWidth } from "../contexts/LayoutContext";
import { ContextualLoader } from "../components/common/LoadingStates";
import EntityContextMenu from "../components/common/EntityContextMenu";
import ConfirmActionDialog from "../components/settings/ui/ConfirmActionDialog";
import useContextMenu from "../hooks/useContextMenu";
import { translate, useUiTranslation } from "../i18n";
import {
  ensureLayoutItem,
  hiddenCardIds,
  showCard,
  visibleLayout,
  withHiddenItems,
} from "../components/dashboard/dashboardCardVisibility";
import { appendCards, mergeSavedLayout } from "../components/dashboard/dashboardSavedLayout";
import { extensionCards } from "../extensions";
import {
  CardBoard,
  barRows,
  barsPreset,
  compactPreset,
  loadBoardState,
  narrowReflow,
  COMPACT_SCALE,
  nextMode,
  repackRows,
  restoreMinimizedSizes,
  saveBoardState,
  useBoardGestures,
  useBoardWidth,
  useCardStacking,
} from "../components/board";

// Cards an extension adds to the dashboard (see extensions.js).
const extraCards = extensionCards();

// In the order the Compact and bar layouts place them.
const cardComponents = {
  approvals: ApprovalsCard,
  imggen: ImageGenerationCard,
  videogen: VideoGenerationCard,
  project: ProjectManagerCard,
  website: WebsiteDataCard,
  tasks: TaskManagerCard,
  chat: SemanticSearchCard,
  clients: ClientsDashboardCard,
  csvgen: CSVGenerationCard,
  codegen: CodeGenerationCard,
  files: FileManagerCard,
  family: FamilySelfImprovementCard,
  autoresearch: RAGAutoresearchCard,
  gpu: GpuStatusCard,
  ...Object.fromEntries(extraCards.map((card) => [card.id, card.component])),
};

// Names for the "Show <card>" items of the background menu; match each card's title.
const cardTitles = () => ({
  project: translate("dashboard.project.title"),
  website: translate("dashboard.website.title"),
  tasks: translate("dashboard.tasks.title"),
  chat: translate("dashboard.chat.title"),
  clients: translate("dashboard.clients.title"),
  csvgen: translate("dashboard.csvgen.title"),
  codegen: translate("dashboard.codegen.title"),
  imggen: translate("dashboard.imggen.title"),
  videogen: translate("dashboard.videogen.title"),
  approvals: translate("dashboard.approvals.title"),
  files: translate("dashboard.files.title"),
  family: translate("dashboard.family.title"),
  autoresearch: translate("dashboard.autoresearch.title"),
  gpu: translate("dashboard.gpu.title"),
  ...Object.fromEntries(
    extraCards.map((card) => [card.id, typeof card.title === "function" ? card.title() : card.title || card.id]),
  ),
});

// Below this card width the board is shown re-packed instead of shrunk.
const NARROW_CARD_PX = 300;

// Turns on each card's right-click menu; a card adds its own items over this.
const NO_CARD_ACTIONS = [];

// Layout mode cycle: normal -> compact -> layered -> modex -> normal
const LAYOUT_MODES = ["normal", "compact", "layered", "modex"];
const layoutModeLabels = () => ({
  normal: translate("dashboard.layoutModes.normal"),
  compact: translate("dashboard.layoutModes.compact"),
  layered: translate("dashboard.layoutModes.layered"),
  modex: translate("dashboard.layoutModes.modex"),
});
const LAYOUT_MODE_ICONS = {
  normal: ViewModule,
  compact: ViewComfy,
  layered: Layers,
  modex: Layers,
};

const DashboardPage = () => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const { gridSettings } = useLayout();
  const dashboardWidth = useDashboardWidth();

  const {
    CARD_MARGIN_PX,
    COLS_COUNT,
    ROW_HEIGHT_PX,
    cardGridW,
    cardGridH,
    cardMinGridW,
    cardMinGridH,
  } = gridSettings;

  const systemName = useAppStore((state) => state.systemName);
  const [initialStateLoaded, setInitialStateLoaded] = useState(false);
  const [layoutError, setLayoutError] = useState(null);
  const [cardColors, setCardColors] = useState({});
  const [minimizedCards, setMinimizedCards] = useState({});
  const [hiddenCards, setHiddenCards] = useState({});
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const backgroundMenu = useContextMenu();
  const [layoutMode, setLayoutMode] = useState("normal");
  const [layoutKey, setLayoutKey] = useState(0); // Increments on every toggle to force the grid to remount
  const [gridContainerRef, gridWidth] = useBoardWidth(dashboardWidth);
  const stacking = useCardStacking();

  // Normal layout: full-size cards in grid
  const defaultFixedLayout = useMemo(() => {
    const { cardGridW, cardGridH, cardMinGridW } = gridSettings;
    // Top row: what is waiting on the person, then the latest pictures and clips.
    const items = [
      { i: "approvals", x: 0, y: 0, w: cardGridW, h: cardGridH },
      { i: "imggen", x: cardGridW, y: 0, w: cardGridW * 2, h: cardGridH },
      { i: "videogen", x: cardGridW * 3, y: 0, w: cardGridW * 2, h: cardGridH },
      { i: "project", x: 0, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "website", x: cardGridW, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "tasks", x: cardGridW * 2, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "chat", x: cardGridW * 3, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "clients", x: cardGridW * 4, y: cardGridH, w: cardGridW, h: cardGridH },
      { i: "csvgen", x: 0, y: cardGridH * 2, w: cardGridW, h: cardGridH },
      { i: "codegen", x: cardGridW, y: cardGridH * 2, w: cardGridW, h: cardGridH },
      { i: "gpu", x: cardGridW * 2, y: cardGridH * 2, w: cardGridW, h: cardGridH },
      { i: "files", x: cardGridW * 3, y: cardGridH * 2, w: cardGridW * 2, h: cardGridH * 1.5 },
      { i: "family", x: 0, y: cardGridH * 3, w: cardGridW * 2, h: cardGridH },
      { i: "autoresearch", x: cardGridW * 2, y: cardGridH * 3, w: cardGridW, h: cardGridH },
    ];
    const all = appendCards(items, extraCards, gridSettings);
    all.forEach((it) => {
      it.minW = cardMinGridW;
      it.isDraggable = true;
      it.isResizable = true;
    });
    return all;
  }, [gridSettings]);

  // Layout presets place only the cards that are showing, so hidden ones leave no gaps.
  const visibleCardIds = useMemo(
    () => Object.keys(cardComponents).filter((id) => !hiddenCards[id]),
    [hiddenCards],
  );

  // Compact: smaller cards in rows filling the width.
  const compactLayout = useMemo(
    () => compactPreset(visibleCardIds, gridSettings, gridWidth),
    [visibleCardIds, gridSettings, gridWidth],
  );

  // Layered: every card minimized to its bar, stacked down the right edge.
  // Mode-X: the same stack with slots tall enough to show each card's content.
  const LAYERED_BAR_PX = 50;
  const MODEX_BAR_PX = 150;
  const barsLayout = useCallback(
    (barPx) => barsPreset(visibleCardIds, gridSettings, gridWidth, barPx),
    [visibleCardIds, gridSettings, gridWidth],
  );
  const allMinimized = useCallback(
    () => Object.fromEntries(Object.keys(cardComponents).map((id) => [id, true])),
    [],
  );

  const [layout, setLayout] = useState(defaultFixedLayout);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const minimizedRef = useRef(minimizedCards);
  minimizedRef.current = minimizedCards;
  const gestures = useBoardGestures(
    useCallback(() => layoutRef.current, []),
    useCallback(() => minimizedRef.current, []),
  );
  // Store the user's normal-mode layout separately so switching modes doesn't lose it
  const normalLayoutRef = useRef(null);
  const { activeModel, isLoadingModel, modelError } = useStatus();

  // Load the saved board: layout, minimized cards, colours, hidden cards, layout mode.
  useEffect(() => {
    const applyDefaults = () => {
      normalLayoutRef.current = defaultFixedLayout;
      setLayout(defaultFixedLayout);
      setCardColors({});
      setMinimizedCards({});
      setHiddenCards({});
      setLayoutMode("normal");
    };
    const isMap = (value) => Boolean(value) && typeof value === "object";

    const fetchDashboardState = async () => {
      setLayoutError(null);
      try {
        const savedState = await loadBoardState("dashboard");
        if (!savedState) {
          applyDefaults();
        } else {
          if (savedState.layoutMode && LAYOUT_MODES.includes(savedState.layoutMode)) {
            setLayoutMode(savedState.layoutMode);
          }
          const minimized = isMap(savedState.minimizedCards) ? savedState.minimizedCards : {};

          let layoutToApply = null;
          if (Array.isArray(savedState.layout) && savedState.layout.length > 0) {
            layoutToApply = savedState.layout;
          } else if (Array.isArray(savedState) && savedState.length > 0) {
            layoutToApply = savedState;
          }

          if (layoutToApply) {
            // Half a default card, so an arrangement made before a card existed
            // stays in view under the row the new card arrives in.
            const merged = mergeSavedLayout(defaultFixedLayout, layoutToApply, {
              addedRowH: Math.round(cardGridH / 2),
              extraItemProps: { minW: cardMinGridW, minH: cardMinGridH, isDraggable: true, isResizable: true },
            });
            const validatedLayout = restoreMinimizedSizes(merged, minimized, savedState.originalDimensions);
            normalLayoutRef.current = validatedLayout;
            setLayout(validatedLayout);
          } else {
            normalLayoutRef.current = defaultFixedLayout;
            setLayout(defaultFixedLayout);
          }

          if (isMap(savedState.cardColors)) setCardColors(savedState.cardColors);
          setMinimizedCards(minimized);
          if (isMap(savedState.hiddenCards)) setHiddenCards(savedState.hiddenCards);
        }
      } catch (e) {
        console.error("Dashboard: Error fetching or processing state:", e);
        const message = e.status
          ? t("dashboard.fetchStateFailed", { statusText: e.statusText, status: e.status })
          : e.message;
        setLayoutError(t("dashboard.loadStateFailed", { message }));
        applyDefaults();
      }
      setInitialStateLoaded(true);
    };
    fetchDashboardState();
  }, [defaultFixedLayout, cardMinGridW, cardMinGridH, cardGridH]);

  // Once, after the saved state is in: place the cards for a saved preset mode.
  // Later mode changes place them in handleCycleLayoutMode.
  useEffect(() => {
    if (!initialStateLoaded) return;
    if (layoutMode === "compact") {
      setLayout(compactLayout);
    } else if (layoutMode === "layered") {
      setLayout(barsLayout(LAYERED_BAR_PX));
      setMinimizedCards(allMinimized());
    } else if (layoutMode === "modex") {
      setLayout(barsLayout(MODEX_BAR_PX));
      setMinimizedCards({});
    }
    // normal: already loaded from saved state
  }, [initialStateLoaded]);

  // Save the board. The layout saved is always the Normal one: presets are derived.
  const saveDashboardState = useCallback(
    async (newLayout, newCardColors, newMinimizedCards, newLayoutMode, newHiddenCards) => {
      try {
        await saveBoardState("dashboard", {
          layout: normalLayoutRef.current || newLayout || layout,
          cardColors: newCardColors || cardColors,
          minimizedCards: newMinimizedCards || minimizedCards,
          layoutMode: newLayoutMode !== undefined ? newLayoutMode : layoutMode,
          hiddenCards: newHiddenCards !== undefined ? newHiddenCards : hiddenCards,
          lastSaved: new Date().toISOString(),
        });
        setLayoutError(null);
      } catch (err) {
        console.error("Failed to save dashboard state:", err);
        setLayoutError(t("dashboard.saveStateFailed"));
      }
    },
    [layout, cardColors, minimizedCards, layoutMode, hiddenCards],
  );

  // On a narrow board the two card-grid modes are shown re-packed into columns
  // of readable width instead of shrinking; the bar modes are narrow strips
  // already and are left alone.
  // Compact cards are small on purpose, so the board is only narrow for them
  // below their own scaled-down minimum; at the full-size minimum Compact was
  // re-packed (and locked) on every ordinary screen.
  const compact = layoutMode === "compact";
  const reflow = narrowReflow(
    gridSettings,
    gridWidth,
    compact ? Math.round(cardGridW * COMPACT_SCALE) : cardGridW,
    compact ? Math.round(NARROW_CARD_PX * COMPACT_SCALE) : NARROW_CARD_PX,
  );
  const isNarrow = (layoutMode === "normal" || layoutMode === "compact") && reflow.isNarrow;

  // A drag or resize ended. The grid also reports programmatic changes through
  // onLayoutChange, which would overwrite a preset; these two only fire for a person.
  const onUserLayoutChange = useCallback(
    (reported, fromDrag) => {
      // The re-packed view is derived from the saved layout; nothing done in it is kept.
      if (isNarrow) return;
      const settled = gestures.settle(reported, fromDrag);
      if (!settled) return;
      const validLayout = withHiddenItems(settled, layout, hiddenCards);
      // Dragging in a preset mode must not change the Normal layout.
      if (layoutMode === "normal") {
        normalLayoutRef.current = validLayout;
      }
      setLayout(validLayout);
      saveDashboardState(validLayout, cardColors, minimizedCards);
    },
    [cardColors, minimizedCards, saveDashboardState, layoutMode, gestures, isNarrow, layout, hiddenCards],
  );

  // A null colour resets the card to the theme's.
  const handleCardColorChange = useCallback(
    (cardId, color) => {
      const newCardColors = { ...cardColors, [cardId]: color };
      if (!color) delete newCardColors[cardId];
      setCardColors(newCardColors);
      saveDashboardState(layout, newCardColors, minimizedCards);
    },
    [cardColors, layout, minimizedCards, saveDashboardState],
  );

  // Minimizing leaves the card's place and size alone: the board shows its bar
  // and the stored size is what it opens to again.
  const handleToggleMinimize = useCallback(
    (cardId) => {
      const expanding = Boolean(minimizedCards[cardId]);
      gestures.beginToggle(cardId);
      const newMinimizedCards = { ...minimizedCards, [cardId]: !expanding };
      setMinimizedCards(newMinimizedCards);

      // A bar in the Layered stack has no size of its own; it opens at the card's default.
      let nextLayout = layout;
      const size = expanding && layoutMode === "layered" && defaultFixedLayout.find((item) => item.i === cardId);
      if (size) {
        nextLayout = layout.map((item) => (item.i === cardId ? { ...item, w: size.w, h: size.h } : item));
        setLayout(nextLayout);
      }
      saveDashboardState(nextLayout, cardColors, newMinimizedCards);
    },
    [minimizedCards, layout, layoutMode, defaultFixedLayout, cardColors, gestures, saveDashboardState],
  );

  const handleResetLayout = useCallback(() => {
    normalLayoutRef.current = defaultFixedLayout;
    setLayout(defaultFixedLayout);
    setLayoutKey((k) => k + 1);
    setCardColors({});
    setMinimizedCards({});
    setHiddenCards({});
    stacking.reset();
    setLayoutMode("normal");
    saveDashboardState(defaultFixedLayout, {}, {}, "normal", {});
  }, [defaultFixedLayout, saveDashboardState, stacking]);

  const handleHideCard = useCallback(
    (cardId) => {
      const next = { ...hiddenCards, [cardId]: true };
      setHiddenCards(next);
      saveDashboardState(undefined, undefined, undefined, undefined, next);
    },
    [hiddenCards, saveDashboardState],
  );

  // Shows one hidden card, or all of them when cardId is omitted.
  const handleShowCards = useCallback(
    (cardId) => {
      const ids = cardId === undefined ? hiddenCardIds(hiddenCards) : [cardId];
      const nextLayout = ids.reduce(
        (acc, id) => ensureLayoutItem(acc, id, defaultFixedLayout.find((item) => item.i === id)),
        layout,
      );
      if (nextLayout !== layout) {
        setLayout(nextLayout);
        if (layoutMode === "normal") normalLayoutRef.current = nextLayout;
      }
      const next = showCard(hiddenCards, cardId);
      setHiddenCards(next);
      saveDashboardState(nextLayout, undefined, undefined, undefined, next);
    },
    [hiddenCards, layout, layoutMode, defaultFixedLayout, saveDashboardState],
  );

  // Each mode change is a one-shot placement: the cards are put where the mode
  // has them and are then free to move.
  const handleCycleLayoutMode = useCallback(() => {
    const mode = nextMode(LAYOUT_MODES, layoutMode);

    if (layoutMode === "normal") {
      normalLayoutRef.current = layout;
    }

    let newLayout;
    let newMinimizedCards = {};
    if (mode === "compact") {
      newLayout = compactLayout;
    } else if (mode === "layered") {
      newLayout = barsLayout(LAYERED_BAR_PX);
      newMinimizedCards = allMinimized();
    } else if (mode === "modex") {
      newLayout = barsLayout(MODEX_BAR_PX);
    } else {
      newLayout = normalLayoutRef.current || defaultFixedLayout;
    }

    setLayoutMode(mode);
    setLayoutKey((k) => k + 1); // remount the grid so the preset's positions apply
    setLayout(newLayout);
    setMinimizedCards(newMinimizedCards);
    saveDashboardState(normalLayoutRef.current || newLayout, cardColors, newMinimizedCards, mode);
  }, [layoutMode, layout, cardColors, defaultFixedLayout, compactLayout, barsLayout, allMinimized, saveDashboardState]);

  // What the board shows: the visible cards, re-packed when the board is narrow.
  // A minimized card counts as its bar when rows are packed.
  const boardLayout = useMemo(() => {
    const shown = visibleLayout(layout, hiddenCards);
    if (!isNarrow) return shown;
    const rows = barRows(ROW_HEIGHT_PX, CARD_MARGIN_PX);
    return repackRows(
      shown.map((item) => (minimizedCards[item.i] ? { ...item, h: rows } : item)),
      COLS_COUNT,
      reflow.cardsPerRow,
      cardGridH,
    );
  }, [isNarrow, reflow.cardsPerRow, layout, hiddenCards, minimizedCards, ROW_HEIGHT_PX, CARD_MARGIN_PX, COLS_COUNT, cardGridH]);

  const LayoutModeIcon = LAYOUT_MODE_ICONS[layoutMode];
  const nextLayoutMode = nextMode(LAYOUT_MODES, layoutMode);

  // Right-click on the grid's empty space; cards open their own menus.
  const handleBackgroundContextMenu = (e) => {
    // A minimized card's grid cell is taller than its title bar; a right-click
    // on that empty part of the cell (the cell itself) counts as background.
    const cell = e.target?.closest?.("[data-card-id]");
    if (cell && cell !== e.target) return;
    backgroundMenu.open(e);
  };

  const hiddenIds = hiddenCardIds(hiddenCards);
  const backgroundActions = backgroundMenu.isOpen
    ? [
        { label: t("dashboard.menu.cycleLayout", { mode: layoutModeLabels()[nextLayoutMode] }), onClick: handleCycleLayoutMode },
        { label: t("dashboard.menu.resetLayout"), onClick: () => setResetConfirmOpen(true) },
        hiddenIds.length === 0 && { label: t("dashboard.menu.noHiddenCards"), disabled: true, dividerBefore: true },
        ...hiddenIds.map((id, idx) => ({
          label: t("dashboard.menu.showCard", { card: cardTitles()[id] || id }),
          onClick: () => handleShowCards(id),
          dividerBefore: idx === 0,
        })),
        hiddenIds.length > 1 && { label: t("dashboard.menu.showAllHidden"), onClick: () => handleShowCards() },
        { label: t("dashboard.stickyNotes"), onClick: () => navigate("/notes"), dividerBefore: true },
      ]
    : [];

  if (!initialStateLoaded) {
    return (
      <PageLayout title={systemName || t("dashboard.title")} variant="grid">
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flex: 1,
          }}
        >
          <ContextualLoader loading message={t("dashboard.loadingDashboard")} showProgress={false} inline />
        </Box>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title={systemName || t("dashboard.title")}
      variant="grid"
      actions={
        <>
          <Tooltip title={t("dashboard.dashboardCards")}>
            <IconButton
              size="small"
              sx={{ color: "primary.main" }}
              aria-haspopup="menu"
              onClick={(e) => backgroundMenu.open(e)}
            >
              <DashboardIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("dashboard.stickyNotes")}>
            <IconButton onClick={() => navigate("/notes")} size="small" sx={{ opacity: 0.5 }}>
              <StickyNote2 fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("dashboard.layoutTooltip", { mode: layoutModeLabels()[layoutMode] })}>
            <IconButton onClick={handleCycleLayoutMode} size="small" sx={{ opacity: 0.6 }}>
              <LayoutModeIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </>
      }
      modelStatus
      activeModel={isLoadingModel ? t("common.loading") : modelError ? t("common.error") : activeModel}
    >

        <Box
          onContextMenu={handleBackgroundContextMenu}
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

        <CardBoard
          containerRef={gridContainerRef}
          gridKey={layoutKey}
          width={gridWidth}
          grid={gridSettings}
          layout={boardLayout}
          overlap={false}
          minimized={minimizedCards}
          stacking={stacking}
          locked={isNarrow}
          dragHandle=".card-header-buttons"
          onDragStart={(cardId) => {
            stacking.raise(cardId);
            gestures.onDragStart();
          }}
          onDragStop={(reported) => onUserLayoutChange(reported, true)}
          onResizeStop={(reported) => onUserLayoutChange(reported, false)}
          renderCard={(layoutItem, isMinimized) => {
            const cardId = layoutItem.i;
            const CardComponent = cardComponents[cardId];
            return CardComponent ? (
              <CardComponent
                id={cardId}
                cardColor={cardColors[cardId]}
                onCardColorChange={(color) => handleCardColorChange(cardId, color)}
                isMinimized={isMinimized}
                onToggleMinimize={() => handleToggleMinimize(cardId)}
                contextMenuActions={NO_CARD_ACTIONS}
                onHideCard={() => handleHideCard(cardId)}
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
                  backgroundImage: "none",
                }}
              >
                <Typography sx={{ color: "warning.contrastText" }}>{t("dashboard.missingCard", { id: cardId })}</Typography>
              </Paper>
            );
          }}
        />
      </Box>

      <EntityContextMenu
        anchorPosition={backgroundMenu.anchorPosition}
        onClose={backgroundMenu.close}
        actions={backgroundActions}
      />
      <ConfirmActionDialog
        open={resetConfirmOpen}
        title={t("dashboard.resetConfirm.title")}
        description={t("dashboard.resetConfirm.description")}
        keeps={t("dashboard.resetConfirm.keeps")}
        confirmLabel={t("dashboard.resetConfirm.confirm")}
        onConfirm={() => {
          setResetConfirmOpen(false);
          handleResetLayout();
        }}
        onClose={() => setResetConfirmOpen(false)}
      />
    </PageLayout>
  );
};

export default DashboardPage;
