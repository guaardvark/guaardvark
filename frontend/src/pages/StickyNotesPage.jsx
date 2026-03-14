// frontend/src/pages/StickyNotesPage.jsx
// Sticky notes board — Google Keep-like experience
// Titles, right-click context menu, search, pin-to-top, auto-save with indicator
// Drag, resize, color change, minimize (double-click header), layout modes, z-index layering

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Box,
  Alert as MuiAlert,
  Paper,
  Typography,
  Tooltip,
  IconButton,
  useTheme,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  InputBase,
  TextField,
  Fade,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
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
  PushPin,
  PushPinOutlined,
  ContentCopy,
  Delete,
  Edit as EditIcon,
  Search as SearchIcon,
  CloudDone,
  CloudOff,
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

const NOTE_COLORS = [
  "rgba(0, 128, 128, 0.15)",   // teal glass (primary)
  "rgba(30, 30, 30, 0.95)",    // dark carbon
  "rgba(138, 155, 174, 0.2)",  // steel glass
  "rgba(0, 229, 255, 0.12)",   // neon cyan glass
  "rgba(206, 147, 216, 0.15)", // magenta glass (secondary)
  "rgba(255, 255, 255, 0.08)", // frosted glass
  "rgba(0, 102, 102, 0.3)",    // deep teal
  "rgba(40, 40, 40, 0.9)",     // charcoal
];

// YIQ contrast helper (same formula as DashboardPage / DashboardCardWrapper)
const getContrastColor = (bgColor) => {
  if (!bgColor) return "rgba(0, 0, 0, 0.87)";
  // Handle rgba() strings
  const rgbaMatch = bgColor.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (rgbaMatch) {
    const r = parseInt(rgbaMatch[1], 10);
    const g = parseInt(rgbaMatch[2], 10);
    const b = parseInt(rgbaMatch[3], 10);
    const yiq = (r * 299 + g * 587 + b * 114) / 1000;
    return yiq > 186 ? "rgba(0, 0, 0, 0.87)" : "rgba(255, 255, 255, 0.95)";
  }
  // Handle hex strings
  let hex = bgColor.replace("#", "");
  if (hex.length === 3)
    hex = hex.split("").map((h) => h + h).join("");
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
    title,
    content,
    color,
    textColor,
    isMinimized,
    isPinned,
    onToggleMinimize,
    onColorChange,
    onContentChange,
    onDeleteRequest,
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


    const dividerColor = "rgba(255,255,255,0.08)";
    const placeholderColor = "rgba(255,255,255,0.2)";

    return (
      <Paper
        elevation={3}
        className={`draggable-card ${isMinimized ? "minimized" : ""}`}
        sx={{
          display: "flex",
          flexDirection: "column",
          height: isMinimized ? "auto" : "100%",
          minHeight: isMinimized ? "50px" : "120px",
          overflow: "hidden",
          borderRadius: "8px",
          backgroundColor: color,
          backdropFilter: "blur(12px)",
          border: `1px solid rgba(255,255,255,0.06)`,
          color: textColor,
          transition: theme.transitions.create(["height", "min-height"], {
            duration: theme.transitions.duration.standard,
          }),
        }}
      >
        {/* ── Header — drag handle + title (always visible) ──────── */}
        <Box
          className="note-header"
          onMouseDown={handleMouseDown}
          sx={{
            display: "flex",
            alignItems: "center",
            px: 1,
            minHeight: "40px",
            cursor: "grab",
            userSelect: "none",
            "&:active": { cursor: "grabbing" },
            "&:hover": {
              backgroundColor: "rgba(255,255,255,0.04)",
              borderRadius: "8px 8px 0 0",
            },
          }}
        >
          {/* Pin indicator */}
          {isPinned && (
            <PushPin sx={{ fontSize: 14, color: textColor, opacity: 0.5, mr: 0.5 }} />
          )}

          {/* Title — display only, rename via right-click menu */}
          <Typography
            sx={{
              flex: 1,
              fontWeight: 600,
              fontSize: "0.77rem",
              color: textColor,
              overflow: "hidden",
              whiteSpace: "nowrap",
              textOverflow: "ellipsis",
              pointerEvents: "none",
              opacity: title ? 1 : 0.3,
              fontStyle: title ? "normal" : "italic",
            }}
          >
            {title || "Untitled"}
          </Typography>

          {/* Color picker dot — matches DashboardCardWrapper (8x8) */}
          <Box sx={{ position: "relative", ml: 0.5 }}>
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
              value={color.startsWith("rgba") ? "#1e1e1e" : color}
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

          {/* Close button — minimizes the note (delete via right-click menu) */}
          <Tooltip title="Close">
            <IconButton
              onClick={(e) => {
                e.stopPropagation();
                onToggleMinimize();
              }}
              className="non-draggable"
              size="small"
              sx={{
                width: 20,
                height: 20,
                p: 0,
                ml: 0.5,
                color: textColor,
                opacity: 0.4,
                "&:hover": { opacity: 1, backgroundColor: "rgba(255,0,0,0.1)" },
              }}
            >
              <Close sx={{ fontSize: 16 }} />
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
                px: 1,
                py: 0.25,
                borderTop: `1px solid ${dividerColor}`,
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
                      opacity: 0.5,
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
                    opacity: 0.5,
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
                  color: theme.palette.primary.light,
                  textDecoration: "underline",
                },
                "&:empty::before": {
                  content: '"Type your note..."',
                  color: placeholderColor,
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
  const [pinnedNotes, setPinnedNotes] = useState({});
  const [searchQuery, setSearchQuery] = useState("");
  const [contextMenu, setContextMenu] = useState(null);
  const [desktopMenu, setDesktopMenu] = useState(null);
  const [saveIndicator, setSaveIndicator] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [renameTarget, setRenameTarget] = useState(null); // { noteId, title }
  const gridContainerRef = useRef(null);
  const [gridWidth, setGridWidth] = useState(dashboardWidth);
  const isTogglingRef = useRef(false);
  const noteRefs = useRef({});
  const saveTimeoutRef = useRef(null);

  // Undo history (CTRL+Z)
  const undoStackRef = useRef([]);
  const MAX_UNDO = 30;

  // Refs for latest state values — avoids stale closures in saveState/debouncedSave
  const notesRef = useRef(notes);
  const noteColorsRef = useRef(noteColors);
  const minimizedCardsRef = useRef(minimizedCards);
  const pinnedNotesRef = useRef(pinnedNotes);
  const layoutModeRef = useRef(layoutMode);
  const layoutRef = useRef(null);
  // Sync refs immediately (not via useEffect which is async)
  notesRef.current = notes;
  noteColorsRef.current = noteColors;
  minimizedCardsRef.current = minimizedCards;
  pinnedNotesRef.current = pinnedNotes;
  layoutModeRef.current = layoutMode;

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

  // Default note size: square (width × width in grid units)
  const makeLayoutItem = useCallback(
    (noteId, index) => ({
      i: noteId,
      x: (index % 4) * cardGridW,
      y: Math.floor(index / 4) * cardGridW,
      w: cardGridW,
      h: cardGridW,
      minW: cardMinGridW,
      isDraggable: true,
      isResizable: true,
    }),
    [cardGridW, cardMinGridW],
  );

  const [layout, setLayout] = useState([]);
  useEffect(() => { layoutRef.current = layout; }, [layout]);
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
            const defaultNotes = { [id]: { content: "", title: "" } };
            const defaultColors = { [id]: NOTE_COLORS[0] };
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
            // Migrate: default missing title to ""
            const migrated = {};
            for (const [id, note] of Object.entries(saved.notes)) {
              migrated[id] = { title: "", ...note };
            }
            setNotes(migrated);
          }
          if (saved.noteColors && typeof saved.noteColors === "object") {
            setNoteColors(saved.noteColors);
          }
          if (saved.minimizedCards && typeof saved.minimizedCards === "object") {
            setMinimizedCards(saved.minimizedCards);
          }
          if (saved.pinnedNotes && typeof saved.pinnedNotes === "object") {
            setPinnedNotes(saved.pinnedNotes);
          }

          const noteIds = Object.keys(saved.notes || {});
          if (Array.isArray(saved.layout) && saved.layout.length > 0) {
            const validLayout = saved.layout.filter((item) =>
              noteIds.includes(item.i),
            );
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
        setNotes({ [id]: { content: "", title: "" } });
        setNoteColors({ [id]: NOTE_COLORS[0] });
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
    async (newLayout, newNoteColors, newMinimizedCards, newLayoutMode, newNotes, newPinnedNotes) => {
      setSaveIndicator("saving");
      try {
        const body = {
          notes: newNotes || notesRef.current,
          layout: normalLayoutRef.current || newLayout || layoutRef.current,
          noteColors: newNoteColors || noteColorsRef.current,
          minimizedCards: newMinimizedCards || minimizedCardsRef.current,
          pinnedNotes: newPinnedNotes || pinnedNotesRef.current,
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
        setSaveIndicator("saved");
        setTimeout(() => setSaveIndicator(null), 2000);
      } catch (err) {
        console.error("Failed to save sticky notes state:", err);
        setLayoutError("Failed to save notes.");
        setSaveIndicator("error");
        setTimeout(() => setSaveIndicator(null), 3000);
      }
    },
    [],
  );

  // Debounced save for content/title typing
  const debouncedSave = useCallback(
    (newNotes) => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
      saveTimeoutRef.current = setTimeout(() => {
        saveState(null, null, null, undefined, newNotes);
      }, 500);
    },
    [saveState],
  );

  // Push state snapshot for undo
  const pushUndo = useCallback(() => {
    const snapshot = JSON.stringify({
      notes: notesRef.current,
      noteColors: noteColorsRef.current,
      layout: layoutRef.current,
    });
    undoStackRef.current.push(snapshot);
    if (undoStackRef.current.length > MAX_UNDO) undoStackRef.current.shift();
  }, []);

  // Undo last action
  const handleUndo = useCallback(() => {
    if (undoStackRef.current.length === 0) return;
    const snapshot = JSON.parse(undoStackRef.current.pop());
    if (snapshot.notes) setNotes(snapshot.notes);
    if (snapshot.noteColors) setNoteColors(snapshot.noteColors);
    if (snapshot.layout) setLayout(snapshot.layout);
    saveState(snapshot.layout, snapshot.noteColors, null, undefined, snapshot.notes);
  }, [saveState]);

  // Global keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      // CTRL+Z — Undo
      if ((e.ctrlKey || e.metaKey) && e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        handleUndo();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleUndo]);

  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    };
  }, []);

  // ── Search filter ─────────────────────────────────────────────────────────

  const filteredNoteIds = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const q = searchQuery.toLowerCase();
    return new Set(
      Object.entries(notes)
        .filter(([, note]) => {
          const titleMatch = (note.title || "").toLowerCase().includes(q);
          const contentMatch = (note.content || "").replace(/<[^>]*>/g, "").toLowerCase().includes(q);
          return titleMatch || contentMatch;
        })
        .map(([id]) => id),
    );
  }, [notes, searchQuery]);

  // ── Event handlers ───────────────────────────────────────────────────────

  const onLayoutChange = useCallback(
    (newLayout) => {
      if (isTogglingRef.current) return;
      const validLayout = newLayout.filter((item) => item !== undefined);
      if (layoutMode === "normal") normalLayoutRef.current = validLayout;
      setLayout(validLayout);
      // Flush any pending debounced save to prevent content loss
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
      // Pass notes explicitly from ref to ensure latest content is saved
      saveState(validLayout, noteColorsRef.current, minimizedCardsRef.current, undefined, notesRef.current);
    },
    [saveState, layoutMode],
  );

  const handleNoteColorChange = useCallback(
    (noteId, color) => {
      pushUndo();
      const c = { ...noteColorsRef.current, [noteId]: color };
      setNoteColors(c);
      // Flush any pending debounced save to prevent content loss
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
        saveTimeoutRef.current = null;
      }
      // Pass notes explicitly from ref to ensure latest content is saved
      saveState(layoutRef.current, c, minimizedCardsRef.current, undefined, notesRef.current);
    },
    [saveState, pushUndo],
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
    [minimizedCards, layout, noteColors, saveState, cardMinGridH, originalDimensions, layoutMode],
  );

  const handleCardClick = useCallback(
    (noteId) => {
      const z = maxZIndex + 1;
      setMaxZIndex(z);
      setCardZIndex((prev) => ({ ...prev, [noteId]: z }));
      const el = document.querySelector(`[data-card-id="${noteId}"]`);
      const gridItem = el?.closest(".react-grid-item") || el;
      if (gridItem) gridItem.style.zIndex = z;
    },
    [maxZIndex],
  );

  const handleCycleLayoutMode = useCallback(() => {
    const idx = LAYOUT_MODES.indexOf(layoutMode);
    const next = LAYOUT_MODES[(idx + 1) % LAYOUT_MODES.length];
    if (layoutMode === "normal") normalLayoutRef.current = layout;
    setLayoutMode(next);
    saveState(normalLayoutRef.current || layout, noteColors, minimizedCards, next);
  }, [layoutMode, layout, noteColors, minimizedCards, saveState]);

  // Add note
  const handleAddNote = useCallback(() => {
    const id = `note-${Date.now()}`;
    const colorIdx = Object.keys(notes).length % NOTE_COLORS.length;
    const newNotes = { ...notes, [id]: { content: "", title: "" } };
    const newColors = { ...noteColors, [id]: NOTE_COLORS[colorIdx] };
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
      const { [noteId]: ____, ...restPinned } = pinnedNotes;
      const newLayout = (normalLayoutRef.current || layout).filter(
        (i) => i.i !== noteId,
      );
      normalLayoutRef.current = newLayout;
      setNotes(rest);
      setNoteColors(restColors);
      setMinimizedCards(restMin);
      setPinnedNotes(restPinned);
      setLayout(newLayout);
      saveState(newLayout, restColors, restMin, undefined, rest, restPinned);
    },
    [notes, noteColors, minimizedCards, pinnedNotes, layout, saveState],
  );

  // Content change (debounced save) — use ref to avoid stale closure
  const handleNoteContentChange = useCallback(
    (noteId, content) => {
      pushUndo();
      const current = notesRef.current;
      const newNotes = { ...current, [noteId]: { ...current[noteId], content } };
      setNotes(newNotes);
      debouncedSave(newNotes);
    },
    [debouncedSave, pushUndo],
  );

  // Title change (debounced save) — use ref to avoid stale closure
  const handleNoteTitleChange = useCallback(
    (noteId, title) => {
      pushUndo();
      const current = notesRef.current;
      const newNotes = { ...current, [noteId]: { ...current[noteId], title } };
      setNotes(newNotes);
      debouncedSave(newNotes);
    },
    [debouncedSave, pushUndo],
  );

  // Duplicate note
  const handleDuplicateNote = useCallback(
    (noteId) => {
      const source = notes[noteId];
      if (!source) return;
      const id = `note-${Date.now()}`;
      const newNotes = { ...notes, [id]: { content: source.content, title: source.title || "" } };
      const newColors = { ...noteColors, [id]: noteColors[noteId] || NOTE_COLORS[0] };
      const item = makeLayoutItem(id, Object.keys(notes).length);
      const newLayout = [...(normalLayoutRef.current || layout), item];
      normalLayoutRef.current = newLayout;
      setNotes(newNotes);
      setNoteColors(newColors);
      setLayout(newLayout);
      saveState(newLayout, newColors, minimizedCards, undefined, newNotes);
    },
    [notes, noteColors, layout, minimizedCards, makeLayoutItem, saveState],
  );

  // Toggle pin
  const handleTogglePin = useCallback(
    (noteId) => {
      const newPinned = { ...pinnedNotes };
      if (newPinned[noteId]) {
        delete newPinned[noteId];
      } else {
        newPinned[noteId] = true;
      }
      setPinnedNotes(newPinned);
      saveState(null, null, null, undefined, undefined, newPinned);
    },
    [pinnedNotes, saveState],
  );

  // Format commands via execCommand
  const handleFormat = useCallback((command) => {
    document.execCommand(command, false, null);
  }, []);

  const handleInsertLink = useCallback(() => {
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
          {/* Search bar */}
          <Box sx={{
            display: "flex",
            alignItems: "center",
            backgroundColor: theme.palette.action.hover,
            borderRadius: 1,
            px: 1,
            mr: 1,
            maxWidth: 200,
          }}>
            <SearchIcon sx={{ fontSize: 18, opacity: 0.5, mr: 0.5 }} />
            <InputBase
              placeholder="Search notes..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              sx={{ fontSize: "0.8rem", py: 0.25 }}
              size="small"
            />
          </Box>

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

          {/* Save indicator */}
          {saveIndicator && (
            <Fade in>
              <Box sx={{ display: "flex", alignItems: "center", ml: 1, opacity: 0.6 }}>
                {saveIndicator === "saving" && (
                  <Typography variant="caption" sx={{ fontSize: "0.65rem", color: "text.secondary" }}>Saving...</Typography>
                )}
                {saveIndicator === "saved" && (
                  <Tooltip title="All changes saved">
                    <CloudDone sx={{ fontSize: 16, color: "success.main" }} />
                  </Tooltip>
                )}
                {saveIndicator === "error" && (
                  <Tooltip title="Failed to save">
                    <CloudOff sx={{ fontSize: 16, color: "error.main" }} />
                  </Tooltip>
                )}
              </Box>
            </Fade>
          )}
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
        onContextMenu={(e) => {
          // Only show desktop menu if click is on the background (not a note)
          if (!e.target.closest('[data-card-id]')) {
            e.preventDefault();
            setDesktopMenu({ x: e.clientX, y: e.clientY });
          }
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
                outline: `2px solid ${theme.palette.primary.main}`,
                borderRadius: "4px",
                opacity: 0.9,
              },
            },
          }}
        >
          {(() => {
            // Filter layout to match visible children — prevents RGL from dropping hidden note positions
            const visibleLayout = layout.filter(
              (item) => notes[item.i] && (filteredNoteIds === null || filteredNoteIds.has(item.i)),
            );
            const isSearching = filteredNoteIds !== null;
            return (
            <ReactGridLayout
              className="layout"
              layout={visibleLayout}
              style={{ transition: "all 0.2s ease-out" }}
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
              draggableHandle=".note-header"
              draggableCancel="button, input, textarea, select, option, .non-draggable, .note-content"
              onDragStop={isSearching ? undefined : onLayoutChange}
              onResizeStop={isSearching ? undefined : onLayoutChange}
              resizeHandles={["s", "w", "e", "n", "sw", "nw", "se", "ne"]}
            >
            {visibleLayout
              .sort((a, b) => {
                const aPin = pinnedNotes[a.i] ? 1 : 0;
                const bPin = pinnedNotes[b.i] ? 1 : 0;
                return bPin - aPin;
              })
              .map((layoutItem) => {
                const noteId = layoutItem.i;
                const note = notes[noteId];
                const isMinimized = minimizedCards[noteId] || false;
                const noteColor = noteColors[noteId] || NOTE_COLORS[0];
                const textColor = getContrastColor(noteColor);

                return (
                  <div
                    key={noteId}
                    data-card-id={noteId}
                    style={{
                      transition:
                        "transform 0.2s ease-out, box-shadow 0.2s ease-out",
                      height: "100%",
                    }}
                    onMouseDown={() => handleCardClick(noteId)}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      setContextMenu({ noteId, x: e.clientX, y: e.clientY });
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
                          borderRadius: "8px",
                          backgroundColor: noteColor,
                          backdropFilter: "blur(12px)",
                          border: "1px solid rgba(255,255,255,0.06)",
                          transition:
                            "background-color 0.15s ease, box-shadow 0.15s ease",
                          "&:hover": { boxShadow: theme.shadows[4], backgroundColor: "rgba(255,255,255,0.04)" },
                          "&:active": { cursor: "grabbing" },
                        }}
                        onClick={() => {
                          setLayoutMode("normal");
                          saveState(normalLayoutRef.current || layout, noteColors, minimizedCards, "normal");
                        }}
                      >
                        {pinnedNotes[noteId] && (
                          <PushPin sx={{ fontSize: 12, color: textColor, opacity: 0.5, mr: 0.5 }} />
                        )}
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
                          {note.title || (note.content
                            ? note.content.replace(/<[^>]*>/g, "").substring(0, 40) || "Empty note"
                            : "Empty note")}
                        </Typography>
                      </Paper>
                    ) : (
                      <StickyNote
                        noteId={noteId}
                        title={note.title || ""}
                        content={note.content}
                        color={noteColor}
                        textColor={textColor}
                        isMinimized={isMinimized}
                        isPinned={!!pinnedNotes[noteId]}
                        onToggleMinimize={() =>
                          handleToggleMinimize(noteId)
                        }
                        onColorChange={(color) =>
                          handleNoteColorChange(noteId, color)
                        }
                        onContentChange={(content) =>
                          handleNoteContentChange(noteId, content)
                        }
                        onDeleteRequest={() => setDeleteConfirm(noteId)}
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
            );
          })()}
        </Box>
      </Box>

      {/* ── Right-click context menu ───────────────────────────────── */}
      <Menu
        open={Boolean(contextMenu)}
        onClose={() => setContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={contextMenu ? { top: contextMenu.y, left: contextMenu.x } : undefined}
        slotProps={{ paper: { sx: { minWidth: 180, borderRadius: "6px" } } }}
      >
        {/* Color swatches */}
        <MenuItem disableRipple disableGutters sx={{ px: 1.5, py: 0.6 }}>
          <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}>
            {NOTE_COLORS.map((c) => (
              <Box
                key={c}
                onClick={() => {
                  if (contextMenu) handleNoteColorChange(contextMenu.noteId, c);
                  setContextMenu(null);
                }}
                sx={{
                  width: 20,
                  height: 20,
                  borderRadius: "50%",
                  backgroundColor: c,
                  cursor: "pointer",
                  border: noteColors[contextMenu?.noteId] === c
                    ? `2px solid ${theme.palette.primary.main}`
                    : "1px solid rgba(255,255,255,0.15)",
                  "&:hover": { transform: "scale(1.2)" },
                  transition: "transform 0.1s ease",
                }}
              />
            ))}
          </Box>
        </MenuItem>
        <Divider />
        <MenuItem onClick={() => {
          if (contextMenu) handleDuplicateNote(contextMenu.noteId);
          setContextMenu(null);
        }}>
          <ListItemIcon><ContentCopy fontSize="small" /></ListItemIcon>
          <ListItemText>Duplicate</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => {
          if (contextMenu) {
            const note = notes[contextMenu.noteId];
            setRenameTarget({ noteId: contextMenu.noteId, title: note?.title || "" });
          }
          setContextMenu(null);
        }}>
          <ListItemIcon><EditIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => {
          if (contextMenu) handleTogglePin(contextMenu.noteId);
          setContextMenu(null);
        }}>
          <ListItemIcon>
            {pinnedNotes[contextMenu?.noteId]
              ? <PushPin fontSize="small" />
              : <PushPinOutlined fontSize="small" />}
          </ListItemIcon>
          <ListItemText>{pinnedNotes[contextMenu?.noteId] ? "Unpin" : "Pin to Top"}</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem
          onClick={() => {
            if (contextMenu) setDeleteConfirm(contextMenu.noteId);
            setContextMenu(null);
          }}
          sx={{ color: "error.main" }}
        >
          <ListItemIcon><Delete fontSize="small" color="error" /></ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* ── Delete confirmation dialog ─────────────────────────────── */}
      <Dialog open={Boolean(deleteConfirm)} onClose={() => setDeleteConfirm(null)} maxWidth="xs">
        <DialogTitle sx={{ fontSize: "0.9rem" }}>
          Delete this note? This cannot be undone.
        </DialogTitle>
        <DialogActions>
          <Button onClick={() => setDeleteConfirm(null)} size="small">Cancel</Button>
          <Button
            onClick={() => {
              handleDeleteNote(deleteConfirm);
              setDeleteConfirm(null);
            }}
            color="error"
            variant="contained"
            size="small"
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Desktop (background) right-click menu ──────────────────── */}
      <Menu
        open={Boolean(desktopMenu)}
        onClose={() => setDesktopMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={desktopMenu ? { top: desktopMenu.y, left: desktopMenu.x } : undefined}
        slotProps={{ paper: { sx: { minWidth: 160, borderRadius: "6px" } } }}
      >
        <MenuItem onClick={() => { handleAddNote(); setDesktopMenu(null); }}>
          <ListItemIcon><Add fontSize="small" /></ListItemIcon>
          <ListItemText>New Note</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={() => { handleCycleLayoutMode(); setDesktopMenu(null); }}>
          <ListItemIcon><LayoutModeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Cycle Layout</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => { navigate("/"); setDesktopMenu(null); }}>
          <ListItemIcon><DashboardIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Go to Dashboard</ListItemText>
        </MenuItem>
      </Menu>

      {/* ── Rename dialog ──────────────────────────────────────────── */}
      <Dialog
        open={Boolean(renameTarget)}
        onClose={() => setRenameTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle sx={{ fontSize: "0.9rem" }}>Rename Note</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            value={renameTarget?.title || ""}
            onChange={(e) => setRenameTarget(prev => prev ? { ...prev, title: e.target.value } : null)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (renameTarget) {
                  handleNoteTitleChange(renameTarget.noteId, renameTarget.title);
                  setRenameTarget(null);
                }
              }
            }}
            placeholder="Note title"
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameTarget(null)} size="small">Cancel</Button>
          <Button
            onClick={() => {
              if (renameTarget) {
                handleNoteTitleChange(renameTarget.noteId, renameTarget.title);
                setRenameTarget(null);
              }
            }}
            variant="contained"
            size="small"
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
};

export default StickyNotesPage;
