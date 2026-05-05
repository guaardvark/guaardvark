// frontend/src/pages/ImagesPage.jsx
// Version 3.0: Files/Documents API-based image management
// - Folders as window states (folded/minimized/maximized) - same as DocumentsPage
// - Desktop icons for folders and root-level images (thumbnails)
// - ImageThumbnailGrid inside folder windows
// - ImageLightbox for full-size viewing with prev/next
// - Tabs: Image Library + Image Gen
// - No upload action (images come from generation only)
// - Root path scoped to /Images/

import React, { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import {
  Box,
  Typography,
  Card,
  CardActionArea,
  CardContent,
  IconButton,
  Tooltip,
  Tabs,
  Tab,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Snackbar,
  Alert as MuiAlert,
  Chip,
  Grid,
  useTheme,
} from '@mui/material';
import {
  Apps as AppsIcon,
  GridView as GridViewIcon,
  ViewList as ViewListIcon,
  BrokenImage as BrokenImageIcon,
  MovieCreation as VideoIcon,
  FolderOutlined,
  PlayArrow as PlayArrowIcon,
  Download as DownloadIcon,
  Close as CloseIcon,
  Refresh as RefreshIcon,
  Videocam as VideocamIcon,
  OpenInNew as OpenInNewIcon,
} from '@mui/icons-material';
import { GuaardvarkLogo } from "../components/branding";
import ReactGridLayoutLib, { WidthProvider } from 'react-grid-layout';
import BatchImageGeneratorPage from './BatchImageGeneratorPage';
import VideoGeneratorPage from './VideoGeneratorPage';
import UpscalingPage from './UpscalingPage';
import FolderWindowWrapper from '../components/documents/FolderWindowWrapper';
import BreadcrumbNav from '../components/filesystem/BreadcrumbNav';
import ImageThumbnailGrid from '../components/images/ImageThumbnailGrid';
import ImagesContextMenu from '../components/images/ImagesContextMenu';
import ImageLightbox from '../components/images/ImageLightbox';
import PageLayout from '../components/layout/PageLayout';
import { useLayout } from '../contexts/LayoutContext';
import { useSnackbar } from '../components/common/SnackbarProvider';
import { ContextualLoader } from '../components/common/LoadingStates';
import { useStatus } from '../contexts/StatusContext';
import axios from 'axios';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

const WindowsGridLayout = WidthProvider(ReactGridLayoutLib);
const IMAGES_ROOT_PATH = '/Images';
const WINDOWS_STATE_ENDPOINT = '/api/state/images-windows';
const API_BASE = '/api/files';
const VIDEO_API_BASE = '/api/batch-video';

// Grid configuration (same as DocumentsPage)
const WINDOWS_COLS = 48;
const WINDOWS_ROW_HEIGHT = 10;
const WINDOWS_MARGIN = [2, 2];
const WINDOWS_PADDING = [4, 4];
const WINDOW_MIN_WIDTH = 15;
const WINDOW_MIN_HEIGHT = 20;
const MINIMIZED_MAX_WIDTH_PX = 350; // Max pixel width for minimized windows

// Desktop icon grid
const ICON_GRID_W = 150;
const ICON_GRID_H = 100;
const ICON_GRID_PAD = 20;

const ImagesPage = () => {
  const { gridSettings } = useLayout();
  const { RGL_WIDTH_PROP_PX } = gridSettings;
  const { showMessage } = useSnackbar();
  const { _activeModel, _isLoadingModel, _modelError } = useStatus();
  const theme = useTheme();

  // Tabs
  const [activeTab, setActiveTab] = useState(0);

  // Data
  const [rootFolders, setRootFolders] = useState([]);
  const [rootFiles, setRootFiles] = useState([]);
  const [loading, setLoading] = useState(true);

  // Window system
  const [windows, setWindows] = useState([]);
  const [windowLayout, setWindowLayout] = useState([]);
  const [windowColors, setWindowColors] = useState({});
  const [windowZIndex, setWindowZIndex] = useState({});
  const [maxZIndex, setMaxZIndex] = useState(0);
  const [iconPositions, setIconPositions] = useState({});
  const iconPositionsRef = useRef({}); // Always-current ref to avoid stale closures
  const stateLoadedRef = useRef(false); // Prevent saving before initial load

  // Context menu
  const [contextMenu, setContextMenu] = useState(null);
  const [contextMenuType, setContextMenuType] = useState('desktop');
  const [contextMenuItem, setContextMenuItem] = useState(null);

  // Clipboard
  const [clipboard, setClipboard] = useState(null);

  // Selection
  const [selectedItems, setSelectedItems] = useState(new Set());
  const [activeContext, setActiveContext] = useState({ type: 'desktop', path: IMAGES_ROOT_PATH });

  // Lightbox
  const [lightbox, setLightbox] = useState(null);

  // Dialogs
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameItem, setRenameItem] = useState(null);
  const [renameName, setRenameName] = useState('');
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  // View mode: 'icons' (desktop icons) or 'list' (list view)
  const [desktopViewMode, setDesktopViewMode] = useState('icons');

  // Feedback
  const [feedback, setFeedback] = useState({ open: false, message: '', severity: 'info' });

  // Video Library tab state
  const [videoBatches, setVideoBatches] = useState([]);
  const [videoBatchDetails, setVideoBatchDetails] = useState({});
  const [videoLoading, setVideoLoading] = useState(false);
  const [videoPlayer, setVideoPlayer] = useState(null); // { url, title, batchId, playlist, currentIndex }
  const [videoDeleteConfirm, setVideoDeleteConfirm] = useState(null);

  // Per-window refresh
  const [folderRefreshKeys, setFolderRefreshKeys] = useState({});

  // Per-window current path (for subfolder navigation within windows)
  const [windowPaths, setWindowPaths] = useState({});

  // Desktop selection
  const [desktopSelectionBox, setDesktopSelectionBox] = useState(null);
  const [isDesktopSelecting, setIsDesktopSelecting] = useState(false);

  // Refs
  const windowIdCounter = useRef(0);
  const windowsRef = useRef([]);
  const windowContainerRef = useRef(null);
  const preMinimizeSizes = useRef({});
  const isMountedRef = useRef(true);
  const desktopContentRef = useRef(null);
  const desktopItemRefs = useRef(new Map());
  const newFolderInputRef = useRef(null);
  const renameInputRef = useRef(null);
  const dragDropHandledRef = useRef(false);
  const isTogglingRef = useRef(false); // Skip onLayoutChange during programmatic minimize/expand
  const windowLayoutRef = useRef(windowLayout);

  // Keep refs in sync
  useEffect(() => { windowsRef.current = windows; }, [windows]);
  useEffect(() => { iconPositionsRef.current = iconPositions; }, [iconPositions]);
  useEffect(() => { windowLayoutRef.current = windowLayout; }, [windowLayout]);
  useEffect(() => {
    isMountedRef.current = true;
    return () => { isMountedRef.current = false; };
  }, []);

  // Compute visible grid bounds
  const getVisibleGridBounds = useCallback(() => {
    const el = windowContainerRef.current;
    if (!el) return { maxW: WINDOW_MIN_WIDTH * 2, maxH: WINDOW_MIN_HEIGHT * 2 };
    const rect = el.getBoundingClientRect();
    const [, my] = WINDOWS_MARGIN;
    const [, py] = WINDOWS_PADDING;
    const rowPitch = WINDOWS_ROW_HEIGHT + my;
    const visibleRows = Math.floor((rect.height - py * 2 + my) / rowPitch);
    return {
      maxW: WINDOWS_COLS,
      maxH: Math.max(WINDOW_MIN_HEIGHT, visibleRows),
    };
  }, []);

  const setDesktopContext = useCallback(() => {
    setActiveContext({ type: 'desktop', path: IMAGES_ROOT_PATH });
  }, []);

  // ──────────────────── Data Loading ────────────────────

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const response = await axios.get(`${API_BASE}/browse?path=${encodeURIComponent(IMAGES_ROOT_PATH)}&fields=light`);
      const data = response.data.data;
      const folders = data.folders || [];
      const files = (data.documents || []).map(f => ({
        ...f,
        filename: f.filename || f.name,
        itemType: 'file',
      }));

      setRootFolders(folders);
      setRootFiles(files);

      return { folders, files };
    } catch (err) {
      // If /Images/ doesn't exist yet, treat as empty
      setRootFolders([]);
      setRootFiles([]);
      return { folders: [], files: [] };
    } finally {
      setLoading(false);
    }
  }, []);

  // Initialize: load data + saved window state
  useEffect(() => {
    const init = async () => {
      const { folders } = await fetchData();

      // Load saved window states
      let savedState = null;
      try {
        const resp = await fetch(WINDOWS_STATE_ENDPOINT);
        if (resp.ok) savedState = await resp.json();
      } catch {
        // No saved window state yet — first run, defaults are fine
      }

      // Seed counter above any saved window IDs to avoid duplicates
      if (savedState?.windows) {
        for (const sw of savedState.windows) {
          const match = sw.id?.match(/^window-(\d+)$/);
          if (match) {
            windowIdCounter.current = Math.max(windowIdCounter.current, parseInt(match[1]) + 1);
          }
        }
      }

      // Create a window for each folder
      const newWindows = folders.map((folder) => {
        const savedWindow = savedState?.windows?.find(w => w.folderId === folder.id);
        const windowId = savedWindow?.id || `window-${windowIdCounter.current++}`;
        return {
          id: windowId,
          folderId: folder.id,
          folder,
          state: savedWindow?.state || 'folded',
        };
      });

      setWindows(newWindows);

      // Restore saved state
      if (savedState) {
        let sanitizedLayout = savedState.windowLayout ? [...savedState.windowLayout] : [];
        const activeIds = new Set(newWindows.filter(w => w.state !== 'folded').map(w => w.id));
        sanitizedLayout = sanitizedLayout.filter(l => activeIds.has(l.i));

        newWindows.forEach(w => {
          if (w.state === 'folded') return;
          const isMin = w.state === 'minimized';
          const existing = sanitizedLayout.find(l => l.i === w.id);
          if (!existing || existing.w < WINDOW_MIN_WIDTH || existing.h < (isMin ? 5 : WINDOW_MIN_HEIGHT)) {
            sanitizedLayout = sanitizedLayout.filter(l => l.i !== w.id);
            sanitizedLayout.push({
              i: w.id, x: 0, y: 0,
              w: WINDOW_MIN_WIDTH * 2,
              h: isMin ? 5 : WINDOW_MIN_HEIGHT * 2,
              minW: WINDOW_MIN_WIDTH,
              minH: isMin ? 5 : WINDOW_MIN_HEIGHT,
            });
          }
        });

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
    };
    init();
  }, [fetchData]);

  // Refresh data without page reload
  const refreshData = useCallback(async () => {
    try {
      const response = await axios.get(`${API_BASE}/browse?path=${encodeURIComponent(IMAGES_ROOT_PATH)}&fields=light`);
      if (!isMountedRef.current) return;

      const data = response.data.data;
      const folders = data.folders || [];
      const files = (data.documents || []).map(f => ({
        ...f,
        filename: f.filename || f.name,
        itemType: 'file',
      }));

      setRootFolders(folders);
      setRootFiles(files);

      // Update windows for new/removed folders
      setWindows(prev => {
        const existingIds = new Set(prev.map(w => w.folderId));
        const newFolders = folders.filter(f => !existingIds.has(f.id));
        const removedIds = new Set(
          prev.filter(w => !folders.find(f => f.id === w.folderId)).map(w => w.folderId)
        );

        const updatedWindows = prev.map(w => {
          const folder = folders.find(f => f.id === w.folderId);
          if (folder) return { ...w, folder };
          return w;
        }).filter(w => !removedIds.has(w.folderId));

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
        showMessage?.('Failed to refresh images', 'error');
      }
    }
  }, [showMessage]);

  // Listen for external updates (batch completion, cross-tab)
  useEffect(() => {
    const handleExternalUpdate = () => {
      refreshData();
      setFolderRefreshKeys(prev => {
        const updated = { ...prev };
        windowsRef.current.forEach(w => {
          updated[w.folderId] = (updated[w.folderId] || 0) + 1;
        });
        return updated;
      });
    };

    window.addEventListener('images-updated', handleExternalUpdate);
    window.addEventListener('documents-updated', handleExternalUpdate);

    return () => {
      window.removeEventListener('images-updated', handleExternalUpdate);
      window.removeEventListener('documents-updated', handleExternalUpdate);
    };
  }, [refreshData]);

  // ──────────────────── Video Library Data ────────────────────

  const fetchVideoBatches = useCallback(async () => {
    try {
      setVideoLoading(true);
      const res = await fetch(`${VIDEO_API_BASE}/list`);
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          const batches = data.data.batches || [];
          // Sort by start_time descending (newest first)
          batches.sort((a, b) => {
            const ta = a.start_time ? new Date(a.start_time).getTime() : 0;
            const tb = b.start_time ? new Date(b.start_time).getTime() : 0;
            return tb - ta;
          });
          setVideoBatches(batches);

          // Fetch details for any batch that may have produced videos —
          // cancelled/error batches often have partial results worth showing.
          const detailPromises = batches
            .filter(b => ['completed', 'partial', 'cancelled', 'error'].includes(b.status))
            .map(async (b) => {
              try {
                const statusRes = await fetch(`${VIDEO_API_BASE}/status/${b.batch_id}`);
                if (statusRes.ok) {
                  const statusData = await statusRes.json();
                  if (statusData.success) {
                    return { batch_id: b.batch_id, ...statusData.data };
                  }
                }
              } catch {
                // One batch's status fetch failed — skip it, others continue
              }
              return null;
            });
          const details = await Promise.all(detailPromises);
          const detailMap = {};
          details.forEach(d => {
            if (d) detailMap[d.batch_id] = d;
          });
          setVideoBatchDetails(detailMap);
        }
      }
    } catch (err) {
      console.error('Failed to fetch video batches:', err);
    } finally {
      setVideoLoading(false);
    }
  }, []);

  // Load video batches when switching to Video Library tab
  useEffect(() => {
    if (activeTab === 0) {
      fetchVideoBatches();
    }
  }, [activeTab, fetchVideoBatches]);

  const handlePlayVideo = useCallback((batchId, videoPath, startIndex) => {
    const details = videoBatchDetails[batchId];
    const playlist = (details?.results || []).filter(r => r.success && r.video_path);
    const idx = startIndex ?? playlist.findIndex(r => r.video_path === videoPath);
    const target = playlist[Math.max(0, idx)] || { video_path: videoPath };
    setVideoPlayer({
      url: `${VIDEO_API_BASE}/video/${batchId}/${target.video_path}`,
      title: target.video_path?.split('/').pop() || 'Video',
      batchId,
      playlist,
      currentIndex: Math.max(0, idx),
    });
  }, [videoBatchDetails]);

  const handleDownloadVideoBatch = useCallback((batchId) => {
    window.open(`${VIDEO_API_BASE}/download/${batchId}`, '_blank');
  }, []);

  const handleDeleteVideoBatch = useCallback(async (batchId) => {
    try {
      const res = await fetch(`${VIDEO_API_BASE}/delete/${batchId}`, { method: 'DELETE' });
      if (res.ok) {
        setVideoBatches(prev => prev.filter(b => b.batch_id !== batchId));
        setVideoBatchDetails(prev => {
          const next = { ...prev };
          delete next[batchId];
          return next;
        });
        showMessage?.('Video batch deleted', 'success');
      } else {
        showMessage?.('Failed to delete batch', 'error');
      }
    } catch (err) {
      showMessage?.('Failed to delete batch', 'error');
    }
    setVideoDeleteConfirm(null);
  }, [showMessage]);

  const _getVideoStatusColor = useCallback((status) => {
    switch (status) {
      case 'completed': return 'success';
      case 'running': case 'generating': return 'info';
      case 'error': case 'failed': return 'error';
      case 'partial': return 'warning';
      default: return 'default';
    }
  }, []);

  const formatVideoTime = useCallback((isoString) => {
    if (!isoString) return '';
    try {
      const d = new Date(isoString);
      return d.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      });
    } catch { return ''; }
  }, []);

  // Legacy batches predate display_name in metadata — pull a humane label
  // out of "VideoBatch_MM-DD-YYYY_NNN" instead of slicing it to "VideoBatch_0".
  const fallbackBatchName = useCallback((batchId) => {
    if (!batchId) return 'Batch';
    const m = batchId.match(/^VideoBatch_(\d{2})-(\d{2})-\d{4}_(\d+)$/);
    if (m) return `${m[1]}/${m[2]} #${m[3]}`;
    return batchId.replace(/^VideoBatch_/, '');
  }, []);

  // ──────────────────── Window State Persistence ────────────────────

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
          windows: windowsData.map(w => ({ id: w.id, folderId: w.folderId, state: w.state })),
          windowLayout: layout,
          windowColors: colors,
          windowZIndex: zIndex,
          maxZIndex: maxZ,
          iconPositions: positionsToSave,
          lastSaved: new Date().toISOString(),
        }),
      });
    } catch {
      // Persisting window layout is best-effort; don't bother the user
    }
  }, []);

  // ──────────────────── Window Management ────────────────────

  // Expand folder from icon to window
  const handleFolderExpand = useCallback((windowId) => {
    isTogglingRef.current = true;
    setWindows(prev => prev.map(w => w.id === windowId ? { ...w, state: 'maximized' } : w));

    const newWindows = windowsRef.current.map(w =>
      w.id === windowId ? { ...w, state: 'maximized' } : w
    );

    let newLayout = [...windowLayoutRef.current];
    const activeWindowIds = newWindows
      .filter(w => w.state === 'maximized' || w.state === 'minimized')
      .map(w => w.id);

    const bounds = getVisibleGridBounds();

    activeWindowIds.forEach(wId => {
      const existingLayout = newLayout.find(l => l.i === wId);
      if (!existingLayout) {
        const existingActiveCount = newLayout.length;
        const CASCADE_STEP = 4;
        const windowW = Math.min(WINDOW_MIN_WIDTH * 2, bounds.maxW);
        const windowH = Math.min(WINDOW_MIN_HEIGHT * 2, bounds.maxH - 2);
        const centerX = Math.max(0, Math.floor((bounds.maxW - windowW) / 3));
        const offsetX = Math.min(centerX + (existingActiveCount * CASCADE_STEP) % Math.max(1, bounds.maxW - windowW), bounds.maxW - windowW);
        const offsetY = Math.min(1 + existingActiveCount * CASCADE_STEP, Math.max(0, bounds.maxH - windowH));

        newLayout.push({
          i: wId, x: offsetX, y: offsetY, w: windowW,
          h: newWindows.find(w => w.id === wId)?.state === 'minimized' ? 5 : windowH,
          minW: WINDOW_MIN_WIDTH,
          minH: newWindows.find(w => w.id === wId)?.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT,
        });
      }
    });

    newLayout = newLayout.filter(l => activeWindowIds.includes(l.i));
    windowLayoutRef.current = newLayout;
    setWindowLayout(newLayout);

    const newMaxZIndex = maxZIndex + 1;
    const newZIndex = { ...windowZIndex, [windowId]: newMaxZIndex };
    setMaxZIndex(newMaxZIndex);
    setWindowZIndex(newZIndex);

    saveWindowState(newWindows, newLayout, windowColors, newZIndex, newMaxZIndex);
    requestAnimationFrame(() => { isTogglingRef.current = false; });
  }, [windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds]);

  // Close window (fold back)
  const handleWindowClose = useCallback((windowId) => {
    setWindows(prev => {
      const newWindows = prev.map(w =>
        w.id === windowId ? { ...w, state: 'folded' } : w
      );
      saveWindowState(newWindows, windowLayout, windowColors, windowZIndex, maxZIndex);
      return newWindows;
    });
  }, [windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Toggle minimize
  const handleToggleMinimize = useCallback((windowId) => {
    const currentWindows = windowsRef.current;
    const win = currentWindows.find(w => w.id === windowId);
    if (!win) return;

    // Prevent onLayoutChange from overwriting our programmatic layout update
    isTogglingRef.current = true;

    const newState = win.state === 'minimized' ? 'maximized' : 'minimized';
    const newWindows = currentWindows.map(w => w.id === windowId ? { ...w, state: newState } : w);
    setWindows(newWindows);

    // Use ref to get the latest layout (avoids stale closure from drag)
    const currentLayout = windowLayoutRef.current;
    // Calculate max grid columns for minimized width (350px cap)
    const colWidth = RGL_WIDTH_PROP_PX / WINDOWS_COLS;
    const maxMinimizedW = Math.max(WINDOW_MIN_WIDTH, Math.ceil(MINIMIZED_MAX_WIDTH_PX / colWidth));

    const newWindowLayout = currentLayout.map(item => {
      if (item.i !== windowId) return item;
      if (newState === 'minimized') {
        preMinimizeSizes.current[windowId] = { w: item.w, h: item.h };
        const cappedW = Math.min(item.w, maxMinimizedW);
        return { ...item, w: cappedW, h: 5, minH: 5, static: false };
      }
      const bounds = getVisibleGridBounds();
      const savedH = Math.min(preMinimizeSizes.current[windowId]?.h || WINDOW_MIN_HEIGHT * 2, bounds.maxH - 2);
      const savedW = preMinimizeSizes.current[windowId]?.w || WINDOW_MIN_WIDTH * 2;
      return { ...item, w: savedW, h: savedH, minH: WINDOW_MIN_HEIGHT, static: false };
    });
    windowLayoutRef.current = newWindowLayout;
    setWindowLayout(newWindowLayout);

    if (newState === 'maximized') {
      const newMaxZ = maxZIndex + 1;
      setMaxZIndex(newMaxZ);
      setWindowZIndex(prev => ({ ...prev, [windowId]: newMaxZ }));
      saveWindowState(newWindows, newWindowLayout, windowColors, { ...windowZIndex, [windowId]: newMaxZ }, newMaxZ);
    } else {
      saveWindowState(newWindows, newWindowLayout, windowColors, windowZIndex, maxZIndex);
    }

    // Allow onLayoutChange again after React processes the state updates
    requestAnimationFrame(() => { isTogglingRef.current = false; });
  }, [windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds, RGL_WIDTH_PROP_PX]);

  // Window color change
  const handleWindowColorChange = useCallback((windowId, color) => {
    const newWindowColors = { ...windowColors, [windowId]: color };
    setWindowColors(newWindowColors);
    saveWindowState(windows, windowLayout, newWindowColors, windowZIndex, maxZIndex);
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Window layout change (drag/resize)
  const handleWindowLayoutChange = useCallback((newLayout) => {
    // Skip when we just programmatically toggled minimize/expand
    if (isTogglingRef.current) return;

    const clamped = newLayout.map(item => ({
      ...item,
      w: Math.max(item.w, WINDOW_MIN_WIDTH),
      x: Math.max(0, Math.min(item.x, WINDOWS_COLS - Math.max(item.w, WINDOW_MIN_WIDTH))),
      minW: WINDOW_MIN_WIDTH,
    }));

    setWindowLayout(prev => {
      const incoming = {};
      for (const item of clamped) incoming[item.i] = item;
      const merged = prev.map(existing => {
        const updated = incoming[existing.i];
        if (updated) {
          return { ...existing, x: updated.x, y: updated.y, w: updated.w, h: updated.h, minW: updated.minW };
        }
        return existing;
      });
      for (const item of clamped) {
        if (!prev.find(p => p.i === item.i)) merged.push(item);
      }
      // Sync ref immediately so other callbacks see the latest layout
      windowLayoutRef.current = merged;
      return merged;
    });

    const currentWindows = windowsRef.current;
    const activeIds = new Set(currentWindows.filter(w => w.state !== 'folded').map(w => w.id));
    const filteredLayout = clamped.filter(l => activeIds.has(l.i));
    saveWindowState(currentWindows, filteredLayout, windowColors, windowZIndex, maxZIndex);
  }, [windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Handle drag/resize stop — react-grid-layout may not fire onLayoutChange for overlapping items
  const handleDragResizeStop = useCallback((layout) => {
    if (isTogglingRef.current) return;
    const clamped = layout.map(item => ({
      ...item,
      w: Math.max(item.w, WINDOW_MIN_WIDTH),
      x: Math.max(0, Math.min(item.x, WINDOWS_COLS - Math.max(item.w, WINDOW_MIN_WIDTH))),
      minW: WINDOW_MIN_WIDTH,
    }));
    setWindowLayout(prev => {
      const incoming = {};
      for (const item of clamped) incoming[item.i] = item;
      const merged = prev.map(existing => {
        const updated = incoming[existing.i];
        if (updated) {
          return { ...existing, x: updated.x, y: updated.y, w: updated.w, h: updated.h, minW: updated.minW };
        }
        return existing;
      });
      for (const item of clamped) {
        if (!prev.find(p => p.i === item.i)) merged.push(item);
      }
      windowLayoutRef.current = merged;
      return merged;
    });
    const currentWindows = windowsRef.current;
    const activeIds = new Set(currentWindows.filter(w => w.state !== 'folded').map(w => w.id));
    const filteredLayout = clamped.filter(l => activeIds.has(l.i));
    saveWindowState(currentWindows, filteredLayout, windowColors, windowZIndex, maxZIndex);
  }, [windowColors, windowZIndex, maxZIndex, saveWindowState]);

  // Bring to front
  const handleWindowClick = useCallback((windowId) => {
    const newMaxZIndex = maxZIndex + 1;
    setMaxZIndex(newMaxZIndex);
    setWindowZIndex(prev => ({ ...prev, [windowId]: newMaxZIndex }));
  }, [maxZIndex]);

  // Selection change
  const handleSelectionChange = useCallback((newSelection) => {
    setSelectedItems(newSelection);
  }, []);

  // Separate windows by state
  const foldedWindows = windows.filter(w => w.state === 'folded');
  const activeWindows = windows.filter(w => w.state === 'minimized' || w.state === 'maximized');

  // Arrange windows
  const handleArrangeWindows = useCallback(() => {
    const activeWindowList = windows.filter(w => w.state === 'maximized' || w.state === 'minimized');
    const n = activeWindowList.length;
    if (n === 0) return;

    let cols;
    if (n === 1) cols = 1;
    else if (n <= 4) cols = 2;
    else if (n <= 9) cols = 3;
    else cols = Math.ceil(Math.sqrt(n));

    const rows = Math.ceil(n / cols);
    const GAP = 2;
    const bounds = getVisibleGridBounds();
    const windowW = Math.min(Math.floor((bounds.maxW - GAP * (cols - 1)) / cols), bounds.maxW);
    const maxRowH = Math.min(
      Math.max(WINDOW_MIN_HEIGHT, Math.floor((bounds.maxH - GAP * (rows - 1)) / rows)),
      bounds.maxH
    );

    const newLayout = activeWindowList.map((win, index) => {
      const col = index % cols;
      const row = Math.floor(index / cols);
      return {
        i: win.id,
        x: col * (windowW + GAP),
        y: row * (maxRowH + GAP),
        w: windowW,
        h: win.state === 'minimized' ? 5 : maxRowH,
        minW: WINDOW_MIN_WIDTH,
        minH: win.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT,
      };
    });

    setWindowLayout(newLayout);
    saveWindowState(windows, newLayout, windowColors, windowZIndex, maxZIndex);
  }, [windows, windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds]);

  // Arrange desktop icons
  const handleArrangeIcons = useCallback(() => {
    const COLS = Math.max(1, Math.floor((RGL_WIDTH_PROP_PX - 40) / ICON_GRID_W));
    const newPositions = {};

    const allItems = [
      ...foldedWindows.map(w => ({ key: `folder-${w.folderId}` })),
      ...rootFiles.map(f => ({ key: `file-${f.id}` })),
    ];

    allItems.forEach((item, idx) => {
      newPositions[item.key] = {
        x: (idx % COLS) * ICON_GRID_W + ICON_GRID_PAD,
        y: Math.floor(idx / COLS) * ICON_GRID_H + ICON_GRID_PAD,
      };
    });

    setIconPositions(newPositions);

    const windowsData = windows.map(w => ({
      id: w.id, folderId: w.folderId, state: w.state,
    }));
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
    }).catch(() => {});
  }, [foldedWindows, rootFiles, windows, windowLayout, windowColors, windowZIndex, maxZIndex, RGL_WIDTH_PROP_PX]);

  // ──────────────────── Context Menu ────────────────────

  const handleContextMenu = useCallback((e, item = null, type = 'desktop') => {
    e.preventDefault();
    e.stopPropagation();
    if (type === 'folder' || type === 'folder-window') {
      setActiveContext({ type: 'folder', path: item?.path || IMAGES_ROOT_PATH, folderId: item?.id });
    } else {
      setDesktopContext();
    }
    setContextMenu({ top: e.clientY, left: e.clientX });
    setContextMenuType(type);
    setContextMenuItem(item);

    if (item) {
      const key = type === 'folder' ? `folder-${item.id}` : `file-${item.id}`;
      if (!selectedItems.has(key)) {
        setSelectedItems(new Set([key]));
      }
    }
  }, [selectedItems, setDesktopContext]);

  // ──────────────────── Folder Operations ────────────────────

  const handleNewFolder = useCallback(() => {
    setContextMenu(null);
    setNewFolderOpen(true);
  }, []);

  const handleCreateFolder = useCallback(async () => {
    if (!newFolderName.trim()) return;
    try {
      const parentPath = contextMenuItem?.path || IMAGES_ROOT_PATH;
      await axios.post(`${API_BASE}/folder`, {
        name: newFolderName,
        parent_path: parentPath,
      });
      showMessage?.(`Folder "${newFolderName}" created`, 'success');
      setNewFolderOpen(false);
      setNewFolderName('');
      await refreshData();
    } catch (err) {
      showMessage?.('Failed to create folder', 'error');
    }
  }, [newFolderName, contextMenuItem, showMessage, refreshData]);

  // ──────────────────── Clipboard Operations ────────────────────

  const handleCopy = useCallback(() => {
    setClipboard({ items: Array.from(selectedItems), operation: 'copy' });
    setContextMenu(null);
    showMessage?.(`Copied ${selectedItems.size} item(s)`, 'info');
  }, [selectedItems, showMessage]);

  const handleCut = useCallback(() => {
    setClipboard({ items: Array.from(selectedItems), operation: 'cut' });
    setContextMenu(null);
    showMessage?.(`Cut ${selectedItems.size} item(s)`, 'info');
  }, [selectedItems, showMessage]);

  const handlePaste = useCallback(async (targetOverride = null) => {
    setContextMenu(null);
    if (!clipboard || clipboard.items.length === 0) return;

    const contextTypeToUse = targetOverride?.type || contextMenuType;
    const contextItemToUse = targetOverride?.item || contextMenuItem;

    let targetPath = IMAGES_ROOT_PATH;
    if ((contextTypeToUse === 'folder-window' || contextTypeToUse === 'folder') && contextItemToUse) {
      targetPath = contextItemToUse.path;
    }

    try {
      for (const key of clipboard.items) {
        const [type, id] = key.split('-');
        if (type === 'folder') {
          const folder = windows.find(w => w.folderId === parseInt(id))?.folder;
          if (folder) {
            if (clipboard.operation === 'copy') {
              await axios.post(`${API_BASE}/folder`, {
                name: `${folder.name} (Copy)`,
                parent_path: targetPath,
              });
            } else {
              await axios.post(`${API_BASE}/folder/${id}/move`, {
                destination_path: targetPath,
              });
            }
          }
        } else {
          if (clipboard.operation === 'copy') {
            await axios.post(`${API_BASE}/document/${id}/copy`, {
              destination_path: targetPath,
            });
          } else {
            await axios.post(`${API_BASE}/document/${id}/move`, {
              destination_path: targetPath,
            });
          }
        }
      }
      showMessage?.(`${clipboard.operation === 'copy' ? 'Pasted' : 'Moved'} ${clipboard.items.length} item(s)`, 'success');
      setClipboard(null);

      await refreshData();
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
  }, [clipboard, windows, showMessage, contextMenuType, contextMenuItem, refreshData]);

  // ──────────────────── Delete ────────────────────

  const handleDelete = useCallback(() => {
    setContextMenu(null);
    if (selectedItems.size === 0) return;
    setDeleteConfirmOpen(true);
  }, [selectedItems]);

  const handleDeleteConfirm = useCallback(async () => {
    setDeleteConfirmOpen(false);
    if (selectedItems.size === 0) return;

    try {
      for (const key of selectedItems) {
        const [type, id] = key.split('-');
        if (type === 'folder') {
          await axios.delete(`${API_BASE}/folder/${id}`);
        } else {
          await axios.delete(`${API_BASE}/document/${id}`);
        }
      }
      showMessage?.(`Deleted ${selectedItems.size} item(s)`, 'success');
      setSelectedItems(new Set());
      await refreshData();
    } catch (err) {
      showMessage?.('Delete failed', 'error');
    }
  }, [selectedItems, showMessage, refreshData]);

  // ──────────────────── Rename ────────────────────

  const handleRename = useCallback(async () => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item) {
      if (selectedItems.size !== 1) return;
      const key = Array.from(selectedItems)[0];
      const [type, id] = key.split('-');
      if (type === 'folder') {
        const win = windows.find(w => w.folderId === parseInt(id));
        if (win) {
          setRenameItem({ type: 'folder', item: win.folder });
          setRenameName(win.folder.name);
          setRenameDialogOpen(true);
        }
      } else {
        const file = rootFiles.find(f => f.id === parseInt(id));
        if (file) {
          setRenameItem({ type: 'file', item: file });
          setRenameName(file.filename || file.name || '');
          setRenameDialogOpen(true);
        }
      }
    } else {
      setRenameItem({ type: contextMenuType === 'folder' || contextMenuType === 'folder-window' ? 'folder' : 'file', item });
      setRenameName(item.name || item.filename || '');
      setRenameDialogOpen(true);
    }
  }, [contextMenuItem, contextMenuType, selectedItems, windows, rootFiles]);

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
      await refreshData();
    } catch (err) {
      showMessage?.('Rename failed', 'error');
    }
  }, [renameItem, renameName, showMessage, refreshData]);

  // ──────────────────── Color Change (from context menu) ────────────────────

  const handleColorChange = useCallback((color) => {
    if (!contextMenuItem || (contextMenuType !== 'folder' && contextMenuType !== 'folder-window')) return;
    const win = windows.find(w => w.folderId === contextMenuItem.id);
    if (win) {
      handleWindowColorChange(win.id, color);
      setContextMenu(null);
    }
  }, [contextMenuItem, contextMenuType, windows, handleWindowColorChange]);

  // ──────────────────── Download ────────────────────

  const handleDownload = useCallback(() => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item || (contextMenuType !== 'file' && contextMenuType !== 'image')) return;

    const downloadUrl = `${API_BASE}/document/${item.id}/download?v=${item.updated_at || Date.now()}`;
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = item.filename || item.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showMessage?.(`Downloading ${item.filename || item.name}`, 'info');
  }, [contextMenuItem, contextMenuType, showMessage]);

  // ──────────────────── Lightbox ────────────────────

  const handleViewFullSize = useCallback(() => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item) return;

    const imageUrl = `${API_BASE}/document/${item.id}/download?v=${item.updated_at || Date.now()}`;
    setLightbox({
      url: imageUrl,
      name: item.filename || item.name,
      fileIndex: 0,
      fileList: [item],
    });
  }, [contextMenuItem]);

  const handleEditImage = useCallback(() => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item) return;

    const imageUrl = `${API_BASE}/document/${item.id}/download?v=${item.updated_at || Date.now()}`;
    setLightbox({
      url: imageUrl,
      name: item.filename || item.name,
      fileIndex: 0,
      fileList: [item],
      editMode: true,
    });
  }, [contextMenuItem]);

  const handleImageDoubleClick = useCallback((item, fileIndex, fileList) => {
    const imageUrl = `${API_BASE}/document/${item.id}/download?v=${item.updated_at || Date.now()}`;
    setLightbox({
      url: imageUrl,
      name: item.filename || item.name,
      fileIndex: fileIndex >= 0 ? fileIndex : 0,
      fileList: fileList || [item],
    });
  }, []);

  const handleDesktopImageDoubleClick = useCallback((file) => {
    const imageUrl = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
    // Navigate through all root-level files in lightbox
    const fileIndex = rootFiles.findIndex(f => f.id === file.id);
    setLightbox({
      url: imageUrl,
      name: file.filename || file.name,
      fileIndex: fileIndex >= 0 ? fileIndex : 0,
      fileList: rootFiles,
    });
  }, [rootFiles]);

  const handleLightboxPrev = useCallback(() => {
    if (!lightbox || lightbox.fileIndex <= 0) return;
    const newIndex = lightbox.fileIndex - 1;
    const file = lightbox.fileList[newIndex];
    setLightbox({
      ...lightbox,
      url: `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`,
      name: file.filename || file.name,
      fileIndex: newIndex,
    });
  }, [lightbox]);

  const handleLightboxNext = useCallback(() => {
    if (!lightbox || lightbox.fileIndex >= lightbox.fileList.length - 1) return;
    const newIndex = lightbox.fileIndex + 1;
    const file = lightbox.fileList[newIndex];
    setLightbox({
      ...lightbox,
      url: `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`,
      name: file.filename || file.name,
      fileIndex: newIndex,
    });
  }, [lightbox]);

  const handleLightboxDownload = useCallback(() => {
    if (!lightbox) return;
    const file = lightbox.fileList[lightbox.fileIndex];
    if (!file) return;
    const link = document.createElement('a');
    link.href = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
    link.download = file.filename || file.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightbox]);

  // ──────────────────── Select All ────────────────────

  const handleSelectAll = useCallback(() => {
    const allKeys = new Set([
      ...foldedWindows.map(f => `folder-${f.folderId}`),
      ...rootFiles.map(file => `file-${file.id}`),
    ]);
    setSelectedItems(allKeys);
  }, [foldedWindows, rootFiles]);

  // ──────────────────── Sort By (context menu) ────────────────────

  const handleSortBy = useCallback((field) => {
    // Re-arrange icon positions based on sort field
    const sortedFolders = [...foldedWindows];
    const sortedFiles = [...rootFiles];

    if (field === 'name') {
      sortedFolders.sort((a, b) => (a.folder.name || '').localeCompare(b.folder.name || ''));
      sortedFiles.sort((a, b) => (a.filename || '').localeCompare(b.filename || ''));
    } else if (field === 'date') {
      sortedFolders.sort((a, b) => new Date(b.folder.updated_at || 0) - new Date(a.folder.updated_at || 0));
      sortedFiles.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
    } else if (field === 'size') {
      sortedFiles.sort((a, b) => (b.size || 0) - (a.size || 0));
    }

    const COLS = Math.max(1, Math.floor((RGL_WIDTH_PROP_PX - 40) / ICON_GRID_W));
    const newPositions = {};
    const allItems = [
      ...sortedFolders.map(w => ({ key: `folder-${w.folderId}` })),
      ...sortedFiles.map(f => ({ key: `file-${f.id}` })),
    ];
    allItems.forEach((item, idx) => {
      newPositions[item.key] = {
        x: (idx % COLS) * ICON_GRID_W + ICON_GRID_PAD,
        y: Math.floor(idx / COLS) * ICON_GRID_H + ICON_GRID_PAD,
      };
    });
    setIconPositions(newPositions);
    saveWindowState(windows, windowLayout, windowColors, windowZIndex, maxZIndex, newPositions);
  }, [foldedWindows, rootFiles, windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, RGL_WIDTH_PROP_PX]);

  // ──────────────────── Drop Handler ────────────────────

  const handleDrop = useCallback(async (e, targetFolder = null) => {
    e.preventDefault();
    e.stopPropagation();

    try {
      const data = e.dataTransfer?.getData('text/plain') || e.dataTransfer?.getData('application/json');
      if (!data) {
        // No data means this might be icon repositioning - don't set flag
        return;
      }

      const itemsToMove = JSON.parse(data);

      // Determine drop target
      let destinationPath = IMAGES_ROOT_PATH;
      let isDroppingOnFolder = false;

      if (targetFolder) {
        destinationPath = targetFolder.path;
        isDroppingOnFolder = true;
      } else {
        const dropTarget = e.currentTarget || e.target;
        const folderElement = dropTarget.closest('[data-folder-path]');
        if (folderElement) {
          destinationPath = folderElement.getAttribute('data-folder-path');
          isDroppingOnFolder = true;
        } else {
          const folderCard = dropTarget.closest('[data-folder-id]');
          if (folderCard) {
            const folderId = parseInt(folderCard.getAttribute('data-folder-id'));
            const win = windows.find(w => w.folderId === folderId);
            if (win) {
              destinationPath = win.folder.path;
              isDroppingOnFolder = true;
            }
          }
        }
      }

      // Check if this is just icon repositioning on the desktop (not dropping onto a folder)
      const sourcePath = itemsToMove[0]?.path;
      const sourceParentPath = sourcePath ? sourcePath.substring(0, sourcePath.lastIndexOf('/')) || IMAGES_ROOT_PATH : IMAGES_ROOT_PATH;

      if (!isDroppingOnFolder && destinationPath === IMAGES_ROOT_PATH && sourceParentPath === IMAGES_ROOT_PATH) {
        // This is icon repositioning on desktop - don't handle here, let handleIconDrop handle it
        return;
      }

      dragDropHandledRef.current = true;

      let successCount = 0;
      for (const item of itemsToMove) {
        try {
          if (item.itemType === 'folder' || item.type === 'folder') {
            if (item.path === destinationPath) continue;
            if (destinationPath.startsWith(item.path + '/')) continue;
            await axios.post(`${API_BASE}/folder/${item.id}/move`, { destination_path: destinationPath });
            successCount++;
          } else {
            const fileParentPath = item.path?.substring(0, item.path.lastIndexOf('/')) || IMAGES_ROOT_PATH;
            if (fileParentPath === destinationPath) continue;
            await axios.post(`${API_BASE}/document/${item.id}/move`, { destination_path: destinationPath });
            successCount++;
          }
        } catch (itemErr) {
          // Continue with other items
        }
      }

      if (successCount > 0) {
        showMessage?.(`Moved ${successCount} item(s)`, 'success');
        setSelectedItems(new Set());
        await refreshData();
        setFolderRefreshKeys(prev => {
          const updated = { ...prev };
          windowsRef.current.forEach(w => {
            updated[w.folderId] = (updated[w.folderId] || 0) + 1;
          });
          return updated;
        });
      }
    } catch (err) {
      showMessage?.('Move failed', 'error');
    } finally {
      setTimeout(() => { dragDropHandledRef.current = false; }, 100);
    }
  }, [windows, showMessage, refreshData]);

  // ──────────────────── Desktop Selection Box ────────────────────

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
      setSelectedItems(newSelection);
      return newBox;
    });
  }, [desktopSelectionBox, isDesktopSelecting]);

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

  // ──────────────────── Keyboard Shortcuts ────────────────────

  useEffect(() => {
    const handleKeyDown = (e) => {
      // Only handle when Image Library tab is active
      if (activeTab !== 0) return;
      if (e.defaultPrevented) return;
      const targetTag = e.target?.tagName;
      if (targetTag === 'INPUT' || targetTag === 'TEXTAREA' || e.target?.isContentEditable) return;

      const key = e.key.toLowerCase();

      if (e.ctrlKey && key === 'a') {
        if (activeContext.type === 'desktop') {
          e.preventDefault();
          handleSelectAll();
        }
        return;
      }
      if (e.ctrlKey && key === 'c' && selectedItems.size > 0) {
        e.preventDefault();
        handleCopy();
        return;
      }
      if (e.ctrlKey && key === 'x' && selectedItems.size > 0) {
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
      if (key === 'delete' && selectedItems.size > 0) {
        e.preventDefault();
        handleDelete();
        return;
      }
      if (key === 'escape') {
        e.preventDefault();
        if (lightbox) {
          setLightbox(null);
        } else {
          setSelectedItems(new Set());
          setContextMenu(null);
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeTab, activeContext, selectedItems, handleCopy, handleCut, handlePaste, handleDelete, handleSelectAll, lightbox]);

  // ──────────────────── Icon Positions ────────────────────

  const resolvedIconPositions = useMemo(() => {
    const positions = {};
    const occupied = new Set();
    const COLS = Math.max(1, Math.floor((RGL_WIDTH_PROP_PX - 40) / ICON_GRID_W));

    const allItems = [
      ...foldedWindows.map(w => ({ key: `folder-${w.folderId}`, id: w.folderId, isFile: false })),
      ...rootFiles.map(f => ({ key: `file-${f.id}`, id: f.id, isFile: true })),
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

  // Snap to grid
  const snapToGrid = useCallback((rawX, rawY, movingKey, currentPositions) => {
    let col = Math.round((rawX - ICON_GRID_PAD) / ICON_GRID_W);
    let row = Math.round((rawY - ICON_GRID_PAD) / ICON_GRID_H);
    col = Math.max(0, col);
    row = Math.max(0, row);

    const occupied = new Set();
    for (const [k, pos] of Object.entries(currentPositions)) {
      if (k === movingKey) continue;
      const c = Math.round((pos.x - ICON_GRID_PAD) / ICON_GRID_W);
      const r = Math.round((pos.y - ICON_GRID_PAD) / ICON_GRID_H);
      occupied.add(`${c},${r}`);
    }

    if (!occupied.has(`${col},${row}`)) {
      return { x: col * ICON_GRID_W + ICON_GRID_PAD, y: row * ICON_GRID_H + ICON_GRID_PAD };
    }

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

    return { x: col * ICON_GRID_W + ICON_GRID_PAD, y: row * ICON_GRID_H + ICON_GRID_PAD };
  }, []);

  // Icon drag repositioning
  const handleIconDrop = useCallback((e, key) => {
    if (dragDropHandledRef.current) return;

    const container = e.currentTarget.closest('[data-desktop-container]');
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const iconWidth = 130;
    const iconHeight = 80;

    const rawX = Math.max(0, e.clientX - rect.left - iconWidth / 2);
    const rawY = Math.max(0, e.clientY - rect.top - iconHeight / 2);

    const snapped = snapToGrid(rawX, rawY, key, resolvedIconPositions);

    const newIconPositions = { ...iconPositions, [key]: snapped };
    setIconPositions(newIconPositions);

    const windowsData = windows.map(w => ({
      id: w.id, folderId: w.folderId, state: w.state,
    }));
    saveWindowState(windowsData, windowLayout, windowColors, windowZIndex, maxZIndex, newIconPositions);
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, snapToGrid, resolvedIconPositions, iconPositions]);

  // ──────────────────── Drag Over Folder Highlight ────────────────────

  const [dragOverFolderId, setDragOverFolderId] = useState(null);

  // ──────────────────── Render ────────────────────

  if (loading) {
    return (
      <PageLayout title="Images">
        <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 400 }}>
          <ContextualLoader loading message="Loading images..." showProgress={false} inline />
        </Box>
      </PageLayout>
    );
  }

  return (
    <PageLayout
      title="Media"
      variant="grid"
      noPadding
      actions={activeTab === 0 ? (
        <>
          <Tooltip title={desktopViewMode === 'icons' ? 'List View' : 'Icon View'}>
            <IconButton
              size="small"
              sx={{ opacity: 0.6 }}
              onClick={() => setDesktopViewMode(prev => prev === 'icons' ? 'list' : 'icons')}
            >
              {desktopViewMode === 'icons' ? <ViewListIcon fontSize="small" /> : <GridViewIcon fontSize="small" />}
            </IconButton>
          </Tooltip>
          {desktopViewMode === 'icons' && (
            <Tooltip title="Arrange Icons">
              <IconButton size="small" sx={{ opacity: 0.6 }} onClick={handleArrangeIcons}>
                <GridViewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          {activeWindows.length > 0 && (
            <Tooltip title="Arrange Windows">
              <IconButton size="small" sx={{ opacity: 0.6 }} onClick={handleArrangeWindows}>
                <AppsIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
        </>
      ) : null}
    >
      {/* Tab bar */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}>
        <Tabs value={activeTab} onChange={(e, v) => setActiveTab(v)}>
          <Tab label="Media Library" />
          <Tab label="Image Gen" />
          <Tab label="Video Gen" />
          <Tab label="Upscaling" />
        </Tabs>
      </Box>

      {/* Media Library Tab */}
      {activeTab === 0 && (<>
        <Box sx={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
          {/* Toolbar */}
          <Box sx={{
            display: 'flex', alignItems: 'center', gap: 1, p: 1,
            borderBottom: 1, borderColor: 'divider', flexShrink: 0,
            zIndex: 10, position: 'relative', bgcolor: 'background.paper',
          }}>
            <Typography variant="subtitle2" sx={{ mr: 1 }}>
              {rootFolders.length} folder{rootFolders.length !== 1 ? 's' : ''}, {rootFiles.length} image{rootFiles.length !== 1 ? 's' : ''}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
          </Box>

          {/* Desktop area */}
          <Box ref={windowContainerRef} sx={{ flex: 1, minHeight: 0, position: 'relative', overflow: 'hidden' }}>
            {/* Desktop area with folded folder icons and image thumbnails */}
            <Box
              data-desktop-container
              ref={desktopContentRef}
              sx={{ position: 'absolute', inset: 0, overflow: 'auto', zIndex: 1, p: 2 }}
              onContextMenu={(e) => handleContextMenu(e, null, 'desktop')}
              onMouseDown={handleDesktopSelectionMouseDown}
              onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
              onDrop={(e) => handleDrop(e, null)}
            >
              {/* Empty state */}
              {foldedWindows.length === 0 && rootFiles.length === 0 && activeWindows.length === 0 && (
                <Box sx={{
                  position: 'absolute', inset: 0,
                  display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center',
                  pointerEvents: 'none', opacity: 0.5, gap: 1,
                }}>
                  <GuaardvarkLogo size={64} sx={{ opacity: 0.4 }} />
                  <Typography variant="h6" color="text.disabled">
                    No images yet
                  </Typography>
                  <Typography variant="body2" color="text.disabled">
                    Generate images from the Image Gen tab or right-click to create a folder
                  </Typography>
                </Box>
              )}

              {/* Desktop list view */}
              {desktopViewMode === 'list' && (foldedWindows.length > 0 || rootFiles.length > 0) && (
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                  {foldedWindows.map((win) => {
                    const key = `folder-${win.folderId}`;
                    const isSelected = selectedItems.has(key);
                    const itemCount = (win.folder.document_count || 0) + (win.folder.subfolder_count || 0);
                    return (
                      <Box
                        key={win.id}
                        draggable
                        onDragStart={(e) => {
                          setDesktopContext();
                          e.dataTransfer.setData('text/plain', JSON.stringify([{
                            id: win.folderId, itemType: 'folder',
                            name: win.folder.name, path: win.folder.path,
                          }]));
                          e.dataTransfer.effectAllowed = 'move';
                        }}
                        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                        onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverFolderId(win.folderId); }}
                        onDragLeave={(e) => { e.stopPropagation(); if (!e.currentTarget.contains(e.relatedTarget)) setDragOverFolderId(null); }}
                        onDrop={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverFolderId(null); handleDrop(e, win.folder); }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.ctrlKey || e.metaKey) {
                            const newSel = new Set(selectedItems);
                            if (newSel.has(key)) newSel.delete(key); else newSel.add(key);
                            setSelectedItems(newSel);
                          } else { setSelectedItems(new Set([key])); }
                        }}
                        onDoubleClick={(e) => { e.stopPropagation(); handleFolderExpand(win.id); }}
                        onContextMenu={(e) => handleContextMenu(e, win.folder, 'folder')}
                        sx={{
                          display: 'flex', alignItems: 'center', gap: 1.5, px: 1.5, py: 0.75,
                          cursor: 'pointer', userSelect: 'none', borderRadius: 0.5,
                          backgroundColor: dragOverFolderId === win.folderId
                            ? theme.palette.action.hover
                            : isSelected ? theme.palette.action.selected : 'transparent',
                          '&:hover': { backgroundColor: theme.palette.action.hover },
                          borderBottom: `1px solid ${theme.palette.divider}`,
                        }}
                      >
                        <FolderOutlined
                          sx={{ fontSize: 22, color: windowColors[win.id] || theme.palette.primary.main }}
                        />
                        <Typography variant="body2" noWrap sx={{ flex: 1 }}>
                          {win.folder.name}
                        </Typography>
                        {itemCount > 0 && (
                          <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
                            {itemCount} item{itemCount !== 1 ? 's' : ''}
                          </Typography>
                        )}
                      </Box>
                    );
                  })}
                  {rootFiles.map((file) => {
                    const key = `file-${file.id}`;
                    const isSelected = selectedItems.has(key);
                    return (
                      <Box
                        key={key}
                        draggable
                        onDragStart={(e) => {
                          setDesktopContext();
                          e.dataTransfer.setData('text/plain', JSON.stringify([{
                            id: file.id, itemType: 'file',
                            filename: file.filename, name: file.filename, path: file.path,
                          }]));
                          e.dataTransfer.effectAllowed = 'move';
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.ctrlKey || e.metaKey) {
                            const newSel = new Set(selectedItems);
                            if (newSel.has(key)) newSel.delete(key); else newSel.add(key);
                            setSelectedItems(newSel);
                          } else { setSelectedItems(new Set([key])); }
                        }}
                        onDoubleClick={(e) => { e.stopPropagation(); handleDesktopImageDoubleClick(file); }}
                        onContextMenu={(e) => handleContextMenu(e, file, 'image')}
                        sx={{
                          display: 'flex', alignItems: 'center', gap: 1.5, px: 1.5, py: 0.75,
                          cursor: 'pointer', userSelect: 'none', borderRadius: 0.5,
                          backgroundColor: isSelected ? theme.palette.action.selected : 'transparent',
                          '&:hover': { backgroundColor: theme.palette.action.hover },
                          borderBottom: `1px solid ${theme.palette.divider}`,
                        }}
                      >
                        <Box
                          component="img"
                          src={`${API_BASE}/thumbnail?path=${encodeURIComponent(file.path)}`}
                          alt={file.filename}
                          loading="lazy"
                          sx={{ width: 28, height: 28, objectFit: 'cover', borderRadius: 0.5, flexShrink: 0 }}
                          onError={(e) => { e.target.style.display = 'none'; }}
                        />
                        <Typography variant="body2" noWrap sx={{ flex: 1 }}>
                          {file.filename}
                        </Typography>
                        {file.size && (
                          <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
                            {formatFileSize(file.size)}
                          </Typography>
                        )}
                      </Box>
                    );
                  })}
                </Box>
              )}

              {/* Desktop selection box (icon view only) */}
              {desktopViewMode === 'icons' && isDesktopSelecting && desktopSelectionBox && (
                <Box sx={{
                  position: 'absolute',
                  left: Math.min(desktopSelectionBox.startX, desktopSelectionBox.currentX),
                  top: Math.min(desktopSelectionBox.startY, desktopSelectionBox.currentY),
                  width: Math.abs(desktopSelectionBox.currentX - desktopSelectionBox.startX),
                  height: Math.abs(desktopSelectionBox.currentY - desktopSelectionBox.startY),
                  bgcolor: (t) => t.palette.primary.main + '1A',
                  border: '1px solid',
                  borderColor: 'primary.main',
                  pointerEvents: 'none',
                  zIndex: 1000,
                }} />
              )}

              {/* Folder icons (icon view only) */}
              {desktopViewMode === 'icons' && foldedWindows.map((win) => {
                const key = `folder-${win.folderId}`;
                const pos = resolvedIconPositions[key] || { x: ICON_GRID_PAD, y: ICON_GRID_PAD };
                const isDragOver = dragOverFolderId === win.folderId;
                const itemCount = (win.folder.document_count || 0) + (win.folder.subfolder_count || 0);

                return (
                  <Box
                    key={win.id}
                    ref={(el) => {
                      if (el) desktopItemRefs.current.set(key, el);
                      else desktopItemRefs.current.delete(key);
                    }}
                    data-folder-id={win.folderId}
                    data-folder-path={win.folder.path}
                    sx={{
                      position: 'absolute',
                      left: `${pos.x}px`,
                      top: `${pos.y}px`,
                      width: 100,
                      cursor: 'grab',
                      '&.desktop-item-card': {},
                      '&:active': { cursor: 'grabbing' },
                    }}
                    className="desktop-item-card"
                    draggable
                    onDragStart={(e) => {
                      setDesktopContext();
                      e.dataTransfer.setData('text/plain', JSON.stringify([{
                        id: win.folderId,
                        itemType: 'folder',
                        name: win.folder.name,
                        path: win.folder.path,
                      }]));
                      e.dataTransfer.effectAllowed = 'move';
                    }}
                    onDragEnd={(e) => handleIconDrop(e, key)}
                    onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                    onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverFolderId(win.folderId); }}
                    onDragLeave={(e) => {
                      e.stopPropagation();
                      if (!e.currentTarget.contains(e.relatedTarget)) setDragOverFolderId(null);
                    }}
                    onDrop={(e) => {
                      e.preventDefault(); e.stopPropagation();
                      setDragOverFolderId(null);
                      handleDrop(e, win.folder);
                    }}
                  >
                    <Card sx={{
                      cursor: 'pointer',
                      border: (selectedItems.has(key) || isDragOver) ? '2px solid' : '1px solid',
                      borderColor: isDragOver ? 'success.main' : selectedItems.has(key) ? 'primary.main' : 'divider',
                      bgcolor: isDragOver ? 'action.hover' : selectedItems.has(key) ? 'action.selected' : 'background.paper',
                      transition: 'all 0.2s ease-in-out',
                      boxShadow: isDragOver ? 6 : undefined,
                      transform: isDragOver ? 'scale(1.05)' : undefined,
                      '&:hover': { boxShadow: 3, bgcolor: 'action.hover' },
                    }}>
                      <CardActionArea
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.ctrlKey || e.metaKey) {
                            const newSelection = new Set(selectedItems);
                            if (newSelection.has(key)) newSelection.delete(key);
                            else newSelection.add(key);
                            setSelectedItems(newSelection);
                          } else {
                            setSelectedItems(new Set([key]));
                          }
                        }}
                        onDoubleClick={(e) => { e.stopPropagation(); handleFolderExpand(win.id); }}
                        onContextMenu={(e) => handleContextMenu(e, win.folder, 'folder')}
                        sx={{ p: 2, textAlign: 'center' }}
                      >
                        <CardContent sx={{ p: 0 }}>
                          <FolderOutlined
                            sx={{ fontSize: 48, color: windowColors[win.id] || theme.palette.primary.main, mb: '4px' }}
                          />
                          <Tooltip title={win.folder.name} enterDelay={600} placement="bottom">
                            <Typography variant="body2" noWrap>
                              {win.folder.name}
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

              {/* Root-level image thumbnails (icon view only) */}
              {desktopViewMode === 'icons' && rootFiles.map((file) => {
                const key = `file-${file.id}`;
                const isSelected = selectedItems.has(key);
                const pos = resolvedIconPositions[key] || { x: ICON_GRID_PAD, y: ICON_GRID_PAD };
                const thumbnailUrl = `${API_BASE}/thumbnail?path=${encodeURIComponent(file.path)}`;

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
                      width: 130,
                      cursor: 'grab',
                      '&.desktop-item-card': {},
                      '&:active': { cursor: 'grabbing' },
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
                    onDragEnd={(e) => handleIconDrop(e, key)}
                    onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                    onDrop={(e) => {
                      e.preventDefault(); e.stopPropagation();
                      const folderCard = e.target.closest('[data-folder-id]');
                      if (folderCard) {
                        const folderId = parseInt(folderCard.getAttribute('data-folder-id'));
                        const win = windows.find(w => w.folderId === folderId);
                        if (win) { handleDrop(e, win.folder); return; }
                      }
                      handleDrop(e, null);
                    }}
                  >
                    <Card sx={{
                      cursor: 'pointer',
                      border: isSelected ? '2px solid' : '1px solid',
                      borderColor: isSelected ? 'primary.main' : 'divider',
                      bgcolor: isSelected ? 'action.selected' : 'background.paper',
                      transition: 'all 0.2s ease-in-out',
                      '&:hover': { boxShadow: 3, bgcolor: 'action.hover' },
                    }}>
                      <CardActionArea
                        onClick={(e) => {
                          e.stopPropagation();
                          if (e.ctrlKey || e.metaKey) {
                            const newSelection = new Set(selectedItems);
                            if (newSelection.has(key)) newSelection.delete(key);
                            else newSelection.add(key);
                            setSelectedItems(newSelection);
                          } else {
                            setSelectedItems(new Set([key]));
                          }
                        }}
                        onDoubleClick={(e) => {
                          e.stopPropagation();
                          handleDesktopImageDoubleClick(file);
                        }}
                        onContextMenu={(e) => handleContextMenu(e, file, 'image')}
                        sx={{ p: 1, textAlign: 'center' }}
                      >
                        <CardContent sx={{ p: 0 }}>
                          {/* Thumbnail with border frame */}
                          <Box sx={{
                            width: '100%',
                            height: 90,
                            borderRadius: 0.5,
                            overflow: 'hidden',
                            backgroundColor: theme.palette.action.hover,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            border: `1px solid ${theme.palette.divider}`,
                            position: 'relative',
                            mb: 0.5,
                          }}>
                            <Box
                              component="img"
                              src={thumbnailUrl}
                              alt={file.filename}
                              loading="lazy"
                              sx={{
                                maxWidth: '100%',
                                maxHeight: '100%',
                                objectFit: 'cover',
                                width: '100%',
                                height: '100%',
                              }}
                              onError={(e) => {
                                e.target.style.display = 'none';
                                if (e.target.nextSibling) e.target.nextSibling.style.display = 'flex';
                              }}
                            />
                            <Box sx={{
                              display: 'none', alignItems: 'center', justifyContent: 'center',
                              width: '100%', height: '100%', position: 'absolute', top: 0, left: 0,
                            }}>
                              <BrokenImageIcon sx={{ fontSize: 32, color: 'text.secondary' }} />
                            </Box>
                          </Box>
                          <Tooltip title={file.filename} enterDelay={600} placement="bottom">
                            <Typography variant="caption" noWrap sx={{
                              display: 'block', fontSize: '0.65rem', lineHeight: 1.2,
                            }}>
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
                position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 2,
                '& .react-grid-layout': { pointerEvents: 'none' },
                '& .react-grid-item': { pointerEvents: 'none' },
                '& .react-grid-item > div': { pointerEvents: 'auto' },
                '& .react-resizable-handle': { pointerEvents: 'auto', zIndex: 10 },
                '& .folder-window-drag-handle': { position: 'relative', zIndex: 20 },
                '& .react-resizable-handle-se': {
                  width: '20px !important', height: '20px !important',
                  bottom: '0 !important', right: '0 !important', cursor: 'se-resize',
                },
                '& .react-resizable-handle-sw': {
                  width: '20px !important', height: '20px !important',
                  bottom: '0 !important', left: '0 !important', cursor: 'sw-resize',
                },
                '& .react-resizable-handle-ne': {
                  width: '20px !important', height: '20px !important',
                  top: '0 !important', right: '0 !important', cursor: 'ne-resize',
                },
                '& .react-resizable-handle-nw': {
                  width: '20px !important', height: '20px !important',
                  top: '0 !important', left: '0 !important', cursor: 'nw-resize',
                },
                '& .react-resizable-handle-s': {
                  width: '100% !important', height: '12px !important',
                  bottom: '0 !important', left: '0 !important', cursor: 's-resize',
                },
                '& .react-resizable-handle-n': {
                  width: '100% !important', height: '6px !important',
                  top: '0 !important', left: '0 !important', cursor: 'n-resize',
                },
                '& .react-resizable-handle-e': {
                  width: '12px !important', height: '100% !important',
                  top: '0 !important', right: '0 !important', cursor: 'e-resize',
                },
                '& .react-resizable-handle-w': {
                  width: '12px !important', height: '100% !important',
                  top: '0 !important', left: '0 !important', cursor: 'w-resize',
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
                  onDragStop={(layout) => handleDragResizeStop(layout)}
                  onResizeStop={(layout) => handleDragResizeStop(layout)}
                  resizeHandles={["se", "sw", "ne", "nw", "s", "n", "e", "w"]}
                >
                  {activeWindows.map((win) => {
                    const layoutItem = windowLayout.find(l => l.i === win.id) || {
                      i: win.id, x: 0, y: 0,
                      w: WINDOW_MIN_WIDTH * 2,
                      h: win.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT * 2,
                      minW: WINDOW_MIN_WIDTH,
                      minH: win.state === 'minimized' ? 5 : WINDOW_MIN_HEIGHT,
                    };

                    return (
                      <div
                        key={win.id}
                        style={{
                          zIndex: windowZIndex[win.id] || 0,
                          pointerEvents: 'auto',
                        }}
                        onClick={() => handleWindowClick(win.id)}
                      >
                        <FolderWindowWrapper
                          title={win.folder.name}
                          isMinimized={win.state === 'minimized'}
                          onToggleMinimize={() => handleToggleMinimize(win.id)}
                          onClose={() => handleWindowClose(win.id)}
                          onDrop={(e) => handleDrop(e, win.folder)}
                          onDragOver={(e) => e.preventDefault()}
                          {...layoutItem}
                        >
                          {/* Breadcrumb navigation for subfolder hierarchy */}
                          {win.state !== 'minimized' && (
                            <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
                              <BreadcrumbNav
                                currentPath={windowPaths[win.id] || win.folder.path}
                                onNavigate={(path) => setWindowPaths(prev => ({ ...prev, [win.id]: path }))}
                              />
                            </Box>
                          )}
                          <ImageThumbnailGrid
                            folder={win.folder}
                            currentPath={windowPaths[win.id] || win.folder.path}
                            onNavigateToPath={(path) => setWindowPaths(prev => ({ ...prev, [win.id]: path }))}
                            viewMode={desktopViewMode === 'list' ? 'list' : 'grid'}
                            selectedItems={selectedItems}
                            onSelectionChange={handleSelectionChange}
                            onContextMenu={handleContextMenu}
                            onDragStart={() => {}}
                            onFolderOpen={(folderId) => {
                              const targetWin = windows.find(w => w.folderId === folderId);
                              if (targetWin) handleFolderExpand(targetWin.id);
                            }}
                            onDrop={(e) => handleDrop(e, win.folder)}
                            onImageDoubleClick={handleImageDoubleClick}
                            refreshKey={folderRefreshKeys[win.folderId] || 0}
                          />
                        </FolderWindowWrapper>
                      </div>
                    );
                  })}
                </WindowsGridLayout>
              </Box>
            )}
          </Box>
        </Box>

        {/* Video Batches Section — stacked thumbnails within Media Library */}
        {/* (still inside activeTab === 0 conditional) */}
        {videoBatches.length > 0 && (
          <Box sx={{ px: 2, pb: 2 }}>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1.5 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600, display: 'flex', alignItems: 'center', gap: 1 }}>
                <VideocamIcon sx={{ fontSize: 20 }} />
                Videos
                <Chip label={videoBatches.length} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
              </Typography>
              <Box sx={{ display: 'flex', gap: 0.5 }}>
                <Button size="small" variant="outlined" startIcon={<VideoIcon />} onClick={() => window.open('/video', '_self')}>
                  Generate
                </Button>
                <IconButton size="small" onClick={fetchVideoBatches} disabled={videoLoading}>
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </Box>
            </Box>
            <Grid container spacing={2}>
              {videoBatches.map((batch) => {
                const details = videoBatchDetails[batch.batch_id];
                const results = details?.results?.filter(r => r.success && r.video_path) || [];
                const firstResult = results[0];
                const videoCount = batch.completed_videos ?? 0;
                const displayName = batch.display_name || fallbackBatchName(batch.batch_id);

                return (
                  <Grid item xs={6} sm={4} md={3} lg={2} key={batch.batch_id}>
                    <Box
                      onClick={() => {
                        if (firstResult) {
                          handlePlayVideo(batch.batch_id, firstResult.video_path);
                        }
                      }}
                      sx={{
                        cursor: firstResult ? 'pointer' : 'default',
                        position: 'relative',
                        borderRadius: 2,
                        overflow: 'visible',
                        transition: 'transform 0.2s, box-shadow 0.2s',
                        '&:hover': {
                          transform: 'translateY(-3px)',
                          boxShadow: 4,
                          '& .batch-play': { opacity: 1 },
                        },
                      }}
                    >
                      <Box sx={{ position: 'relative', aspectRatio: '16/9' }}>
                        {/* Stack layers */}
                        {videoCount > 2 && (
                          <Box sx={{
                            position: 'absolute', top: -5, left: 5, right: -5, bottom: 5,
                            bgcolor: 'grey.800', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                          }} />
                        )}
                        {videoCount > 1 && (
                          <Box sx={{
                            position: 'absolute', top: -2, left: 2, right: -2, bottom: 2,
                            bgcolor: 'grey.850', borderRadius: 1.5, border: 1, borderColor: 'grey.700',
                          }} />
                        )}
                        {/* Main thumbnail */}
                        <Box sx={{
                          position: 'relative', width: '100%', height: '100%',
                          bgcolor: 'grey.900', borderRadius: 1.5, overflow: 'hidden',
                          border: 1, borderColor: 'grey.700',
                        }}>
                          {firstResult?.thumbnail_path ? (
                            <Box
                              component="img"
                              src={`${VIDEO_API_BASE}/video/${batch.batch_id}/${firstResult.thumbnail_path}`}
                              alt={displayName}
                              sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => { e.target.style.display = 'none'; }}
                            />
                          ) : (
                            <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                              <VideocamIcon sx={{ fontSize: 32, color: 'grey.600' }} />
                            </Box>
                          )}
                          {/* Hover overlay */}
                          <Box className="batch-play" sx={{
                            position: 'absolute', inset: 0,
                            bgcolor: 'rgba(0,0,0,0.45)',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            opacity: 0, transition: 'opacity 0.2s',
                          }}>
                            <PlayArrowIcon sx={{ fontSize: 36, color: 'white' }} />
                          </Box>
                          {/* Count badge */}
                          <Chip
                            label={`${videoCount}`}
                            size="small"
                            sx={{
                              position: 'absolute', top: 4, right: 4,
                              height: 18, fontSize: '0.6rem', minWidth: 24,
                              bgcolor: 'rgba(0,0,0,0.7)', color: 'white',
                              '& .MuiChip-label': { px: 0.5 },
                            }}
                          />
                          {batch.status !== 'completed' && (
                            <Chip label={batch.status} size="small"
                              color={batch.status === 'error' ? 'error' : 'info'}
                              sx={{ position: 'absolute', bottom: 4, left: 4, height: 16, fontSize: '0.55rem' }}
                            />
                          )}
                        </Box>
                      </Box>
                      <Box sx={{ pt: 0.5, px: 0.25 }}>
                        <Typography variant="caption" noWrap sx={{ fontWeight: 500, display: 'block', fontSize: '0.7rem' }}>
                          {displayName}
                        </Typography>
                        {batch.start_time && (
                          <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.6rem' }}>
                            {formatVideoTime(batch.start_time)}
                          </Typography>
                        )}
                      </Box>
                      {/* Action icons on hover */}
                      <Box sx={{
                        position: 'absolute', top: -8, right: -8,
                        display: 'flex', gap: 0.25,
                        opacity: 0, transition: 'opacity 0.2s',
                        '.MuiBox-root:hover > &': { opacity: 1 },
                      }}>
                        <IconButton size="small" onClick={(e) => { e.stopPropagation(); handleDownloadVideoBatch(batch.batch_id); }}
                          sx={{ bgcolor: 'background.paper', boxShadow: 2, p: 0.25, '&:hover': { bgcolor: 'primary.dark' } }}>
                          <DownloadIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                        <IconButton size="small" onClick={(e) => { e.stopPropagation(); setVideoDeleteConfirm(batch.batch_id); }}
                          sx={{ bgcolor: 'background.paper', boxShadow: 2, p: 0.25, '&:hover': { bgcolor: 'error.dark' } }}>
                          <CloseIcon sx={{ fontSize: 14 }} />
                        </IconButton>
                      </Box>
                    </Box>
                  </Grid>
                );
              })}
            </Grid>
          </Box>
        )}
      </>)}

      {/* Image Gen Tab */}
      {activeTab === 1 && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 2, p: 1 }}>
          <BatchImageGeneratorPage embedded />
        </Box>
      )}

      {/* Video Gen Tab */}
      {activeTab === 2 && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <VideoGeneratorPage embedded />
        </Box>
      )}

      {activeTab === 3 && (
        <Box sx={{ flexGrow: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <UpscalingPage embedded />
        </Box>
      )}

      {/* Context Menu */}
      <ImagesContextMenu
        anchorPosition={contextMenu}
        onClose={() => {
          setContextMenu(null);
          setContextMenuItem(null);
          setContextMenuType('desktop');
        }}
        onNewFolder={handleNewFolder}
        onCut={handleCut}
        onCopy={handleCopy}
        onPaste={handlePaste}
        onDelete={handleDelete}
        onRename={handleRename}
        onDownload={handleDownload}
        onViewFullSize={handleViewFullSize}
        onEdit={handleEditImage}
        onColorChange={handleColorChange}
        onSelectAll={handleSelectAll}
        onSortBy={handleSortBy}
        onArrangeIcons={handleArrangeIcons}
        onArrangeWindows={handleArrangeWindows}
        hasClipboard={Boolean(clipboard)}
        hasSelection={selectedItems.size > 0}
        contextType={contextMenuType}
        selectedItem={contextMenuItem}
        folderColor={contextMenuItem && (contextMenuType === 'folder' || contextMenuType === 'folder-window')
          ? windowColors[windows.find(w => w.folderId === contextMenuItem.id)?.id]
          : null}
      />

      {/* Lightbox */}
      {lightbox && (
        <ImageLightbox
          imageUrl={lightbox.url}
          imageName={lightbox.name}
          documentId={lightbox.fileList[lightbox.fileIndex]?.id}
          initialEditMode={lightbox.editMode || false}
          onClose={() => setLightbox(null)}
          onPrev={handleLightboxPrev}
          onNext={handleLightboxNext}
          onDownload={handleLightboxDownload}
          onImageEdited={() => {
            setLightbox(null);
            refreshData();
          }}
          hasPrev={lightbox.fileIndex > 0}
          hasNext={lightbox.fileIndex < lightbox.fileList.length - 1}
        />
      )}

      {/* New Folder Dialog */}
      <Dialog
        open={newFolderOpen}
        onClose={() => setNewFolderOpen(false)}
        TransitionProps={{
          onEntered: () => { newFolderInputRef.current?.focus(); },
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
          },
        }}
      >
        <DialogTitle>Rename {renameItem?.type === 'folder' ? 'Folder' : 'Image'}</DialogTitle>
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
            Delete {selectedItems.size} item{selectedItems.size !== 1 ? 's' : ''}? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteConfirmOpen(false)}>Cancel</Button>
          <Button onClick={handleDeleteConfirm} variant="contained" color="error">
            Delete
          </Button>
        </DialogActions>
      </Dialog>

      {/* Feedback Snackbar */}
      <Snackbar
        open={feedback.open}
        autoHideDuration={4000}
        onClose={() => setFeedback(prev => ({ ...prev, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <MuiAlert
          elevation={6}
          variant="filled"
          onClose={() => setFeedback(prev => ({ ...prev, open: false }))}
          severity={feedback.severity}
        >
          {feedback.message}
        </MuiAlert>
      </Snackbar>

      {/* Video Player Dialog with Prev/Next + Thumbnail Strip */}
      <Dialog
        open={!!videoPlayer}
        onClose={() => setVideoPlayer(null)}
        maxWidth="md"
        fullWidth
        PaperProps={{ sx: { bgcolor: 'grey.900', borderRadius: 2, overflow: 'hidden' } }}
      >
        {videoPlayer && (() => {
          const { playlist, currentIndex, batchId } = videoPlayer;
          const hasPrev = currentIndex > 0;
          const hasNext = currentIndex < playlist.length - 1;
          const navigateTo = (idx) => {
            const r = playlist[idx];
            setVideoPlayer(prev => ({
              ...prev,
              url: `${VIDEO_API_BASE}/video/${batchId}/${r.video_path}`,
              currentIndex: idx,
              title: r.video_path?.split('/').pop() || `Video ${idx + 1}`,
            }));
          };
          return (
            <>
              <DialogTitle sx={{ color: 'grey.300', display: 'flex', justifyContent: 'space-between', alignItems: 'center', py: 1, px: 2 }}>
                <Typography variant="subtitle2" noWrap sx={{ flex: 1, mr: 2, color: 'grey.400' }}>
                  {videoPlayer.title}
                  <Typography component="span" variant="caption" sx={{ ml: 1, color: 'grey.600' }}>
                    {currentIndex + 1} / {playlist.length}
                  </Typography>
                </Typography>
                <IconButton size="small" onClick={() => setVideoPlayer(null)} sx={{ color: 'grey.400' }}>
                  <CloseIcon />
                </IconButton>
              </DialogTitle>
              <DialogContent sx={{ p: 0, position: 'relative' }}>
                <Box sx={{ position: 'relative', bgcolor: 'black' }}>
                  <video
                    key={videoPlayer.url}
                    src={videoPlayer.url}
                    controls
                    autoPlay
                    loop
                    style={{ width: '100%', display: 'block', maxHeight: '65vh' }}
                  />
                  {/* Prev overlay */}
                  {hasPrev && (
                    <IconButton
                      onClick={() => navigateTo(currentIndex - 1)}
                      sx={{
                        position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
                        bgcolor: 'rgba(0,0,0,0.5)', color: 'white', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' },
                      }}
                    >
                      <PlayArrowIcon sx={{ transform: 'rotate(180deg)' }} />
                    </IconButton>
                  )}
                  {/* Next overlay */}
                  {hasNext && (
                    <IconButton
                      onClick={() => navigateTo(currentIndex + 1)}
                      sx={{
                        position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
                        bgcolor: 'rgba(0,0,0,0.5)', color: 'white', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' },
                      }}
                    >
                      <PlayArrowIcon />
                    </IconButton>
                  )}
                </Box>

                {/* Thumbnail strip */}
                {playlist.length > 1 && (
                  <Box sx={{
                    display: 'flex', gap: 0.5, px: 1, py: 1,
                    overflowX: 'auto', bgcolor: 'grey.900',
                    '&::-webkit-scrollbar': { height: 4 },
                    '&::-webkit-scrollbar-thumb': { bgcolor: 'grey.700', borderRadius: 2 },
                  }}>
                    {playlist.map((r, idx) => {
                      const thumbUrl = r.thumbnail_path
                        ? `${VIDEO_API_BASE}/video/${batchId}/${r.thumbnail_path}`
                        : null;
                      return (
                        <Box
                          key={r.item_id || idx}
                          onClick={() => navigateTo(idx)}
                          sx={{
                            flexShrink: 0, width: 80, height: 45,
                            borderRadius: 1, overflow: 'hidden', cursor: 'pointer',
                            border: 2, borderColor: idx === currentIndex ? 'primary.main' : 'transparent',
                            opacity: idx === currentIndex ? 1 : 0.6,
                            transition: 'opacity 0.2s, border-color 0.2s',
                            '&:hover': { opacity: 1 },
                            bgcolor: 'grey.800',
                          }}
                        >
                          {thumbUrl ? (
                            <Box component="img" src={thumbUrl} alt={`Video ${idx + 1}`}
                              sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                              onError={(e) => { e.target.style.display = 'none'; }}
                            />
                          ) : (
                            <Box sx={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                              <VideocamIcon sx={{ fontSize: 16, color: 'grey.500' }} />
                            </Box>
                          )}
                        </Box>
                      );
                    })}
                  </Box>
                )}
              </DialogContent>
              <DialogActions sx={{ justifyContent: 'space-between', px: 2, py: 1 }}>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button size="small" disabled={!hasPrev} onClick={() => navigateTo(currentIndex - 1)}>
                    Previous
                  </Button>
                  <Button size="small" disabled={!hasNext} onClick={() => navigateTo(currentIndex + 1)}>
                    Next
                  </Button>
                </Box>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button size="small" onClick={() => window.open(videoPlayer.url, '_blank')} startIcon={<OpenInNewIcon />}>
                    Open
                  </Button>
                  <Button size="small" onClick={() => {
                    const a = document.createElement('a');
                    a.href = videoPlayer.url;
                    a.download = videoPlayer.title;
                    a.click();
                  }} startIcon={<DownloadIcon />}>
                    Download
                  </Button>
                </Box>
              </DialogActions>
            </>
          );
        })()}
      </Dialog>

      {/* Video Delete Confirmation Dialog */}
      <Dialog open={Boolean(videoDeleteConfirm)} onClose={() => setVideoDeleteConfirm(null)}>
        <DialogTitle>Delete Video Batch</DialogTitle>
        <DialogContent>
          <Typography>
            Are you sure you want to delete this video batch? This action cannot be undone.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setVideoDeleteConfirm(null)}>Cancel</Button>
          <Button
            onClick={() => handleDeleteVideoBatch(videoDeleteConfirm)}
            variant="contained"
            color="error"
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
};

function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export default ImagesPage;
