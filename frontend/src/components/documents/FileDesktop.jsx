// frontend/src/components/documents/FileDesktop.jsx
// The desktop the Files page and the Media page's library share: the folders
// under `rootPath` as windows in three states, the files beside them, and
// everything done to them (drag, drop and import, cut/copy/paste, rename,
// delete, colours, properties, sorting, the viewers).
// - Folded: folder icon on the desktop
// - Minimized: title bar only
// - Maximized: a window showing the folder's contents
// The page around it supplies the header (FileDesktopActions drives this
// component through its ref) and, for media, what a click on a file opens
// and the extra file-menu items.

import React, { useState, useCallback, useEffect, useRef, useMemo, forwardRef, useImperativeHandle } from "react";
import { Box, Typography, Card, CardActionArea, CardContent, IconButton, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, Button, TextField, useTheme, CircularProgress } from "@mui/material";
import { BrandLogo } from "../branding";
import {
  Apps as AppsIcon,
  GridView as GridViewIcon,
  FolderOutlined,
  Code,
  ArrowDownward as ArrowDownwardIcon,
  ArrowUpward as ArrowUpwardIcon,
  UploadFile as UploadFileIcon,
  ViewComfy as ViewComfyIcon,
  ViewList as ViewListIcon,
  ViewModule as ViewModuleIcon,
} from "@mui/icons-material";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  getFileIcon,
  getFileIconSmall,
  getItemKey,
  FolderIndexIndicator,
  formatDate,
  isImageFile,
  isVideoFile,
  isCodeFile,
  isPdfFile,
  isDocxFile,
  isAudioFile,
} from "./fileUtils.jsx";
import ImageLightbox from "../images/ImageLightbox";
import CodeViewerModal from "./CodeViewerModal";
import PdfViewerModal from "./PdfViewerModal";
import DocxViewerModal from "./DocxViewerModal";
import AudioPlayerModal from "./AudioPlayerModal";
import MediaPreviewOverlay from "./MediaPreviewOverlay";
import ReactGridLayoutLib, { WidthProvider } from 'react-grid-layout/legacy';
import FolderWindow from "./FolderWindow";
import DocumentsContextMenu from "./DocumentsContextMenu";
import { menuTargetPath, folderLabel } from "./menuTarget";
import {
  snapshotDrop,
  collectDroppedFiles,
  prunedNote,
  dragCarriesFiles,
  ensureFolderPath,
  filesFolderApi,
} from "../../utils/droppedFiles";
import FilePropertiesModal from "../modals/FilePropertiesModal";
import FolderPropertiesModal from "../modals/FolderPropertiesModal";
import EntityFilesPanel from "./EntityFilesPanel";
import { entityFilesFilter } from "../../utils/entityLinks";
import { useLayout } from "../../contexts/LayoutContext";
import { useSnackbar } from "../common/SnackbarProvider";
import { triggerIndexing, indexBulk } from "../../api/indexingService";
import { reviewRepoScope } from "../../api/documentService";
import axios from 'axios';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import { useUiTranslation, translate } from '../../i18n';
import useDeferredClick from '../../hooks/useDeferredClick';
import DesktopToolbar from "./DesktopToolbar";
import { FileThumb, ListFileCells, ThumbFileBody, ThumbFolderBody, useFileMetaLine } from "./DesktopCards";
import {
  ALL_KINDS, FILES_VIEW_DEFAULTS, LIST_ROW_HEIGHT, MEDIA_KINDS, geometryOf, kindOf, layoutKeyOf,
  matchesSearch, resolvePlaces, sortItems,
} from "./desktopView";

const WindowsGridLayout = WidthProvider(ReactGridLayoutLib);
const API_BASE = '/api/files'; // Use relative path so Vite proxy handles CORS

// What the Files page offers that a media library does not.
export const FILES_FEATURES = Object.freeze({
  indexing: true,       // index status, Index / Index Contents, re-index from Properties
  bulkImport: true,     // Bulk Import Here on a folder
  codeEditor: true,     // Open in Code Editor
  reviewWithAgent: true, // the live repository's Review with Agent
  entityPanel: true,    // ?entity= files panel
  openParam: true,      // ?open=<folder path> opens that folder's window
});
const NO_EVENTS = [];

/** A stored folder path in one form: no leading or trailing slash, '/' for the top. */
const normalizePath = (path) => String(path || '/').replace(/^\/+|\/+$/g, '') || '/';

// Grid configuration for maximized/minimized windows
// With ~860px container, 48 cols, margin [2,2]: colWidth≈16px, colPitch≈18px
const WINDOWS_COLS = 48;
const WINDOWS_ROW_HEIGHT = 10;
const WINDOWS_MARGIN = [2, 2];
const WINDOWS_PADDING = [4, 4];
const WINDOW_MIN_WIDTH = 15;  // ~270px
const WINDOW_MIN_HEIGHT = 20; // ~240px
const MINIMIZED_MAX_WIDTH_PX = 350; // Max pixel width for minimized windows

// Shared frozen Set so windows with no selection all get the same reference —
// avoids spurious re-renders and `.has` on undefined.
const EMPTY_SELECTION = new Set();

// Statuses the orange dot stands for (see IndexStatusIndicator in fileUtils).
const INDEX_IN_PROGRESS = new Set(['INDEXING', 'PENDING']);
// After an Index action the listing is re-read on this cadence while
// something is still in progress, for at most this many reads.
const INDEX_POLL_INTERVAL_MS = 3000;
const INDEX_POLL_MAX_TICKS = 40;
// Listings read per poll tick: root, the open windows, the indexed folders
// and their still-unindexed subfolders.
const INDEX_POLL_MAX_LISTINGS = 12;

// The live repository mount and everything under it is read-only: the
// server lists them with source_type "live_repo" and string ids ("repo:…"),
// and delete, rename and index take integer ids. The context menu hides
// those actions for such an item; the keyboard paths filter the keys.
const isLiveRepoItem = (item) => item?.source_type === 'live_repo';
const isLiveRepoKey = (key) => /^(folder|file)-repo:/.test(String(key));
const writableKeys = (keys) => Array.from(keys).filter((key) => !isLiveRepoKey(key));

// What the bulk index call did, from its queued and skipped counts. The
// server skips a document whose content hash has not changed since it was
// indexed, so "everything skipped" is the already-indexed case.
export const indexingMessage = (queued, skipped, folderCount) => {
  if (queued === 0) {
    if (skipped === 0) return translate('documents.indexing.nothing');
    return skipped === 1
      ? translate('documents.indexing.alreadyIndexedOne')
      : translate('documents.indexing.alreadyIndexedAll', { count: skipped });
  }
  const documents = translate('documents.indexing.documentCount', { count: queued });
  const folders = translate('documents.indexing.folderCount', { count: folderCount });
  if (folderCount > 0) {
    return skipped > 0
      ? translate('documents.indexing.startedFromFoldersSkipped', { documents, folders, skipped })
      : translate('documents.indexing.startedFromFolders', { documents, folders });
  }
  return skipped > 0
    ? translate('documents.indexing.startedSkipped', { documents, skipped })
    : translate('documents.indexing.started', { documents });
};

/**
 * @param {Object} props
 * @param {string} [props.rootPath] - stored path of the folder the desktop shows ('/' for everything)
 * @param {string} props.stateEndpoint - /api/state key the window layout and icon places are kept under
 * @param {Object} [props.features] - FILES_FEATURES, or a subset for a library that is not the Files page
 * @param {Object} [props.view] - mode (icons, thumbnails, list), scope (folders, all), kinds, sort; see desktopView.js
 * @param {(patch) => void} [props.onViewChange] - the page keeps the view; this changes part of it
 * @param {boolean} [props.mediaOnly] - show only pictures, video and sound, and the folders holding them
 * @param {(file, siblings) => void} [props.onFileClick] - a plain click on a file (after the double-click window)
 * @param {(file) => Array<{label: string, onClick: Function}>} [props.fileMenuActions] - first items of a file's menu
 * @param {boolean} [props.keyboardSuspended] - a dialog of the page's own is open; leave the keys alone
 * @param {string[]} [props.refreshOnEvents] - window events that mean the listing changed elsewhere
 */
const FileDesktop = forwardRef(({
  rootPath = '/',
  stateEndpoint,
  features = FILES_FEATURES,
  view = FILES_VIEW_DEFAULTS,
  onViewChange,
  mediaOnly = false,
  onFileClick,
  fileMenuActions,
  keyboardSuspended = false,
  refreshOnEvents = NO_EVENTS,
}, ref) => {
  const { t } = useUiTranslation();
  const { gridSettings } = useLayout();
  const { RGL_WIDTH_PROP_PX } = gridSettings;
  const { showMessage } = useSnackbar();
  const theme = useTheme();
  const viewMode = view.mode;
  const showAll = view.scope === 'all';
  const geom = geometryOf(view.mode);
  const layoutKey = layoutKeyOf(view);
  const metaLine = useFileMetaLine();
  const isRoot = useCallback((path) => normalizePath(path) === normalizePath(rootPath), [rootPath]);
  // Slashes stay as they are, so the Files page asks for path=/ as it always has.
  const rootQuery = `path=${encodeURIComponent(rootPath).replace(/%2F/gi, '/')}&fields=media${mediaOnly ? '&media_only=1' : ''}`;
  const { schedule: scheduleFileClick, cancel: cancelFileClick } = useDeferredClick();

  // All folders are represented as windows with different states
  const [windows, setWindows] = useState([]); // Array of { id, folderId, folder, state: 'folded'|'minimized'|'maximized' }
  const [windowLayout, setWindowLayout] = useState([]); // Layout for maximized/minimized windows
  const [windowColors, setWindowColors] = useState({});
  // Colours for folders below the top level, keyed by folder id (they have no window).
  const [subfolderColors, setSubfolderColors] = useState({});
  const subfolderColorsRef = useRef({});
  const [windowZIndex, setWindowZIndex] = useState({});
  const [maxZIndex, setMaxZIndex] = useState(0);
  // Per-surface selection: `desktop` is the root, each window.id is its own slot.
  // Keeping selections isolated per window means clicking in window B doesn't wipe window A.
  const [selectedItemsByContext, setSelectedItemsByContext] = useState({ desktop: new Set() });
  const [activeContext, setActiveContext] = useState({ type: 'desktop', path: rootPath });
  const [loading, setLoading] = useState(true);
  const [rootFiles, setRootFiles] = useState([]); // Files at root level (desktop)
  // Every file below the root, for the All items scope, and the search text.
  const [allFiles, setAllFiles] = useState([]);
  const [allLoading, setAllLoading] = useState(false);
  const [search, setSearch] = useState('');
  const showAllRef = useRef(showAll);
  showAllRef.current = showAll;
  const loadAllFilesRef = useRef(null);
  const [iconPositions, setIconPositions] = useState({}); // Icon positions: { 'folder-123': { x: 100, y: 200 }, 'file-456': { x: 300, y: 50 } }
  const iconPositionsRef = useRef({}); // Always-current ref to avoid stale closures
  // Icon places for each placed view and scope (layoutKeyOf); iconPositions
  // holds those on screen. Saved as iconPositions (icons and folders, the
  // places the desktop has always kept) and layoutPositions (the others).
  const layoutPositionsRef = useRef({});
  const activeLayoutRef = useRef(layoutKey);
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

  // Keep a ref of windows so event listeners don't need re-registration.
  // Assigned during render: the grid reports its first layout from its own
  // effect, which runs before this component's effects, and that report saves
  // the window list read from here.
  windowsRef.current = windows;

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

  // Another view or scope shows its own places.
  useEffect(() => {
    if (activeLayoutRef.current === layoutKey) return;
    layoutPositionsRef.current[activeLayoutRef.current] = iconPositionsRef.current;
    activeLayoutRef.current = layoutKey;
    const placed = layoutPositionsRef.current[layoutKey] || {};
    iconPositionsRef.current = placed;
    setIconPositions(placed);
  }, [layoutKey]);

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
  // Actions the surface that opened the menu offers, e.g. opening a subfolder in place.
  const [contextMenuExtras, setContextMenuExtras] = useState(null);
  const [clipboard, setClipboard] = useState(null);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [newFolderParent, setNewFolderParent] = useState(rootPath);
  // Folder the next file-picker import lands in; set by whoever opens the picker.
  const uploadTargetRef = useRef(rootPath);
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameItem, setRenameItem] = useState(null);
  const [renameName, setRenameName] = useState('');
  const [propertiesOpen, setPropertiesOpen] = useState(false);
  const [selectedForProperties, setSelectedForProperties] = useState(null);
  const [uploadProgress, setUploadProgress] = useState(null); // { current: N, total: M } or null
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [lightbox, setLightbox] = useState(null); // { url, name, documentId, editMode }
  const [mediaPreview, setMediaPreview] = useState(null); // { file, siblings }
  const [codeViewer, setCodeViewer] = useState(null); // { file } for CodeViewerModal
  const [pdfViewer, setPdfViewer] = useState(null); // { file } for PdfViewerModal
  const [audioPlayer, setAudioPlayer] = useState(null); // { file } for AudioPlayerModal
  const [docxViewer, setDocxViewer] = useState(null); // { file } for DocxViewerModal
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const entityFilter = features.entityPanel ? entityFilesFilter(searchParams) : null;
  const [dragOverFolderId, setDragOverFolderId] = useState(null); // Folder ID being dragged over
  const [desktopFileDragOver, setDesktopFileDragOver] = useState(false);
  const fileInputRef = useRef(null);
  const dragDropHandledRef = useRef(false); // Track if drop was handled to prevent repositioning
  const newFolderInputRef = useRef(null);
  const renameInputRef = useRef(null);

  // Load folders and files, initialize folders as folded windows
  useEffect(() => {
    const loadFoldersAndFiles = async () => {
      try {
        setLoading(true);
        const response = await axios.get(`${API_BASE}/browse?${rootQuery}`);
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
          const stateResponse = await fetch(stateEndpoint);
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
          if (savedState.subfolderColors && typeof savedState.subfolderColors === 'object') {
            setSubfolderColors(savedState.subfolderColors);
            subfolderColorsRef.current = savedState.subfolderColors;
          }
          if (savedState.windowZIndex) setWindowZIndex(savedState.windowZIndex);
          if (savedState.maxZIndex) setMaxZIndex(savedState.maxZIndex);
          layoutPositionsRef.current = {
            ...(savedState.layoutPositions && typeof savedState.layoutPositions === 'object' ? savedState.layoutPositions : {}),
            'icons:folders': savedState.iconPositions || {},
          };
          const placed = layoutPositionsRef.current[activeLayoutRef.current] || {};
          setIconPositions(placed);
          iconPositionsRef.current = placed;
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
      const response = await axios.get(`${API_BASE}/browse?${rootQuery}`);

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

        const finalWindows = [...updatedWindows, ...newWindows];
        // Prune any stale icon positions for items no longer present (quality + prevents ghost icons)
        const validKeys = new Set([
          ...finalWindows.map((w) => `folder-${w.folderId}`),
          ...files.map((f) => getItemKey(f, 'file')),
        ]);
        // The root listing names the folders scope's items; All items keeps its places.
        if (activeLayoutRef.current.endsWith(':folders')) {
          setIconPositions((prev) => {
            const next = {};
            Object.keys(prev).forEach((k) => {
              if (validKeys.has(k)) next[k] = prev[k];
            });
            iconPositionsRef.current = next;
            return next;
          });
        }

        return finalWindows;
      });
      if (showAllRef.current) loadAllFilesRef.current?.();
      return { folders, files };
    } catch (err) {
      if (isMountedRef.current) {
        showMessage?.(t('documents.page.refreshFailed'), 'error');
      }
    }
    return null;
  }, [showMessage, rootQuery]);

  // Re-fetch every open folder window. Changes made on this page announce
  // themselves as self-originated, which the listener below skips.
  const refreshOpenWindows = useCallback(() => {
    setFolderRefreshKeys(prev => {
      const updated = { ...prev };
      windowsRef.current.forEach(w => {
        updated[w.folderId] = (updated[w.folderId] || 0) + 1;
      });
      return updated;
    });
  }, []);

  // Index and Index Contents queue Celery work and return at once, so the
  // status dots only change when the listings are read again. Re-read them
  // now, then every few seconds while any watched document is still INDEXING
  // or PENDING (INDEXED, ERROR and NOT_INDEXED are final), until it settles or
  // the poll has run long enough to call the job stuck. Watched: the root,
  // every open window, each folder that was indexed, and the subfolders of
  // those whose counts still show unindexed documents.
  const indexPollRef = useRef(null);
  const stopIndexPoll = useCallback(() => {
    if (indexPollRef.current) {
      clearTimeout(indexPollRef.current);
      indexPollRef.current = null;
    }
  }, []);
  useEffect(() => stopIndexPoll, [stopIndexPoll]);

  const readListing = useCallback(async (path) => {
    const response = await axios.get(`${API_BASE}/browse`, { params: { path, fields: 'light' } });
    const data = response.data?.data || {};
    return { folders: data.folders || [], documents: data.documents || [] };
  }, []);

  const followIndexing = useCallback(async ({ folderIds = [], folderPaths = [] } = {}) => {
    stopIndexPoll();
    const targetIds = new Set(folderIds);
    const targetPaths = new Set(folderPaths.filter(Boolean));
    const underTarget = (path) =>
      Array.from(targetPaths).some((t) => path === t || path.startsWith(`${t}/`));
    let ticks = 0;
    const tick = async () => {
      indexPollRef.current = null;
      if (!isMountedRef.current) return;
      const fresh = await refreshData();
      if (!isMountedRef.current) return;
      refreshOpenWindows();
      if (!fresh) return;

      const listings = [{ folders: fresh.folders, documents: fresh.files }];
      const queue = windowsRef.current
        .filter((w) => w.state !== 'folded' && w.folder?.path && !isLiveRepoItem(w.folder))
        .map((w) => w.folder.path);
      const noteFolders = (listing) => {
        listing.folders.forEach((f) => {
          if (!f.path || isLiveRepoItem(f)) return;
          if (targetIds.has(f.id)) targetPaths.add(f.path);
          if (underTarget(f.path) && (f.document_count || 0) > (f.indexed_document_count || 0)) {
            queue.push(f.path);
          }
        });
      };
      noteFolders(listings[0]);
      targetPaths.forEach((p) => queue.push(p));
      const seen = new Set([rootPath]);
      while (queue.length > 0 && listings.length < INDEX_POLL_MAX_LISTINGS) {
        const path = queue.shift();
        if (seen.has(path)) continue;
        seen.add(path);
        try {
          const listing = await readListing(path);
          listings.push(listing);
          noteFolders(listing);
        } catch (err) {
          // A window on a folder that is gone; nothing to watch there.
        }
      }
      if (!isMountedRef.current) return;

      const busy = listings.some((l) => l.documents.some((d) => INDEX_IN_PROGRESS.has(d.index_status)));
      ticks += 1;
      if (busy && ticks < INDEX_POLL_MAX_TICKS) {
        indexPollRef.current = setTimeout(tick, INDEX_POLL_INTERVAL_MS);
      }
    };
    await tick();
  }, [refreshData, refreshOpenWindows, stopIndexPoll, readListing]);

  // Listen for file/folder changes from any tab/component and refresh in place
  useEffect(() => {
    const handleExternalUpdate = (event) => {
      const payload = event?.detail || event?.data || {};
      const fromSelf = payload.source && payload.source === clientIdRef.current;
      // Always refresh root data for cross-tab / external consistency (lightweight fields=light)
      if (!fromSelf) {
        refreshData();
      }
      // Only force re-fetch inside open folder windows for *external* changes.
      // Self-changes (e.g. our deletes) use optimistic 'documents-items-removed' for instant UX.
      if (!fromSelf) {
        setFolderRefreshKeys(prev => {
          const updated = { ...prev };
          windowsRef.current.forEach(w => {
            updated[w.folderId] = (updated[w.folderId] || 0) + 1;
          });
          return updated;
        });
      }
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

  // Axios interceptor: any successful mutation to /api/files triggers a refresh event (for cross-tab).
  // Primary delete/move/create now use optimistic removal where possible; refreshData is recovery + external sync.
  // Self-originated events are filtered in handler to avoid unnecessary folder re-fetches.
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
      layoutPositionsRef.current[activeLayoutRef.current] = positionsToSave;
      const { 'icons:folders': savedIconPositions = {}, ...layoutPositions } = layoutPositionsRef.current;
      await fetch(stateEndpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          windows: windowsData,
          windowLayout: layout,
          windowColors: colors,
          windowZIndex: zIndex,
          maxZIndex: maxZ,
          iconPositions: savedIconPositions,
          layoutPositions,
          subfolderColors: subfolderColorsRef.current,
          lastSaved: new Date().toISOString(),
        }),
      });
      // Persistence is fire-and-forget — response intentionally not checked
    } catch {
      // Layout save is best-effort; if it fails the user just loses layout on refresh
    }
  }, []);

  // Where a window opened from its desktop icon goes, in grid units: beside the
  // icon when it fits, over it when it does not, and always inside the visible
  // area, so an icon low on the page opens low and one at the top opens high.
  // Null when the icon is not on screen.
  const placeNearIcon = useCallback((folderId, w, h) => {
    const icon = desktopItemRefs.current.get(`folder-${folderId}`);
    const container = windowContainerRef.current;
    if (!icon?.isConnected || !container) return null;
    const iconRect = icon.getBoundingClientRect();
    const box = container.getBoundingClientRect();
    if (!iconRect.width || !box.width) return null;
    const [mx, my] = WINDOWS_MARGIN;
    const [px, py] = WINDOWS_PADDING;
    const colPitch = (box.width - px * 2 - mx * (WINDOWS_COLS - 1)) / WINDOWS_COLS + mx;
    const rowPitch = WINDOWS_ROW_HEIGHT + my;
    const iconLeft = Math.floor((iconRect.left - box.left - px) / colPitch);
    const iconRight = Math.ceil((iconRect.right - box.left - px) / colPitch);
    const iconTop = Math.round((iconRect.top - box.top - py) / rowPitch);
    const GAP = 1;
    let x;
    if (iconRight + GAP + w <= WINDOWS_COLS) x = iconRight + GAP;
    else if (iconLeft - GAP - w >= 0) x = iconLeft - GAP - w;
    else x = iconLeft;
    const { maxH } = getVisibleGridBounds();
    return {
      x: Math.max(0, Math.min(x, WINDOWS_COLS - w)),
      y: Math.max(0, Math.min(iconTop, maxH - h)),
    };
  }, [getVisibleGridBounds]);

  // Handle folder double-click - expand from folded to maximized
  const handleFolderExpand = useCallback((windowId) => {
    const opening = windowsRef.current.find(w => w.id === windowId);
    const bounds = getVisibleGridBounds();
    // A window keeps the size it had when it was last open (not its minimized bar).
    const remembered = windowLayout.find(l => l.i === windowId && l.h >= WINDOW_MIN_HEIGHT);
    const openW = Math.min(remembered?.w || WINDOW_MIN_WIDTH * 2, bounds.maxW);
    const openH = Math.min(remembered?.h || WINDOW_MIN_HEIGHT * 2, bounds.maxH - 2);
    // Read the icon's place now: once the window opens, the icon is gone.
    const nearIcon = opening && opening.state === 'folded'
      ? placeNearIcon(opening.folderId, openW, openH)
      : null;

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
    let newLayout = nearIcon
      ? [
        ...windowLayout.filter(l => l.i !== windowId),
        { i: windowId, ...nearIcon, w: openW, h: openH, minW: WINDOW_MIN_WIDTH, minH: WINDOW_MIN_HEIGHT },
      ]
      : [...windowLayout];

    // First, ensure all currently active windows have layout items
    const activeWindowIds = newWindows
      .filter(w => w.state === 'maximized' || w.state === 'minimized')
      .map(w => w.id);

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
  }, [windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, getVisibleGridBounds, placeNearIcon]);

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
    const map = { ...subfolderColors };
    windows.forEach(w => {
      if (windowColors[w.id]) {
        map[w.folderId] = windowColors[w.id];
      }
    });
    return map;
  }, [windows, windowColors, subfolderColors]);

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
    setActiveContext({ type: 'desktop', path: rootPath });
  }, [rootPath]);

  // Handle drag start (for items in folder windows)
  const handleDragStart = useCallback((_e, _item, _type) => {
    // Drag start is handled by FolderContents, just log for debugging
  }, []);

  // Handle drop in folder window or on desktop
  const handleDrop = useCallback(async (e, targetFolder = null) => {
    e.preventDefault();
    e.stopPropagation();
    // Drop targets inside the desktop stop propagation, so its highlight is cleared here.
    setDesktopFileDragOver(false);

    try {
      // Check if this is a file drop from outside the browser
      const hasFiles = dragCarriesFiles(e)
        && (e.dataTransfer.items?.length > 0 || e.dataTransfer.files?.length > 0);
      if (hasFiles) {
        // Before any await: the browser empties the item list once this handler yields.
        const dropped = snapshotDrop(e.dataTransfer);
        // Mark that drop was handled (file upload)
        dragDropHandledRef.current = true;

        // Determine upload destination
        let uploadPath = rootPath; // Default to the desktop

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

        // Files with relative paths; dropped folders are walked recursively
        const prune = { folders: 0, files: 0 };
        const filesToUpload = await collectDroppedFiles(dropped, { prune });
        // prunedNote() words what the walk left out; it fills {{skipped}} below.
        const skippedNote = prune.folders || prune.files ? prunedNote(prune) : '';

        if (filesToUpload.length === 0) {
          if (skippedNote) showMessage?.(t('documents.import.nothingToImport', { skipped: prunedNote(prune) }), 'info');
          setTimeout(() => { dragDropHandledRef.current = false; }, 100);
          return;
        }

        // Upload each file, creating nested folders as needed. One failure
        // does not stop the rest; the message reports both counts.
        let uploadedCount = 0;
        const failures = [];
        const folderApi = filesFolderApi(axios, API_BASE);
        const folderCache = new Map();
        setUploadProgress({ current: 0, total: filesToUpload.length });
        try {
          for (const { file, relativePath } of filesToUpload) {
            try {
              const lastSlash = relativePath.lastIndexOf('/');
              const folderPath = lastSlash > 0 ? relativePath.substring(0, lastSlash) : '';

              let targetUploadPath = uploadPath;
              if (folderPath) {
                targetUploadPath = await ensureFolderPath(folderPath, uploadPath, folderApi, folderCache);
              }

              const formData = new FormData();
              formData.append('file', file);
              formData.append('folder_path', targetUploadPath);

              await axios.post(`${API_BASE}/upload`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
              });
              uploadedCount++;
            } catch (uploadErr) {
              failures.push(t('documents.import.fileFailure', {
                path: relativePath,
                error: uploadErr.response?.data?.message || uploadErr.message || t('documents.import.uploadFailed'),
              }));
            }
            setUploadProgress({ current: uploadedCount + failures.length, total: filesToUpload.length });
          }
        } finally {
          setUploadProgress(null);
        }

        const destination = folderLabel(uploadPath);
        if (failures.length === 0) {
          showMessage?.(skippedNote
            ? t('documents.import.importedSkipped', { count: uploadedCount, folder: destination, skipped: skippedNote })
            : t('documents.import.imported', { count: uploadedCount, folder: destination }), 'success');
        } else if (uploadedCount > 0) {
          const partial = { count: uploadedCount, folder: destination, failed: failures.length, error: failures[0] };
          showMessage?.(skippedNote
            ? t('documents.import.importedPartlySkipped', { ...partial, skipped: skippedNote })
            : t('documents.import.importedPartly', partial), 'warning');
        } else {
          showMessage?.(t('documents.import.failed', { error: failures[0] }), 'error');
        }

        // Refresh data after upload
        await refreshData();
        refreshOpenWindows();

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
      let destinationPath = rootPath; // Default to the desktop
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
      const sourceParentPath = sourcePath ? sourcePath.substring(0, sourcePath.lastIndexOf('/')) || '/' : rootPath;

      if (!isDroppingOnFolder && isRoot(destinationPath) && isRoot(sourceParentPath)) {
        // This is icon repositioning on desktop - don't handle it here, let handleIconDrop handle it
        return;
      }

      // Mark that drop was handled (actual move operation)
      dragDropHandledRef.current = true;

      // Track affected folders (source and destination) for refresh
      const affectedFolderIds = new Set();

      // Find destination folder ID
      if (!isRoot(destinationPath)) {
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
              moveResults.errors.push(t('documents.move.intoOwnSubfolder', { name: item.name }));
              continue;
            }

            // Track source folder (parent of the folder being moved)
            // Extract parent path from item.path
            const sourcePath = item.path.substring(0, item.path.lastIndexOf('/')) || '/';
            if (!isRoot(sourcePath)) {
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
            if (!isRoot(sourcePath)) {
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
          const itemErrorMsg = itemErr.response?.data?.message || itemErr.message || t('documents.move.unknownError');
          moveResults.errors.push(t('documents.move.itemFailure', { name: item.name || item.filename, error: itemErrorMsg }));
        }
      }

      // Show appropriate message based on results
      if (moveResults.failed === 0) {
        showMessage?.(t('documents.move.moved', { count: moveResults.success }), 'success');
      } else if (moveResults.success === 0) {
        // All failed - don't refresh, just show error
        const errorMsg = moveResults.errors.length > 0
          ? moveResults.errors[0]
          : t('documents.move.failed');
        showMessage?.(errorMsg, 'error');
        return; // Don't refresh if nothing moved
      } else {
        // Partial success
        showMessage?.(t('documents.move.movedPartly', { count: moveResults.success, failed: moveResults.failed }), 'warning');
      }

      // Clear selection after move operation (even if partial failure).
      // A move can touch items from any surface, so reset every selection slot.
      if (moveResults.success > 0) {
        setSelectedItemsByContext({ desktop: new Set() });

        // Refresh data after move
        await refreshData();
        refreshOpenWindows();
      }
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || t('documents.move.dropFailed');
      showMessage?.(t('documents.move.operationFailed', { error: errorMsg }), 'error');
    } finally {
      // Reset after a short delay to allow onDragEnd to check
      setTimeout(() => {
        dragDropHandledRef.current = false;
      }, 100);
    }
  }, [windows, showMessage, refreshData, refreshOpenWindows, rootPath, isRoot]);

  // Separate windows by state (needed by arrange callbacks and render)
  const foldedWindows = useMemo(() => windows.filter(w => w.state === 'folded'), [windows]);
  const activeWindows = useMemo(() => windows.filter(w => w.state === 'minimized' || w.state === 'maximized'), [windows]);

  // Every file below the root (All items), filtered on the server by kind and
  // search. A newer request wins over a slower older one.
  const allRequestRef = useRef(0);
  const loadAllFiles = useCallback(async () => {
    const params = new URLSearchParams({ path: rootPath, recursive: '1', fields: 'media' });
    if (view.kinds.length) params.set('kinds', view.kinds.join(','));
    else if (mediaOnly) params.set('media_only', '1');
    if (search) params.set('q', search);
    const request = ++allRequestRef.current;
    setAllLoading(true);
    try {
      const response = await axios.get(`${API_BASE}/browse?${params.toString()}`);
      if (!isMountedRef.current || request !== allRequestRef.current) return;
      const docs = response.data?.data?.documents || [];
      setAllFiles(docs.map(f => ({ ...f, filename: f.filename || f.name, itemType: 'file' })));
    } catch {
      if (isMountedRef.current && request === allRequestRef.current) {
        showMessage?.(t('documents.page.refreshFailed'), 'error');
      }
    } finally {
      if (isMountedRef.current && request === allRequestRef.current) setAllLoading(false);
    }
  }, [rootPath, view.kinds, mediaOnly, search, showMessage, t]);
  loadAllFilesRef.current = loadAllFiles;
  useEffect(() => {
    if (showAll) loadAllFiles();
  }, [showAll, loadAllFiles]);

  // What the desktop shows, in the view's order: the root's folders and files
  // filtered by kind and search, or every file below the root.
  const sortedDesktop = useMemo(() => {
    const kinds = new Set(view.kinds);
    const folders = showAll ? [] : foldedWindows.filter((w) => matchesSearch(search, w.folder));
    const files = showAll
      ? allFiles
      : rootFiles.filter((f) => (kinds.size === 0 || kinds.has(kindOf(f))) && matchesSearch(search, f));
    return sortItems(folders, files, view.sort);
  }, [showAll, foldedWindows, rootFiles, allFiles, view.kinds, view.sort, search]);

  // Build a dataTransfer payload for desktop icon drags. If the dragged icon is part
  // of a multi-selection, ship the whole set; otherwise just the one item.
  const buildDesktopDragPayload = (thisItemKey, fallbackItem) => {
    if (desktopSelection.size <= 1 || !desktopSelection.has(thisItemKey)) {
      return [fallbackItem];
    }
    return Array.from(desktopSelection).map(k => {
      const [type, idStr] = k.split('-');
      const id = parseInt(idStr);
      if (type === 'folder') {
        const w = foldedWindows.find(fw => fw.folderId === id);
        if (!w) return null;
        return { id, itemType: 'folder', name: w.folder.name, path: w.folder.path };
      }
      const f = sortedDesktop.files.find(rf => rf.id === id);
      if (!f) return null;
      return { id, itemType: 'file', name: f.filename, filename: f.filename, path: f.path };
    }).filter(Boolean);
  };

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

  // Icon columns that fit the desktop as it is drawn; the layout width is the
  // fallback before it has been measured.
  // The desktop's drawn width, kept current as the window resizes.
  const [desktopWidth, setDesktopWidth] = useState(0);
  const [desktopHeight, setDesktopHeight] = useState(0);
  useEffect(() => {
    const el = desktopContentRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const update = () => {
      setDesktopWidth(Math.floor(el.clientWidth));
      setDesktopHeight(Math.floor(el.clientHeight));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [loading]);
  const visibleWidth = desktopWidth || desktopContentRef.current?.clientWidth || RGL_WIDTH_PROP_PX;

  const iconColumns = useCallback(
    () => Math.max(1, Math.floor((visibleWidth - 40) / geom.w)),
    [visibleWidth, geom],
  );

  // Cards are drawn only near what is in view: one screen above and below.
  // The band moves every half screen, so scrolling re-renders rarely.
  const [scrollBand, setScrollBand] = useState(0);
  const handleDesktopScroll = useCallback((e) => {
    const el = e.currentTarget;
    const band = Math.floor(el.scrollTop / Math.max(200, el.clientHeight / 2));
    setScrollBand((prev) => (prev === band ? prev : band));
  }, []);
  const visibleRange = useMemo(() => {
    const height = desktopHeight || 800;
    const top = scrollBand * Math.max(200, height / 2);
    return { from: top - height, to: top + 2 * height };
  }, [scrollBand, desktopHeight]);
  const isOnScreen = useCallback(
    (pos) => !pos || (pos.y + geom.h >= visibleRange.from && pos.y <= visibleRange.to),
    [visibleRange, geom],
  );

  // Arrange all desktop icons into a clean grid (reset positions). `ordered`,
  // when given, is the order to lay them out in (Sort by).
  const handleArrangeIcons = useCallback((ordered = null) => {
    const COLS = iconColumns();
    const newPositions = {};

    // Folders first, then files - sequential grid positions
    const allItems = ordered || [
      ...sortedDesktop.folders.map(w => ({ key: `folder-${w.folderId}` })),
      ...sortedDesktop.files.map(f => ({ key: getItemKey(f, 'file') })),
    ];

    allItems.forEach((item, idx) => {
      newPositions[item.key] = {
        x: (idx % COLS) * geom.w + geom.pad,
        y: Math.floor(idx / COLS) * geom.h + geom.pad,
      };
    });

    setIconPositions(newPositions);
    iconPositionsRef.current = newPositions;

    // Save to backend
    const windowsData = windows.map(w => ({
      id: w.id,
      folderId: w.folderId,
      state: w.state,
    }));
    // Need to save with the new positions directly since state hasn't updated yet
    saveWindowState(windowsData, windowLayout, windowColors, windowZIndex, maxZIndex, newPositions);
  }, [sortedDesktop, windows, windowLayout, windowColors, windowZIndex, maxZIndex, iconColumns, geom, saveWindowState]);

  // A new sort: the page keeps it, and on a placed view the icons are laid out
  // again in that order. Asking for the current field again flips its direction.
  const nextSort = useCallback((field) => (
    field === view.sort.by
      ? { by: field, dir: view.sort.dir === 'asc' ? 'desc' : 'asc' }
      : { by: field, dir: field === 'date' || field === 'size' ? 'desc' : 'asc' }
  ), [view.sort]);
  const applySort = useCallback((sort) => {
    onViewChange?.({ sort });
    if (viewMode === 'list') return;
    const { folders, files } = sortItems(sortedDesktop.folders, sortedDesktop.files, sort);
    handleArrangeIcons([
      ...folders.map(w => ({ key: `folder-${w.folderId}` })),
      ...files.map(f => ({ key: getItemKey(f, 'file') })),
    ]);
  }, [onViewChange, viewMode, sortedDesktop, handleArrangeIcons]);
  const handleSortBy = useCallback((field) => {
    setContextMenu(null);
    applySort(nextSort(field));
  }, [applySort, nextSort]);

  // Context menu handlers
  // Right-click handler. `surface` tells us where the click physically happened —
  // 'desktop' for icons/space on the desktop, 'window' for anything inside a folder
  // window. Without it, type='folder' couldn't distinguish a desktop folder icon
  // from an in-window subfolder, and copy/cut would key off the wrong selection slot.
  const handleContextMenu = useCallback((e, item = null, type = 'desktop', surface = 'desktop', extras = null) => {
    e.preventDefault();
    e.stopPropagation();
    if (surface === 'desktop') {
      setDesktopContext();
      // A right button press does not focus the desktop first, so the ref
      // can still name the last window; the selection below must not land there.
      activeContextKeyRef.current = 'desktop';
    }
    // For surface='window', the folder window's onMouseDown has already set
    // activeContext to that window — don't clobber it from the item type.
    setContextMenu({ top: e.clientY, left: e.clientX });
    setContextMenuType(type);
    setContextMenuItem(item);
    setContextMenuExtras(extras);

    // If right-clicking on an item, select it if not already selected.
    // Refs are used here because the mouseDown that set the new activeContext
    // fires immediately before this callback, and we want the freshest slot.
    // A window background ('folder-window') carries the window's own folder,
    // which is not an item of that window and must not become its selection.
    if (item && (type === 'folder' || type === 'file')) {
      const key = type === 'folder' ? `folder-${item.id}` : `file-${item.id}`;
      const currentKey = activeContextKeyRef.current;
      const currentSelection = selectionByContextRef.current[currentKey] || EMPTY_SELECTION;
      if (!currentSelection.has(key)) {
        handleSelectionChange(currentKey, new Set([key]));
      }
    }
  }, [setDesktopContext, handleSelectionChange]);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
    setContextMenuItem(null);
    setContextMenuType('desktop');
    setContextMenuExtras(null);
  }, []);

  const handleNewFolder = useCallback(() => {
    setNewFolderParent(menuTargetPath(contextMenuType, contextMenuItem, rootPath));
    setContextMenu(null);
    setNewFolderOpen(true);
  }, [contextMenuType, contextMenuItem, rootPath]);

  const handleCreateFolder = useCallback(async () => {
    if (!newFolderName.trim()) return;
    try {
      await axios.post(`${API_BASE}/folder`, {
        name: newFolderName,
        parent_path: newFolderParent,
      });
      showMessage?.(t('documents.newFolder.created', { name: newFolderName }), 'success');
      setNewFolderOpen(false);
      setNewFolderName('');

      // Directly refresh data after creating folder
      await refreshData();
      refreshOpenWindows();
    } catch (err) {
      showMessage?.(t('documents.newFolder.failed'), 'error');
    }
  }, [newFolderName, newFolderParent, showMessage, refreshData, refreshOpenWindows]);

  const openImportPicker = useCallback((targetPath) => {
    uploadTargetRef.current = targetPath || rootPath;
    fileInputRef.current?.click();
  }, [rootPath]);

  // Import from the right-click menu lands in whatever was right-clicked.
  const handleUpload = useCallback(() => {
    setContextMenu(null);
    openImportPicker(menuTargetPath(contextMenuType, contextMenuItem, rootPath));
  }, [contextMenuType, contextMenuItem, openImportPicker, rootPath]);

  // Import from the toolbar lands in the focused open folder window, else the desktop.
  const handleToolbarImport = useCallback(() => {
    closeContextMenu();
    let target = rootPath;
    if (activeContext.type === 'folder') {
      const win = windowsRef.current.find(w => w.folderId === activeContext.folderId);
      if (win && win.state === 'maximized') target = activeContext.path || win.folder.path;
    }
    openImportPicker(target);
  }, [activeContext, closeContextMenu, openImportPicker, rootPath]);

  const handleFileUpload = useCallback(async (e) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    const uploadPath = uploadTargetRef.current || rootPath;

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
      const destination = folderLabel(uploadPath);
      const msg = skipped > 0
        ? t('documents.import.importedFiltered', { count: uploaded, folder: destination, ignored: skipped })
        : t('documents.import.imported', { count: uploaded, folder: destination });
      showMessage?.(msg, 'success');

      // Refresh data after upload
      await refreshData();
      refreshOpenWindows();
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || t('documents.import.failedPlain');
      showMessage?.(t('documents.import.failed', { error: errorMsg }), 'error');
    } finally {
      // Reset file input
      if (e.target) {
        e.target.value = '';
      }
    }
  }, [showMessage, refreshData, refreshOpenWindows, rootPath]);

  const handleCopy = useCallback(() => {
    setClipboard({ items: Array.from(activeSelection), operation: 'copy' });
    setContextMenu(null);
    showMessage?.(t('documents.clipboard.copied', { count: activeSelection.size }), 'info');
  }, [activeSelection, showMessage]);

  const handleCut = useCallback(() => {
    setClipboard({ items: Array.from(activeSelection), operation: 'cut' });
    setContextMenu(null);
    showMessage?.(t('documents.clipboard.cut', { count: activeSelection.size }), 'info');
  }, [activeSelection, showMessage]);

  // The folder copy route takes a folder id; everything else on this page knows paths.
  const folderIdForPath = useCallback(async (path) => {
    if (!path || path === '/') return null;
    const response = await axios.get(`${API_BASE}/browse`, {
      params: { path, fields: 'light', limit: 1 },
    });
    const id = response.data?.data?.folder_id;
    if (id === undefined || id === null) {
      throw new Error(t('documents.clipboard.foldersNotPastable', { folder: folderLabel(path) }));
    }
    return id;
  }, []);

  // `target` is { path } from Ctrl+V; from the menu it is omitted and the
  // right-clicked item decides.
  const handlePaste = useCallback(async (target = null) => {
    setContextMenu(null);
    if (!clipboard || clipboard.items.length === 0) return;

    const targetPath = typeof target?.path === 'string'
      ? target.path
      : menuTargetPath(contextMenuType, contextMenuItem, rootPath);
    const isCopy = clipboard.operation === 'copy';
    // A right-clicked folder already carries its id; otherwise it is looked up once.
    let destFolderId = contextMenuType === 'folder' && contextMenuItem?.path === targetPath
      ? contextMenuItem.id
      : undefined;

    let done = 0;
    const errors = [];
    for (const key of clipboard.items) {
      const sep = key.indexOf('-');
      const type = key.slice(0, sep);
      const id = Number(key.slice(sep + 1));
      try {
        if (!Number.isInteger(id)) {
          throw new Error(t('documents.clipboard.liveRepoReadOnly'));
        }
        if (type === 'folder') {
          if (isCopy) {
            if (destFolderId === undefined) destFolderId = await folderIdForPath(targetPath);
            await axios.post(`${API_BASE}/folder/${id}/copy`, { target_folder_id: destFolderId });
          } else {
            await axios.post(`${API_BASE}/folder/${id}/move`, { destination_path: targetPath });
          }
        } else if (isCopy) {
          await axios.post(`${API_BASE}/document/${id}/copy`, { destination_path: targetPath });
        } else {
          await axios.post(`${API_BASE}/document/${id}/move`, { destination_path: targetPath });
        }
        done += 1;
      } catch (err) {
        errors.push(err.response?.data?.message || err.message || t('documents.clipboard.itemFailed'));
      }
    }

    const destination = folderLabel(targetPath);
    if (errors.length === 0) {
      showMessage?.(isCopy
        ? t('documents.clipboard.pasted', { count: done, folder: destination })
        : t('documents.clipboard.moved', { count: done, folder: destination }), 'success');
    } else if (done > 0) {
      const partial = { count: done, folder: destination, failed: errors.length, error: errors[0] };
      showMessage?.(isCopy
        ? t('documents.clipboard.pastedPartly', partial)
        : t('documents.clipboard.movedPartly', partial), 'warning');
    } else {
      showMessage?.(isCopy
        ? t('documents.clipboard.pasteFailed', { error: errors[0] })
        : t('documents.clipboard.moveFailed', { error: errors[0] }), 'error');
      return;
    }
    setClipboard(null);

    await refreshData();
    refreshOpenWindows();
  }, [clipboard, showMessage, contextMenuType, contextMenuItem, refreshData, refreshOpenWindows, folderIdForPath, rootPath]);

  // The keys in the selection that the server can delete.
  const deletableSelection = useMemo(() => writableKeys(activeSelection), [activeSelection]);

  const handleDelete = useCallback(() => {
    setContextMenu(null);
    if (activeSelection.size === 0) return;
    if (deletableSelection.length === 0) {
      showMessage?.(t('documents.delete.liveRepoReadOnly'), 'info');
      return;
    }
    setDeleteConfirmOpen(true);
  }, [activeSelection, deletableSelection, showMessage]);

  // Optimistically remove items from visible UI state immediately (for instant feel).
  // Only affects desktop root lists if deleting from desktop context.
  // Always prunes iconPositions and clears the relevant selection slot.
  // Dispatches event so open FolderContents (and others) can filter locally.
  const applyOptimisticDeletions = useCallback((keys) => {
    if (!keys || keys.length === 0) return;
    const contextKey = activeContextKeyRef.current || 'desktop';
    const folderIds = new Set();
    const fileIds = new Set();
    const keysToRemoveFromPositions = new Set(keys);

    keys.forEach((key) => {
      const [type, idStr] = String(key).split('-');
      const id = parseInt(idStr, 10);
      if (!isNaN(id)) {
        if (type === 'folder') folderIds.add(id);
        else fileIds.add(id);
      }
    });

    // Desktop-level lists only when acting on desktop surface
    if (contextKey === 'desktop') {
      if (fileIds.size > 0) {
        setRootFiles((prev) => prev.filter((f) => !fileIds.has(f.id)));
      }
      if (folderIds.size > 0) {
        setWindows((prev) => prev.filter((w) => !folderIds.has(w.folderId)));
      }
    }

    // Always prune icon positions for removed desktop/root keys (prevents ghosts)
    setIconPositions((prev) => {
      const next = { ...prev };
      keysToRemoveFromPositions.forEach((k) => {
        delete next[k];
        // Also try alternate forms if any
        const alt = k.startsWith('file-') || k.startsWith('folder-') ? k : null;
        if (alt) delete next[alt];
      });
      iconPositionsRef.current = next;
      return next;
    });

    // Clear the selection for the acting context/slot
    handleSelectionChange(contextKey, new Set());

    // Notify other surfaces (esp. folder windows) for instant removal of their items
    try {
      window.dispatchEvent(new CustomEvent('documents-items-removed', { detail: { keys, contextKey } }));
    } catch (e) {
      // dispatch best-effort; ignore in non-browser or blocked envs
    }
  }, [handleSelectionChange]);

  const handleDeleteConfirm = useCallback(() => {
    setDeleteConfirmOpen(false);
    const keysToDelete = deletableSelection;
    if (keysToDelete.length === 0) return;

    // INSTANT: remove from UI now (optimistic). This is the key UX fix.
    applyOptimisticDeletions(keysToDelete);

    // Fire deletes async (parallel, prefer bulk endpoint if available)
    const doDeletes = async () => {
      try {
        // Try bulk first
        const bulkPayload = {
          items: keysToDelete.map((k) => {
            const [type, idStr] = String(k).split('-');
            return { type: type === 'folder' ? 'folder' : 'document', id: parseInt(idStr, 10) };
          }),
        };
        try {
          await axios.post(`${API_BASE}/bulk-delete`, bulkPayload);
        } catch (bulkErr) {
          // Fallback to individuals (parallel) on bulk failure/unavailable
          await Promise.allSettled(
            keysToDelete.map((key) => {
              const [type, id] = key.split('-');
              const url = type === 'folder' ? `${API_BASE}/folder/${id}` : `${API_BASE}/document/${id}`;
              return axios.delete(url);
            })
          );
        }
        showMessage?.(t('documents.delete.deleted', { count: keysToDelete.length }), 'success');
        // No full refresh needed on success path (optimistic + server done); cross-tab uses announce via interceptor
        // Optional: we can still announce to be sure
        // announceDocumentsChange(); // interceptor will have fired per response or bulk
      } catch (err) {
        showMessage?.(t('documents.delete.someFailed'), 'error');
        // On error, re-sync to restore any inconsistent UI
        await refreshData();
      }
    };
    doDeletes();
  }, [deletableSelection, showMessage, refreshData, applyOptimisticDeletions]);

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
            showMessage?.(t('documents.properties.fileNotFound'), 'error');
          }
        } catch (err) {
          // Fallback: try browsing root
          try {
            const browseResponse = await axios.get(`${API_BASE}/browse?${rootQuery}`);
            const data = browseResponse.data.data;
            const file = (data.documents || []).find(d => d.id === parseInt(id));
            if (file) {
              setRenameItem({ type: 'file', item: file });
              setRenameName(file.filename || file.name || '');
              setRenameDialogOpen(true);
            } else {
              showMessage?.(t('documents.properties.fileNotFound'), 'error');
            }
          } catch (browseErr) {
            showMessage?.(t('documents.properties.loadFailed'), 'error');
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
        showMessage?.(t('documents.rename.folderRenamed', { name: renameName.trim() }), 'success');
      } else {
        await axios.put(`${API_BASE}/document/${renameItem.item.id}`, {
          filename: renameName.trim(),
        });
        showMessage?.(t('documents.rename.fileRenamed', { name: renameName.trim() }), 'success');
      }
      setRenameDialogOpen(false);
      setRenameItem(null);
      setRenameName('');

      // Refresh data after rename: the desktop and every open window, since a
      // change made on this page does not come back through the listener.
      await refreshData();
      refreshOpenWindows();
    } catch (err) {
      // The server says why a name is refused; show that over a bare "failed".
      const reason = err.response?.data?.message || err.response?.data?.error?.message;
      showMessage?.(reason ? t('documents.rename.failedReason', { reason }) : t('documents.rename.failed'), 'error');
    }
  }, [renameItem, renameName, showMessage, refreshData, refreshOpenWindows]);

  const handleColorChange = useCallback((color) => {
    if (!contextMenuItem || contextMenuType !== 'folder') return;

    const window = windows.find(w => w.folderId === contextMenuItem.id);
    if (window) {
      handleWindowColorChange(window.id, color);
    } else {
      const next = { ...subfolderColorsRef.current, [contextMenuItem.id]: color };
      subfolderColorsRef.current = next;
      setSubfolderColors(next);
      saveWindowState(windows, windowLayout, windowColors, windowZIndex, maxZIndex);
    }
    setContextMenu(null);
  }, [contextMenuItem, contextMenuType, windows, windowLayout, windowColors, windowZIndex, maxZIndex, handleWindowColorChange, saveWindowState]);

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
    showMessage?.(t('documents.page.downloading', { name: item.filename || item.name }), 'info');
  }, [contextMenuItem, contextMenuType, showMessage]);

  const openFile = useCallback((file, { editMode = false, siblings } = {}) => {
    if (!file) return;
    const filename = file.filename || file.name || '';
    if (editMode && isImageFile(filename)) {
      const imageUrl = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
      setLightbox({
        url: imageUrl,
        name: filename,
        documentId: file.id,
        editMode: true,
      });
      return;
    }
    if (isImageFile(filename) || isVideoFile(filename)) {
      setMediaPreview({ file, siblings: siblings || [] });
      return;
    }
    if (isCodeFile(filename)) {
      // Covers code and plain text (.txt, .md, .csv, .log); Edit opens the
      // modal's editor, double-click opens it read-only.
      setCodeViewer({ file, editMode });
      return;
    }
    if (isPdfFile(filename)) {
      setPdfViewer({ file });
      return;
    }
    if (isDocxFile(filename)) {
      setDocxViewer({ file });
      return;
    }
    if (isAudioFile(filename)) {
      setAudioPlayer({ file });
      return;
    }
    window.open(`${API_BASE}/document/${file.id}/download`, '_blank');
  }, []);

  const handleEditFile = useCallback(() => {
    setContextMenu(null);
    const item = contextMenuItem;
    if (!item || contextMenuType !== 'file') return;
    openFile(item, { editMode: true });
  }, [contextMenuItem, contextMenuType, openFile]);

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
          source: targetFile.source_type === 'live_repo' ? 'live_repo' : 'document',
          filePath: targetFile.relative_path || targetFile.path,
          relativePath: targetFile.relative_path,
        }
      }
    });
  }, [contextMenuItem, navigate]);

  // Bulk Import with this folder as the one to import into.
  const handleBulkImportHere = useCallback(() => {
    const item = contextMenuItem;
    setContextMenu(null);
    if (!item || !Number.isInteger(item.id)) return;
    navigate(`/documents/bulk-import?into=${encodeURIComponent(item.path)}&into_id=${item.id}`);
  }, [contextMenuItem, navigate]);

  const handleReviewWithAgent = useCallback(async () => {
    const item = contextMenuItem;
    if (!item || item.source_type !== 'live_repo') {
      showMessage?.(t('documents.review.liveRepoOnly'), 'warning');
      setContextMenu(null);
      return;
    }
    setContextMenu(null);
    try {
      const result = await reviewRepoScope({
        path: item.relative_path || '',
        prompt: 'Review this scope for concrete bugs or unsafe code. Propose only surgical fixes as pending fixes.',
      });
      const taskId = result?.data?.task_id || result?.task_id;
      showMessage?.(taskId
        ? t('documents.review.dispatchedTask', { taskId })
        : t('documents.review.dispatched'), 'success');
    } catch (err) {
      showMessage?.(err?.message || t('documents.review.failed'), 'error');
    }
  }, [contextMenuItem, showMessage]);

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
            showMessage?.(t('documents.properties.fileNotFound'), 'error');
          }
        } catch (err) {
          // Fallback: try browsing root
          try {
            const browseResponse = await axios.get(`${API_BASE}/browse?${rootQuery}`);
            const data = browseResponse.data.data;
            const file = (data.documents || []).find(d => d.id === parseInt(id));
            if (file) {
              setSelectedForProperties({ type: 'file', item: file });
              setPropertiesOpen(true);
            } else {
              showMessage?.(t('documents.properties.fileNotFound'), 'error');
            }
          } catch (browseErr) {
            showMessage?.(t('documents.properties.loadFailed'), 'error');
          }
        }
      }
    }
  }, [contextMenuItem, contextMenuType, activeSelection, windows, showMessage]);

  const handleFilePropertiesSave = useCallback(async (fileId, payload) => {
    try {
      await axios.put(`${API_BASE}/document/${fileId}/link`, payload);
      showMessage?.(t('documents.properties.fileUpdated'), 'success');
      setPropertiesOpen(false);
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || t('documents.properties.fileUpdateFailed');
      showMessage?.(t('documents.properties.updateFailed', { error: errorMsg }), 'error');
    }
  }, [showMessage]);

  const handleFilePropertiesDelete = useCallback(async (fileId) => {
    try {
      await axios.delete(`${API_BASE}/document/${fileId}`);
      showMessage?.(t('documents.properties.fileDeleted'), 'success');
      // Instant reflect + cross-surface: dispatch removal before/after close
      try {
        window.dispatchEvent(new CustomEvent('documents-items-removed', { detail: { keys: [`file-${fileId}`], contextKey: 'properties' } }));
      } catch (e) {
        // ignore
      }
      setPropertiesOpen(false);
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || t('documents.properties.fileDeleteFailed');
      showMessage?.(t('documents.properties.deleteFailed', { error: errorMsg }), 'error');
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
        result?.message || t('documents.properties.reindexStarted'),
        'success'
      );
      followIndexing();
    } catch (err) {
      const errorMsg = err.response?.data?.message || err.message || t('documents.properties.reindexStartFailed');
      showMessage?.(t('documents.properties.reindexFailed', { error: errorMsg }), 'error');
      throw err;
    }
  }, [showMessage, followIndexing]);

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

    itemKeys = writableKeys(itemKeys);
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
      const queued = Number(result?.total_documents) || 0;
      const skipped = Number(result?.skipped) || 0;
      showMessage?.(indexingMessage(queued, skipped, folderIds.length), queued > 0 ? 'success' : 'info');
      if (queued > 0) {
        followIndexing({
          folderIds,
          folderPaths: contextMenuType === 'folder' && contextMenuItem?.path ? [contextMenuItem.path] : [],
        });
      }
    } catch (err) {
      const errorMsg = err.message || t('documents.indexing.startFailed');
      showMessage?.(t('documents.indexing.failed', { error: errorMsg }), 'error');
    }
  }, [contextMenuItem, contextMenuType, activeSelection, showMessage, followIndexing]);

  // Desktop drag-to-select handlers
  const handleDesktopSelectionMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    if (e.target.closest('.desktop-item-card')) return;

    const el = desktopContentRef.current;
    const rect = el?.getBoundingClientRect();
    if (!rect) return;

    setDesktopContext();
    // Content coordinates: the box and the cards scroll together.
    const startX = e.clientX - rect.left + el.scrollLeft;
    const startY = e.clientY - rect.top + el.scrollTop;

    setIsDesktopSelecting(true);
    setDesktopSelectionBox({ startX, startY, currentX: startX, currentY: startY });
  }, [setDesktopContext]);

  const handleDesktopSelectionMouseMove = useCallback((e) => {
    if (!isDesktopSelecting || !desktopSelectionBox) return;
    const el = desktopContentRef.current;
    const rect = el?.getBoundingClientRect();
    if (!rect) return;

    const currentX = e.clientX - rect.left + el.scrollLeft;
    const currentY = e.clientY - rect.top + el.scrollTop;

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
          left: itemRect.left - rect.left + el.scrollLeft,
          top: itemRect.top - rect.top + el.scrollTop,
          right: itemRect.right - rect.left + el.scrollLeft,
          bottom: itemRect.bottom - rect.top + el.scrollTop,
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
      if (e.defaultPrevented || keyboardSuspended) return;
      const targetTag = e.target?.tagName;
      if (targetTag === 'INPUT' || targetTag === 'TEXTAREA' || e.target?.isContentEditable) {
        return;
      }

      const key = e.key.toLowerCase();

      if (e.ctrlKey && key === 'a') {
        if (activeContext.type === 'desktop') {
          e.preventDefault();
          const allKeys = new Set([
            ...sortedDesktop.folders.filter(f => !isLiveRepoItem(f.folder)).map(f => `folder-${f.folderId}`),
            ...sortedDesktop.files.filter(file => !isLiveRepoItem(file)).map(file => getItemKey(file, 'file')),
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
        handlePaste({ path: activeContext.type === 'folder' ? activeContext.path : rootPath });
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
  }, [activeContext, sortedDesktop, activeSelection, handleCopy, handleCut, handlePaste, handleDelete, handleSelectionChange, keyboardSuspended, rootPath]);


  // Compute collision-free positions for ALL icons.
  // Saved positions are kept as-is while they fit the desktop's width; the
  // rest, and items never placed, get the first free grid cell. A saved place
  // past the right edge (a wider window, or the Media Library's old 150 px
  // columns) is not lost: it comes back when the desktop is wide enough.
  // Saved places can come from another grid, so a cell is free only when no
  // card would cover it, not when no saved place rounds to it.
  const resolvedIconPositions = useMemo(() => {
    if (viewMode === 'list') return {};
    const items = [
      ...sortedDesktop.folders.map(w => ({ key: `folder-${w.folderId}` })),
      ...sortedDesktop.files.map(f => ({ key: getItemKey(f, 'file') })),
    ];
    return resolvePlaces(items, iconPositions, geom, visibleWidth);
  }, [viewMode, sortedDesktop, iconPositions, geom, visibleWidth]);

  // How far down the placed cards reach, so the desktop scrolls to the last
  // one even though only those near the view are drawn.
  const desktopExtent = useMemo(() => {
    if (viewMode === 'list') {
      return 32 + (sortedDesktop.folders.length + sortedDesktop.files.length) * LIST_ROW_HEIGHT;
    }
    let bottom = 0;
    Object.values(resolvedIconPositions).forEach((pos) => { if (pos.y > bottom) bottom = pos.y; });
    return bottom + geom.h + geom.pad;
  }, [viewMode, sortedDesktop, resolvedIconPositions, geom]);

  // Find the nearest unoccupied grid cell for an icon
  const snapToGrid = useCallback((rawX, rawY, movingKey, currentPositions) => {
    // Snap to nearest grid cell
    let col = Math.round((rawX - geom.pad) / geom.w);
    let row = Math.round((rawY - geom.pad) / geom.h);
    // An icon dropped at the right edge stays on the desktop.
    const maxCols = iconColumns();
    col = Math.max(0, Math.min(col, maxCols - 1));
    row = Math.max(0, row);

    // Every other icon's place (currentPositions is the fully resolved map);
    // a cell is taken when a card there would cover one of them.
    const others = Object.entries(currentPositions)
      .filter(([k]) => k !== movingKey)
      .map(([, pos]) => pos);
    const covers = (x, y) => others.some((p) => Math.abs(p.x - x) < geom.span && Math.abs(p.y - y) < geom.h);
    const isFree = (c, r) => !covers(c * geom.w + geom.pad, r * geom.h + geom.pad);

    // If target cell is free, use it
    if (isFree(col, row)) {
      return { x: col * geom.w + geom.pad, y: row * geom.h + geom.pad };
    }

    // Otherwise spiral outward to find nearest free cell
    for (let dist = 1; dist < 50; dist++) {
      for (let dc = -dist; dc <= dist; dc++) {
        for (let dr = -dist; dr <= dist; dr++) {
          if (Math.abs(dc) !== dist && Math.abs(dr) !== dist) continue;
          const nc = col + dc;
          const nr = row + dr;
          if (nc < 0 || nr < 0 || nc >= maxCols) continue;
          if (isFree(nc, nr)) {
            return { x: nc * geom.w + geom.pad, y: nr * geom.h + geom.pad };
          }
        }
      }
    }

    // Fallback (shouldn't happen)
    return { x: col * geom.w + geom.pad, y: row * geom.h + geom.pad };
  }, [iconColumns, geom]);

  // Handle icon drag end (repositioning)
  const handleIconDrop = useCallback((e, key) => {
    // Only reposition if drop was not handled (i.e., not moved to another location)
    if (dragDropHandledRef.current) {
      return;
    }

    const container = e.currentTarget.closest('[data-desktop-container]');
    if (!container) return;

    const rect = container.getBoundingClientRect();
    const iconWidth = geom.cardW;
    const iconHeight = geom.h * 0.8;

    // Raw position from mouse, in content coordinates (the desktop scrolls)
    const rawX = Math.max(0, e.clientX - rect.left + container.scrollLeft - iconWidth / 2);
    const rawY = Math.max(0, e.clientY - rect.top + container.scrollTop - iconHeight / 2);

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
  }, [windows, windowLayout, windowColors, windowZIndex, maxZIndex, saveWindowState, snapToGrid, resolvedIconPositions, iconPositions, geom]);

  // Top-level folders own a window; folders inside a window do not.
  // ?open=<folder path> (Bulk Import's Open Files) opens the window of the
  // top-level folder that holds it, then leaves the address as it was.
  const openParam = features.openParam ? searchParams.get('open') : null;
  useEffect(() => {
    if (!openParam || loading) return;
    const top = openParam.replace(/^\/+/, '').split('/')[0];
    const win = windows.find((w) => String(w.folder?.path || '').replace(/^\/+/, '') === top);
    if (win && win.state === 'folded') handleFolderExpand(win.id);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      next.delete('open');
      return next;
    }, { replace: true });
  }, [openParam, loading, windows, handleFolderExpand, setSearchParams]);

  // What the page header drives (FileDesktopActions).
  useImperativeHandle(ref, () => ({
    importFiles: handleToolbarImport,
    arrangeIcons: () => handleArrangeIcons(),
    arrangeWindows: handleArrangeWindows,
    refresh: () => { refreshData(); refreshOpenWindows(); },
  }), [handleToolbarImport, handleArrangeIcons, handleArrangeWindows, refreshData, refreshOpenWindows]);

  // Listings changed by something other than this desktop (a batch finishing).
  useEffect(() => {
    if (!refreshOnEvents.length) return undefined;
    const onChange = () => { refreshData(); refreshOpenWindows(); };
    refreshOnEvents.forEach((name) => window.addEventListener(name, onChange));
    return () => refreshOnEvents.forEach((name) => window.removeEventListener(name, onChange));
  }, [refreshOnEvents, refreshData, refreshOpenWindows]);

  // A plain click on a file opens what the page offers for it, once the
  // double-click window has passed; a double click opens the file itself.
  const clickFile = useCallback((file, siblings) => {
    if (onFileClick) scheduleFileClick(() => onFileClick(file, siblings));
  }, [onFileClick, scheduleFileClick]);

  const contextMenuFolderWindow = contextMenuItem && contextMenuType === 'folder'
    ? windows.find(w => w.folderId === contextMenuItem.id) || null
    : null;
  // The open window whose title bar was right-clicked: its menu adds Minimize and Close.
  const contextMenuTitleWindow = contextMenuExtras?.windowId
    ? windows.find(w => w.id === contextMenuExtras.windowId && w.state !== 'folded') || null
    : null;

  if (loading) {
    return (
      <Box sx={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Typography color="text.secondary">{t('documents.page.loadingFolders')}</Typography>
      </Box>
    );
  }

  return (
    <>
      {onViewChange && (
        <DesktopToolbar
          view={view}
          onViewChange={onViewChange}
          onSort={applySort}
          kinds={mediaOnly ? MEDIA_KINDS : ALL_KINDS}
          itemCount={sortedDesktop.folders.length + sortedDesktop.files.length}
          onSearch={setSearch}
        />
      )}
      <Box ref={windowContainerRef} sx={{ flex: 1, minHeight: 0, position: "relative", overflow: "hidden" }}>
        {entityFilter && (
          <EntityFilesPanel
            kind={entityFilter.kind}
            id={entityFilter.id}
            onOpenFile={(doc) => openFile(doc)}
            onClose={() => setSearchParams({})}
          />
        )}
        {/* Desktop area with folded folder icons */}
        <Box
          data-desktop-container
          ref={desktopContentRef}
          sx={{
            position: "absolute", inset: 0, overflow: "auto", zIndex: 1, p: 2,
            transition: "background-color 0.15s ease",
            backgroundColor: desktopFileDragOver ? theme.palette.action.hover : "transparent",
          }}
          onContextMenu={handleContextMenu}
          onScroll={handleDesktopScroll}
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
            if (dragCarriesFiles(e)) setDesktopFileDragOver(true);
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget)) setDesktopFileDragOver(false);
          }}
          onDrop={handleDrop}
        >
          {/* The bottom of the last card, so the desktop scrolls that far */}
          <Box sx={{ position: 'absolute', top: desktopExtent, left: 0, width: 1, height: 1, pointerEvents: 'none' }} />
          {showAll && allLoading && allFiles.length === 0 && (
            <Typography variant="body2" color="text.secondary" sx={{ position: 'absolute', top: 16, left: 16 }}>
              {t('documents.page.loadingAll')}
            </Typography>
          )}
          {/* Empty state */}
          {sortedDesktop.folders.length === 0 && sortedDesktop.files.length === 0 && activeWindows.length === 0 && !allLoading && (
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
              <BrandLogo size={64} sx={{ opacity: 0.4 }} />
              <Typography variant="h6" color="text.secondary">
                {t('documents.page.emptyTitle')}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t('documents.page.emptyHint')}
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
          {/* List view: a header that sorts, then one row per folder and file,
              drawn only near what is in view */}
          {viewMode === 'list' && (() => {
            const rows = [...sortedDesktop.folders, ...sortedDesktop.files];
            const folderCount = sortedDesktop.folders.length;
            const first = Math.max(0, Math.floor(visibleRange.from / LIST_ROW_HEIGHT));
            const last = Math.min(rows.length, Math.ceil(visibleRange.to / LIST_ROW_HEIGHT));
            const rowSx = {
              display: 'flex', alignItems: 'center', gap: 1.5, px: 1.5, height: LIST_ROW_HEIGHT, flexShrink: 0,
              boxSizing: 'border-box', cursor: 'pointer', userSelect: 'none',
              borderBottom: `1px solid ${theme.palette.divider}`,
            };
            const header = (field, label, sx) => (
              <Box
                component="button"
                type="button"
                onClick={() => applySort(nextSort(field))}
                sx={{
                  all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 0.25,
                  fontSize: '0.75rem', fontWeight: 600,
                  color: view.sort.by === field ? 'text.primary' : 'text.secondary', ...sx,
                }}
              >
                {label}
                {view.sort.by === field && (view.sort.dir === 'asc'
                  ? <ArrowUpwardIcon sx={{ fontSize: 14 }} />
                  : <ArrowDownwardIcon sx={{ fontSize: 14 }} />)}
              </Box>
            );
            const folderRow = (window) => {
              const key = `folder-${window.folderId}`;
              const isSelected = desktopSelection.has(key);
              const isDragOver = dragOverFolderId === window.folderId;
              const itemCount = (window.folder.document_count || 0) + (window.folder.subfolder_count || 0);
              return (
                <Box
                  key={key}
                  ref={(el) => {
                    if (el) desktopItemRefs.current.set(key, el);
                    else desktopItemRefs.current.delete(key);
                  }}
                  className="desktop-item-card"
                  data-folder-id={window.folderId}
                  data-folder-path={window.folder.path}
                  draggable
                  onDragStart={(e) => {
                    setDesktopContext();
                    const fallback = { id: window.folderId, itemType: 'folder', name: window.folder.name, path: window.folder.path };
                    e.dataTransfer.setData('text/plain', JSON.stringify(buildDesktopDragPayload(key, fallback)));
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); }}
                  onDragEnter={(e) => { e.preventDefault(); e.stopPropagation(); setDragOverFolderId(window.folderId); }}
                  onDragLeave={(e) => {
                    e.stopPropagation();
                    if (!e.currentTarget.contains(e.relatedTarget)) setDragOverFolderId(null);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setDragOverFolderId(null);
                    handleDrop(e, window.folder);
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setDesktopContext();
                    if (e.ctrlKey || e.metaKey) {
                      const next = new Set(desktopSelection);
                      if (next.has(key)) next.delete(key); else next.add(key);
                      handleSelectionChange('desktop', next);
                    } else {
                      handleSelectionChange('desktop', new Set([key]));
                    }
                  }}
                  onDoubleClick={(e) => { e.stopPropagation(); handleFolderExpand(window.id); }}
                  onContextMenu={(e) => handleContextMenu(e, window.folder, 'folder', 'desktop')}
                  sx={{
                    ...rowSx,
                    bgcolor: isDragOver ? 'action.hover' : isSelected ? 'action.selected' : 'transparent',
                    outline: isDragOver ? `1px solid ${theme.palette.success.main}` : 'none',
                    '&:hover': { bgcolor: 'action.hover' },
                  }}
                >
                  <Box sx={{ width: 64, flexShrink: 0, display: 'flex', justifyContent: 'center' }}>
                    <FolderOutlined sx={{ fontSize: 26, color: windowColors[window.id] || theme.palette.primary.main }} />
                  </Box>
                  <Typography variant="body2" noWrap sx={{ flex: 1, minWidth: 0 }}>{window.folder.name}</Typography>
                  <Typography variant="caption" color="text.secondary" noWrap sx={{ width: 110, flexShrink: 0 }}>
                    {t('documents.page.kindLabel.folder')}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ width: 150, flexShrink: 0, textAlign: 'right' }}>
                    {formatDate(window.folder.updated_at || window.folder.created_at)}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" noWrap sx={{ width: 72, flexShrink: 0, textAlign: 'right' }}>
                    {itemCount > 0 ? t('documents.page.folderItemCount', { count: itemCount }) : ''}
                  </Typography>
                </Box>
              );
            };
            const fileRow = (file) => {
              const key = getItemKey(file, 'file');
              const isSelected = desktopSelection.has(key);
              const kind = kindOf(file);
              return (
                <Box
                  key={key}
                  ref={(el) => {
                    if (el) desktopItemRefs.current.set(key, el);
                    else desktopItemRefs.current.delete(key);
                  }}
                  className="desktop-item-card"
                  data-file-id={file.id}
                  data-file-path={file.path}
                  draggable
                  onDragStart={(e) => {
                    setDesktopContext();
                    const fallback = { id: file.id, itemType: 'file', filename: file.filename, name: file.filename, path: file.path };
                    e.dataTransfer.setData('text/plain', JSON.stringify(buildDesktopDragPayload(`file-${file.id}`, fallback)));
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setDesktopContext();
                    if (e.ctrlKey || e.metaKey) {
                      const next = new Set(desktopSelection);
                      if (next.has(key)) next.delete(key); else next.add(key);
                      handleSelectionChange('desktop', next);
                    } else {
                      handleSelectionChange('desktop', new Set([key]));
                      clickFile(file, sortedDesktop.files);
                    }
                  }}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    cancelFileClick();
                    openFile(file, { siblings: sortedDesktop.files });
                  }}
                  onContextMenu={(e) => { cancelFileClick(); handleContextMenu(e, file, 'file', 'desktop'); }}
                  sx={{ ...rowSx, bgcolor: isSelected ? 'action.selected' : 'transparent', '&:hover': { bgcolor: 'action.hover' } }}
                >
                  <Box sx={{ width: 64, flexShrink: 0, display: 'flex', justifyContent: 'center' }}>
                    {kind === 'other'
                      ? getFileIconSmall(file.filename, isSelected, theme, features.indexing ? file.index_status : null, null)
                      : <Box sx={{ width: 64 }}><FileThumb file={file} height={36} size={160} /></Box>}
                  </Box>
                  <ListFileCells file={file} kindLabel={t(`documents.page.kindLabel.${kind}`)} />
                </Box>
              );
            };
            return (
              <Box sx={{ display: 'flex', flexDirection: 'column', mx: -2, mt: -2 }}>
                <Box
                  sx={{
                    position: 'sticky', top: -16, zIndex: 3, display: 'flex', alignItems: 'center', gap: 1.5,
                    px: 1.5, height: 32, flexShrink: 0, bgcolor: 'background.paper',
                    borderBottom: `1px solid ${theme.palette.divider}`,
                  }}
                >
                  <Box sx={{ width: 64, flexShrink: 0 }} />
                  {header('name', t('documents.page.columns.name'), { flex: 1 })}
                  {header('type', t('documents.page.columns.type'), { width: 110, flexShrink: 0 })}
                  {header('date', t('documents.page.columns.date'), { width: 150, flexShrink: 0, justifyContent: 'flex-end' })}
                  {header('size', t('documents.page.columns.size'), { width: 72, flexShrink: 0, justifyContent: 'flex-end' })}
                </Box>
                <Box sx={{ height: first * LIST_ROW_HEIGHT, flexShrink: 0 }} />
                {rows.slice(first, last).map((row, i) => (first + i < folderCount ? folderRow(row) : fileRow(row)))}
                <Box sx={{ height: Math.max(0, rows.length - last) * LIST_ROW_HEIGHT, flexShrink: 0 }} />
              </Box>
            );
          })()}

          {/* Folders */}
          {viewMode !== 'list' && sortedDesktop.folders.filter((w) => isOnScreen(resolvedIconPositions[`folder-${w.folderId}`])).map((window) => {
            const key = `folder-${window.folderId}`;
            const pos = resolvedIconPositions[key] || { x: geom.pad, y: geom.pad };
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
                  width: geom.cardW,
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
                  const fallback = {
                    id: window.folderId,
                    itemType: 'folder',
                    name: window.folder.name,
                    path: window.folder.path,
                  };
                  const payload = buildDesktopDragPayload(`folder-${window.folderId}`, fallback);
                  e.dataTransfer.setData('text/plain', JSON.stringify(payload));
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
                    onContextMenu={(e) => handleContextMenu(e, window.folder, 'folder', 'desktop')}
                    sx={{ px: 1, pt: 1, pb: 1, textAlign: 'center' }}
                  >
                    <CardContent sx={{ p: 0 }}>
                      {viewMode === 'thumbnails' ? (
                        <ThumbFolderBody
                          folder={window.folder}
                          color={windowColors[window.id] || theme.palette.primary.main}
                          itemCountLabel={itemCount > 0 ? t('documents.page.folderItemCount', { count: itemCount }) : ''}
                        />
                      ) : (<>
                      <Box sx={{ position: 'relative', display: 'inline-flex' }}>
                        <FolderOutlined
                          sx={{ fontSize: 44, color: windowColors[window.id] || theme.palette.primary.main, mb: '2px' }}
                        />
                        {features.indexing && <FolderIndexIndicator item={window.folder} theme={theme} />}
                      </Box>
                      {window.folder.is_repository && (
                        <Box sx={{ position: 'absolute', top: 8, right: 8 }}>
                          <Tooltip title={t('documents.folder.codeRepository')}>
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
                          {t('documents.page.folderItemCount', { count: itemCount })}
                        </Typography>
                      )}
                      </>)}
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Box>
            );
          })}

          {/* Files */}
          {viewMode !== 'list' && sortedDesktop.files.filter((f) => isOnScreen(resolvedIconPositions[getItemKey(f, 'file')])).map((file) => {
            const key = getItemKey(file, 'file');
            const isSelected = desktopSelection.has(key);
            const pos = resolvedIconPositions[key] || { x: geom.pad, y: geom.pad };

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
                  width: geom.cardW,
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
                  const fallback = {
                    id: file.id,
                    itemType: 'file',
                    filename: file.filename,
                    name: file.filename,
                    path: file.path,
                  };
                  const payload = buildDesktopDragPayload(`file-${file.id}`, fallback);
                  e.dataTransfer.setData('text/plain', JSON.stringify(payload));
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
                        clickFile(file, sortedDesktop.files);
                      }
                    }}
                    onDoubleClick={(e) => {
                      e.stopPropagation();
                      cancelFileClick();
                      openFile(file, { siblings: sortedDesktop.files });
                    }}
                    onContextMenu={(e) => { cancelFileClick(); handleContextMenu(e, file, 'file', 'desktop'); }}
                    sx={{ px: 1, pt: 1, pb: 1, textAlign: 'center' }}
                  >
                    <CardContent sx={{ p: 0 }}>
                      {viewMode === 'thumbnails' ? (
                        <ThumbFileBody file={file} metaLine={metaLine} />
                      ) : (<>
                      {/* A thumbnail is drawn 16 px larger than its size, so it is asked
                          for smaller to keep the card inside its row. */}
                      {getFileIcon(
                        file.filename, isSelected, theme,
                        isImageFile(file.filename) || isVideoFile(file.filename) ? 40 : 44,
                        features.indexing ? file.index_status : null, file.path,
                      )}
                      <Tooltip title={file.filename} enterDelay={600} placement="bottom">
                        <Typography variant="body2" noWrap sx={{ mt: 0.5 }}>
                          {file.filename}
                        </Typography>
                      </Tooltip>
                      </>)}
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
            // Handle size and position are global (index.css); this layer only
            // hands them the pointer back.
            '& .react-resizable-handle': { pointerEvents: 'auto' },
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
                      onFileOpen={(e, file, extras) => {
                        e?.stopPropagation?.();
                        openFile(file, extras);
                      }}
                      onFileClick={onFileClick}
                      onContextMenu={(e, item, type, extras) => handleContextMenu(e, item, type, 'window', extras)}
                      onTitleContextMenu={(e) => handleContextMenu(e, window.folder, 'folder', 'desktop', { windowId: window.id })}
                      onFocusContext={setActiveContext}
                      refreshKey={folderRefreshKeys[window.folderId] || 0}
                      folderColors={folderIdToColor}
                      mediaOnly={mediaOnly}
                      largeCards={viewMode === 'thumbnails'}
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
        onClose={closeContextMenu}
        onNewFolder={handleNewFolder}
        onUpload={handleUpload}
        onCopy={handleCopy}
        onCut={handleCut}
        onPaste={() => handlePaste()}
        onDelete={contextMenuItem?.source_type === 'live_repo' ? undefined : handleDelete}
        onProperties={contextMenuItem?.source_type === 'live_repo' ? undefined : handleProperties}
        onRename={contextMenuItem?.source_type === 'live_repo' ? undefined : handleRename}
        onDownload={handleDownload}
        onEdit={handleEditFile}
        onColorChange={handleColorChange}
        onIndex={!features.indexing || contextMenuItem?.source_type === 'live_repo' ? undefined : handleIndex}
        onReviewWithAgent={features.reviewWithAgent && contextMenuItem?.source_type === 'live_repo' ? handleReviewWithAgent : undefined}
        onBulkImportHere={features.bulkImport && contextMenuType === 'folder' && contextMenuItem?.source_type !== 'live_repo' ? handleBulkImportHere : undefined}
        extraActions={contextMenuType === 'file' && contextMenuItem && fileMenuActions ? fileMenuActions(contextMenuItem) : null}
        onSortBy={contextMenuType === 'desktop' ? handleSortBy : undefined}
        sortField={view.sort.by}
        onArrangeIcons={contextMenuType === 'desktop' && viewMode !== 'list' ? () => handleArrangeIcons() : undefined}
        onOpenWindow={contextMenuFolderWindow
          ? (contextMenuFolderWindow.state === 'folded' ? () => handleFolderExpand(contextMenuFolderWindow.id) : undefined)
          : contextMenuType === 'folder' ? contextMenuExtras?.openInPlace : undefined}
        openLabel={contextMenuFolderWindow ? t('documents.contextMenu.openInWindow') : t('documents.contextMenu.open')}
        onToggleMinimize={contextMenuTitleWindow ? () => handleToggleMinimize(contextMenuTitleWindow.id) : undefined}
        minimizeLabel={contextMenuTitleWindow?.state === 'minimized' ? t('documents.contextMenu.restore') : t('documents.contextMenu.minimize')}
        onCloseWindow={contextMenuTitleWindow ? () => handleWindowClose(contextMenuTitleWindow.id) : undefined}
        isImage={contextMenuItem && contextMenuType === 'file' && isImageFile(contextMenuItem.filename || contextMenuItem.name || '')}
        isCode={contextMenuItem && contextMenuType === 'file' && isCodeFile(contextMenuItem.filename || contextMenuItem.name || '')}
        isPdf={contextMenuItem && contextMenuType === 'file' && isPdfFile(contextMenuItem.filename || contextMenuItem.name || '')}
        onOpenInCodeEditor={features.codeEditor ? () => handleOpenInCodeEditor() : undefined}
        hasClipboard={Boolean(clipboard)}
        hasSelection={activeSelection.size > 0}
        contextType={contextMenuType}
        selectedItem={contextMenuItem}
        folderColor={contextMenuItem && contextMenuType === 'folder'
          ? folderIdToColor[contextMenuItem.id] || null
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
        <DialogTitle>{t('documents.newFolder.title')}</DialogTitle>
        <DialogContent>
          <TextField
            inputRef={newFolderInputRef}
            autoFocus
            label={t('documents.newFolder.nameLabel')}
            fullWidth
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleCreateFolder()}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setNewFolderOpen(false)}>{t('documents.actions.cancel')}</Button>
          <Button onClick={handleCreateFolder} variant="contained">{t('documents.newFolder.create')}</Button>
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
        <DialogTitle>{renameItem?.type === 'folder' ? t('documents.rename.folderTitle') : t('documents.rename.fileTitle')}</DialogTitle>
        <DialogContent>
          <TextField
            inputRef={renameInputRef}
            autoFocus
            label={renameItem?.type === 'folder' ? t('documents.newFolder.nameLabel') : t('documents.rename.fileNameLabel')}
            fullWidth
            value={renameName}
            onChange={(e) => setRenameName(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleRenameSubmit()}
          />
          {renameItem?.type !== 'folder' && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              {t('documents.rename.fileHint')}
            </Typography>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameDialogOpen(false)}>{t('documents.actions.cancel')}</Button>
          <Button onClick={handleRenameSubmit} variant="contained" disabled={!renameName.trim()}>
            {t('documents.rename.submit')}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteConfirmOpen} onClose={() => setDeleteConfirmOpen(false)}>
        <DialogTitle>{t('documents.delete.confirmTitle')}</DialogTitle>
        <DialogContent>
          <Typography>
            {t('documents.delete.confirmBody', { count: deletableSelection.length })}
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteConfirmOpen(false)}>{t('documents.actions.cancel')}</Button>
          <Button onClick={handleDeleteConfirm} variant="contained" color="error">
            {t('documents.delete.confirm')}
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
                  t('documents.properties.folderApplied', { files: stats.files_updated, folders: stats.folders_processed }),
                  'success'
                );
              } else {
                showMessage?.(t('documents.properties.folderUpdated'), 'success');
              }

              setPropertiesOpen(false);
            } catch (err) {
              const errorMsg = err.response?.data?.message || err.message || t('documents.properties.folderUpdateFailed');
              showMessage?.(t('documents.properties.updateFailed', { error: errorMsg }), 'error');
            }
          }}
          onDelete={async (folderId) => {
            await axios.delete(`${API_BASE}/folder/${folderId}`);
            showMessage?.(t('documents.properties.folderDeleted'), 'success');
            // Optimistic + cross update for instant removal from desktop/folders
            try {
              window.dispatchEvent(new CustomEvent('documents-items-removed', { detail: { keys: [`folder-${folderId}`], contextKey: 'properties' } }));
            } catch (e) {
              // ignore dispatch failure
            }
            // Close the properties modal on successful folder delete
            setPropertiesOpen(false);
            // Also trigger a light refresh for counts/parents in other views
            setTimeout(() => { try { refreshData(); } catch (e) { /* ignore */ } }, 0);
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

      {mediaPreview && (
        <MediaPreviewOverlay
          file={mediaPreview.file}
          siblings={mediaPreview.siblings}
          onClose={() => setMediaPreview(null)}
          onFileOpen={(e, file, extras) => {
            e?.stopPropagation?.();
            openFile(file, extras);
          }}
        />
      )}

      {/* Code Viewer Modal */}
      <CodeViewerModal
        open={!!codeViewer}
        onClose={() => setCodeViewer(null)}
        file={codeViewer?.file}
        startEditing={Boolean(codeViewer?.editMode)}
        onOpenInCodeEditor={handleOpenInCodeEditor}
      />

      {/* PDF Viewer Modal */}
      <PdfViewerModal
        open={!!pdfViewer}
        onClose={() => setPdfViewer(null)}
        file={pdfViewer?.file}
      />
      <DocxViewerModal
        open={!!docxViewer}
        onClose={() => setDocxViewer(null)}
        file={docxViewer?.file}
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
            {t('documents.page.uploadProgress', { current: uploadProgress.current, count: uploadProgress.total })}
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
    </>
  );
});

FileDesktop.displayName = 'FileDesktop';

/**
 * The header buttons for a FileDesktop: Import, the Icons / Thumbnails / List
 * views, Arrange Icons (placed views) and Arrange Windows. `desktopRef` is the
 * FileDesktop's ref; `view` and `onViewChange` are the page's view.
 */
export const FileDesktopActions = ({ desktopRef, view, onViewChange }) => {
  const { t } = useUiTranslation();
  const call = (name) => () => desktopRef.current?.[name]?.();
  const mode = view?.mode || 'icons';
  const viewButton = (value, title, Icon) => (
    <Tooltip title={title}>
      <IconButton
        onClick={() => onViewChange({ mode: value })}
        size="small"
        aria-pressed={mode === value}
        sx={{ opacity: mode === value ? 1 : 0.5, color: mode === value ? 'primary.main' : undefined }}
      >
        <Icon fontSize="small" />
      </IconButton>
    </Tooltip>
  );
  return (
    <>
      <Tooltip title={t('documents.page.importFiles')}>
        <IconButton onClick={call('importFiles')} size="small" sx={{ opacity: 0.6 }}>
          <UploadFileIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      {onViewChange && viewButton('icons', t('documents.page.iconView'), ViewModuleIcon)}
      {onViewChange && viewButton('thumbnails', t('documents.page.thumbnailView'), ViewComfyIcon)}
      {onViewChange && viewButton('list', t('documents.page.listView'), ViewListIcon)}
      {mode !== 'list' && (
        <Tooltip title={t('documents.page.arrangeIcons')}>
          <IconButton onClick={call('arrangeIcons')} size="small" sx={{ opacity: 0.6 }}>
            <GridViewIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      <Tooltip title={t('documents.page.arrangeWindows')}>
        <IconButton onClick={call('arrangeWindows')} size="small" sx={{ opacity: 0.6 }}>
          <AppsIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </>
  );
};

export default FileDesktop;

