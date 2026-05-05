// frontend/src/pages/DocumentsPage.jsx
// Version 9.0: Folders as window states (folded/minimized/maximized)
// - All folders are windows in different states
// - Folded: Folder icon card on desktop
// - Minimized: Title bar only
// - Maximized: Full window showing contents
// - No react-grid-layout for folder icons (simple positioning)

import React, { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { Box, Typography, Card, CardActionArea, CardContent, IconButton, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, useTheme, CircularProgress } from "@mui/material";
import { GuaardvarkLogo } from "../components/branding";
import { Apps as AppsIcon, GridView as GridViewIcon, FolderOutlined, Code, UploadFile as UploadFileIcon } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import { getFileIcon, getItemKey, FolderIndexIndicator, isImageFile, isCodeFile, isPdfFile, isAudioFile } from "../components/documents/fileUtils.jsx";
import ImageLightbox from "../components/images/ImageLightbox";
import CodeViewerModal from "../components/documents/CodeViewerModal";
import PdfViewerModal from "../components/documents/PdfViewerModal";
import AudioPlayerModal from "../components/documents/AudioPlayerModal";
import ReactGridLayoutLib, { WidthProvider } from 'react-grid-layout';
import FolderWindow from "../components/documents/FolderWindow";
import DocumentsContextMenu from "../components/documents/DocumentsContextMenu";
import FilePropertiesModal from "../components/modals/FilePropertiesModal";
import FolderPropertiesModal from "../components/modals/FolderPropertiesModal";
import { useLayout } from "../contexts/LayoutContext";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { triggerIndexing, indexBulk } from "../api/indexingService";
import axios from 'axios';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

const WindowsGridLayout = WidthProvider(ReactGridLayoutLib);
const WINDOWS_STATE_ENDPOINT = '/api/state/documents-windows-v2';
const API_BASE = '/api/files'; // Use relative path so Vite proxy handles CORS

// Grid configuration for maximized/minimized windows
// With ~860px container, 48 cols, margin [2,2]: colWidth≈16px, colPitch≈18px
const WINDOWS_COLS = 48;
const WINDOWS_ROW_HEIGHT = 10;
const WINDOWS_MARGIN = [2, 2];
const WINDOWS_PADDING = [4, 4];
const WINDOW_MIN_WIDTH = 15;  // ~270px
const WINDOW_MIN_HEIGHT = 20; // ~240px
const MINIMIZED_MAX_WIDTH_PX = 350; // Max pixel width for minimized windows

// Snap-to-grid constants for desktop icons
const ICON_GRID_W = 120;
const ICON_GRID_H = 100;
const ICON_GRID_PAD = 20;

// Shared frozen Set so windows with no selection all get the same reference —
// avoids spurious re-renders and `.has` on undefined.
const EMPTY_SELECTION = new Set();

const DocumentsPage = () => {
  const { gridSettings } = useLayout();
  const { RGL_WIDTH_PROP_PX, _CONTAINER_PADDING_PX } = gridSettings;
  const { showMessage } = useSnackbar();
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const theme = useTheme();

  // All folders are represented as windows with different states
  const [windows, setWindows] = useState([]); // Array of { id, folderId, folder, state: 'folded'|'minimized'|'maximized' }
  const [windowLayout, setWindowLayout] = useState([]); // Layout for maximized/minimized windows
  const [windowColors, setWindowColors] = useState({});
  const [windowZIndex, setWindowZIndex] = useState({});
  const [maxZIndex, setMaxZIndex] = useState(0);
  // Per-surface selection: `desktop` is the root, each window.id is its own slot.
  // Keeping selections isolated per window means clicking in window B doesn't wipe window A.
  const [selectedItemsByContext, setSelectedItemsByContext] = useState({ desktop: new Set() });
  const [activeContext, setActiveContext] = useState({ type: 'desktop', path: '/' });
  const [loading, setLoading] = useState(true);
  const [rootFiles, setRootFiles] = useState([]); // Files at root level (desktop)
  const [iconPositions, setIconPositions] = useState({}); // Icon positions: { 'folder-123': { x: 100, y: 200 }, 'file-456': { x: 300, y: 50 } }
  const iconPositionsRef = useRef({}); // Always-current ref to avoid stale closures
  const stateLoadedRef = useRef(false); // Prevent saving before initial load
  const [folderRefreshKeys, setFolderRefreshKeys] = useState({}); // Refresh keys for folder windows: { folderId: refreshKey }
  const windowIdCounter = useRef(0);
  const isMountedRef = useRef(true);
  const windowsRef = useRef([]);
  const documentsChannelRef = useRef(null);
  const clientIdRef = useRef(`documents-page-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  const desktopContentRef = useRef(null);
  const windowContainerRef = useRef(null);
  const desktopItemRefs = useRef(new Map());
  const [desktopSelectionBox, setDesktopSelectionBox] = useState(null);
  const [isDesktopSelecting, setIsDesktopSelecting] = useState(false);

  // Keep a ref of windows so event listeners don't need re-registration
  useEffect(() => {
    windowsRef.current = windows;
  }, [windows]);

  // Which selection slot "owns" the current keyboard/context-menu actions.
  // Desktop surface → 'desktop'; a focused folder window → that window's id.
  const activeContextKey = useMemo(() => {
    if (activeContext.type === 'desktop') return 'desktop';
    const win = windows.find((w) => w.folderId === activeContext.folderId);
    return win ? win.id : 'desktop';
  }, [activeContext, windows]);

  // The selection Set the user is currently acting on — handlers below read this
  // so Ctrl+C / Delete / right-click target only the focused surface's selection.
  const activeSelection = selectedItemsByContext[activeContextKey] || EMPTY_SELECTION;
  const desktopSelection = selectedItemsByContext.desktop || EMPTY_SELECTION;

  // Refs so event-driven callbacks (onMouseDown → setActiveContext → oncontextmenu)
  // can read the very latest focus/selection without stale closures or huge dep arrays.
  const activeContextKeyRef = useRef('desktop');
  const selectionByContextRef = useRef({ desktop: new Set() });
  useEffect(() => { activeContextKeyRef.current = activeContextKey; }, [activeContextKey]);
  useEffect(() => { selectionByContextRef.current = selectedItemsByContext; }, [selectedItemsByContext]);

  // Keep iconPositions ref in sync with state (avoids stale closures in saveWindowState)
  useEffect(() => {
    iconPositionsRef.current = iconPositions;
  }, [iconPositions]);

  // Track mounted state - set to true on mount, false on unmount
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  // Compute how many grid units fit in the visible container (WidthProvider uses DOM width)
  const getVisibleGridBounds = useCallback(() => {
    const el = windowContainerRef.current;
    if (!el) return { maxW: WINDOW_MIN_WIDTH * 2, maxH: WINDOW_MIN_HEIGHT * 2 };
    const rect = el.getBoundingClientRect();
    const [_mx, my] = WINDOWS_MARGIN;
    const [_px, py] = WINDOWS_PADDING;
    // WidthProvider uses actual DOM width, so col count = WINDOWS_COLS in available space
    const rowPitch = WINDOWS_ROW_HEIGHT + my;
    const visibleRows = Math.floor((rect.height - py * 2 + my) / rowPitch);
    return {
      maxW: WINDOWS_COLS, // all columns are visible (WidthProvider sizes to container)
      maxH: Math.max(WINDOW_MIN_HEIGHT, visibleRows),
    };
  }, []);

  // Context menu & operations
  const [contextMenu, setContextMenu] = useState(null);
  const [contextMenuType, setContextMenuType] = useState('desktop'); // 'desktop', 'folder', 'file'
  const [contextMenuItem, setContextMenuItem] = useState(null); // The item that was right-clicked
  const [clipboard, setClipboard] = useState(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameItem, setRenameItem] = useState(null);
  const [renameName, setRenameName] = useState('');
  const [propertiesOpen, setPropertiesOpen] = useState(false);
  const [selectedForProperties, setSelectedForProperties] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null); // { current: N, total: M } or null
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [lightbox, setLightbox] = useState(null); // { url, name, documentId, editMode }
  const [codeViewer, setCodeViewer] = useState(null); // { file } for CodeViewerModal
  const [pdfViewer, setPdfViewer] = useState(null); // { file } for PdfViewerModal
  const [audioPlayer, setAudioPlayer] = useState(null); // { file } for AudioPlayerModal
  const navigate = useNavigate();
  const [dragOverFolderId, setDragOverFolderId] = useState(null); // Folder ID being dragged over
  const fileInputRef = useRef(null);
  const dragDropHandledRef = useRef(false); // Track if drop was handled to prevent repositioning
  const newFolderInputRef = useRef(null);
  const renameInputRef = useRef(null);

  // Load folders and files, initialize folders as folded windows
  useEffect(() => {
    const loadFoldersAndFiles = async () => {
      try {
        setLoading(true);
        const response = await axios.get(`${API_BASE}/browse?path=/&fields=light`);
        const data = response.data.data;
        const folders = data.folders || [];
        const files = (data.documents || []).map(f => ({
          ...f,
          filename: f.filename || f.name,
          itemType: 'file'
        }));

        // Set root-level files
        setRootFiles(files);

        // Load saved window states from backend
        let savedState = null;
        try {
          const stateResponse = await fetch(WINDOWS_STATE_ENDPOINT);
          if (stateResponse.ok) {
            savedState = await stateResponse.json();
          }
        } catch {
          // No saved state yet — first run, defaults are fine
        }

        // Create a window for each folder
        const newWindows = folders.map((folder, _index) => {
          const windowId = `window-${windowIdCounter.current++}`;
          const savedWindow = savedState?.windows?.find(w => w.folderId === folder.id);

          return {
            id: windowId,
            folderId: folder.id,
            folder: folder,
            state: savedWindow?.state || 'folded', // Default to folded (folder icon)
          };
        });

        setWindows(newWindows);

        // Restore window layout, colors, z-index, icon positions from saved state
        if (savedState) {
          // Sanitize window layout — ensure active windows have valid layout items
          let sanitizedLayout = savedState.windowLayout ? [...savedState.windowLayout] : [];

          // Build set of active (non-folded) window IDs
          const activeIds = new Set(
            newWindows.filter(w => w.state !== 'folded').map(w => w.id)
          );

          // Remove stale layout items for windows that no longer exist
          sanitizedLayout = sanitizedLayout.filter(l => activeIds.has(l.i));

          // Ensure every active window has a layout item with valid dimensions
          newWindows.forEach(w => {
            if (w.state === 'folded') return;
            const isMin = w.state === 'minimized';
            const existing = sanitizedLayout.find(l => l.i === w.id);
            if (!existing || existing.w < WINDOW_MIN_WIDTH || existing.h < (isMin ? 5 : WINDOW_MIN_HEIGHT)) {
              sanitizedLayout = sanitizedLayout.filter(l => l.i !== w.id);
              sanitizedLayout.push({
                i: w.id,
                x: 0, y: 0,
                w: WINDOW_MIN_WIDTH * 2,
                h: isMin ? 5 : WINDOW_MIN_HEIGHT * 2,
                minW: WINDOW_MIN_WIDTH,
                minH: isMin ? 5 : WINDOW_MIN_HEIGHT,
              });
            }
          });

          // Clamp all positions to current grid bounds
          sanitizedLayout = sanitizedLayout.map(l => ({
            ...l,
            w: Math.max(WINDOW_MIN_WIDTH, Math.min(l.w, WINDOWS_COLS)),
            x: Math.max(0, Math.min(l.x, WINDOWS_COLS - Math.max(WINDOW_MIN_WIDTH, Math.min(l.w, WINDOWS_COLS)))),
            minW: WINDOW_MIN_WIDTH,
          }));

          setWindowLayout(sanitizedLayout);
          if (savedState.windowColors) setWindowColors(savedState.windowColors);
          if (savedState.windowZIndex) setWindowZIndex(savedState.windowZIndex);
          if (savedState.maxZIndex) setMaxZIndex(savedState.maxZIndex);
          if (savedState.iconPositions) {
            setIconPositions(savedState.iconPositions);
            iconPositionsRef.current = savedState.iconPositions;
          }
        }

        // Mark state as loaded — saveWindowState can now safely persist
        stateLoadedRef.current = true;
        setLoading(false);
      } catch (err) {
        stateLoadedRef.current = true;
        setLoading(false);
      }
    };

    loadFoldersAndFiles();
  }, []);

  // Broadcast helper so other tabs/components can trigger a refresh
  const announceDocumentsChange = useCallback(() => {
    const payload = {
      type: 'documents-refresh',
      source: clientIdRef.current,
      ts: Date.now(),
    };
    // Local custom event (fallback if BroadcastChannel unavailable)
    window.dispatchEvent(new CustomEvent('documents-updated', { detail: payload }));
    // Cross-tab broadcast when supported
    if (documentsChannelRef.current) {
      try {
        documentsChannelRef.current.postMessage(payload);
      } catch (err) {
        // Ignore broadcast failures; refresh is still local
      }
    }
  }, []);

  // Refresh data without page reload
  const refreshData = useCallback(async () => {
    try {
      const response = await axios.get(`${API_BASE}/browse?path=/&fields=light`);

      // Check if component is still mounted before updating state
      if (!isMountedRef.current) return;

      const data = response.data.data;
      const folders = data.folders || [];
      const files = (data.documents || []).map(f => ({
        ...f,
        filename: f.filename || f.name,
        itemType: 'file'
      }));

      setRootFiles(files);

      // Update windows for new/removed folders
      setWindows(prev => {
        const existingIds = new Set(prev.map(w => w.folderId));
        const newFolders = folders.filter(f => !existingIds.has(f.id));
        const removedIds = new Set(
          prev.filter(w => !folders.find(f => f.id === w.folderId)).map(w => w.folderId)
        );

        // Update existing windows with fresh folder data
        const updatedWindows = prev.map(w => {
          const folder = folders.find(f => f.id === w.folderId);
          if (folder) {
            return { ...w, folder };
          }
          return w;
        }).filter(w => !removedIds.has(w.folderId));

        // Add new folders as folded windows
        const newWindows = newFolders.map(folder => ({
          id: `window-${windowIdCounter.current++}`,
          folderId: folder.id,
          folder,
          state: 'folded',
        }));

        return [...updatedWindows, ...newWindows];
      });
    } catch (err) {
      if (isMountedRef.current) {
        showMessage?.('Failed to refresh', 'error');
      }
    }
  }, [showMessage]);

  // Listen for file/folder changes from any tab/component and refresh in place
  useEffect(() => {
    const handleExternalUpdate = (event) => {
      const _payload = event?.detail || event?.data;
      refreshData();
      // Force any open folder windows to re-fetch their contents
      setFolderRefreshKeys(prev => {
        const updated = { ...prev };
        windowsRef.current.forEach(w => {
          updated[w.folderId] = (updated[w.folderId] || 0) + 1;
        });
        return updated;
      });
    };

    window.addEventListener('documents-updated', handleExternalUpdate);

    let channel = null;
    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel('documents-updates');
      documentsChannelRef.current = channel;
      channel.onmessage = handleExternalUpdate;
    }

    return () => {
      window.removeEventListener('documents-updated', handleExternalUpdate);
      if (channel) {
        channel.close();
      }
      documentsChannelRef.current = null;
    };
  }, [refreshData]);

  // Axios interceptor: any successful mutation to /api/files triggers a refresh event
  // This is for cross-tab sync - each handler also calls refreshData directly
  useEffect(() => {
    // Helper to extract pathname from full or relative URLs
    const getPathname = (urlString) => {
      if (!urlString) return '';
      try {
        // If it's an absolute URL, extract pathname
        if (/^https?:\/\//.test(urlString)) {
          return new URL(urlString).pathname;
        }
      } catch (e) {
        // URL parsing failed, return as-is
      }
      return urlString;
    };

    const interceptorId = axios.interceptors.response.use(
      (response) => {
        const { method, url } = response.config || {};
        const normalizedMethod = method?.toLowerCase();
        const pathname = getPathname(url);
        const isFilesMutation = pathname.startsWith(API_BASE) && ['post', 'put', 'delete'].includes(normalizedMethod);
        if (isFilesMutation) {
          announceDocumentsChange();
        }
        return response;
      },
      (error) => Promise.reject(error)
    );

    return () => {
      axios.interceptors.response.eject(interceptorId);
    };
  }, [announceDocumentsChange]);

  // Save window state to backend
  const saveWindowState = useCallback(async (windowsData, layout, colors, zIndex, maxZ, currentIconPositions) => {
    // Don't save until initial state has been loaded from backend
    if (!stateLoadedRef.current) return;
    try {
      // Use explicit param if given, otherwise use ref (always current, avoids stale closures)
      const positionsToSave = currentIconPositions || iconPositionsRef.current;
      await fetch(WINDOWS_STATE_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          windows: windowsData,
          windowLayout: layout,
          windowColors: colors,
          windowZIndex: zIndex,
          maxZIndex: maxZ,
          iconPositions: positionsToSave,
          lastSaved: new Date().toISOString(),
        }),
      });
      // Persistence is fire-and-forget — response intentionally not checked
    } catch {
      // Layout save is best-effort; if it fails the user just loses layout on refresh
    }
  }, []);

  // Handle folder double-click - expand from folded to maximized
  const handleFolderExpand = useCallback((windowId) => {
    // Use functional form to avoid stale closure on windows
    setWindows(prev => {
      const window = prev.find(w => w.id === windowId);
      if (!window) return prev;
      return prev.map(w => w.id === windowId ? { ...w, state: 'maximized' } : w);
    });

    // Read fresh windows for layout calculation
    const newWindows = windowsRef.current.map(w =>
      w.id === windowId ? { ...w, state: 'maximized' } : w
    );

    // CRITICAL: Ensure all active windows have layout items to prevent snapping
    // Build a complete layout with all maximized/minimized windows
    let newLayout = [...windowLayout];

    // First, ensure all currently active windows have layout items
    const activeWindowIds = newWindows
      .filter(w => w.state === 'maximized' || w.state === 'minimized')
      .map(w => w.id);

    // Clamp windows to visible viewport
    const bounds = getVisibleGridBounds();

    activeWindowIds.forEach(wId => {
      const existingLayout = newLayout.find(l => l.i === wId);
      if (!existingLayout) {
        // Cascade new windows, clamped to visible area
        const existingActiveCount = newLayout.length;
        const CASCADE_STEP = 4;
        const windowW = Math.min(WINDOW_MIN_WIDTH * 2, bounds.maxW);
        const windowH = Math.min(WINDOW_MIN_HEIGHT * 2, bounds.maxH - 2);
        const centerX = Math.max(0, Math.floor((bounds.maxW - windowW) / 3));
        const centerY = 1;
        const offsetX = Math.min(centerX + (existingActiveCount * CASCADE_STEP) % Math.max(1, bounds.maxW - windowW), bounds.maxW - windowW);
        const offsetY = Math.min(centerY + (existingActiveCount * CASCADE_STEP), Math.max(0, bounds.maxH - windowH));

        newLayout.push({
          i: wId,
          x: offsetX,
          y: offsetY,
          w: windowW,
          h: newWindows.find(w => w.id === wId)?.state === 'minimized' ? 5 : windowH,
          minW: WINDOW_MIN_WIDTH,
          minH: newWindows.find(w => w.id === wId)?.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT,
        });
      }
    });

    // Remove layout items for folded windows (cleanup)
    newLayout = newLayout.filter(l => activeWindowIds.includes(l.i));

    setWindowLayout(newLayout);

    // Bring to front
    const newMaxZIndex = maxZIndex + 1;
    const newZIndex = { ...windowZIndex, [windowId]: newMaxZIndex };
    setMaxZIndex(newMaxZIndex);
    setWindowZIndex(newZIndex);

    saveWindowState(newWindows, newLayout, windowColors, newZIndex, newMaxZIndex);
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds]);

  // Handle window close - fold back to folder icon
  const handleWindowClose = useCallback((windowId) => {
    setWindows(prev => {
      const newWindows = prev.map(w =>
        w.id === windowId ? { ...w, state: 'folded' } : w
      );
      // Save using fresh data (inside setter to avoid stale closure)
      saveWindowState(newWindows, windowLayout, windowColors, windowZIndex, maxZIndex);
      return newWindows;
    });
    // Drop the closed window's selection slot so reopening starts clean
    // and long-lived sessions don't accumulate stale Sets.
    setSelectedItemsByContext(prev => {
      if (!(windowId in prev)) return prev;
      const { [windowId]: _removed, ...rest } = prev;
      return rest;
    });
  }, [windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Track saved sizes for windows before minimize (so maximize restores the right size)
  const preMinimizeSizes = useRef({});

  // Handle window minimize
  const handleToggleMinimize = useCallback((windowId) => {
    const window = windows.find(w => w.id === windowId);
    if (!window) return;

    const newState = window.state === 'minimized' ? 'maximized' : 'minimized';
    const newWindows = windows.map(w => {
      if (w.id === windowId) {
        return { ...w, state: newState };
      }
      return w;
    });
    setWindows(newWindows);

    // Calculate max grid columns for minimized width (350px cap)
    const colWidth = RGL_WIDTH_PROP_PX / WINDOWS_COLS;
    const maxMinimizedW = Math.max(WINDOW_MIN_WIDTH, Math.ceil(MINIMIZED_MAX_WIDTH_PX / colWidth));

    // Adjust ONLY the target window layout — leave all other items untouched
    const newWindowLayout = windowLayout.map(item => {
      if (item.i !== windowId) return item;
      const { x, y, w, h } = item;

      if (newState === 'minimized') {
        // Save current size before minimizing
        preMinimizeSizes.current[windowId] = { w, h };
        const cappedW = Math.min(w, maxMinimizedW);
        return { ...item, x, y, w: cappedW, h: 5, minH: 5, static: false };
      } else {
        // Restore saved size, or use default — clamped to visible area
        const bounds = getVisibleGridBounds();
        const savedH = Math.min(preMinimizeSizes.current[windowId]?.h || WINDOW_MIN_HEIGHT * 2, bounds.maxH - 2);
        const savedW = preMinimizeSizes.current[windowId]?.w || WINDOW_MIN_WIDTH * 2;
        return { ...item, x, y, w: savedW, h: savedH, minH: WINDOW_MIN_HEIGHT, static: false };
      }
    });
    setWindowLayout(newWindowLayout);

    // Bring to front on maximize
    if (newState === 'maximized') {
      const newMaxZ = maxZIndex + 1;
      setMaxZIndex(newMaxZ);
      setWindowZIndex(prev => ({ ...prev, [windowId]: newMaxZ }));
      saveWindowState(newWindows, newWindowLayout, windowColors, { ...windowZIndex, [windowId]: newMaxZ }, newMaxZ);
    } else {
      saveWindowState(newWindows, newWindowLayout, windowColors, windowZIndex, maxZIndex);
    }
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds, RGL_WIDTH_PROP_PX]);

  // Handle window color change
  const handleWindowColorChange = useCallback((windowId, color) => {
    const newWindowColors = { ...windowColors, [windowId]: color };
    setWindowColors(newWindowColors);
    saveWindowState(windows, windowLayout, newWindowColors, windowZIndex, maxZIndex);
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Build folder-ID-to-color map for propagating colors into nested FolderContents
  const folderIdToColor = useMemo(() => {
    const map = {};
    windows.forEach(w => {
      if (windowColors[w.id]) {
        map[w.folderId] = windowColors[w.id];
      }
    });
    return map;
  }, [windows, windowColors]);

  // Handle window layout change (drag/resize)
  // Guard: RGL may report layouts missing items or with extra items during transitions.
  // Merge incoming positions into our canonical layout to prevent data loss.
  const handleWindowLayoutChange = useCallback((newLayout) => {
    // Enforce minimum dimensions on every layout item RGL reports
    const clamped = newLayout.map(item => ({
      ...item,
      w: Math.max(item.w, WINDOW_MIN_WIDTH),
      x: Math.max(0, Math.min(item.x, WINDOWS_COLS - Math.max(item.w, WINDOW_MIN_WIDTH))),
      minW: WINDOW_MIN_WIDTH,
    }));

    setWindowLayout(prev => {
      const incoming = {};
      for (const item of clamped) {
        incoming[item.i] = item;
      }
      const merged = prev.map(existing => {
        const updated = incoming[existing.i];
        if (updated) {
          return { ...existing, x: updated.x, y: updated.y, w: updated.w, h: updated.h, minW: updated.minW };
        }
        return existing;
      });
      for (const item of clamped) {
        if (!prev.find(p => p.i === item.i)) {
          merged.push(item);
        }
      }
      return merged;
    });
    saveWindowState(windowsRef.current, clamped, windowColors, windowZIndex, maxZIndex);
  }, [windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Bring window to front on click
  const handleWindowClick = useCallback((windowId) => {
    const newMaxZIndex = maxZIndex + 1;
    setMaxZIndex(newMaxZIndex);
    setWindowZIndex(prev => ({ ...prev, [windowId]: newMaxZIndex }));
  }, [maxZIndex]);

  // Scoped selection update — caller passes the context key ('desktop' or window.id)
  // along with the new Set. Isolates per-surface selections from each other.
  const handleSelectionChange = useCallback((contextKey, newSelection) => {
    setSelectedItemsByContext((prev) => ({ ...prev, [contextKey]: newSelection }));
  }, []);

  // Track active context for keyboard shortcuts and paste targets
  const setDesktopContext = useCallback(() => {
    setActiveContext({ type: 'desktop', path: '/' });
  }, []);

  // Handle drag start (for items in folder windows)
  const handleDragStart = useCallback((_e, _item, _type) => {
    // Drag start is handled by FolderContents, just log for debugging
  }, []);

  // Recursively traverse a FileSystemEntry (file or directory) and collect files with relative paths
  const traverseFolderEntry = useCallback(async (entry, basePath = '') => {
    const files = [];

    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => {
        entry.file(resolve, reject);
      });
      files.push({
        file,
        relativePath: basePath ? `${basePath}/${file.name}` : file.name
      });
    } else if (entry.isDirectory) {
      const dirReader = entry.createReader();

      const readEntries = async () => {
        const entries = await new Promise((resolve, reject) => {
          dirReader.readEntries(resolve, reject);
        });

        if (entries.length > 0) {
          for (const childEntry of entries) {
            const childPath = basePath ? `${basePath}/${entry.name}` : entry.name;
            const childFiles = await traverseFolderEntry(childEntry, childPath);
            files.push(...childFiles);
          }

          // Continue reading batches (browser API may batch at ~100 entries)
          await readEntries();
        }
      };

      await readEntries();
    }

    return files;
  }, []);

  // Ensure a nested folder path exists, creating parent folders as needed
  const ensureFolderPath = useCallback(async (relativePath, baseFolder = '/') => {
    if (!relativePath || relativePath === '/') return baseFolder;

    const parts = relativePath.split('/').filter(Boolean);
    let currentFolder = baseFolder;

    for (const part of parts) {
      const checkResponse = await axios.get(`${API_BASE}/browse`, {
        params: { path: currentFolder },
      });

      const existingFolder = checkResponse.data.data.folders?.find(f => f.name === part);

      if (existingFolder) {
        currentFolder = existingFolder.path;
      } else {
        const createResponse = await axios.post(`${API_BASE}/folder`, {
          name: part,
          parent_path: currentFolder,
        });
        currentFolder = createResponse.data.data.path;
      }
    }

    return currentFolder;
  }, []);

  // Handle drop in folder window or on desktop
  const handleDrop = useCallback(async (e, targetFolder = null) => {
    e.preventDefault();
    e.stopPropagation();

    try {
      // Check if this is a file drop from outside the browser
      const hasFiles = e.dataTransfer.types?.includes('Files') && e.dataTransfer.items?.length > 0;
      if (hasFiles) {
        // Mark that drop was handled (file upload)
        dragDropHandledRef.current = true;

        // Determine upload destination
        let uploadPath = '/'; // Default to desktop root

        if (targetFolder) {
          uploadPath = targetFolder.path;
        } else {
          const dropTarget = e.currentTarget || e.target;
          const folderElement = dropTarget.closest('[data-folder-path]');
          if (folderElement) {
            uploadPath = folderElement.getAttribute('data-folder-path');
          } else {
            const folderCard = dropTarget.closest('[data-folder-id]');
            if (folderCard) {
              const folderId = parseInt(folderCard.getAttribute('data-folder-id'));
              const win = windows.find(w => w.folderId === folderId);
              if (win) {
                uploadPath = win.folder.path;
              }
            }
          }
        }

        // Collect files with relative paths (supports recursive folder traversal)
        const filesToUpload = [];
        const items = e.dataTransfer.items;

        for (let i = 0; i < items.length; i++) {
          const item = items[i];
          if (item.kind === 'file') {
            const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
            if (entry) {
              const filesWithPaths = await traverseFolderEntry(entry);
              filesToUpload.push(...filesWithPaths);
            } else {
              // Fallback for browsers without webkitGetAsEntry
              const file = item.getAsFile();
              if (file) {
                filesToUpload.push({ file, relativePath: file.name });
              }
            }
          }
        }

        // Fallback to e.dataTransfer.files if nothing collected
        if (filesToUpload.length === 0 && e.dataTransfer.files?.length > 0) {
          for (const file of e.dataTransfer.files) {
            filesToUpload.push({ file, relativePath: file.name });
          }
        }

        if (filesToUpload.length === 0) {
          setTimeout(() => { dragDropHandledRef.current = false; }, 100);
          return;
        }

        // Upload each file, creating nested folders as needed
        let uploadedCount = 0;
        setUploadProgress({ current: 0, total: filesToUpload.length });
        try {
          for (const { file, relativePath } of filesToUpload) {
            const lastSlash = relativePath.lastIndexOf('/');
            const folderPath = lastSlash > 0 ? relativePath.substring(0, lastSlash) : '';

            let targetUploadPath = uploadPath;
            if (folderPath) {
              targetUploadPath = await ensureFolderPath(folderPath, uploadPath);
            }

            const formData = new FormData();
            formData.append('file', file);
            formData.append('folder_path', targetUploadPath);

            await axios.post(`${API_BASE}/upload`, formData, {
              headers: { 'Content-Type': 'multipart/form-data' },
            });
            uploadedCount++;
            setUploadProgress({ current: uploadedCount, total: filesToUpload.length });
          }
        } finally {
          setUploadProgress(null);
        }

        showMessage?.(`Imported ${uploadedCount} file(s)`, 'success');

        // Refresh data after upload
        await refreshData();

        // Reset after a short delay
        setTimeout(() => {
          dragDropHandledRef.current = false;
        }, 100);
        return;
      }

      // Otherwise, handle internal drag-and-drop (moving files/folders)
      const data = e.dataTransfer.getData('text/plain');
      if (!data) {
        // No data means this might be icon repositioning - don't set flag
        return;
      }

      const itemsToMove = JSON.parse(data);

      // Determine drop target
      let destinationPath = '/'; // Default to desktop root
      let isDroppingOnFolder = false;

      if (targetFolder) {
        // Explicit folder target passed
        destinationPath = targetFolder.path;
        isDroppingOnFolder = true;
      } else {
        // Try to detect from DOM
        const dropTarget = e.currentTarget || e.target;

        // Check if dropping into a folder window
        const folderElement = dropTarget.closest('[data-folder-path]');
        if (folderElement) {
          destinationPath = folderElement.getAttribute('data-folder-path');
          isDroppingOnFolder = true;
        } else {
          // Check if dropping on a folder icon on desktop
          const folderCard = dropTarget.closest('[data-folder-id]');
          if (folderCard) {
            const folderId = parseInt(folderCard.getAttribute('data-folder-id'));
            const window = windows.find(w => w.folderId === folderId);
            if (window) {
              destinationPath = window.folder.path;
              isDroppingOnFolder = true;
            }
          }
        }
      }

      // Check if this is just repositioning (dropping on desktop, not on a folder)
      // If source and destination are both root, and not dropping on a folder, it's repositioning
      const sourcePath = itemsToMove[0]?.path;
      const sourceParentPath = sourcePath ? sourcePath.substring(0, sourcePath.lastIndexOf('/')) || '/' : '/';

      if (!isDroppingOnFolder && destinationPath === '/' && sourceParentPath === '/') {
        // This is icon repositioning on desktop - don't handle it here, let handleIconDrop handle it
        return;
      }

      // Mark that drop was handled (actual move operation)
      dragDropHandledRef.current = true;

      // Track affected folders (source and destination) for refresh
      const affectedFolderIds = new Set();

      // Find destination folder ID
      if (destinationPath !== '/') {
        const destFolder = windows.find(w => w.folder.path === destinationPath)?.folder;
        if (destFolder) {
          affectedFolderIds.add(destFolder.id);
        }
      }

      // Move each item and track successes/failures
      const moveResults = { success: 0, failed: 0, errors: [] };

      for (const item of itemsToMove) {
        try {
          if (item.itemType === 'folder') {
            // Don't move folder into itself
            if (item.path === destinationPath) {
              continue;
            }

            // Prevent moving folder into its own descendant
            if (destinationPath.startsWith(item.path + '/')) {
              moveResults.failed++;
              moveResults.errors.push(`Cannot move "${item.name}" into its own subfolder`);
              continue;
            }

            // Track source folder (parent of the folder being moved)
            // Extract parent path from item.path
            const sourcePath = item.path.substring(0, item.path.lastIndexOf('/')) || '/';
            if (sourcePath !== '/') {
              const sourceFolder = windows.find(w => w.folder.path === sourcePath)?.folder;
              if (sourceFolder) {
                affectedFolderIds.add(sourceFolder.id);
              }
            }

            // Track the folder being moved itself (if it's open as a window)
            affectedFolderIds.add(item.id);

            // Move folder
            await axios.post(`${API_BASE}/folder/${item.id}/move`, {
              destination_path: destinationPath,
            });
            moveResults.success++;
          } else {
            // Don't move file to same location
            const fileParentPath = item.path.substring(0, item.path.lastIndexOf('/')) || '/';
            if (fileParentPath === destinationPath) {
              continue;
            }

            // Track source folder (where file came from)
            // Extract parent path from item.path
            const sourcePath = item.path.substring(0, item.path.lastIndexOf('/')) || '/';
            if (sourcePath !== '/') {
              const sourceFolder = windows.find(w => w.folder.path === sourcePath)?.folder;
              if (sourceFolder) {
                affectedFolderIds.add(sourceFolder.id);
              }
            }

            // Move file
            await axios.post(`${API_BASE}/document/${item.id}/move`, {
              destination_path: destinationPath,
            });
            moveResults.success++;
          }
        } catch (itemErr) {
          moveResults.failed++;
          const itemErrorMsg = itemErr.response?.data?.message || itemErr.message || 'Unknown error';
          moveResults.errors.push(`${item.name || item.filename}: ${itemErrorMsg}`);
        }
      }

      // Show appropriate message based on results
      if (moveResults.failed === 0) {
        showMessage?.(`Moved ${moveResults.success} item(s)`, 'success');
      } else if (moveResults.success === 0) {
        // All failed - don't refresh, just show error
        const errorMsg = moveResults.errors.length > 0
          ? moveResults.errors[0]
          : 'Failed to move items';
        showMessage?.(errorMsg, 'error');
        return; // Don't refresh if nothing moved
      } else {
        // Partial success
        showMessage?.(`Moved ${moveResults.success} item(s), ${moveResults.failed} failed`, 'warning');
      }

      // Clear selection after move operation (even if partial failure).
      // A move can touch items from any surface, so reset every selection slot.
      if (moveResults.success > 0) {
        setSelectedItemsByContext({ desktop: new Set() });

        // Refresh data after move
        await refreshData();
      }
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || 'Failed to handle drop';
      showMessage?.(`Operation failed: ${errorMsg}`, 'error');
    } finally {
      // Reset after a short delay to allow onDragEnd to check
      setTimeout(() => {
        dragDropHandledRef.current = false;
      }, 100);
    }
  }, [windows, showMessage, refreshData, traverseFolderEntry, ensureFolderPath]);

  // Separate windows by state (needed by arrange callbacks and render)
  const foldedWindows = windows.filter(w => w.state === 'folded');
  const activeWindows = windows.filter(w => w.state === 'minimized' || w.state === 'maximized');

  // Auto-arrange all windows - intelligently pick columns based on count
  const handleArrangeWindows = useCallback(() => {
    const activeWindowList = windows.filter(w => w.state === 'maximized' || w.state === 'minimized');
    const n = activeWindowList.length;
    if (n === 0) return;

    // Pick optimal column count based on window count
    let cols;
    if (n === 1) cols = 1;
    else if (n === 2) cols = 2;
    else if (n <= 4) cols = 2;
    else if (n <= 6) cols = 3;
    else if (n <= 9) cols = 3;
    else cols = Math.ceil(Math.sqrt(n)); // For 10+, use square-ish grid

    const rows = Math.ceil(n / cols);
    const GAP = 2; // grid units gap between windows
    const bounds = getVisibleGridBounds();
    const windowW = Math.min(Math.floor((bounds.maxW - GAP * (cols - 1)) / cols), bounds.maxW);
    const WINDOW_HEIGHT_MIN = 5;

    // Calculate row height to fill available viewport evenly
    const maxRowH = Math.min(
      Math.max(WINDOW_MIN_HEIGHT, Math.floor((bounds.maxH - GAP * (rows - 1)) / rows)),
      bounds.maxH
    );

    const newLayout = activeWindowList.map((window, index) => {
      const col = index % cols;
      const row = Math.floor(index / cols);
      const isMinimized = window.state === 'minimized';

      return {
        i: window.id,
        x: col * (windowW + GAP),
        y: row * (maxRowH + GAP),
        w: windowW,
        h: isMinimized ? WINDOW_HEIGHT_MIN : maxRowH,
        minW: WINDOW_MIN_WIDTH,
        minH: isMinimized ? WINDOW_HEIGHT_MIN : WINDOW_MIN_HEIGHT,
      };
    });

    setWindowLayout(newLayout);
    saveWindowState(windows, newLayout, windowColors, windowZIndex, maxZIndex);
  }, [windows, windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds]);

  // Arrange all desktop icons into a clean grid (reset positions)
  const handleArrangeIcons = useCallback(() => {
    const COLS = Math.max(1, Math.floor((RGL_WIDTH_PROP_PX - 40) / ICON_GRID_W));
    const newPositions = {};

    // Folders first, then files - sequential grid positions
    const allItems = [
      ...foldedWindows.map(w => ({ key: `folder-${w.folderId}` })),
      ...rootFiles.map(f => ({ key: getItemKey(f, 'file') })),
    ];

    allItems.forEach((item, idx) => {
      newPositions[item.key] = {
        x: (idx % COLS) * ICON_GRID_W + ICON_GRID_PAD,
        y: Math.floor(idx / COLS) * ICON_GRID_H + ICON_GRID_PAD,
      };
    });

    setIconPositions(newPositions);

    // Save to backend
    const windowsData = windows.map(w => ({
      id: w.id,
      folderId: w.folderId,
      state: w.state,
    }));
    // Need to save with the new positions directly since state hasn't updated yet
    fetch(WINDOWS_STATE_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        windows: windowsData,
        windowLayout,
        windowColors,
        windowZIndex,
        maxZIndex,
        iconPositions: newPositions,
        lastSaved: new Date().toISOString(),
      }),
    }).catch(() => { });
  }, [foldedWindows, rootFiles, windows, windowLayout, windowColors, windowZIndex, maxZIndex, RGL_WIDTH_PROP_PX]);

  // Context menu handlers
  const handleContextMenu = useCallback((e, item = null, type = 'desktop') => {
    e.preventDefault();
    e.stopPropagation();
    if (type === 'folder' || type === 'folder-window') {
      setActiveContext({
        type: 'folder',
        path: item?.path || '/',
        folderId: item?.id,
      });
    } else {
      setDesktopContext();
    }
    setContextMenu({ top: e.clientY, left: e.clientX });
    setContextMenuType(type);
    setContextMenuItem(item);

    // If right-clicking on an item, select it if not already selected.
    // Refs are used here because the mouseDown that set the new activeContext
    // fires immediately before this callback, and we want the freshest slot.
    if (item) {
      const key = type === 'folder' ? `folder-${item.id}` : `file-${item.id}`;
      const currentKey = activeContextKeyRef.current;
      const currentSelection = selectionByContextRef.current[currentKey] || EMPTY_SELECTION;
      if (!currentSelection.has(key)) {
        handleSelectionChange(currentKey, new Set([key]));
      }
    }
  }, [setDesktopContext, handleSelectionChange]);

  const handleNewFolder = useCallback(async () => {
    setContextMenu(null);
    setNewFolderOpen(true);
  }, []);

  const handleCreateFolder = useCallback(async () => {
    if (!newFolderName.trim()) return;
    try {
      // Determine parent path based on context
      // If contextMenuItem is set and has a path, use it (folder window context)
      // Otherwise, default to root '/' (desktop context)
      const parentPath = contextMenuItem?.path || '/';

      await axios.post(`${API_BASE}/folder`, {
        name: newFolderName,
        parent_path: parentPath,
      });
      showMessage?.(`Folder "${newFolderName}" created`, 'success');
      setNewFolderOpen(false);
      setNewFolderName('');

      // Directly refresh data after creating folder
      await refreshData();
    } catch (err) {
      showMessage?.('Failed to create folder', 'error');
    }
  }, [newFolderName, contextMenuItem, showMessage, refreshData]);

  const handleUpload = useCallback(() => {
    setContextMenu(null);
    fileInputRef.current?.click();
  }, []);

  const handleFileUpload = useCallback(async (e) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    // Determine upload path based on context
    // If contextMenuItem is set and has a path, use it (folder window context)
    // Otherwise, default to root '/' (desktop context)
    const uploadPath = contextMenuItem?.path || '/';

    try {
      // Upload files one by one (backend expects single file per request)
      const uploadPromises = Array.from(files).map(async (file) => {
        const formData = new FormData();
        formData.append('file', file); // Backend expects 'file' (singular)
        formData.append('folder_path', uploadPath); // Backend expects 'folder_path'

        const response = await axios.post(`${API_BASE}/upload`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data',
          },
        });
        return response.data;
      });

      const results = await Promise.all(uploadPromises);
      const uploaded = results.filter(r => !r?.skipped).length;
      const skipped = results.filter(r => r?.skipped).length;
      const msg = skipped > 0
        ? `Imported ${uploaded} file(s) (${skipped} ignored by filter)`
        : `Imported ${uploaded} file(s)`;
      showMessage?.(msg, 'success');

      // Refresh data after upload
      await refreshData();
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || 'Import failed';
      showMessage?.(`Import failed: ${errorMsg}`, 'error');
    } finally {
      // Reset file input
      if (e.target) {
        e.target.value = '';
      }
    }
  }, [contextMenuItem, showMessage, refreshData]);

  const handleCopy = useCallback(() => {
    setClipboard({ items: Array.from(activeSelection), operation: 'copy' });
    setContextMenu(null);
    showMessage?.(`Copied ${activeSelection.size} item(s)`, 'info');
  }, [activeSelection, showMessage]);

  const handleCut = useCallback(() => {
    setClipboard({ items: Array.from(activeSelection), operation: 'cut' });
    setContextMenu(null);
    showMessage?.(`Cut ${activeSelection.size} item(s)`, 'info');
  }, [activeSelection, showMessage]);

  const handlePaste = useCallback(async (targetOverride = null) => {
    setContextMenu(null);
    if (!clipboard || clipboard.items.length === 0) return;

    // Determine target path based on context
    const contextTypeToUse = targetOverride?.type || contextMenuType;
    const contextItemToUse = targetOverride?.item || contextMenuItem;

    let targetPath = '/'; // Default to desktop root
    if (contextTypeToUse === 'folder-window' && contextItemToUse) {
      targetPath = contextItemToUse.path;
    } else if (contextTypeToUse === 'folder' && contextItemToUse) {
      targetPath = contextItemToUse.path;
    }

    try {
      for (const key of clipboard.items) {
        const [type, id] = key.split('-');
        if (type === 'folder') {
          const folder = windows.find(w => w.folderId === parseInt(id))?.folder;
          if (folder) {
            if (clipboard.operation === 'copy') {
              // Copy folder
              await axios.post(`${API_BASE}/folder`, {
                name: `${folder.name} (Copy)`,
                parent_path: targetPath,
              });
            } else {
              // Move folder (cut)
              await axios.put(`${API_BASE}/folder/${id}`, {
                name: folder.name,
                parent_path: targetPath,
              });
            }
          }
        } else {
          // Document operations
          if (clipboard.operation === 'copy') {
            // Reference-based copy (will be implemented in backend)
            await axios.post(`${API_BASE}/document/${id}/copy`, {
              destination_path: targetPath,
            });
          } else {
            // Move document (cut)
            await axios.post(`${API_BASE}/document/${id}/move`, {
              destination_path: targetPath,
            });
          }
        }
      }
      showMessage?.(`${clipboard.operation === 'copy' ? 'Pasted' : 'Moved'} ${clipboard.items.length} item(s)`, 'success');
      setClipboard(null); // Clear clipboard after paste

      // Refresh data to show pasted items
      await refreshData();

      // Also refresh open folder windows
      setFolderRefreshKeys(prev => {
        const updated = { ...prev };
        windowsRef.current.forEach(w => {
          updated[w.folderId] = (updated[w.folderId] || 0) + 1;
        });
        return updated;
      });
    } catch (err) {
      showMessage?.(`${clipboard.operation === 'copy' ? 'Paste' : 'Move'} failed`, 'error');
    }
  }, [clipboard, windows, showMessage, contextMenuType, contextMenuItem, refreshData, setFolderRefreshKeys]);

  const handleDelete = useCallback(() => {
    setContextMenu(null);
    if (activeSelection.size === 0) return;
    setDeleteConfirmOpen(true);
  }, [activeSelection]);

  const handleDeleteConfirm = useCallback(async () => {
    setDeleteConfirmOpen(false);
    if (activeSelection.size === 0) return;

    try {
      for (const key of activeSelection) {
        const [type, id] = key.split('-');
        if (type === 'folder') {
          await axios.delete(`${API_BASE}/folder/${id}`);
        } else {
          await axios.delete(`${API_BASE}/document/${id}`);
        }
      }
      showMessage?.(`Deleted ${activeSelection.size} item(s)`, 'success');
      // Clear only the slot we just deleted from
      handleSelectionChange(activeContextKeyRef.current, new Set());
      await refreshData();
    } catch (err) {
      showMessage?.('Delete failed', 'error');
    }
  }, [activeSelection, showMessage, refreshData, handleSelectionChange]);

  const handleRename = useCallback(async () => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item) {
      // Fallback to selected item
      if (activeSelection.size !== 1) return;
      const key = Array.from(activeSelection)[0];
      const [type, id] = key.split('-');
      if (type === 'folder') {
        const window = windows.find(w => w.folderId === parseInt(id));
        if (window) {
          setRenameItem({ type: 'folder', item: window.folder });
          setRenameName(window.folder.name);
          setRenameDialogOpen(true);
        }
      } else {
        // File rename from selection - fetch file directly by ID
        try {
          const response = await axios.get(`${API_BASE}/document/${id}`);
          if (response.data && response.data.data) {
            const file = response.data.data;
            setRenameItem({ type: 'file', item: file });
            setRenameName(file.filename || file.name || '');
            setRenameDialogOpen(true);
          } else {
            showMessage?.('File not found', 'error');
          }
        } catch (err) {
          // Fallback: try browsing root
          try {
            const browseResponse = await axios.get(`${API_BASE}/browse?path=/&fields=light`);
            const data = browseResponse.data.data;
            const file = (data.documents || []).find(d => d.id === parseInt(id));
            if (file) {
              setRenameItem({ type: 'file', item: file });
              setRenameName(file.filename || file.name || '');
              setRenameDialogOpen(true);
            } else {
              showMessage?.('File not found', 'error');
            }
          } catch (browseErr) {
            showMessage?.('Failed to load file details', 'error');
          }
        }
        return;
      }
    } else {
      setRenameItem({ type: contextMenuType, item });
      setRenameName(item.name || item.filename || '');
      setRenameDialogOpen(true);
    }
  }, [contextMenuItem, contextMenuType, activeSelection, windows, showMessage]);

  const handleRenameSubmit = useCallback(async () => {
    if (!renameItem || !renameName.trim()) return;

    try {
      if (renameItem.type === 'folder') {
        await axios.put(`${API_BASE}/folder/${renameItem.item.id}`, {
          name: renameName.trim(),
        });
        showMessage?.(`Folder renamed to "${renameName.trim()}"`, 'success');
      } else {
        await axios.put(`${API_BASE}/document/${renameItem.item.id}`, {
          filename: renameName.trim(),
        });
        showMessage?.(`File renamed to "${renameName.trim()}"`, 'success');
      }
      setRenameDialogOpen(false);
      setRenameItem(null);
      setRenameName('');

      // Refresh data after rename
      await refreshData();
    } catch (err) {
      showMessage?.('Rename failed', 'error');
    }
  }, [renameItem, renameName, showMessage, refreshData]);

  const handleColorChange = useCallback((color) => {
    if (!contextMenuItem || contextMenuType !== 'folder') return;

    const window = windows.find(w => w.folderId === contextMenuItem.id);
    if (window) {
      handleWindowColorChange(window.id, color);
      setContextMenu(null);
    }
  }, [contextMenuItem, contextMenuType, windows, handleWindowColorChange]);

  const handleDownload = useCallback(() => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item || contextMenuType !== 'file') return;

    // Create download link
    const downloadUrl = `${API_BASE}/document/${item.id}/download`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = item.filename || item.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showMessage?.(`Downloading ${item.filename || item.name}`, 'info');
  }, [contextMenuItem, contextMenuType, showMessage]);

  const handleEditImage = useCallback(() => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item || contextMenuType !== 'file') return;
    const filename = item.filename || item.name || '';
    if (isImageFile(filename)) {
      const imageUrl = `${API_BASE}/document/${item.id}/download?v=${item.updated_at || Date.now()}`;
      setLightbox({
        url: imageUrl,
        name: filename,
        documentId: item.id,
        editMode: true,
      });
    } else if (isCodeFile(filename)) {
      setCodeViewer({ file: item });
    } else if (isPdfFile(filename)) {
      setPdfViewer({ file: item });
    } else if (isAudioFile(filename)) {
      setAudioPlayer({ file: item });
    }
  }, [contextMenuItem, contextMenuType]);

  // Open file in Code Editor page — navigates with file data in router state
  const handleOpenInCodeEditor = useCallback((file, content = null) => {
    setContextMenu(null);
    const targetFile = file || contextMenuItem;
    if (!targetFile) return;
    navigate('/code-editor', {
      state: {
        openFile: {
          id: targetFile.id,
          filename: targetFile.filename || targetFile.name,
          content: content,
          source: 'document',
        }
      }
    });
  }, [contextMenuItem, navigate]);

  const handleProperties = useCallback(async () => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (item) {
      setSelectedForProperties({ type: contextMenuType, item });
      setPropertiesOpen(true);
    } else {
      // Fallback to selected item
      if (activeSelection.size !== 1) return;
      const key = Array.from(activeSelection)[0];
      const [type, id] = key.split('-');
      if (type === 'folder') {
        const window = windows.find(w => w.folderId === parseInt(id));
        if (window) {
          setSelectedForProperties({ type: 'folder', item: window.folder });
          setPropertiesOpen(true);
        }
      } else {
        // File properties from selection - fetch file directly by ID
        try {
          const response = await axios.get(`${API_BASE}/document/${id}`);
          if (response.data && response.data.data) {
            const file = response.data.data;
            setSelectedForProperties({ type: 'file', item: file });
            setPropertiesOpen(true);
          } else {
            showMessage?.('File not found', 'error');
          }
        } catch (err) {
          // Fallback: try browsing root
          try {
            const browseResponse = await axios.get(`${API_BASE}/browse?path=/&fields=light`);
            const data = browseResponse.data.data;
            const file = (data.documents || []).find(d => d.id === parseInt(id));
            if (file) {
              setSelectedForProperties({ type: 'file', item: file });
              setPropertiesOpen(true);
            } else {
              showMessage?.('File not found', 'error');
            }
          } catch (browseErr) {
            showMessage?.('Failed to load file details', 'error');
          }
        }
      }
    }
  }, [contextMenuItem, contextMenuType, activeSelection, windows, showMessage]);

  const handleFilePropertiesSave = useCallback(async (fileId, payload) => {
    try {
      await axios.put(`${API_BASE}/document/${fileId}/link`, payload);
      showMessage?.('File properties updated successfully', 'success');
      setPropertiesOpen(false);
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || 'Failed to update file properties';
      showMessage?.(`Update failed: ${errorMsg}`, 'error');
    }
  }, [showMessage]);

  const handleFilePropertiesDelete = useCallback(async (fileId) => {
    try {
      await axios.delete(`${API_BASE}/document/${fileId}`);
      showMessage?.('File deleted successfully', 'success');
      setPropertiesOpen(false);
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || 'Failed to delete file';
      showMessage?.(`Delete failed: ${errorMsg}`, 'error');
      throw err; // Re-throw so modal can handle it
    }
  }, [showMessage]);

  const handleFilePropertiesReindex = useCallback(async (fileId) => {
    try {
      const result = await triggerIndexing(fileId);

      if (result?.error) {
        throw new Error(result.error);
      }

      showMessage?.(
        result?.message || 'Reindexing triggered successfully. Status will update shortly.',
        'success'
      );
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || 'Failed to trigger reindexing';
      showMessage?.(`Reindexing failed: ${errorMsg}`, 'error');
      throw err;
    }
  }, [showMessage]);

  // Handle bulk index from context menu
  const handleIndex = useCallback(async () => {
    setContextMenu(null);

    // Determine which items to index
    let itemKeys;
    if (contextMenuItem) {
      const key = contextMenuType === 'folder'
        ? `folder-${contextMenuItem.id}`
        : `file-${contextMenuItem.id}`;
      // Use selection if the right-clicked item is in it, otherwise just the item
      itemKeys = activeSelection.has(key) ? Array.from(activeSelection) : [key];
    } else {
      itemKeys = Array.from(activeSelection);
    }

    if (itemKeys.length === 0) return;

    // Split into folder IDs and document IDs
    const folderIds = [];
    const documentIds = [];
    for (const key of itemKeys) {
      const [type, id] = key.split('-');
      if (type === 'folder') {
        folderIds.push(parseInt(id));
      } else {
        documentIds.push(parseInt(id));
      }
    }

    try {
      const result = await indexBulk(folderIds, documentIds);
      if (result?.error) {
        throw new Error(result.error);
      }
      const parts = [];
      if (result.total_documents) parts.push(`${result.total_documents} document${result.total_documents !== 1 ? 's' : ''}`);
      if (folderIds.length > 0) parts.push(`from ${folderIds.length} folder${folderIds.length !== 1 ? 's' : ''}`);
      showMessage?.(`Indexing ${parts.join(' ')}`, 'success');
    } catch (err) {
      const errorMsg = err.message || 'Failed to trigger indexing';
      showMessage?.(`Indexing failed: ${errorMsg}`, 'error');
    }
  }, [contextMenuItem, contextMenuType, activeSelection, showMessage]);

  // Desktop drag-to-select handlers
  const handleDesktopSelectionMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.desktop-item-card')) return;

    const rect = desktopContentRef.current?.getBoundingClientRect();
    if (!rect) return;

    setDesktopContext();
    const startX = e.clientX - rect.left;
    const startY = e.clientY - rect.top;

    setIsDesktopSelecting(true);
    setDesktopSelectionBox({ startX, startY, currentX: startX, currentY: startY });
  }, [setDesktopContext]);

  const handleDesktopSelectionMouseMove = useCallback((e) => {
    if (!isDesktopSelecting || !desktopSelectionBox) return;
    const rect = desktopContentRef.current?.getBoundingClientRect();
    if (!rect) return;

    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;

    setDesktopSelectionBox(prev => {
      if (!prev) return prev;
      const newBox = { ...prev, currentX, currentY };
      const minX = Math.min(newBox.startX, currentX);
      const maxX = Math.max(newBox.startX, currentX);
      const minY = Math.min(newBox.startY, currentY);
      const maxY = Math.max(newBox.startY, currentY);

      const newSelection = new Set();
      desktopItemRefs.current.forEach((element, key) => {
        if (!element) return;
        const itemRect = element.getBoundingClientRect();
        const itemRelativeRect = {
          left: itemRect.left - rect.left,
          top: itemRect.top - rect.top,
          right: itemRect.right - rect.left,
          bottom: itemRect.bottom - rect.top,
        };

        if (
          itemRelativeRect.left < maxX &&
          itemRelativeRect.right > minX &&
          itemRelativeRect.top < maxY &&
          itemRelativeRect.bottom > minY
        ) {
          newSelection.add(key);
        }
      });

      handleSelectionChange('desktop', newSelection);
      return newBox;
    });
  }, [desktopSelectionBox, isDesktopSelecting, handleSelectionChange]);

  const handleDesktopSelectionMouseUp = useCallback(() => {
    setIsDesktopSelecting(false);
    setDesktopSelectionBox(null);
  }, []);

  useEffect(() => {
    if (isDesktopSelecting) {
      window.addEventListener('mousemove', handleDesktopSelectionMouseMove);
      window.addEventListener('mouseup', handleDesktopSelectionMouseUp);
      return () => {
        window.removeEventListener('mousemove', handleDesktopSelectionMouseMove);
        window.removeEventListener('mouseup', handleDesktopSelectionMouseUp);
      };
    }
  }, [isDesktopSelecting, handleDesktopSelectionMouseMove, handleDesktopSelectionMouseUp]);

  // Keyboard shortcuts (global)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.defaultPrevented) return;
      const targetTag = e.target?.tagName;
      if (targetTag === 'INPUT' || targetTag === 'TEXTAREA' || e.target?.isContentEditable) {
        return;
      }

      const key = e.key.toLowerCase();

      if (e.ctrlKey && key === 'a') {
        if (activeContext.type === 'desktop') {
          e.preventDefault();
          const allKeys = new Set([
            ...foldedWindows.map(f => `folder-${f.folderId}`),
            ...rootFiles.map(file => getItemKey(file, 'file')),
          ]);
          handleSelectionChange('desktop', allKeys);
        }
        return;
      }

      if (e.ctrlKey && key === 'c' && activeSelection.size > 0) {
        e.preventDefault();
        handleCopy();
        return;
      }

      if (e.ctrlKey && key === 'x' && activeSelection.size > 0) {
        e.preventDefault();
        handleCut();
        return;
      }

      if (e.ctrlKey && key === 'v') {
        e.preventDefault();
        const target = activeContext.type === 'folder'
          ? { type: 'folder-window', item: { path: activeContext.path, id: activeContext.folderId } }
          : { type: 'desktop', item: null };
        handlePaste(target);
        return;
      }

      if (key === 'delete' && activeSelection.size > 0) {
        e.preventDefault();
        handleDelete();
        return;
      }

      if (key === 'escape') {
        e.preventDefault();
        handleSelectionChange(activeContextKeyRef.current, new Set());
        setContextMenu(null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeContext, foldedWindows, rootFiles, activeSelection, handleCopy, handleCut, handlePaste, handleDelete, handleSelectionChange]);


  // Compute collision-free positions for ALL icons.
  // Saved positions are kept as-is; unsaved items get the first free grid cell.
  const resolvedIconPositions = useMemo(() => {
    const positions = {};
    const occupied = new Set();
    const COLS = Math.max(1, Math.floor((RGL_WIDTH_PROP_PX - 40) / ICON_GRID_W));

    // All icon keys in order: folders first, then files
    const allItems = [
      ...foldedWindows.map(w => ({ key: `folder-${w.folderId}`, id: w.folderId, isFile: false })),
      ...rootFiles.map(f => ({ key: getItemKey(f, 'file'), id: f.id, isFile: true })),
    ];

    // First pass: register occupied cells from saved positions
    for (const item of allItems) {
      const saved = iconPositions[item.key];
      if (saved) {
        positions[item.key] = saved;
        const c = Math.round((saved.x - ICON_GRID_PAD) / ICON_GRID_W);
        const r = Math.round((saved.y - ICON_GRID_PAD) / ICON_GRID_H);
        occupied.add(`${c},${r}`);
      }
    }

    // Second pass: assign first-free-cell to items without saved positions
    let nextIdx = 0;
    for (const item of allItems) {
      if (positions[item.key]) continue;
      // Walk grid cells in order until we find an unoccupied one
      while (nextIdx < 10000) {
        const c = nextIdx % COLS;
        const r = Math.floor(nextIdx / COLS);
        nextIdx++;
        if (!occupied.has(`${c},${r}`)) {
          positions[item.key] = {
            x: c * ICON_GRID_W + ICON_GRID_PAD,
            y: r * ICON_GRID_H + ICON_GRID_PAD,
          };
          occupied.add(`${c},${r}`);
          break;
        }
      }
    }

    return positions;
  }, [iconPositions, foldedWindows, rootFiles, RGL_WIDTH_PROP_PX]);

  // Find the nearest unoccupied grid cell for an icon
  const snapToGrid = useCallback((rawX, rawY, movingKey, currentPositions) => {
    // Snap to nearest grid cell
    let col = Math.round((rawX - ICON_GRID_PAD) / ICON_GRID_W);
    let row = Math.round((rawY - ICON_GRID_PAD) / ICON_GRID_H);
    col = Math.max(0, col);
    row = Math.max(0, row);

    // Build set of occupied cells (excluding the icon being moved)
    // currentPositions is the fully-resolved map so every icon has a position
    const occupied = new Set();
    for (const [k, pos] of Object.entries(currentPositions)) {
      if (k === movingKey) continue;
      const c = Math.round((pos.x - ICON_GRID_PAD) / ICON_GRID_W);
      const r = Math.round((pos.y - ICON_GRID_PAD) / ICON_GRID_H);
      occupied.add(`${c},${r}`);
    }

    // If target cell is free, use it
    if (!occupied.has(`${col},${row}`)) {
      return { x: col * ICON_GRID_W + ICON_GRID_PAD, y: row * ICON_GRID_H + ICON_GRID_PAD };
    }

    // Otherwise spiral outward to find nearest free cell
    for (let dist = 1; dist < 50; dist++) {
      for (let dc = -dist; dc <= dist; dc++) {
        for (let dr = -dist; dr <= dist; dr++) {
          if (Math.abs(dc) !== dist && Math.abs(dr) !== dist) continue;
          const nc = col + dc;
          const nr = row + dr;
          if (nc < 0 || nr < 0) continue;
          if (!occupied.has(`${nc},${nr}`)) {
            return { x: nc * ICON_GRID_W + ICON_GRID_PAD, y: nr * ICON_GRID_H + ICON_GRID_PAD };
          }
        }
      }
    }

    // Fallback (shouldn't happen)
    return { x: col * ICON_GRID_W + ICON_GRID_PAD, y: row * ICON_GRID_H + ICON_GRID_PAD };
  }, []);

  // Handle icon drag end (repositioning)
  const handleIconDrop = useCallback((e, key) => {
    // Only reposition if drop was not handled (i.e., not moved to another location)
    if (dragDropHandledRef.current) {
      return;
    }

    const container = e.currentTarget.closest('[data-desktop-container]');
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const iconWidth = 100;
    const iconHeight = 80;

    // Raw position from mouse
    const rawX = Math.max(0, e.clientX - rect.left - iconWidth / 2);
    const rawY = Math.max(0, e.clientY - rect.top - iconHeight / 2);

    // Snap to grid and avoid overlaps (use resolved positions for accurate occupancy)
    const snapped = snapToGrid(rawX, rawY, key, resolvedIconPositions);

    const newIconPositions = { ...iconPositions, [key]: snapped };
    setIconPositions(newIconPositions);

    // Save to backend with the new positions directly (state hasn't updated yet)
    const windowsData = windows.map(w => ({
      id: w.id,
      folderId: w.folderId,
      state: w.state,
    }));
    saveWindowState(windowsData, windowLayout, windowColors, windowZIndex, maxZIndex, newIconPositions);
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, snapToGrid, resolvedIconPositions, iconPositions]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", flexDirection: "column", gap: 2, height: "100%", p: 2 }}>
        <Typography variant="h5">Files</Typography>
        <Typography>Loading folders...</Typography>
      </Box>
    );
  }

  return (
    <PageLayout
      title="Files"
      variant="grid"
      actions={
        <>
          <Tooltip title="Import Files">
            <IconButton onClick={handleUpload} size="small" sx={{ opacity: 0.6 }}>
              <UploadFileIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Arrange Icons">
            <IconButton onClick={handleArrangeIcons} size="small" sx={{ opacity: 0.6 }}>
              <GridViewIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Arrange Windows">
            <IconButton onClick={handleArrangeWindows} size="small" sx={{ opacity: 0.6 }}>
              <AppsIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </>
      }
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel}
    >
      <Box ref={windowContainerRef} sx={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
        {/* Desktop area with folded folder icons */}
        <Box
          data-desktop-container
          ref={desktopContentRef}
          sx={{ position: "absolute", inset: 0, overflow: "auto", zIndex: 1, p: 2 }}
          onContextMenu={handleContextMenu}
          onMouseDown={handleDesktopSelectionMouseDown}
          onDragOver={(e) => {
            e.preventDefault();
            e.stopPropagation();
            // Allow file drops from outside browser
            if (e.dataTransfer.types.includes('Files')) {
              e.dataTransfer.dropEffect = 'copy';
            }
          }}
          onDragEnter={(e) => {
            // Visual feedback for file drops
            if (e.dataTransfer.types.includes('Files')) {
              e.currentTarget.style.backgroundColor = 'action.hover';
            }
          }}
          onDragLeave={(e) => {
            // Remove visual feedback
            if (!e.currentTarget.contains(e.relatedTarget)) {
              e.currentTarget.style.backgroundColor = '';
            }
          }}
          onDrop={handleDrop}
        >
          {/* Empty state */}
          {foldedWindows.length === 0 && rootFiles.length === 0 && activeWindows.length === 0 && (
            <Box
              sx={{
                position: 'absolute',
                inset: 0,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                pointerEvents: 'none',
                opacity: 0.5,
                gap: 1,
              }}
            >
              <GuaardvarkLogo size={64} sx={{ opacity: 0.4 }} />
              <Typography variant="h6" color="text.disabled">
                No documents yet
              </Typography>
              <Typography variant="body2" color="text.disabled">
                Drag files here or right-click to create a folder
              </Typography>
            </Box>
          )}

          {/* Desktop selection box */}
          {isDesktopSelecting && desktopSelectionBox && (
            <Box
              sx={{
                position: 'absolute',
                left: Math.min(desktopSelectionBox.startX, desktopSelectionBox.currentX),
                top: Math.min(desktopSelectionBox.startY, desktopSelectionBox.currentY),
                width: Math.abs(desktopSelectionBox.currentX - desktopSelectionBox.startX),
                height: Math.abs(desktopSelectionBox.currentY - desktopSelectionBox.startY),
                bgcolor: (theme) => theme.palette.primary.main + '1A',
                border: '1px solid',
                borderColor: 'primary.main',
                pointerEvents: 'none',
                zIndex: 1000,
              }}
            />
          )}
          {/* Folders */}
          {foldedWindows.map((window) => {
            const key = `folder-${window.folderId}`;
            const pos = resolvedIconPositions[key] || { x: ICON_GRID_PAD, y: ICON_GRID_PAD };
            const isDragOver = dragOverFolderId === window.folderId;
            const itemCount = (window.folder.document_count || 0) + (window.folder.subfolder_count || 0);

            return (
              <Box
                key={window.id}
                ref={(el) => {
                  if (el) desktopItemRefs.current.set(key, el);
                  else desktopItemRefs.current.delete(key);
                }}
                data-folder-id={window.folderId}
                data-folder-path={window.folder.path}
                sx={{
                  position: 'absolute',
                  left: `${pos.x}px`,
                  top: `${pos.y}px`,
                  width: 100,
                  cursor: 'grab',
                  '&.desktop-item-card': {},
                  '&:active': {
                    cursor: 'grabbing',
                  },
                }}
                className="desktop-item-card"
                draggable
                onDragStart={(e) => {
                  setDesktopContext();
                  e.dataTransfer.setData('text/plain', JSON.stringify([{
                    id: window.folderId,
                    itemType: 'folder',
                    name: window.folder.name,
                    path: window.folder.path,
                  }]));
                  e.dataTransfer.effectAllowed = 'move';
                }}
                onDragEnd={(e) => {
                  handleIconDrop(e, key);
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
                onDragEnter={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setDragOverFolderId(window.folderId);
                }}
                onDragLeave={(e) => {
                  e.stopPropagation();
                  if (!e.currentTarget.contains(e.relatedTarget)) {
                    setDragOverFolderId(null);
                  }
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setDragOverFolderId(null);
                  handleDrop(e, window.folder);
                }}
              >
                <Card
                  sx={{
                    cursor: 'pointer',
                    border: (desktopSelection.has(key) || isDragOver) ? '2px solid' : '1px solid',
                    borderColor: isDragOver ? 'success.main' : desktopSelection.has(key) ? 'primary.main' : 'divider',
                    bgcolor: isDragOver ? 'action.hover' : desktopSelection.has(key) ? 'action.selected' : 'background.paper',
                    transition: 'all 0.2s ease-in-out',
                    boxShadow: isDragOver ? 6 : undefined,
                    transform: isDragOver ? 'scale(1.05)' : undefined,
                    '&:hover': {
                      boxShadow: 3,
                      bgcolor: 'action.hover',
                    },
                  }}
                >
                  <CardActionArea
                    onClick={(e) => {
                      e.stopPropagation();
                      setDesktopContext();
                      if (e.ctrlKey || e.metaKey) {
                        const newSelection = new Set(desktopSelection);
                        if (newSelection.has(key)) {
                          newSelection.delete(key);
                        } else {
                          newSelection.add(key);
                        }
                        handleSelectionChange('desktop', newSelection);
                      } else {
                        handleSelectionChange('desktop', new Set([key]));
                      }
                    }}
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      handleFolderExpand(window.id);
                    }}
                    onContextMenu={(e) => handleContextMenu(e, window.folder, 'folder')}
                    sx={{ p: 2, textAlign: 'center' }}
                  >
                    <CardContent sx={{ p: 0 }}>
                      <Box sx={{ position: 'relative', display: 'inline-flex' }}>
                        <FolderOutlined
                          sx={{ fontSize: 48, color: windowColors[window.id] || theme.palette.primary.main, mb: '4px' }}
                        />
                        <FolderIndexIndicator item={window.folder} theme={theme} />
                      </Box>
                      {window.folder.is_repository && (
                        <Box sx={{ position: 'absolute', top: 8, right: 8 }}>
                          <Tooltip title="Code Repository">
                            <Code sx={{ fontSize: 16, color: theme.palette.primary.main }} />
                          </Tooltip>
                        </Box>
                      )}
                      <Tooltip title={window.folder.name} enterDelay={600} placement="bottom">
                        <Typography variant="body2" noWrap>
                          {window.folder.name}
                        </Typography>
                      </Tooltip>
                      {itemCount > 0 && (
                        <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', lineHeight: 1.2 }}>
                          {itemCount} item{itemCount !== 1 ? 's' : ''}
                        </Typography>
                      )}
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Box>
            );
          })}

          {/* Files */}
          {rootFiles.map((file) => {
            const key = getItemKey(file, 'file');
            const isSelected = desktopSelection.has(key);
            const pos = resolvedIconPositions[key] || { x: ICON_GRID_PAD, y: ICON_GRID_PAD };

            return (
              <Box
                key={key}
                ref={(el) => {
                  if (el) desktopItemRefs.current.set(key, el);
                  else desktopItemRefs.current.delete(key);
                }}
                data-file-id={file.id}
                data-file-path={file.path}
                sx={{
                  position: 'absolute',
                  left: `${pos.x}px`,
                  top: `${pos.y}px`,
                  width: 100,
                  cursor: 'grab',
                  '&.desktop-item-card': {},
                  '&:active': {
                    cursor: 'grabbing',
                  },
                }}
                className="desktop-item-card"
                draggable
                onDragStart={(e) => {
                  setDesktopContext();
                  e.dataTransfer.setData('text/plain', JSON.stringify([{
                    id: file.id,
                    itemType: 'file',
                    filename: file.filename,
                    name: file.filename,
                    path: file.path,
                  }]));
                  e.dataTransfer.effectAllowed = 'move';
                }}
                onDragEnd={(e) => {
                  handleIconDrop(e, key);
                }}
                onDragOver={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  // Check if dropping on a folder icon (check parent elements)
                  const folderCard = e.target.closest('[data-folder-id]');
                  if (folderCard) {
                    const folderId = parseInt(folderCard.getAttribute('data-folder-id'));
                    const window = windows.find(w => w.folderId === folderId);
                    if (window) {
                      handleDrop(e, window.folder);
                      return;
                    }
                  }
                  // Dropping on desktop or file icon - pass null to let handleDrop detect destination
                  handleDrop(e, null);
                }}
              >
                <Card
                  sx={{
                    cursor: 'pointer',
                    border: isSelected ? '2px solid' : '1px solid',
                    borderColor: isSelected ? 'primary.main' : 'divider',
                    bgcolor: isSelected ? 'action.selected' : 'background.paper',
                    transition: 'all 0.2s ease-in-out',
                    '&:hover': {
                      boxShadow: 3,
                      bgcolor: 'action.hover',
                    },
                  }}
                >
                  <CardActionArea
                    onClick={(e) => {
                      e.stopPropagation();
                      setDesktopContext();
                      if (e.ctrlKey || e.metaKey) {
                        // Multi-select
                        const newSelection = new Set(desktopSelection);
                        if (newSelection.has(key)) {
                          newSelection.delete(key);
                        } else {
                          newSelection.add(key);
                        }
                        handleSelectionChange('desktop', newSelection);
                      } else {
                        // Single select
                        handleSelectionChange('desktop', new Set([key]));
                      }
                    }}
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      const filename = file.filename || file.name || '';
                      if (isImageFile(filename)) {
                        const imageUrl = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
                        setLightbox({ url: imageUrl, name: filename, documentId: file.id, editMode: false });
                      } else if (isCodeFile(filename)) {
                        setCodeViewer({ file });
                      } else if (isPdfFile(filename)) {
                        setPdfViewer({ file });
                      } else if (isAudioFile(filename)) {
                        setAudioPlayer({ file });
                      } else {
                        window.open(`${API_BASE}/document/${file.id}/download`, '_blank');
                      }
                    }}
                    onContextMenu={(e) => handleContextMenu(e, file, 'file')}
                    sx={{ p: 2, textAlign: 'center' }}
                  >
                    <CardContent sx={{ p: 0 }}>
                      {getFileIcon(file.filename, isSelected, theme, 48, file.index_status, file.path)}
                      <Tooltip title={file.filename} enterDelay={600} placement="bottom">
                        <Typography variant="body2" noWrap sx={{ mt: 1 }}>
                          {file.filename}
                        </Typography>
                      </Tooltip>
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Box>
            );
          })}
        </Box>

        {/* Folder windows layer (minimized/maximized) */}
        {activeWindows.length > 0 && (
          <Box sx={{
            position: "absolute", inset: 0, pointerEvents: "none", zIndex: 2,
            // Force RGL internals to not capture pointer events — only our window content should
            '& .react-grid-layout': { pointerEvents: 'none' },
            '& .react-grid-item': { pointerEvents: 'none' },
            '& .react-grid-item > div': { pointerEvents: 'auto' },
            // Resize handles — large hit area for easy grabbing
            '& .react-resizable-handle': {
              pointerEvents: 'auto',
              zIndex: 10,
            },
            '& .react-resizable-handle-se': {
              width: '20px !important',
              height: '20px !important',
              bottom: '0 !important',
              right: '0 !important',
              cursor: 'se-resize',
            },
            '& .react-resizable-handle-sw': {
              width: '20px !important',
              height: '20px !important',
              bottom: '0 !important',
              left: '0 !important',
              cursor: 'sw-resize',
            },
            '& .react-resizable-handle-ne': {
              width: '20px !important',
              height: '20px !important',
              top: '0 !important',
              right: '0 !important',
              cursor: 'ne-resize',
            },
            '& .react-resizable-handle-nw': {
              width: '20px !important',
              height: '20px !important',
              top: '0 !important',
              left: '0 !important',
              cursor: 'nw-resize',
            },
            '& .react-resizable-handle-s': {
              width: '100% !important',
              height: '12px !important',
              bottom: '0 !important',
              left: '0 !important',
              cursor: 's-resize',
            },
            '& .react-resizable-handle-n': {
              width: '100% !important',
              height: '6px !important',
              top: '0 !important',
              left: '0 !important',
              cursor: 'n-resize',
            },
            '& .react-resizable-handle-e': {
              width: '12px !important',
              height: '100% !important',
              top: '0 !important',
              right: '0 !important',
              cursor: 'e-resize',
            },
            '& .react-resizable-handle-w': {
              width: '12px !important',
              height: '100% !important',
              top: '0 !important',
              left: '0 !important',
              cursor: 'w-resize',
            },
          }}>
            <WindowsGridLayout
              layout={windowLayout}
              cols={WINDOWS_COLS}
              rowHeight={WINDOWS_ROW_HEIGHT}
              margin={WINDOWS_MARGIN}
              containerPadding={WINDOWS_PADDING}
              isDraggable={true}
              isResizable={true}
              compactType={null}
              preventCollision={false}
              allowOverlap={true}
              useCSSTransforms={false}
              draggableHandle=".folder-window-drag-handle"
              draggableCancel="button, input, textarea, select, option, .non-draggable"
              onLayoutChange={handleWindowLayoutChange}
              resizeHandles={["se", "sw", "ne", "nw", "s", "n", "e", "w"]}
            >
              {activeWindows.map((window) => {
                const layoutItem = windowLayout.find(l => l.i === window.id) || {
                  i: window.id,
                  x: 0,
                  y: 0,
                  w: WINDOW_MIN_WIDTH * 2,
                  h: window.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT * 2,
                  minW: WINDOW_MIN_WIDTH,
                  minH: window.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT,
                };

                return (
                  <div
                    key={window.id}
                    style={{
                      zIndex: windowZIndex[window.id] || 0,
                      pointerEvents: "auto",
                    }}
                    onClick={() => handleWindowClick(window.id)}
                  >
                    <FolderWindow
                      id={window.id}
                      folder={window.folder}
                      isMinimized={window.state === 'minimized'}
                      onToggleMinimize={() => handleToggleMinimize(window.id)}
                      onClose={() => handleWindowClose(window.id)}
                      selectedItems={selectedItemsByContext[window.id] || EMPTY_SELECTION}
                      onSelectionChange={(newSelection) => handleSelectionChange(window.id, newSelection)}
                      onItemsMove={handleDrop}
                      onDragStart={handleDragStart}
                      onDrop={handleDrop}
                      onFolderOpen={handleFolderExpand}
                      onFileOpen={(e, file) => {
                        e.stopPropagation();
                        const filename = file.filename || file.name || '';
                        if (isImageFile(filename)) {
                          const imageUrl = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
                          setLightbox({ url: imageUrl, name: filename, documentId: file.id, editMode: false });
                        } else if (isCodeFile(filename)) {
                          setCodeViewer({ file });
                        } else if (isPdfFile(filename)) {
                          setPdfViewer({ file });
                        } else if (isAudioFile(filename)) {
                          setAudioPlayer({ file });
                        } else {
                          window.open(`${API_BASE}/document/${file.id}/download`, '_blank');
                        }
                      }}
                      onContextMenu={handleContextMenu}
                      onFocusContext={setActiveContext}
                      refreshKey={folderRefreshKeys[window.folderId] || 0}
                      folderColors={folderIdToColor}
                      {...layoutItem}
                    />
                  </div>
                );
              })}
            </WindowsGridLayout>
          </Box>
        )}
      </Box>

      {/* Context Menu */}
      <DocumentsContextMenu
        anchorPosition={contextMenu}
        onClose={() => {
          setContextMenu(null);
          setContextMenuItem(null);
          setContextMenuType('desktop');
        }}
        onNewFolder={handleNewFolder}
        onUpload={handleUpload}
        onCopy={handleCopy}
        onCut={handleCut}
        onPaste={handlePaste}
        onDelete={handleDelete}
        onProperties={handleProperties}
        onRename={handleRename}
        onDownload={handleDownload}
        onEdit={handleEditImage}
        onColorChange={handleColorChange}
        onIndex={handleIndex}
        onOpenWindow={contextMenuItem && contextMenuType === 'folder' ? () => {
          const win = windows.find(w => w.folderId === contextMenuItem.id);
          if (win) {
            handleFolderExpand(win.id);
          }
          setContextMenu(null);
          setContextMenuItem(null);
        } : undefined}
        isImage={contextMenuItem && contextMenuType === 'file' && isImageFile(contextMenuItem.filename || contextMenuItem.name || '')}
        isCode={contextMenuItem && contextMenuType === 'file' && isCodeFile(contextMenuItem.filename || contextMenuItem.name || '')}
        isPdf={contextMenuItem && contextMenuType === 'file' && isPdfFile(contextMenuItem.filename || contextMenuItem.name || '')}
        onOpenInCodeEditor={() => handleOpenInCodeEditor()}
        hasClipboard={Boolean(clipboard)}
        hasSelection={activeSelection.size > 0}
        contextType={contextMenuType}
        selectedItem={contextMenuItem}
        folderColor={contextMenuItem && contextMenuType === 'folder'
          ? windowColors[windows.find(w => w.folderId === contextMenuItem.id)?.id]
          : null}
      />

      {/* New Folder Dialog */}
      <Dialog
        open={newFolderOpen}
        onClose={() => setNewFolderOpen(false)}
        TransitionProps={{
          onEntered: () => {
            // Focus the input field after the dialog transition completes
            newFolderInputRef.current?.focus();
          }
        }}
      >
        <DialogTitle>Create New Folder</DialogTitle>
        <DialogContent>
          <TextField
            inputRef={newFolderInputRef}
            autoFocus
            label="Folder Name"
            fullWidth
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleCreateFolder()}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewFolderOpen(false)}>Cancel</Button>
          <Button onClick={handleCreateFolder} variant="contained">Create</Button>
        </DialogActions>
      </Dialog>

      {/* Rename Dialog */}
      <Dialog
        open={renameDialogOpen}
        onClose={() => setRenameDialogOpen(false)}
        TransitionProps={{
          onEntered: () => {
            const input = renameInputRef.current;
            if (input) {
              input.focus();
              // Pre-select filename stem (everything before last dot) for files
              if (renameItem?.type !== 'folder') {
                const name = renameName;
                const lastDot = name.lastIndexOf('.');
                if (lastDot > 0) {
                  input.setSelectionRange(0, lastDot);
                } else {
                  input.select();
                }
              } else {
                input.select();
              }
            }
          }
        }}
      >
        <DialogTitle>Rename {renameItem?.type === 'folder' ? 'Folder' : 'File'}</DialogTitle>
        <DialogContent>
          <TextField
            inputRef={renameInputRef}
            autoFocus
            label={renameItem?.type === 'folder' ? 'Folder Name' : 'File Name'}
            fullWidth
            value={renameName}
            onChange={(e) => setRenameName(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleRenameSubmit()}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameDialogOpen(false)}>Cancel</Button>
          <Button onClick={handleRenameSubmit} variant="contained" disabled={!renameName.trim()}>
            Rename
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteConfirmOpen} onClose={() => setDeleteConfirmOpen(false)}>
        <DialogTitle>Confirm Delete</DialogTitle>
        <DialogContent>
          <Typography>
            Delete {activeSelection.size} item{activeSelection.size !== 1 ? 's' : ''}? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteConfirmOpen(false)}>Cancel</Button>
          <Button onClick={handleDeleteConfirm} variant="contained" color="error">
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Properties Modal */}
      {propertiesOpen && selectedForProperties?.type === 'folder' && (
        <FolderPropertiesModal
          open={propertiesOpen}
          onClose={() => setPropertiesOpen(false)}
          folderData={selectedForProperties.item}
          onSave={async (folderId, updates) => {
            try {
              const response = await axios.put(`${API_BASE}/folder/${folderId}/link`, updates);

              // Show success message with cascade stats if available
              if (response.data?.data?.cascade_stats) {
                const stats = response.data.data.cascade_stats;
                showMessage?.(
                  `Properties applied successfully! Files updated: ${stats.files_updated}, Subfolders processed: ${stats.folders_processed}`,
                  'success'
                );
              } else {
                showMessage?.('Folder properties updated successfully', 'success');
              }

              setPropertiesOpen(false);
            } catch (err) {
              const errorMsg = err.response?.data?.message || err.message || 'Failed to update folder properties';
              showMessage?.(`Update failed: ${errorMsg}`, 'error');
            }
          }}
          onDelete={async (folderId) => {
            await axios.delete(`${API_BASE}/folder/${folderId}`);
            showMessage?.('Folder deleted', 'success');
          }}
        />
      )}
      {propertiesOpen && selectedForProperties?.type === 'file' && (
        <FilePropertiesModal
          open={propertiesOpen}
          onClose={() => setPropertiesOpen(false)}
          fileData={selectedForProperties.item}
          onSave={handleFilePropertiesSave}
          onDelete={handleFilePropertiesDelete}
          onReindex={handleFilePropertiesReindex}
        />
      )}

      {/* Image Lightbox / Editor */}
      {lightbox && (
        <ImageLightbox
          imageUrl={lightbox.url}
          imageName={lightbox.name}
          documentId={lightbox.documentId}
          onClose={() => setLightbox(null)}
          onImageEdited={() => {
            setLightbox(null);
            refreshData();
          }}
          initialEditMode={lightbox.editMode || false}
        />
      )}

      {/* Code Viewer Modal */}
      <CodeViewerModal
        open={!!codeViewer}
        onClose={() => setCodeViewer(null)}
        file={codeViewer?.file}
        onOpenInCodeEditor={handleOpenInCodeEditor}
      />

      {/* PDF Viewer Modal */}
      <PdfViewerModal
        open={!!pdfViewer}
        onClose={() => setPdfViewer(null)}
        file={pdfViewer?.file}
      />

      {/* Audio Player Modal */}
      <AudioPlayerModal
        open={!!audioPlayer}
        onClose={() => setAudioPlayer(null)}
        file={audioPlayer?.file}
      />

      {/* Upload Progress Overlay */}
      {uploadProgress && (
        <Box
          sx={{
            position: 'fixed',
            bottom: 24,
            right: 24,
            bgcolor: 'background.paper',
            borderRadius: 2,
            boxShadow: 6,
            px: 3,
            py: 2,
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            zIndex: 1300,
          }}
        >
          <CircularProgress size={24} />
          <Typography variant="body2">
            Uploading {uploadProgress.current}/{uploadProgress.total} file{uploadProgress.total !== 1 ? 's' : ''}...
          </Typography>
        </Box>
      )}

      {/* Hidden File Input for Upload */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileUpload}
      />
    </PageLayout>
  );
};

export default DocumentsPage;

