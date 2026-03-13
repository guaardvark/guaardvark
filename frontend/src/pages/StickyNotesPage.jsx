// frontend/src/pages/StickyNotesPage.jsx
// Sticky notes board — identical grid mechanics to DashboardPage
// Each grid item is an editable sticky note with basic formatting (B/I/U/Link)
// Supports drag, resize, color change, minimize (double-click header), layout modes, z-index layering

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  Paper,
  Typography,
  Tooltip,
  IconButton,
  useTheme,
} from "@mui/material";
import ReactGridLayout from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import {
  ViewModule,
  ViewComfy,
  ViewList,
  FormatBold,
  FormatItalic,
  FormatUnderlined,
  InsertLink,
  Add,
  Close,
  Dashboard as DashboardIcon,
  StickyNote2,
} from "@mui/icons-material";

import { useNavigate } from "react-router-dom";
import { useAppStore } from "../stores/useAppStore";
import PageLayout from "../components/layout/PageLayout";
import { useLayout, useDashboardWidth } from "../contexts/LayoutContext";
import { ContextualLoader } from "../components/common/LoadingStates";

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

const PASTEL_COLORS = [
  "#fff9c4", // yellow
  "#f8bbd0", // pink
  "#c8e6c9", // green
  "#bbdefb", // blue
  "#e1bee7", // purple
  "#ffe0b2", // orange
  "#b2dfdb", // teal
  "#d7ccc8", // brown
];

// YIQ contrast helper (same formula as DashboardPage / DashboardCardWrapper)
const getContrastColor = (bgColor) => {
  if (!bgColor) return "rgba(0, 0, 0, 0.87)";
  let hex = bgColor.replace("#", "");
  if (hex.length === 3)
    hex = hex
      .split("")
      .map((h) => h + h)
      .join("");
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);
  const yiq = (r * 299 + g * 587 + b * 114) / 1000;
  return yiq > 186 ? "rgba(0, 0, 0, 0.87)" : "rgba(255, 255, 255, 0.95)";
};

// ─── Inline StickyNote component ────────────────────────────────────────────

const StickyNote = React.memo(
  ({
    noteId,
    content,
    color,
    textColor,
    isMinimized,
    onToggleMinimize,
    onColorChange,
    onContentChange,
    onDelete,
    onFormat,
    onInsertLink,
    theme,
    noteRef,
  }) => {
    const colorInputRef = useRef(null);
    const contentRef = useRef(null);
    const [lastClickTime, setLastClickTime] = useState(0);
    const [clickCount, setClickCount] = useState(0);
    const clickTimeoutRef = useRef(null);

    // Populate contentEditable on mount only
    useEffect(() => {
      if (contentRef.current && content !== undefined) {
        contentRef.current.innerHTML = content;
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
      return () => {
        if (clickTimeoutRef.current) clearTimeout(clickTimeoutRef.current);
      };
    }, []);

    // Double-click detection on header (same pattern as DashboardCardWrapper)
    const handleMouseDown = useCallback(
      (e) => {
        const now = Date.now();
        const diff = now - lastClickTime;

        if (clickTimeoutRef.current) {
          clearTimeout(clickTimeoutRef.current);
          clickTimeoutRef.current = null;
        }

        if (diff < 500 && clickCount === 1) {
          setClickCount(0);
          setLastClickTime(0);
          if (onToggleMinimize) onToggleMinimize();
        } else {
          setLastClickTime(now);
          setClickCount(1);
          clickTimeoutRef.current = setTimeout(() => {
            setClickCount(0);
            setLastClickTime(0);
          }, 500);
        }
      },
      [lastClickTime, clickCount, onToggleMinimize],
    );

    const handleInput = useCallback(() => {
      if (contentRef.current) {
        onContentChange(contentRef.current.innerHTML);
      }
    }, [onContentChange]);

    const handleBlur = useCallback(() => {
      if (contentRef.current) {
        onContentChange(contentRef.current.innerHTML);
      }
    }, [onContentChange]);

    const dividerColor =
      textColor === "rgba(0, 0, 0, 0.87)"
        ? "rgba(0,0,0,0.1)"
        : "rgba(255,255,255,0.15)";

    return (
      <Paper
        elevation={2}
        className={`draggable-card ${isMinimized ? "minimized" : ""}`}
        sx={{
          display: "flex",
          flexDirection: "column",
          height: isMinimized ? "auto" : "100%",
          minHeight: isMinimized ? "50px" : "120px",
          overflow: "hidden",
          borderRadius: "5px",
          backgroundColor: color,
          color: textColor,
          transition: theme.transitions.create(["height", "min-height"], {
            duration: theme.transitions.duration.standard,
          }),
        }}
      >
        {/* ── Header — drag handle ─────────────────────────────────── */}
        <Box
          className="note-header"
          onMouseDown={handleMouseDown}
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            px: 0.75,
            py: 0.25,
            minHeight: "32px",
            cursor: "grab",
            userSelect: "none",
            "&:active": { cursor: "grabbing" },
            "&:hover": {
              backgroundColor: "rgba(0,0,0,0.04)",
              borderRadius: "4px 4px 0 0",
            },
          }}
        >
          {/* Color picker dot */}
          <Box sx={{ position: "relative", mr: 0.5 }}>
            <Tooltip title="Change color">
              <IconButton
                onClick={() => colorInputRef.current?.click()}
                className="non-draggable"
                sx={{
                  width: 8,
                  height: 8,
                  minWidth: 8,
                  minHeight: 8,
                  p: 0,
                  borderRadius: "50%",
                  backgroundColor: color,
                  border: `1px solid ${textColor}`,
                  transition: "all 0.2s ease",
                  "&:hover": {
                    transform: "scale(1.3)",
                    boxShadow: `0 0 3px ${color}`,
                  },
                }}
              >
                <Box
                  sx={{
                    width: 2,
                    height: 2,
                    borderRadius: "50%",
                    backgroundColor: textColor,
                  }}
                />
              </IconButton>
            </Tooltip>
            <input
              ref={colorInputRef}
              type="color"
              value={color}
              onChange={(e) => onColorChange(e.target.value)}
              style={{
                position: "absolute",
                opacity: 0,
                pointerEvents: "none",
                width: 1,
                height: 1,
              }}
            />
          </Box>

          {/* Delete button */}
          <Tooltip title="Delete note">
            <IconButton
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              className="non-draggable"
              size="small"
              sx={{
                width: 16,
                height: 16,
                p: 0,
                color: textColor,
                opacity: 0.4,
                "&:hover": { opacity: 1, color: theme.palette.error.main },
              }}
            >
              <Close sx={{ fontSize: 12 }} />
            </IconButton>
          </Tooltip>
        </Box>

        {/* ── Content area (hidden when minimized) ─────────────────── */}
        {!isMinimized && (
          <>
            {/* Formatting toolbar */}
            <Box
              sx={{
                display: "flex",
                gap: 0.25,
                px: 0.75,
                pb: 0.5,
                borderBottom: `1px solid ${dividerColor}`,
              }}
            >
              {[
                { cmd: "bold", icon: <FormatBold sx={{ fontSize: 14 }} />, tip: "Bold (Ctrl+B)" },
                { cmd: "italic", icon: <FormatItalic sx={{ fontSize: 14 }} />, tip: "Italic (Ctrl+I)" },
                { cmd: "underline", icon: <FormatUnderlined sx={{ fontSize: 14 }} />, tip: "Underline (Ctrl+U)" },
              ].map(({ cmd, icon, tip }) => (
                <Tooltip key={cmd} title={tip}>
                  <IconButton
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onFormat(cmd);
                    }}
                    className="non-draggable"
                    size="small"
                    sx={{
                      width: 22,
                      height: 22,
                      p: 0,
                      color: textColor,
                      opacity: 0.6,
                      "&:hover": { opacity: 1 },
                    }}
                  >
                    {icon}
                  </IconButton>
                </Tooltip>
              ))}
              <Tooltip title="Insert Link">
                <IconButton
                  onMouseDown={(e) => {
                    e.preventDefault();
                    onInsertLink();
                  }}
                  className="non-draggable"
                  size="small"
                  sx={{
                    width: 22,
                    height: 22,
                    p: 0,
                    color: textColor,
                    opacity: 0.6,
                    "&:hover": { opacity: 1 },
                  }}
                >
                  <InsertLink sx={{ fontSize: 14 }} />
                </IconButton>
              </Tooltip>
            </Box>

            {/* Editable content */}
            <Box
              ref={(el) => {
                contentRef.current = el;
                if (noteRef) noteRef(el);
              }}
              className="note-content non-draggable"
              contentEditable
              suppressContentEditableWarning
              onBlur={handleBlur}
              onInput={handleInput}
              sx={{
                flexGrow: 1,
                p: 1,
                overflow: "auto",
                outline: "none",
                fontSize: "0.85rem",
                lineHeight: 1.5,
                color: textColor,
                cursor: "text",
                minHeight: 60,
                "& a": {
                  color:
                    textColor === "rgba(0, 0, 0, 0.87)"
                      ? theme.palette.primary.dark
                      : theme.palette.primary.light,
                  textDecoration: "underline",
                },
                "&:empty::before": {
                  content: '"Type your note..."',
                  color:
                    textColor === "rgba(0, 0, 0, 0.87)"
                      ? "rgba(0,0,0,0.3)"
                      : "rgba(255,255,255,0.3)",
                  fontStyle: "italic",
                },
              }}
            />
          </>
        )}
      </Paper>
    );
  },
);

StickyNote.displayName = "StickyNote";

// ─── Main page component ────────────────────────────────────────────────────

const StickyNotesPage = () => {
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
    cardGridW,
    cardGridH,
  } = gridSettings;

  const [initialStateLoaded, setInitialStateLoaded] = useState(false);
  const [layoutError, setLayoutError] = useState(null);
  const [notes, setNotes] = useState({});
  const [noteColors, setNoteColors] = useState({});
  const [minimizedCards, setMinimizedCards] = useState({});
  const [originalDimensions, setOriginalDimensions] = useState({});
  const [cardZIndex, setCardZIndex] = useState({});
  const [maxZIndex, setMaxZIndex] = useState(0);
  const [layoutMode, setLayoutMode] = useState("normal");
  const gridContainerRef = useRef(null);
  const [gridWidth, setGridWidth] = useState(dashboardWidth);
  const isTogglingRef = useRef(false);
  const noteRefs = useRef({});
  const saveTimeoutRef = useRef(null);

  // Refs for latest state values — avoids stale closures in saveState/debouncedSave
  const notesRef = useRef(notes);
  const noteColorsRef = useRef(noteColors);
  const minimizedCardsRef = useRef(minimizedCards);
  const layoutModeRef = useRef(layoutMode);
  const layoutRef = useRef(layout);
  useEffect(() => { notesRef.current = notes; }, [notes]);
  useEffect(() => { noteColorsRef.current = noteColors; }, [noteColors]);
  useEffect(() => { minimizedCardsRef.current = minimizedCards; }, [minimizedCards]);
  useEffect(() => { layoutModeRef.current = layoutMode; }, [layoutMode]);
  useEffect(() => { layoutRef.current = layout; }, [layout]);

  // ── Grid width tracking ──────────────────────────────────────────────────

  useEffect(() => {
    if (dashboardWidth > 0) setGridWidth(dashboardWidth);
  }, [dashboardWidth]);

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

  // ── Layout helpers ───────────────────────────────────────────────────────

  const makeLayoutItem = useCallback(
    (noteId, index) => ({
      i: noteId,
      x: (index % 4) * cardGridW,
      y: Math.floor(index / 4) * cardGridH,
      w: cardGridW,
      h: cardGridH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: true,
    }),
    [cardGridW, cardGridH, cardMinGridW],
  );

  const [layout, setLayout] = useState([]);
  const normalLayoutRef = useRef(null);

  // Compact layout (derived)
  const compactLayout = useMemo(() => {
    const noteIds = Object.keys(notes);
    const compactW = Math.round(cardGridW * 0.71);
    const compactH = Math.round(cardGridH * 0.71);
    const colWidthPx = gridWidth / COLS_COUNT;
    const cardPixelW = compactW * colWidthPx;
    const cardsPerRow = Math.max(1, Math.floor(gridWidth / cardPixelW));

    return noteIds.map((id, idx) => ({
      i: id,
      x: (idx % cardsPerRow) * compactW,
      y: Math.floor(idx / cardsPerRow) * compactH,
      w: compactW,
      h: compactH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: false,
    }));
  }, [notes, cardGridW, cardGridH, gridWidth, COLS_COUNT, cardMinGridW]);

  // Collapsed layout (derived)
  const collapsedLayout = useMemo(() => {
    const noteIds = Object.keys(notes);
    const colWidthPx = gridWidth / COLS_COUNT;
    const barW = Math.round(300 / colWidthPx);
    const barH = Math.round(50 / ROW_HEIGHT_PX);
    const barX = Math.max(0, COLS_COUNT - barW);

    return noteIds.map((id, idx) => ({
      i: id,
      x: barX,
      y: idx * barH,
      w: barW,
      h: barH,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: false,
    }));
  }, [notes, gridWidth, COLS_COUNT, ROW_HEIGHT_PX, cardMinGridW]);

  // ── Load saved state ─────────────────────────────────────────────────────

  useEffect(() => {
    const fetchState = async () => {
      setLayoutError(null);
      try {
        const res = await fetch("/api/state/sticky-notes");
        if (!res.ok) {
          if (res.status === 404) {
            // First visit — one default note
            const id = `note-${Date.now()}`;
            const defaultNotes = { [id]: { content: "" } };
            const defaultColors = { [id]: PASTEL_COLORS[0] };
            const defaultLayout = [makeLayoutItem(id, 0)];
            setNotes(defaultNotes);
            setNoteColors(defaultColors);
            normalLayoutRef.current = defaultLayout;
            setLayout(defaultLayout);
            setLayoutMode("normal");
          } else {
            throw new Error(`${res.statusText} (${res.status})`);
          }
        } else {
          const saved = await res.json();

          if (saved.layoutMode && LAYOUT_MODES.includes(saved.layoutMode)) {
            setLayoutMode(saved.layoutMode);
          }
          if (saved.notes && typeof saved.notes === "object") {
            setNotes(saved.notes);
          }
          if (saved.noteColors && typeof saved.noteColors === "object") {
            setNoteColors(saved.noteColors);
          }
          if (saved.minimizedCards && typeof saved.minimizedCards === "object") {
            setMinimizedCards(saved.minimizedCards);
          }

          const noteIds = Object.keys(saved.notes || {});
          if (Array.isArray(saved.layout) && saved.layout.length > 0) {
            const validLayout = saved.layout.filter((item) =>
              noteIds.includes(item.i),
            );
            // Add missing notes to layout
            noteIds.forEach((id, idx) => {
              if (!validLayout.some((item) => item.i === id)) {
                validLayout.push(makeLayoutItem(id, idx));
              }
            });
            normalLayoutRef.current = validLayout;
            setLayout(validLayout);
          } else if (noteIds.length > 0) {
            const dl = noteIds.map((id, idx) => makeLayoutItem(id, idx));
            normalLayoutRef.current = dl;
            setLayout(dl);
          }
        }
      } catch (e) {
        console.error("StickyNotes: Error fetching state:", e);
        setLayoutError(`Failed to load notes: ${e.message}. Using defaults.`);
        const id = `note-${Date.now()}`;
        setNotes({ [id]: { content: "" } });
        setNoteColors({ [id]: PASTEL_COLORS[0] });
        const dl = [makeLayoutItem(id, 0)];
        normalLayoutRef.current = dl;
        setLayout(dl);
        setLayoutMode("normal");
      }
      setInitialStateLoaded(true);
    };
    fetchState();
  }, [makeLayoutItem]);

  // ── Apply layout mode ────────────────────────────────────────────────────

  useEffect(() => {
    if (!initialStateLoaded) return;
    isTogglingRef.current = true;
    if (layoutMode === "compact") {
      setLayout(compactLayout);
    } else if (layoutMode === "collapsed") {
      setLayout(collapsedLayout);
    } else {
      setLayout(normalLayoutRef.current || []);
    }
    requestAnimationFrame(() => {
      isTogglingRef.current = false;
    });
  }, [layoutMode, initialStateLoaded, compactLayout, collapsedLayout]);

  // ── Persistence ──────────────────────────────────────────────────────────

  const saveState = useCallback(
    async (
      newLayout,
      newNoteColors,
      newMinimizedCards,
      newLayoutMode,
      newNotes,
    ) => {
      try {
        const body = {
          notes: newNotes || notesRef.current,
          layout: normalLayoutRef.current || newLayout || layoutRef.current,
          noteColors: newNoteColors || noteColorsRef.current,
          minimizedCards: newMinimizedCards || minimizedCardsRef.current,
          layoutMode:
            newLayoutMode !== undefined ? newLayoutMode : layoutModeRef.current,
          lastSaved: new Date().toISOString(),
        };
        const res = await fetch("/api/state/sticky-notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`(${res.status})`);
        setLayoutError(null);
      } catch (err) {
        console.error("Failed to save sticky notes state:", err);
        setLayoutError("Failed to save notes.");
      }
    },
    [],
  );

  // Debounced save for content typing
  const debouncedSave = useCallback(
    (newNotes) => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = setTimeout(() => {
        saveState(null, null, null, undefined, newNotes);
      }, 500);
    },
    [saveState],
  );

  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    };
  }, []);

  // ── Event handlers ───────────────────────────────────────────────────────

  const onLayoutChange = useCallback(
    (newLayout) => {
      if (isTogglingRef.current) return;
      const validLayout = newLayout.filter((item) => item !== undefined);
      if (layoutMode === "normal") normalLayoutRef.current = validLayout;
      setLayout(validLayout);
      saveState(validLayout, noteColors, minimizedCards);
    },
    [noteColors, minimizedCards, saveState, layoutMode],
  );

  const handleNoteColorChange = useCallback(
    (noteId, color) => {
      const c = { ...noteColors, [noteId]: color };
      setNoteColors(c);
      saveState(layout, c, minimizedCards);
    },
    [noteColors, layout, minimizedCards, saveState],
  );

  const handleToggleMinimize = useCallback(
    (noteId) => {
      if (layoutMode !== "normal") return;
      isTogglingRef.current = true;

      const newMin = { ...minimizedCards, [noteId]: !minimizedCards[noteId] };
      setMinimizedCards(newMin);

      const newOrig = { ...originalDimensions };
      const adjusted = layout.map((item) => {
        if (item.i === noteId) {
          if (newMin[noteId]) {
            newOrig[noteId] = { w: item.w, h: item.h };
            return { ...item, h: cardMinGridH };
          }
          const orig = newOrig[noteId];
          if (orig) {
            delete newOrig[noteId];
            return { ...item, w: orig.w, h: orig.h };
          }
          return item;
        }
        return item;
      });

      setOriginalDimensions(newOrig);
      setLayout(adjusted);
      normalLayoutRef.current = adjusted;
      saveState(adjusted, noteColors, newMin);
      requestAnimationFrame(() => {
        isTogglingRef.current = false;
      });
    },
    [
      minimizedCards,
      layout,
      noteColors,
      saveState,
      cardMinGridH,
      originalDimensions,
      layoutMode,
    ],
  );

  const handleCardClick = useCallback(
    (noteId) => {
      const z = maxZIndex + 1;
      setMaxZIndex(z);
      setCardZIndex((prev) => ({ ...prev, [noteId]: z }));
      const el = document.querySelector(`[data-card-id="${noteId}"]`);
      if (el) {
        el.style.setProperty("z-index", z, "important");
        const paper = el.querySelector(".MuiPaper-root");
        if (paper) paper.style.setProperty("z-index", z, "important");
      }
    },
    [maxZIndex],
  );

  const handleCycleLayoutMode = useCallback(() => {
    const idx = LAYOUT_MODES.indexOf(layoutMode);
    const next = LAYOUT_MODES[(idx + 1) % LAYOUT_MODES.length];
    if (layoutMode === "normal") normalLayoutRef.current = layout;
    setLayoutMode(next);
    saveState(
      normalLayoutRef.current || layout,
      noteColors,
      minimizedCards,
      next,
    );
  }, [layoutMode, layout, noteColors, minimizedCards, saveState]);

  // Add note
  const handleAddNote = useCallback(() => {
    const id = `note-${Date.now()}`;
    const colorIdx = Object.keys(notes).length % PASTEL_COLORS.length;
    const newNotes = { ...notes, [id]: { content: "" } };
    const newColors = { ...noteColors, [id]: PASTEL_COLORS[colorIdx] };
    const item = makeLayoutItem(id, Object.keys(notes).length);
    const newLayout = [...(normalLayoutRef.current || layout), item];
    normalLayoutRef.current = newLayout;

    setNotes(newNotes);
    setNoteColors(newColors);
    setLayout(newLayout);
    saveState(newLayout, newColors, minimizedCards, undefined, newNotes);
  }, [notes, noteColors, layout, minimizedCards, makeLayoutItem, saveState]);

  // Delete note
  const handleDeleteNote = useCallback(
    (noteId) => {
      const { [noteId]: _, ...rest } = notes;
      const { [noteId]: __, ...restColors } = noteColors;
      const { [noteId]: ___, ...restMin } = minimizedCards;
      const newLayout = (normalLayoutRef.current || layout).filter(
        (i) => i.i !== noteId,
      );
      normalLayoutRef.current = newLayout;
      setNotes(rest);
      setNoteColors(restColors);
      setMinimizedCards(restMin);
      setLayout(newLayout);
      saveState(newLayout, restColors, restMin, undefined, rest);
    },
    [notes, noteColors, minimizedCards, layout, saveState],
  );

  // Content change (debounced save)
  const handleNoteContentChange = useCallback(
    (noteId, content) => {
      const newNotes = { ...notes, [noteId]: { content } };
      setNotes(newNotes);
      debouncedSave(newNotes);
    },
    [notes, debouncedSave],
  );

  // Format commands via execCommand
  const handleFormat = useCallback((command) => {
    document.execCommand(command, false, null);
  }, []);

  const handleInsertLink = useCallback(() => {
    // Save selection before prompt steals focus
    const sel = window.getSelection();
    const range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
    const url = window.prompt("Enter URL:");
    if (url && range) {
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand("createLink", false, url);
    }
  }, []);

  // ── Render ───────────────────────────────────────────────────────────────

  const LayoutModeIcon = LAYOUT_MODE_ICONS[layoutMode];
  const isCompact = layoutMode === "compact";
  const isCollapsed = layoutMode === "collapsed";

  if (!initialStateLoaded) {
    return (
      <PageLayout title="Notes" variant="grid">
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flex: 1,
          }}
        >
          <ContextualLoader
            loading
            message="Loading notes..."
            showProgress={false}
            inline
          />
        </Box>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title="Notes"
      variant="grid"
      actions={
        <>
          {/* Cards / Notes toggle */}
          <Tooltip title="Dashboard Cards">
            <IconButton
              onClick={() => navigate("/")}
              size="small"
              sx={{ opacity: 0.5 }}
            >
              <DashboardIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Sticky Notes">
            <IconButton
              size="small"
              sx={{ color: "primary.main" }}
            >
              <StickyNote2 fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Add note */}
          <Tooltip title="Add Note">
            <IconButton onClick={handleAddNote} size="small" sx={{ ml: 1 }}>
              <Add fontSize="small" />
            </IconButton>
          </Tooltip>

          {/* Layout mode cycle */}
          <Tooltip
            title={`Layout: ${LAYOUT_MODE_LABELS[layoutMode]} (click to cycle)`}
          >
            <IconButton
              onClick={handleCycleLayoutMode}
              size="small"
              sx={{ opacity: 0.6 }}
            >
              <LayoutModeIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </>
      }
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
              },
            },
            "& .react-grid-item .MuiPaper-root": {
              zIndex: "inherit !important",
            },
          }}
        >
          <ReactGridLayout
            className="layout"
            layout={layout}
            style={{ transition: "all 0.2s ease-out" }}
            cols={COLS_COUNT}
            rowHeight={ROW_HEIGHT_PX}
            width={gridWidth}
            containerPadding={[CONTAINER_PADDING_PX, CONTAINER_PADDING_PX]}
            margin={[CARD_MARGIN_PX, CARD_MARGIN_PX]}
            isDraggable={true}
            isResizable={!isCompact && !isCollapsed}
            compactType={
              isCompact ? "horizontal" : isCollapsed ? "vertical" : null
            }
            preventCollision={false}
            useCSSTransforms={false}
            allowOverlap={!isCompact && !isCollapsed}
            draggableHandle=".note-header"
            draggableCancel="button, input, textarea, select, option, .non-draggable, .note-content"
            onLayoutChange={onLayoutChange}
            resizeHandles={
              !isCompact && !isCollapsed
                ? ["s", "w", "e", "n", "sw", "nw", "se", "ne"]
                : []
            }
          >
            {layout
              .filter((item) => notes[item.i])
              .map((layoutItem) => {
                const noteId = layoutItem.i;
                const note = notes[noteId];
                const isMinimized = minimizedCards[noteId] || false;
                const noteColor = noteColors[noteId] || PASTEL_COLORS[0];
                const textColor = getContrastColor(noteColor);

                return (
                  <div
                    key={noteId}
                    data-card-id={noteId}
                    style={{
                      zIndex: cardZIndex[noteId] || 0,
                      transition:
                        "transform 0.2s ease-out, box-shadow 0.2s ease-out",
                      position: "relative",
                    }}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (isCollapsed) {
                        setLayoutMode("normal");
                        saveState(
                          normalLayoutRef.current || layout,
                          noteColors,
                          minimizedCards,
                          "normal",
                        );
                      } else {
                        handleCardClick(noteId);
                      }
                    }}
                  >
                    {isCollapsed ? (
                      <Paper
                        elevation={1}
                        className="note-header"
                        sx={{
                          display: "flex",
                          alignItems: "center",
                          height: "100%",
                          px: 2,
                          cursor: "grab",
                          userSelect: "none",
                          borderRadius: 1,
                          backgroundColor: noteColor,
                          transition:
                            "background-color 0.15s ease, box-shadow 0.15s ease",
                          "&:hover": { boxShadow: theme.shadows[4] },
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
                            color: textColor,
                            pointerEvents: "none",
                          }}
                        >
                          {note.content
                            ? note.content
                                .replace(/<[^>]*>/g, "")
                                .substring(0, 40) || "Empty note"
                            : "Empty note"}
                        </Typography>
                      </Paper>
                    ) : (
                      <StickyNote
                        noteId={noteId}
                        content={note.content}
                        color={noteColor}
                        textColor={textColor}
                        isMinimized={isMinimized}
                        onToggleMinimize={() =>
                          handleToggleMinimize(noteId)
                        }
                        onColorChange={(color) =>
                          handleNoteColorChange(noteId, color)
                        }
                        onContentChange={(content) =>
                          handleNoteContentChange(noteId, content)
                        }
                        onDelete={() => handleDeleteNote(noteId)}
                        onFormat={handleFormat}
                        onInsertLink={handleInsertLink}
                        theme={theme}
                        noteRef={(el) => {
                          noteRefs.current[noteId] = el;
                        }}
                      />
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

export default StickyNotesPage;
