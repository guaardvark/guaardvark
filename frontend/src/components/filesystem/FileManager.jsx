// frontend/src/components/filesystem/FileManager.jsx
// True file manager with blank slate approach using REST API
// - Drag & drop for moving files/folders
// - Grid/List view toggle
// - Create folders anywhere
// - Upload, rename, move, delete files and folders
// - Multi-select with CTRL+Click and drag-to-select

import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { BASE_URL } from '../../api/apiClient';
import {
  Box,
  Paper,
  Typography,
  Breadcrumbs,
  Link,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Menu,
  MenuItem,
  CircularProgress,
  Alert,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  ListItemIcon,
  ListItemText,
  Card,
  CardContent,
  CardActionArea,
  Grid,
  ToggleButtonGroup,
  ToggleButton,
  Tooltip,
  Chip,
  Checkbox,
  useTheme,
  alpha,
  LinearProgress,
} from '@mui/material';
import {
  Folder as FolderIcon,
  InsertDriveFile as FileIcon,
  MoreVert as MoreVertIcon,
  CreateNewFolder as CreateNewFolderIcon,
  Upload as UploadIcon,
  Delete as DeleteIcon,
  ViewList as ViewListIcon,
  ViewModule as ViewModuleIcon,
  SelectAll as SelectAllIcon,
  Image as ImageIcon,
  PictureAsPdf as PdfIcon,
  Code as CodeIcon,
  Description as DocumentIcon,
  TableChart as SpreadsheetIcon,
  VideoFile as VideoIcon,
  AudioFile as AudioIcon,
  Archive as ArchiveIcon,
  DataObject as JsonIcon,
} from '@mui/icons-material';
import ReactGridLayoutLib, { WidthProvider } from 'react-grid-layout';
import axios from 'axios';
import FilePropertiesModal from '../modals/FilePropertiesModal';
import FolderPropertiesModal from '../modals/FolderPropertiesModal';
import CSVSpreadsheetViewer from './CSVSpreadsheetViewer';
import { useSnackbar } from '../common/SnackbarProvider';
import { useLayout } from '../../contexts/LayoutContext';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

const FolderGridLayout = WidthProvider(ReactGridLayoutLib);
const FOLDER_LAYOUT_COLS = 48; // More columns for smoother positioning
const FOLDER_ROW_HEIGHT = 30; // Row height in pixels
const FOLDER_GRID_WIDTH = 4; // 4 cols × ~36px = ~145px width
const FOLDER_GRID_HEIGHT = 4; // 4 rows × 30px = 120px height
const FOLDER_LAYOUT_KEY = (path) => `folderLayout:${path || '/'}`;
const FOLDER_COLORS_KEY = 'folderColors';
const FOLDER_STATE_ENDPOINT = '/api/state/folders';
const FOLDER_COLOR_CHOICES = ['#1976d2', '#9c27b0', '#d32f2f', '#f57c00', '#388e3c', '#455a64', '#7b1fa2', '#5d4037'];

const API_BASE = `${BASE_URL}/files`;

// Constants
const MAX_FILENAME_LENGTH = 255;
const MAX_FILE_SIZE_MB = 100; // Maximum file size in MB
const BYTES_PER_MB = 1024 * 1024;
const INVALID_FILENAME_CHARS = /[<>:"/\\|?*\x00-\x1f]/;

// File extension to icon mapping
const getFileIcon = (filename, isSelected, theme) => {
  if (!filename) return <FileIcon sx={{ fontSize: 64, color: isSelected ? 'primary.main' : 'action.active' }} />;
  
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const iconColor = isSelected ? 'primary.main' : 'action.active';
  const iconStyle = { 
    fontSize: 64, 
    color: iconColor,
    filter: isSelected ? `drop-shadow(0 0 6px ${theme.palette.primary.main}80)` : 'none',
    transform: isSelected ? 'scale(1.05)' : 'scale(1)',
    transition: 'all 0.15s ease-in-out',
  };
  
  // Images
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff'].includes(ext)) {
    return <ImageIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'success.main' }} />;
  }
  // PDF
  if (ext === 'pdf') {
    return <PdfIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'error.main' }} />;
  }
  // Code files
  if (['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'html', 'css', 'scss', 'less', 'vue', 'sh', 'bash', 'zsh', 'sql'].includes(ext)) {
    return <CodeIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'info.main' }} />;
  }
  // JSON/Config files
  if (['json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'env', 'config'].includes(ext)) {
    return <JsonIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'warning.main' }} />;
  }
  // Spreadsheets
  if (['csv', 'xls', 'xlsx', 'ods'].includes(ext)) {
    return <SpreadsheetIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'success.main' }} />;
  }
  // Documents
  if (['doc', 'docx', 'txt', 'rtf', 'odt', 'md', 'markdown'].includes(ext)) {
    return <DocumentIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'info.main' }} />;
  }
  // Video
  if (['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'].includes(ext)) {
    return <VideoIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'secondary.main' }} />;
  }
  // Audio
  if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma'].includes(ext)) {
    return <AudioIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'error.light' }} />;
  }
  // Archives
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'].includes(ext)) {
    return <ArchiveIcon sx={{ ...iconStyle, color: isSelected ? 'primary.main' : 'text.secondary' }} />;
  }
  
  // Default file icon
  return <FileIcon sx={iconStyle} />;
};

// Small file icon for list view
const getFileIconSmall = (filename, isSelected) => {
  if (!filename) return <FileIcon color={isSelected ? 'primary' : 'action'} />;
  
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const color = isSelected ? 'primary' : 'inherit';
  
  // Images
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff'].includes(ext)) {
    return <ImageIcon sx={{ color: isSelected ? 'primary.main' : 'success.main' }} />;
  }
  // PDF
  if (ext === 'pdf') {
    return <PdfIcon sx={{ color: isSelected ? 'primary.main' : 'error.main' }} />;
  }
  // Code files
  if (['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'html', 'css', 'scss', 'less', 'vue', 'sh', 'bash', 'zsh', 'sql'].includes(ext)) {
    return <CodeIcon sx={{ color: isSelected ? 'primary.main' : 'info.main' }} />;
  }
  // JSON/Config files
  if (['json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'env', 'config'].includes(ext)) {
    return <JsonIcon sx={{ color: isSelected ? 'primary.main' : 'warning.main' }} />;
  }
  // Spreadsheets
  if (['csv', 'xls', 'xlsx', 'ods'].includes(ext)) {
    return <SpreadsheetIcon sx={{ color: isSelected ? 'primary.main' : 'success.main' }} />;
  }
  // Documents
  if (['doc', 'docx', 'txt', 'rtf', 'odt', 'md', 'markdown'].includes(ext)) {
    return <DocumentIcon sx={{ color: isSelected ? 'primary.main' : 'info.main' }} />;
  }
  // Video
  if (['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'].includes(ext)) {
    return <VideoIcon sx={{ color: isSelected ? 'primary.main' : 'secondary.main' }} />;
  }
  // Audio
  if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma'].includes(ext)) {
    return <AudioIcon sx={{ color: isSelected ? 'primary.main' : 'error.light' }} />;
  }
  // Archives
  if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'].includes(ext)) {
    return <ArchiveIcon sx={{ color: isSelected ? 'primary.main' : 'text.secondary' }} />;
  }
  
  // Default file icon
  return <FileIcon color={color} />;
};

const FileManager = () => {
  const theme = useTheme();
  const { showMessage } = useSnackbar();
  const { gridSettings } = useLayout();
  const [currentPath, setCurrentPath] = useState('/');
  const [items, setItems] = useState({ folders: [], documents: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [contextMenu, setContextMenu] = useState(null);
  const [selectedItem, setSelectedItem] = useState(null);
  const [viewMode, setViewMode] = useState(() => {
    // Load saved view preference from localStorage
    return localStorage.getItem('fileManagerViewMode') || 'list';
  });
  const [draggedItem, setDraggedItem] = useState(null); // Single item for backward compatibility
  const [draggedItems, setDraggedItems] = useState([]); // Array of items for multi-select drag
  const [dropTarget, setDropTarget] = useState(null);
  const [clipboard, setClipboard] = useState(null); // { item, operation: 'copy' | 'cut' }
  const [folderLayouts, setFolderLayouts] = useState({});
  const [folderColors, setFolderColors] = useState({});
  const [folderStateLoaded, setFolderStateLoaded] = useState(false);
  
  // Operation states
  const [isOperationInProgress, setIsOperationInProgress] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({ files: [], current: 0, total: 0 });
  
  // Confirmation dialog state
  const [confirmDialog, setConfirmDialog] = useState({
    open: false,
    title: '',
    message: '',
    onConfirm: null,
    onCancel: null,
  });
  
  // Validation errors for dialogs
  const [createFolderError, setCreateFolderError] = useState('');
  const [renameFolderError, setRenameFolderError] = useState('');
  const [renameFileError, setRenameFileError] = useState('');

  // Multi-select state
  const [selectedItems, setSelectedItems] = useState(new Set()); // Set of "type-id" keys
  
  // Drag-to-select state
  const [selectionBox, setSelectionBox] = useState(null); // { startX, startY, currentX, currentY }
  const [isSelecting, setIsSelecting] = useState(false);
  const contentAreaRef = useRef(null);
  const itemRefs = useRef(new Map()); // Map of "type-id" -> DOM element ref
  const folderGridRef = useRef(null);
  const lastSyncedFoldersRef = useRef(new Map()); // Track last synced folder IDs per path

  // Dialogs
  const [createFolderOpen, setCreateFolderOpen] = useState(false);
  const [renameFolderOpen, setRenameFolderOpen] = useState(false);
  const [renameFileOpen, setRenameFileOpen] = useState(false);
  const [propertiesOpen, setPropertiesOpen] = useState(false);
  const [folderPropertiesOpen, setFolderPropertiesOpen] = useState(false);
  const [csvViewerOpen, setCsvViewerOpen] = useState(false);
  const [csvFileData, setCsvFileData] = useState(null);
  const [newFolderName, setNewFolderName] = useState('');
  const [newName, setNewName] = useState('');

  const fileInputRef = useRef(null);

  // Helper to create unique key for items
  const getItemKey = (item, type) => `${type}-${item.id}`;
  
  // Validation helpers
  const validateName = (name, type = 'name') => {
    if (!name || !name.trim()) {
      return `${type === 'folder' ? 'Folder' : 'File'} name cannot be empty`;
    }
    const trimmedName = name.trim();
    if (INVALID_FILENAME_CHARS.test(trimmedName)) {
      return `${type === 'folder' ? 'Folder' : 'File'} name contains invalid characters. Please use only letters, numbers, spaces, hyphens, underscores${type === 'file' ? ', and dots' : ''}.`;
    }
    if (trimmedName.length > MAX_FILENAME_LENGTH) {
      return `${type === 'folder' ? 'Folder' : 'File'} name is too long. Maximum length is ${MAX_FILENAME_LENGTH} characters.`;
    }
    return null;
  };
  
  // Confirmation dialog helpers
  const showConfirmDialog = (title, message, onConfirm, onCancel = null) => {
    setConfirmDialog({
      open: true,
      title,
      message,
      onConfirm: () => {
        setConfirmDialog({ open: false, title: '', message: '', onConfirm: null, onCancel: null });
        if (onConfirm) onConfirm();
      },
      onCancel: () => {
        setConfirmDialog({ open: false, title: '', message: '', onConfirm: null, onCancel: null });
        if (onCancel) onCancel();
      },
    });
  };
  
  const handleConfirmDialogClose = () => {
    setConfirmDialog({ open: false, title: '', message: '', onConfirm: null, onCancel: null });
  };

  // Load persisted folder layouts and colors from backend (fallback to localStorage)
  useEffect(() => {
    const loadFromLocalStorage = () => {
      try {
        const storedLayouts = localStorage.getItem('folderLayouts');
        if (storedLayouts) {
          setFolderLayouts(JSON.parse(storedLayouts));
        }
      } catch (err) {
        // Silently fail - will use default layout
      }
      try {
        const storedColors = localStorage.getItem(FOLDER_COLORS_KEY);
        if (storedColors) {
          setFolderColors(JSON.parse(storedColors));
        }
      } catch (err) {
        // Silently fail - will use default colors
      }
    };

    const loadFolderState = async () => {
      try {
        const res = await fetch(FOLDER_STATE_ENDPOINT);
        if (res.ok) {
          const state = await res.json();
          if (state.folderLayouts && typeof state.folderLayouts === 'object') {
            setFolderLayouts(state.folderLayouts);
          }
          if (state.folderColors && typeof state.folderColors === 'object') {
            setFolderColors(state.folderColors);
          }
        } else {
          loadFromLocalStorage();
        }
      } catch (err) {
        loadFromLocalStorage();
      } finally {
        setFolderStateLoaded(true);
      }
    };

    loadFolderState();
  }, []);

  const saveFolderState = useCallback(async (newLayouts, newColors) => {
    const stateToSave = {
      folderLayouts: newLayouts ?? folderLayouts,
      folderColors: newColors ?? folderColors,
      lastSaved: new Date().toISOString(),
    };
    try {
      await fetch(FOLDER_STATE_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(stateToSave),
      });
      // Silently fail - state will be saved to localStorage as fallback
    } catch {
      // Silently fail - state will be saved to localStorage as fallback
    }
  }, [folderLayouts, folderColors]);

  const saveFolderLayouts = useCallback((newLayouts) => {
    setFolderLayouts(newLayouts);
    void saveFolderState(newLayouts, folderColors);
  }, [saveFolderState, folderColors]);

  const saveFolderColors = useCallback((newColors) => {
    setFolderColors(newColors);
    void saveFolderState(folderLayouts, newColors);
  }, [saveFolderState, folderLayouts]);
  
  // Helper to get all items as array with keys
  const getAllItemsWithKeys = useCallback(() => {
    return [
      ...items.folders.map(f => ({ ...f, itemType: 'folder', key: getItemKey(f, 'folder') })),
      ...items.documents.map(d => ({ ...d, itemType: 'file', key: getItemKey(d, 'file') }))
    ];
  }, [items]);

  const generateDefaultFolderLayout = useCallback((folders) => {
    // Use fixed constants for compact folder dimensions
    const defaultW = FOLDER_GRID_WIDTH; // Fixed 2 columns
    const defaultH = FOLDER_GRID_HEIGHT; // Fixed 3 rows (90px total)

    return folders.map((folder, idx) => {
      const colsPerRow = Math.floor(FOLDER_LAYOUT_COLS / defaultW);
      return {
        i: getItemKey(folder, 'folder'),
        x: (idx % colsPerRow) * defaultW,
        y: Math.floor(idx / colsPerRow) * defaultH,
        w: defaultW,
        h: defaultH,
        isDraggable: true,
        isResizable: false,
        minW: 4,
        minH: 4,
      };
    });
  }, []);

  const syncFolderLayout = useCallback((currentLayout, folders) => {
    const layoutMap = new Map(currentLayout.map(item => [item.i, item]));
    const defaultW = FOLDER_GRID_WIDTH;
    const defaultH = FOLDER_GRID_HEIGHT;
    const colsPerRow = Math.floor(FOLDER_LAYOUT_COLS / defaultW);
    
    const nextLayout = folders.map((folder, idx) => {
      const key = getItemKey(folder, 'folder');
      const existing = layoutMap.get(key);
      if (existing) {
        // Preserve existing layout but ensure it has required properties
        return {
          ...existing,
          isDraggable: existing.isDraggable !== false,
          isResizable: false, // Always disable resizing
        };
      }
      // New folder - use default layout
      return {
        i: key,
        x: (idx % colsPerRow) * defaultW,
        y: Math.floor(idx / colsPerRow) * defaultH,
        w: defaultW,
        h: defaultH,
        isDraggable: true,
        isResizable: false,
      };
    });
    return nextLayout;
  }, []);

  // Migrate legacy layouts (old 120px row height) to new compact layout
  const migrateLegacyLayout = useCallback((layout) => {
    // Detect old layouts: h == 6 (old) or h < 12 (any previous version)
    // New layouts should have h == 12 and w == 12
    const needsMigration = layout.some(item => item.w !== FOLDER_GRID_WIDTH || item.h !== FOLDER_GRID_HEIGHT);

    if (!needsMigration) return layout;

    console.log('Migrating legacy folder layout to new grid dimensions');

    // Reset all items to new fixed dimensions and default positions
    const colsPerRow = Math.floor(FOLDER_LAYOUT_COLS / FOLDER_GRID_WIDTH);

    return layout.map((item, idx) => ({
      ...item,
      w: FOLDER_GRID_WIDTH,
      h: FOLDER_GRID_HEIGHT,
      x: (idx % colsPerRow) * FOLDER_GRID_WIDTH,
      y: Math.floor(idx / colsPerRow) * FOLDER_GRID_HEIGHT,
    }));
  }, []);

  // Toggle item selection
  const toggleItemSelection = useCallback((item, type, ctrlKey = false) => {
    const key = getItemKey(item, type);
    setSelectedItems(prev => {
      const newSet = new Set(prev);
      if (ctrlKey) {
        // CTRL+Click: toggle this item
        if (newSet.has(key)) {
          newSet.delete(key);
        } else {
          newSet.add(key);
        }
      } else {
        // Regular click: select only this item
        newSet.clear();
        newSet.add(key);
      }
      return newSet;
    });
    // Also update selectedItem for context menu compatibility
    setSelectedItem({ ...item, itemType: type });
  }, []);

  // Clear all selections
  const clearSelection = useCallback(() => {
    setSelectedItems(new Set());
    setSelectedItem(null);
  }, []);

  // Select all items
  const selectAll = useCallback(() => {
    const allKeys = new Set(getAllItemsWithKeys().map(item => item.key));
    setSelectedItems(allKeys);
  }, [getAllItemsWithKeys]);

  // Get selected items as array
  const getSelectedItemsArray = useCallback(() => {
    const allItems = getAllItemsWithKeys();
    return allItems.filter(item => selectedItems.has(item.key));
  }, [getAllItemsWithKeys, selectedItems]);

  // Fetch folder contents
  const fetchContents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(`${API_BASE}/browse`, {
        params: { path: currentPath },
      });
      setItems({
        folders: response.data.data.folders || [],
        documents: response.data.data.documents || [],
      });
    } catch (err) {
      setError(err.response?.data?.message || err.message);
    } finally {
      setLoading(false);
    }
  }, [currentPath]);

  useEffect(() => {
    fetchContents();
  }, [fetchContents]);

  // Sync folder layout with current path/folders (optimized with useMemo)
  const folderLayoutKey = useMemo(() => FOLDER_LAYOUT_KEY(currentPath), [currentPath]);
  const currentFolderIds = useMemo(() => 
    items.folders.map(f => f.id).sort().join(','), 
    [items.folders]
  );

  useEffect(() => {
    if (!folderStateLoaded || !items.folders.length) return;

    const lastSyncedIds = lastSyncedFoldersRef.current.get(folderLayoutKey);
    
    // Only sync if folder IDs have changed
    if (currentFolderIds === lastSyncedIds) return;
    
    lastSyncedFoldersRef.current.set(folderLayoutKey, currentFolderIds);

    setFolderLayouts(prev => {
      let existing = prev[folderLayoutKey] || generateDefaultFolderLayout(items.folders);

      // Migrate legacy layouts to compact dimensions
      existing = migrateLegacyLayout(existing);

      const synced = syncFolderLayout(existing, items.folders);

      // Quick check - if lengths differ, definitely changed
      if (existing.length !== synced.length) {
        const updated = { ...prev, [folderLayoutKey]: synced };
        // Save state asynchronously (don't await)
        saveFolderState(updated, folderColors).catch(() => {});
        return updated;
      }

      // Only do deep comparison if lengths match
      const layoutsEqual = existing.every((item, idx) => {
        const syncedItem = synced[idx];
        return item.i === syncedItem.i && item.x === syncedItem.x && 
               item.y === syncedItem.y && item.w === syncedItem.w && item.h === syncedItem.h;
      });

      // Only update if layout actually changed
      if (!layoutsEqual) {
        const updated = { ...prev, [folderLayoutKey]: synced };
        // Save state asynchronously (don't await)
        saveFolderState(updated, folderColors).catch(() => {});
        return updated;
      }
      return prev;
    });
  }, [folderLayoutKey, currentFolderIds, items.folders, folderStateLoaded, generateDefaultFolderLayout, syncFolderLayout, folderColors, saveFolderState]);

  // Clear selection when path changes
  useEffect(() => {
    clearSelection();
  }, [currentPath, clearSelection]);

  // Keyboard shortcuts (CTRL+A, Escape)
  useEffect(() => {
    const handleKeyDown = (e) => {
      // CTRL+A to select all
      if (e.ctrlKey && e.key === 'a' && !createFolderOpen && !renameFolderOpen && !renameFileOpen) {
        e.preventDefault();
        selectAll();
      }
      // Escape to clear selection
      if (e.key === 'Escape') {
        clearSelection();
        setContextMenu(null);
      }
      // Delete key to delete selected items
      if (e.key === 'Delete' && selectedItems.size > 0 && !createFolderOpen && !renameFolderOpen && !renameFileOpen) {
        e.preventDefault();
        handleDeleteSelected();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectAll, clearSelection, selectedItems, createFolderOpen, renameFolderOpen, renameFileOpen]);

  // Drag-to-select handlers
  const handleSelectionMouseDown = useCallback((e) => {
    // Only start selection box on left mouse button and on empty area
    if (e.button !== 0) return;
    
    // Don't start if clicking on an item (check for item-related classes)
    const target = e.target;
    const isOnItem = target.closest('[data-item-key]') || 
                     target.closest('.MuiCard-root') || 
                     target.closest('.MuiTableRow-root') ||
                     target.closest('.MuiIconButton-root') ||
                     target.closest('.MuiButton-root');
    
    if (isOnItem) return;

    const contentArea = contentAreaRef.current;
    if (!contentArea) return;

    const rect = contentArea.getBoundingClientRect();
    const startX = e.clientX - rect.left + contentArea.scrollLeft;
    const startY = e.clientY - rect.top + contentArea.scrollTop;

    // If not holding CTRL, clear previous selection
    if (!e.ctrlKey) {
      clearSelection();
    }

    setSelectionBox({ startX, startY, currentX: startX, currentY: startY });
    setIsSelecting(true);
  }, [clearSelection]);

  const handleSelectionMouseMove = useCallback((e) => {
    if (!isSelecting || !selectionBox) return;

    const contentArea = contentAreaRef.current;
    if (!contentArea) return;

    const rect = contentArea.getBoundingClientRect();
    const currentX = e.clientX - rect.left + contentArea.scrollLeft;
    const currentY = e.clientY - rect.top + contentArea.scrollTop;

    setSelectionBox(prev => ({ ...prev, currentX, currentY }));

    // Calculate selection rectangle bounds
    const selRect = {
      left: Math.min(selectionBox.startX, currentX),
      right: Math.max(selectionBox.startX, currentX),
      top: Math.min(selectionBox.startY, currentY),
      bottom: Math.max(selectionBox.startY, currentY),
    };

    // Check which items intersect with the selection rectangle
    const newSelected = new Set(e.ctrlKey ? selectedItems : []);
    
    itemRefs.current.forEach((element, key) => {
      if (!element) return;
      
      const itemRect = element.getBoundingClientRect();
      const contentRect = contentArea.getBoundingClientRect();
      
      // Convert item rect to content area coordinates
      const itemBounds = {
        left: itemRect.left - contentRect.left + contentArea.scrollLeft,
        right: itemRect.right - contentRect.left + contentArea.scrollLeft,
        top: itemRect.top - contentRect.top + contentArea.scrollTop,
        bottom: itemRect.bottom - contentRect.top + contentArea.scrollTop,
      };

      // Check intersection
      const intersects = !(
        itemBounds.right < selRect.left ||
        itemBounds.left > selRect.right ||
        itemBounds.bottom < selRect.top ||
        itemBounds.top > selRect.bottom
      );

      if (intersects) {
        newSelected.add(key);
      }
    });

    setSelectedItems(newSelected);
  }, [isSelecting, selectionBox, selectedItems]);

  const handleSelectionMouseUp = useCallback(() => {
    setIsSelecting(false);
    setSelectionBox(null);
  }, []);

  // Add global mouse up listener for drag selection
  useEffect(() => {
    if (isSelecting) {
      window.addEventListener('mouseup', handleSelectionMouseUp);
      window.addEventListener('mousemove', handleSelectionMouseMove);
      return () => {
        window.removeEventListener('mouseup', handleSelectionMouseUp);
        window.removeEventListener('mousemove', handleSelectionMouseMove);
      };
    }
  }, [isSelecting, handleSelectionMouseUp, handleSelectionMouseMove]);

  // Navigation
  const handleNavigate = useCallback((path) => {
    setCurrentPath(path);
  }, []);

  const handleFolderClick = useCallback((folder) => {
    handleNavigate(folder.path);
  }, [handleNavigate]);

  // Context menu
  const handleContextMenu = useCallback((event, item, type) => {
    event.preventDefault();
    event.stopPropagation();
    
    setContextMenu({
      mouseX: event.clientX - 2,
      mouseY: event.clientY - 4,
    });
    
    if (item) {
      const key = getItemKey(item, type);
      const itemWithType = { ...item, itemType: type };
      
      // If right-clicking on a non-selected item, select only that item
      if (!selectedItems.has(key)) {
        setSelectedItems(new Set([key]));
      }
      // If right-clicking on a selected item, keep current selection
      setSelectedItem(itemWithType);
    } else {
      // Blank space context menu - clear selection
      setSelectedItem(null);
    }
  }, [selectedItems]);

  const handleCloseContextMenu = () => {
    setContextMenu(null);
    // Don't clear selectedItem here to preserve selection after context menu closes
  };

  // Delete selected items (batch delete)
  const handleDeleteSelected = async () => {
    const selected = getSelectedItemsArray();
    if (selected.length === 0) return;

    const itemNames = selected.map(item => item.name || item.filename).join(', ');
    showConfirmDialog(
      `Delete ${selected.length} item(s)?`,
      `Are you sure you want to delete the following items?\n\n${itemNames}`,
      async () => {
        setIsOperationInProgress(true);
        handleCloseContextMenu();
        const results = { success: [], failed: [] };
        
        try {
          for (const item of selected) {
            try {
              if (item.itemType === 'folder') {
                await axios.delete(`${API_BASE}/folder/${item.id}`);
              } else {
                await axios.delete(`${API_BASE}/document/${item.id}`);
              }
              results.success.push(item.name || item.filename);
            } catch (err) {
              results.failed.push({
                name: item.name || item.filename,
                error: err.response?.data?.message || 'Unknown error',
              });
            }
          }
          
          clearSelection();
          await fetchContents();
          
          if (results.failed.length === 0) {
            showMessage(`Successfully deleted ${results.success.length} item(s)`, 'success');
          } else {
            const failedNames = results.failed.map(f => `${f.name}: ${f.error}`).join('\n');
            showMessage(
              `Deleted ${results.success.length} item(s), but ${results.failed.length} failed:\n${failedNames}`,
              'warning'
            );
          }
        } catch (err) {
          showMessage(err.response?.data?.message || 'Failed to delete some items', 'error');
        } finally {
          setIsOperationInProgress(false);
        }
      },
      () => handleCloseContextMenu()
    );
  };

  // Handle view mode change and save to localStorage
  const handleViewModeChange = (event, newView) => {
    if (newView) {
      setViewMode(newView);
      localStorage.setItem('fileManagerViewMode', newView);
    }
  };

  // Create folder
  const handleCreateFolderClick = () => {
    setNewFolderName('');
    setCreateFolderError('');
    setCreateFolderOpen(true);
    handleCloseContextMenu();
  };

  const handleCreateFolder = async () => {
    const validationError = validateName(newFolderName, 'folder');
    if (validationError) {
      setCreateFolderError(validationError);
      return;
    }
    
    setCreateFolderError('');
    setIsOperationInProgress(true);
    
    try {
      const trimmedName = newFolderName.trim();
      await axios.post(`${API_BASE}/folder`, {
        name: trimmedName,
        parent_path: currentPath,
      });
      setCreateFolderOpen(false);
      setNewFolderName('');
      setCreateFolderError('');
      await fetchContents();
      showMessage('Folder created successfully', 'success');
    } catch (err) {
      const errorMsg = err.response?.data?.message || 'Failed to create folder';
      setCreateFolderError(errorMsg);
      showMessage(errorMsg, 'error');
    } finally {
      setIsOperationInProgress(false);
    }
  };

  // Rename folder
  const handleRenameFolderClick = () => {
    setNewName(selectedItem.name);
    setRenameFolderError('');
    setRenameFolderOpen(true);
    handleCloseContextMenu();
  };

  const handleRenameFolder = async () => {
    if (!selectedItem) return;
    
    const validationError = validateName(newName, 'folder');
    if (validationError) {
      setRenameFolderError(validationError);
      return;
    }
    
    setRenameFolderError('');
    setIsOperationInProgress(true);
    
    try {
      const trimmedName = newName.trim();
      await axios.put(`${API_BASE}/folder/${selectedItem.id}`, {
        name: trimmedName,
      });
      setRenameFolderOpen(false);
      setNewName('');
      setRenameFolderError('');
      await fetchContents();
      showMessage('Folder renamed successfully', 'success');
    } catch (err) {
      const errorMsg = err.response?.data?.message || 'Failed to rename folder';
      setRenameFolderError(errorMsg);
      showMessage(errorMsg, 'error');
    } finally {
      setIsOperationInProgress(false);
    }
  };

  // Delete folder
  const handleDeleteFolder = async () => {
    if (!selectedItem) return;

    showConfirmDialog(
      'Delete Folder',
      `Are you sure you want to delete "${selectedItem.name}" and all its contents? This action cannot be undone.`,
      async () => {
        setIsOperationInProgress(true);
        handleCloseContextMenu();
        
        try {
          await axios.delete(`${API_BASE}/folder/${selectedItem.id}`);
          await fetchContents();
          showMessage('Folder deleted successfully', 'success');
        } catch (err) {
          showMessage(err.response?.data?.message || 'Failed to delete folder', 'error');
        } finally {
          setIsOperationInProgress(false);
        }
      },
      () => handleCloseContextMenu()
    );
  };

  const handleFolderColorSelect = useCallback((color) => {
    if (!selectedItem || selectedItem.itemType !== 'folder') return;
    const newColors = { ...folderColors, [selectedItem.id]: color };
    saveFolderColors(newColors);
    handleCloseContextMenu();
  }, [selectedItem, folderColors, saveFolderColors]);

  // Upload files
  const handleUploadClick = () => {
    fileInputRef.current?.click();
    handleCloseContextMenu();
  };

  // Helper function to ensure folder exists, creating parent folders as needed
  const ensureFolderPath = async (relativePath, baseFolder = currentPath) => {
    if (!relativePath || relativePath === '/') return baseFolder;

    const parts = relativePath.split('/').filter(Boolean);
    let currentFolder = baseFolder;

    for (const part of parts) {
      // Check if this folder already exists at the current path
      const checkResponse = await axios.get(`${API_BASE}/browse`, {
        params: { path: currentFolder },
      });

      const existingFolder = checkResponse.data.data.folders?.find(f => f.name === part);

      if (existingFolder) {
        // Folder exists, use its path
        currentFolder = existingFolder.path;
      } else {
        // Create the folder
        const createResponse = await axios.post(`${API_BASE}/folder`, {
          name: part,
          parent_path: currentFolder,
        });

        // Use the created folder's path
        currentFolder = createResponse.data.data.path;
      }
    }

    return currentFolder;
  };

  // Upload files with folder structure preservation
  const handleFileSelectWithPaths = async (filesWithPaths) => {
    if (!filesWithPaths || filesWithPaths.length === 0) return;

    // Validate file sizes
    const oversizedFiles = [];
    const validFilesWithPaths = [];
    for (const { file, relativePath } of filesWithPaths) {
      const sizeMB = file.size / BYTES_PER_MB;
      if (sizeMB > MAX_FILE_SIZE_MB) {
        oversizedFiles.push({ name: relativePath, size: sizeMB });
      } else {
        validFilesWithPaths.push({ file, relativePath });
      }
    }

    if (oversizedFiles.length > 0) {
      const oversizedNames = oversizedFiles.map(f => `${f.name} (${f.size.toFixed(2)} MB)`).join('\n');
      showMessage(
        `The following files exceed the maximum size of ${MAX_FILE_SIZE_MB} MB:\n${oversizedNames}`,
        'error'
      );
      if (validFilesWithPaths.length === 0) return;
    }

    setIsOperationInProgress(true);
    setUploadProgress({
      files: validFilesWithPaths.map(({ relativePath }) => ({ name: relativePath, status: 'pending' })),
      current: 0,
      total: validFilesWithPaths.length
    });

    const results = { success: [], failed: [] };

    for (let i = 0; i < validFilesWithPaths.length; i++) {
      const { file, relativePath } = validFilesWithPaths[i];

      setUploadProgress(prev => ({
        ...prev,
        current: i + 1,
        files: prev.files.map((f, idx) =>
          idx === i ? { ...f, status: 'uploading' } : f
        ),
      }));

      try {
        // Extract folder path from relative path
        const lastSlashIndex = relativePath.lastIndexOf('/');
        const folderPath = lastSlashIndex > 0 ? relativePath.substring(0, lastSlashIndex) : '';

        // Ensure the folder exists (create if necessary)
        let targetFolder = currentPath;
        if (folderPath) {
          targetFolder = await ensureFolderPath(folderPath, currentPath);
        }

        // Upload the file to the target folder
        const formData = new FormData();
        formData.append('file', file);
        formData.append('folder_path', targetFolder);

        await axios.post(`${API_BASE}/upload`, formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });

        results.success.push(relativePath);
        setUploadProgress(prev => ({
          ...prev,
          files: prev.files.map((f, idx) =>
            idx === i ? { ...f, status: 'success' } : f
          ),
        }));
      } catch (err) {
        const errorMsg = err.response?.data?.message || err.message;
        results.failed.push({ name: relativePath, error: errorMsg });
        setUploadProgress(prev => ({
          ...prev,
          files: prev.files.map((f, idx) =>
            idx === i ? { ...f, status: 'error', error: errorMsg } : f
          ),
        }));
        showMessage(`Upload failed for ${relativePath}: ${errorMsg}`, 'error');
      }
    }

    // Refresh after all uploads complete
    await fetchContents();

    setIsOperationInProgress(false);
    setUploadProgress({ files: [], current: 0, total: 0 });

    if (results.success.length > 0 && results.failed.length === 0) {
      showMessage(`Successfully uploaded ${results.success.length} file(s) with folder structure preserved`, 'success');
    } else if (results.success.length > 0) {
      showMessage(
        `Uploaded ${results.success.length} file(s), but ${results.failed.length} failed`,
        'warning'
      );
    }
  };

  const handleFileSelect = async (files) => {
    if (!files || files.length === 0) return;

    // Convert plain files to files with paths (no folder structure)
    const filesWithPaths = files.map(file => ({
      file,
      relativePath: file.name
    }));

    await handleFileSelectWithPaths(filesWithPaths);
  };

  // Rename file
  const handleRenameFileClick = () => {
    setNewName(selectedItem.filename);
    setRenameFileError('');
    setRenameFileOpen(true);
    handleCloseContextMenu();
  };

  const handleRenameFile = async () => {
    if (!selectedItem) return;
    
    const validationError = validateName(newName, 'file');
    if (validationError) {
      setRenameFileError(validationError);
      return;
    }
    
    setRenameFileError('');
    setIsOperationInProgress(true);
    
    try {
      const trimmedName = newName.trim();
      await axios.put(`${API_BASE}/document/${selectedItem.id}`, {
        filename: trimmedName,
      });
      setRenameFileOpen(false);
      setNewName('');
      setRenameFileError('');
      await fetchContents();
      showMessage('File renamed successfully', 'success');
    } catch (err) {
      const errorMsg = err.response?.data?.message || 'Failed to rename file';
      setRenameFileError(errorMsg);
      showMessage(errorMsg, 'error');
    } finally {
      setIsOperationInProgress(false);
    }
  };

  // Delete file
  const handleDeleteFile = async () => {
    if (!selectedItem) return;

    showConfirmDialog(
      'Delete File',
      `Are you sure you want to delete "${selectedItem.filename}"? This action cannot be undone.`,
      async () => {
        setIsOperationInProgress(true);
        handleCloseContextMenu();
        
        try {
          await axios.delete(`${API_BASE}/document/${selectedItem.id}`);
          await fetchContents();
          showMessage('File deleted successfully', 'success');
        } catch (err) {
          showMessage(err.response?.data?.message || 'Failed to delete file', 'error');
        } finally {
          setIsOperationInProgress(false);
        }
      },
      () => handleCloseContextMenu()
    );
  };

  // Download file
  const handleDownloadFile = async () => {
    if (!selectedItem) return;

    setIsOperationInProgress(true);
    handleCloseContextMenu();

    try {
      const response = await axios.get(`${API_BASE}/document/${selectedItem.id}/download`, {
        responseType: 'blob',
      });

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', selectedItem.filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      showMessage('File downloaded successfully', 'success');
    } catch (err) {
      showMessage(err.response?.data?.message || 'Failed to download file', 'error');
    } finally {
      setIsOperationInProgress(false);
    }
  };

  // Open CSV in spreadsheet view
  const handleOpenCSV = () => {
    if (!selectedItem) return;
    
    setCsvFileData(selectedItem);
    setCsvViewerOpen(true);
    handleCloseContextMenu();
  };

  // Close CSV viewer
  const handleCloseCSV = () => {
    setCsvViewerOpen(false);
    setCsvFileData(null);
  };

  // Handle CSV save
  const handleCSVSave = async () => {
    // Refresh the file list to show updated file
    await fetchContents();
  };

  // Helper function to recursively traverse folders and collect files with paths
  const traverseFolderEntry = useCallback(async (entry, basePath = '') => {
    const files = [];

    if (entry.isFile) {
      // Get the file object
      const file = await new Promise((resolve, reject) => {
        entry.file(resolve, reject);
      });
      // Store file with its relative path
      files.push({
        file,
        relativePath: basePath ? `${basePath}/${file.name}` : file.name
      });
    } else if (entry.isDirectory) {
      const dirReader = entry.createReader();

      // Read all entries in this directory (may require multiple calls)
      const readEntries = async () => {
        const entries = await new Promise((resolve, reject) => {
          dirReader.readEntries(resolve, reject);
        });

        if (entries.length > 0) {
          // Process each entry recursively
          for (const childEntry of entries) {
            const childPath = basePath ? `${basePath}/${entry.name}` : entry.name;
            const childFiles = await traverseFolderEntry(childEntry, childPath);
            files.push(...childFiles);
          }

          // Continue reading (directories may have more entries)
          const moreFiles = await readEntries();
          files.push(...moreFiles);
        }

        return files;
      };

      const dirFiles = await readEntries();
      files.push(...dirFiles);
    }

    return files;
  }, []);

  // Drag and drop for file upload (from outside browser)
  const handleFileDrag = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();

    // Only activate drag zone if dragging files from outside (not internal items)
    const hasInternalDrag = draggedItem || draggedItems.length > 0;
    if (!hasInternalDrag && (e.type === 'dragenter' || e.type === 'dragover')) {
      // Check if dragging files from outside
      const hasFiles = e.dataTransfer?.types?.includes('Files');
      if (hasFiles) {
        setDragActive(true);
      }
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  }, [draggedItem, draggedItems]);

  const handleFileDrop = useCallback(async (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    // Only handle file drops from outside (not internal items)
    const hasInternalDrag = draggedItem || draggedItems.length > 0;
    if (hasInternalDrag) return;

    const items = e.dataTransfer.items;
    const filesToUpload = [];

    if (items) {
      // Use DataTransferItem API to handle folders
      for (let i = 0; i < items.length; i++) {
        const item = items[i];

        if (item.kind === 'file') {
          const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;

          if (entry) {
            // Traverse the entry (could be file or folder)
            const filesWithPaths = await traverseFolderEntry(entry);
            filesToUpload.push(...filesWithPaths);
          } else {
            // Fallback: treat as regular file
            const file = item.getAsFile();
            if (file) {
              filesToUpload.push({ file, relativePath: file.name });
            }
          }
        }
      }
    } else if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      // Fallback for browsers that don't support DataTransferItem
      for (const file of e.dataTransfer.files) {
        filesToUpload.push({ file, relativePath: file.name });
      }
    }

    if (filesToUpload.length > 0) {
      handleFileSelectWithPaths(filesToUpload);
    }
  }, [draggedItem, draggedItems, traverseFolderEntry]);

  // Drag and drop for moving items
  const handleItemDragStart = (e, item, type) => {
    e.stopPropagation();
    
    // Check if this item is part of a multi-selection
    const itemKey = getItemKey(item, type);
    const isMultiSelect = selectedItems.size > 1 && selectedItems.has(itemKey);
    
    if (isMultiSelect) {
      // Drag all selected items
      const selected = getSelectedItemsArray();
      setDraggedItems(selected);
      setDraggedItem(null); // Clear single item
      // Store count in dataTransfer for visual feedback
      e.dataTransfer.setData('text/plain', `${selected.length} items`);
      e.dataTransfer.effectAllowed = 'move';
      // Create a custom drag image showing count
      const dragImage = document.createElement('div');
      dragImage.style.position = 'absolute';
      dragImage.style.top = '-1000px';
      dragImage.style.padding = '8px 12px';
      dragImage.style.background = 'rgba(25, 118, 210, 0.9)';
      dragImage.style.color = 'white';
      dragImage.style.borderRadius = '4px';
      dragImage.style.fontSize = '14px';
      dragImage.style.fontWeight = '500';
      dragImage.textContent = `${selected.length} item${selected.length > 1 ? 's' : ''}`;
      document.body.appendChild(dragImage);
      e.dataTransfer.setDragImage(dragImage, dragImage.offsetWidth / 2, dragImage.offsetHeight / 2);
      setTimeout(() => document.body.removeChild(dragImage), 0);
    } else {
      // Drag single item
      setDraggedItem({ ...item, itemType: type });
      setDraggedItems([]); // Clear multi-select
      e.dataTransfer.effectAllowed = 'move';
    }
  };

  const handleItemDragEnd = () => {
    setDraggedItem(null);
    setDraggedItems([]);
    setDropTarget(null);
  };

  const handleFolderDragOver = (e, folder) => {
    e.preventDefault();
    e.stopPropagation();
    
    // Check if we're dragging multiple items
    const itemsToCheck = draggedItems.length > 0 ? draggedItems : (draggedItem ? [draggedItem] : []);
    
    if (itemsToCheck.length > 0) {
      // Don't allow dropping if any dragged folder is the target or a parent
      const canDrop = itemsToCheck.every(item => {
        if (item.itemType === 'folder' && item.id === folder.id) {
          return false; // Can't drop folder into itself
        }
        // Can drop files or other folders
        return true;
      });
      
      if (canDrop) {
        setDropTarget(folder.id);
        e.dataTransfer.dropEffect = 'move';
      } else {
        e.dataTransfer.dropEffect = 'none';
      }
    }
  };

  const handleFolderDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);
  };

  const handleFolderDrop = async (e, targetFolder) => {
    e.preventDefault();
    e.stopPropagation();
    setDropTarget(null);

    // Determine which items to move (multi-select or single)
    const itemsToMove = draggedItems.length > 0 ? draggedItems : (draggedItem ? [draggedItem] : []);
    
    if (itemsToMove.length === 0) return;

    // Validate drop - don't allow dropping folders into themselves
    const validItems = itemsToMove.filter(item => {
      if (item.itemType === 'folder' && item.id === targetFolder.id) {
        return false; // Can't drop folder into itself
      }
      return true;
    });

    if (validItems.length === 0) {
      showMessage('Cannot drop items into themselves', 'warning');
      setDraggedItem(null);
      setDraggedItems([]);
      return;
    }

    setIsOperationInProgress(true);

    const results = { success: [], failed: [] };

    try {
      // Move all valid items
      for (const item of validItems) {
        try {
          if (item.itemType === 'file') {
            // Move file
            await axios.post(`${API_BASE}/document/${item.id}/move`, {
              destination_path: targetFolder.path,
            });
            results.success.push(item.filename || item.name);
          } else if (item.itemType === 'folder') {
            // Move folder (rename with new parent path)
            await axios.put(`${API_BASE}/folder/${item.id}`, {
              name: item.name,
              parent_path: targetFolder.path,
            });
            results.success.push(item.name);
          }
        } catch (err) {
          results.failed.push({
            name: item.filename || item.name,
            error: err.response?.data?.message || 'Unknown error',
          });
        }
      }

      // Clear selection after successful move
      if (results.success.length > 0) {
        clearSelection();
      }

      await fetchContents();

      // Show results
      if (results.failed.length === 0) {
        showMessage(
          `Successfully moved ${results.success.length} item(s) to "${targetFolder.name}"`,
          'success'
        );
      } else {
        const failedNames = results.failed.map(f => `${f.name}: ${f.error}`).join('\n');
        showMessage(
          `Moved ${results.success.length} item(s), but ${results.failed.length} failed:\n${failedNames}`,
          'warning'
        );
      }
    } catch (err) {
      showMessage(err.response?.data?.message || 'Failed to move items', 'error');
    } finally {
      setIsOperationInProgress(false);
      setDraggedItem(null);
      setDraggedItems([]);
    }
  };

  // Utility functions
  const formatBytes = (bytes) => {
    if (!bytes) return '0 B';
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    return Math.round(bytes / Math.pow(1024, i)) + ' ' + sizes[i];
  };

  const formatDate = (dateString) => {
    if (!dateString) return '';
    const date = new Date(dateString);
    return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
  };

  // Cut and Paste operations (Copy not yet implemented)
  const handleCut = () => {
    setClipboard({ item: selectedItem, operation: 'cut' });
    handleCloseContextMenu();
  };

  const handlePaste = async () => {
    if (!clipboard) return;
    handleCloseContextMenu();
    setIsOperationInProgress(true);

    try {
      const { item, operation } = clipboard;

      if (item.itemType === 'file') {
        if (operation === 'cut') {
          // Move file
          await axios.post(`${API_BASE}/document/${item.id}/move`, {
            destination_path: currentPath,
          });
          setClipboard(null); // Clear clipboard after cut
          showMessage('File moved successfully', 'success');
        } else {
          // Copy file - TODO: implement file copy endpoint
          showMessage('File copy functionality is not yet implemented', 'info');
          return;
        }
      } else if (item.itemType === 'folder') {
        if (operation === 'cut') {
          // Move folder
          await axios.put(`${API_BASE}/folder/${item.id}`, {
            name: item.name,
            parent_path: currentPath,
          });
          setClipboard(null); // Clear clipboard after cut
          showMessage('Folder moved successfully', 'success');
        } else {
          // Copy folder - TODO: implement folder copy endpoint
          showMessage('Folder copy functionality is not yet implemented', 'info');
          return;
        }
      }

      await fetchContents();
    } catch (err) {
      showMessage(err.response?.data?.message || 'Failed to paste item', 'error');
    } finally {
      setIsOperationInProgress(false);
    }
  };

  const handleProperties = () => {
    if (selectedItem?.itemType === 'file') {
      setPropertiesOpen(true);
      setContextMenu(null); // Close context menu but keep selectedItem for modal
    } else if (selectedItem?.itemType === 'folder') {
      setFolderPropertiesOpen(true);
      setContextMenu(null); // Close context menu but keep selectedItem for modal
    } else {
      handleCloseContextMenu();
    }
  };

  const handleFolderPropertiesSave = async (folderId, payload) => {
    setIsOperationInProgress(true);
    
    try {
      const response = await axios.put(`${API_BASE}/folder/${folderId}/link`, payload);
      setFolderPropertiesOpen(false);
      setSelectedItem(null);
      await fetchContents();
      
      // Show success message with cascade stats if available
      if (response.data?.data?.cascade_stats) {
        const stats = response.data.data.cascade_stats;
        showMessage(
          `Properties applied successfully! Files updated: ${stats.files_updated}, Subfolders processed: ${stats.folders_processed}`,
          'success'
        );
      } else {
        showMessage('Properties updated successfully', 'success');
      }
    } catch (err) {
      showMessage(err.response?.data?.message || 'Failed to update folder properties', 'error');
    } finally {
      setIsOperationInProgress(false);
    }
  };

  // Breadcrumbs
  const renderBreadcrumbs = () => {
    const parts = currentPath === '/' ? [] : currentPath.split('/').filter(Boolean);

    return (
      <Breadcrumbs sx={{ mb: 2 }}>
        <Link
          component="button"
          variant="body1"
          onClick={() => handleNavigate('/')}
          underline="hover"
          color={currentPath === '/' ? 'text.primary' : 'inherit'}
        >
          Files
        </Link>
        {parts.map((part, index) => {
          // Path without leading slash (matches DB storage format)
          const pathUpToHere = parts.slice(0, index + 1).join('/');
          const isLast = index === parts.length - 1;
          return (
            <Link
              key={index}
              component="button"
              variant="body1"
              onClick={() => handleNavigate(pathUpToHere)}
              underline="hover"
              color={isLast ? 'text.primary' : 'inherit'}
            >
              {part}
            </Link>
          );
        })}
      </Breadcrumbs>
    );
  };

  // Grid View
  const renderGridView = () => {
    const folderLayout = folderLayouts[folderLayoutKey] || generateDefaultFolderLayout(items.folders);
    const folderItems = items.folders.map(f => ({ ...f, itemType: 'folder' }));
    const fileItems = items.documents.map(d => ({ ...d, itemType: 'file' }));

    // Get grid settings from LayoutContext (critical for proper grid calculations)
    const { RGL_WIDTH_PROP_PX, CONTAINER_PADDING_PX, CARD_MARGIN_PX } = gridSettings;
    const outerBoxWidth = RGL_WIDTH_PROP_PX + CONTAINER_PADDING_PX * 2;

    return (
      <Box
        sx={{
          p: 2,
          position: 'relative',
          minHeight: '400px',
          display: 'flex',
          justifyContent: 'center',
          cursor: 'default',
          '& .react-grid-layout': {
            position: 'relative',
          },
          '& .react-grid-item': {
            transition: 'transform 0.2s ease-out !important',
            '&.react-grid-placeholder': {
              transition: 'all 0.2s ease-out !important',
              opacity: 0.3,
              bgcolor: 'action.hover',
            },
            '&.react-draggable-dragging': {
              transition: 'none !important',
              zIndex: 1000,
            },
          },
        }}
      >
        <Box
          sx={{
            width: '100%',
            maxWidth: `${outerBoxWidth}px`,
            overflow: 'visible',
          }}
        >
          <FolderGridLayout
            ref={folderGridRef}
            layout={folderLayout}
            cols={FOLDER_LAYOUT_COLS}
            rowHeight={FOLDER_ROW_HEIGHT}
            width={RGL_WIDTH_PROP_PX}
            margin={[8, 8]}
            containerPadding={[CONTAINER_PADDING_PX / 10, CONTAINER_PADDING_PX / 10]}
            isDraggable={!draggedItem && draggedItems.length === 0} // Disable layout dragging when dragging files/folders
            isResizable={false}
            compactType={null}
            preventCollision={false}
            allowOverlap={true}
            useCSSTransforms={false}
            draggableHandle=".folder-drag-handle"
            draggableCancel="button, input, textarea, select, option, .non-draggable"
            onLayoutChange={(newLayout) => {
              const key = FOLDER_LAYOUT_KEY(currentPath);
              const updated = { ...folderLayouts, [key]: newLayout };
              saveFolderLayouts(updated);
            }}
            onContextMenu={(e) => {
              console.log('[FolderGridLayout contextMenu] triggered');
              // Call with just event to let defaults apply
              handleContextMenu(e);
            }}
          >
          {folderItems.map((item) => {
            const key = getItemKey(item, 'folder');
            const isSelected = selectedItems.has(key);
            const folderColor = folderColors[item.id] || theme.palette.primary.main;

            const layoutItem = folderLayout.find(l => l.i === key) || { w: FOLDER_GRID_WIDTH, h: FOLDER_GRID_HEIGHT, x: 0, y: 0, minW: FOLDER_GRID_WIDTH, minH: FOLDER_GRID_HEIGHT };
            
            return (
              <div 
                key={key} 
                data-grid={layoutItem}
                style={{
                  position: 'relative',
                  zIndex: isSelected ? 10 : 1,
                }}
              >
                <Card
                  ref={(el) => {
                    if (el) itemRefs.current.set(key, el);
                    else itemRefs.current.delete(key);
                  }}
                  data-item-key={key}
                  sx={{
                    width: '100%',
                    height: '100%',
                    cursor: 'grab',
                    border: isSelected ? '2px solid' : dropTarget === item.id ? '2px solid' : '1px solid',
                    borderColor: isSelected ? 'primary.main' : dropTarget === item.id ? 'primary.main' : 'divider',
                    bgcolor: isSelected ? 'action.selected' : dropTarget === item.id ? 'action.hover' : 'background.paper',
                    borderRadius: 1,
                    transition: 'all 0.1s ease-in-out',
                    display: 'flex',
                    flexDirection: 'column',
                    overflow: 'hidden',
                    '&:hover': {
                      bgcolor: isSelected ? 'action.selected' : 'action.hover',
                      boxShadow: 2,
                    },
                    '&:active': {
                      cursor: 'grabbing',
                    },
                  }}
                >
                  <CardActionArea
                    className="folder-drag-handle"
                    onClick={(e) => {
                      // Single click: select/highlight (CTRL for multi-select)
                      if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        toggleItemSelection(item, item.itemType, true);
                      } else {
                        toggleItemSelection(item, item.itemType, false);
                      }
                    }}
                    onDoubleClick={(e) => {
                      // Double click: open folder
                      e.preventDefault();
                      e.stopPropagation();
                      handleFolderClick(item);
                    }}
                    onContextMenu={(e) => handleContextMenu(e, item, item.itemType)}
                    sx={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', p: 0.5 }}
                  >
                    <CardContent sx={{ textAlign: 'center', position: 'relative', width: '100%', p: 0.5, '&:last-child': { pb: 0.5 } }}>
                      <FolderIcon
                        sx={{
                          fontSize: 48,
                          color: folderColor,
                          cursor: 'grab',
                          filter: isSelected ? `drop-shadow(0 0 6px ${alpha(folderColor, 0.8)})` : 'none',
                          transform: isSelected ? 'scale(1.05)' : 'scale(1)',
                          transition: 'all 0.15s ease-in-out',
                          '&:active': { cursor: 'grabbing' },
                        }}
                      />
                      <Typography variant="caption" noWrap sx={{ mt: 0.5, px: 0.5, fontSize: '0.75rem' }}>
                        {item.name}
                      </Typography>
                    </CardContent>
                  </CardActionArea>
                </Card>
              </div>
            );
          })}
          </FolderGridLayout>
        </Box>

        <Grid
          container
          spacing={2}
          sx={{ p: 2, pt: 3, cursor: 'default' }}
          onContextMenu={(e) => {
            console.log('[Grid files contextMenu]', {
              target: e.target,
              currentTarget: e.currentTarget,
              isCurrentTarget: e.target === e.currentTarget,
            });
            if (e.target === e.currentTarget || e.target.closest('.MuiGrid-root') === e.currentTarget) {
              console.log('[Grid files] Triggering blank space context menu');
              // Call with just event to let defaults apply
              handleContextMenu(e);
            }
          }}
        >
          {fileItems.map((item) => {
            const key = getItemKey(item, item.itemType);
            const isSelected = selectedItems.has(key);
            return (
              <Grid item xs={6} sm={4} md={3} lg={2} key={key}>
                <Card
                  ref={(el) => {
                    if (el) itemRefs.current.set(key, el);
                    else itemRefs.current.delete(key);
                  }}
                  data-item-key={key}
                  draggable
                  onDragStart={(e) => handleItemDragStart(e, item, item.itemType)}
                  onDragEnd={handleItemDragEnd}
                  sx={{
                    height: '90px',
                    cursor: 'grab',
                    border: isSelected ? '2px solid' : '1px solid',
                    borderColor: isSelected ? 'primary.main' : 'divider',
                    bgcolor: isSelected ? 'action.selected' : 'background.paper',
                    borderRadius: 1,
                    transition: 'all 0.1s ease-in-out',
                    '&:hover': {
                      bgcolor: isSelected ? 'action.selected' : 'action.hover',
                      boxShadow: 2,
                    },
                    '&:active': {
                      cursor: 'grabbing',
                    },
                  }}
                >
                  <CardActionArea
                    onClick={(e) => {
                      // Single click: only select (CTRL for multi-select)
                      if (e.ctrlKey || e.metaKey) {
                        e.preventDefault();
                        toggleItemSelection(item, item.itemType, true);
                      } else {
                        toggleItemSelection(item, item.itemType, false);
                      }
                    }}
                    onDoubleClick={(e) => {
                      // Double click: open CSV files in spreadsheet viewer
                      if (item.filename?.toLowerCase().endsWith('.csv')) {
                        e.preventDefault();
                        e.stopPropagation();
                        setSelectedItem({ ...item, itemType: 'file' });
                        setCsvFileData({ ...item, itemType: 'file' });
                        setCsvViewerOpen(true);
                      }
                    }}
                    onContextMenu={(e) => handleContextMenu(e, item, item.itemType)}
                    sx={{ p: 0.5 }}
                  >
                    <CardContent sx={{ textAlign: 'center', position: 'relative', p: 0.5, '&:last-child': { pb: 0.5 } }}>
                      {getFileIcon(item.filename, isSelected, theme)}
                      <Typography variant="caption" noWrap sx={{ mt: 0.5, fontSize: '0.75rem' }}>
                        {item.filename}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                        {formatBytes(item.size)}
                      </Typography>
                      {item.index_status && (
                        <Chip
                          label={item.index_status}
                          size="small"
                          color={
                            item.index_status === 'INDEXED' ? 'success' :
                            item.index_status === 'INDEXING' ? 'primary' :
                            item.index_status === 'ERROR' ? 'error' : 'default'
                          }
                          sx={{ mt: 0.5, height: 18, fontSize: '0.65rem' }}
                        />
                      )}
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Grid>
            );
          })}
          {folderItems.length === 0 && fileItems.length === 0 && (
            <Grid item xs={12}>
              <Typography variant="body2" color="text.secondary" align="center" sx={{ py: 4 }}>
                This folder is empty. Create a folder or upload files to get started.
              </Typography>
            </Grid>
          )}
        </Grid>
      </Box>
    );
  };

  // List View
  const renderListView = () => (
    <TableContainer sx={{ cursor: 'default' }}>
      <Table>
        <TableHead>
          <TableRow>
            <TableCell padding="checkbox" sx={{ width: 50 }}>
              <Checkbox
                indeterminate={selectedItems.size > 0 && selectedItems.size < (items.folders.length + items.documents.length)}
                checked={selectedItems.size > 0 && selectedItems.size === (items.folders.length + items.documents.length)}
                onChange={(e) => {
                  if (e.target.checked) {
                    selectAll();
                  } else {
                    clearSelection();
                  }
                }}
              />
            </TableCell>
            <TableCell>Name</TableCell>
            <TableCell>Type</TableCell>
            <TableCell align="right">Size</TableCell>
            <TableCell align="right">Modified</TableCell>
            <TableCell align="right">Actions</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {/* Folders */}
          {items.folders.map((folder) => {
            const key = getItemKey(folder, 'folder');
            const isSelected = selectedItems.has(key);
            
            return (
              <TableRow
                key={key}
                ref={(el) => {
                  if (el) itemRefs.current.set(key, el);
                  else itemRefs.current.delete(key);
                }}
                data-item-key={key}
                draggable
                onDragStart={(e) => handleItemDragStart(e, folder, 'folder')}
                onDragEnd={handleItemDragEnd}
                onDragOver={(e) => handleFolderDragOver(e, folder)}
                onDragLeave={handleFolderDragLeave}
                onDrop={(e) => handleFolderDrop(e, folder)}
                hover
                selected={isSelected}
                sx={{
                  cursor: 'grab',
                  bgcolor: isSelected ? 'action.selected' : dropTarget === folder.id ? 'action.hover' : 'inherit',
                  '&.Mui-selected': {
                    bgcolor: 'action.selected',
                    '&:hover': {
                      bgcolor: 'action.selected',
                    },
                  },
                  '&:active': {
                    cursor: 'grabbing',
                  },
                }}
                onClick={(e) => {
                  // Single click: select/highlight (CTRL for multi-select)
                  if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    toggleItemSelection(folder, 'folder', true);
                  } else {
                    toggleItemSelection(folder, 'folder', false);
                  }
                }}
                onDoubleClick={(e) => {
                  // Double click: open folder
                  e.preventDefault();
                  e.stopPropagation();
                  handleFolderClick(folder);
                }}
                onContextMenu={(e) => handleContextMenu(e, folder, 'folder')}
              >
                <TableCell padding="checkbox">
                  <Checkbox
                    checked={isSelected}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleItemSelection(folder, 'folder', true);
                    }}
                  />
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <FolderIcon color={isSelected ? 'primary' : 'primary'} />
                    <Typography>{folder.name}</Typography>
                  </Box>
                </TableCell>
                <TableCell>Folder</TableCell>
                <TableCell align="right">-</TableCell>
                <TableCell align="right">{formatDate(folder.updated_at)}</TableCell>
                <TableCell align="right">
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleContextMenu(e, folder, 'folder');
                    }}
                  >
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            );
          })}

          {/* Files */}
          {items.documents.map((doc) => {
            const key = getItemKey(doc, 'file');
            const isSelected = selectedItems.has(key);
            
            return (
              <TableRow
                key={key}
                ref={(el) => {
                  if (el) itemRefs.current.set(key, el);
                  else itemRefs.current.delete(key);
                }}
                data-item-key={key}
                draggable
                onDragStart={(e) => handleItemDragStart(e, doc, 'file')}
                onDragEnd={handleItemDragEnd}
                hover
                selected={isSelected}
                sx={{
                  cursor: 'grab',
                  bgcolor: isSelected ? 'action.selected' : 'inherit',
                  '&.Mui-selected': {
                    bgcolor: 'action.selected',
                    '&:hover': {
                      bgcolor: 'action.selected',
                    },
                  },
                  '&:active': {
                    cursor: 'grabbing',
                  },
                }}
                onClick={(e) => {
                  // Single click: only select (CTRL for multi-select)
                  if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    toggleItemSelection(doc, 'file', true);
                  } else {
                    toggleItemSelection(doc, 'file', false);
                  }
                }}
                onDoubleClick={(e) => {
                  // Double click: open CSV files in spreadsheet viewer
                  if (doc.filename?.toLowerCase().endsWith('.csv')) {
                    e.preventDefault();
                    e.stopPropagation();
                    setSelectedItem({ ...doc, itemType: 'file' });
                    setCsvFileData({ ...doc, itemType: 'file' });
                    setCsvViewerOpen(true);
                  }
                }}
                onContextMenu={(e) => handleContextMenu(e, doc, 'file')}
              >
                <TableCell padding="checkbox">
                  <Checkbox
                    checked={isSelected}
                    onClick={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleItemSelection(doc, 'file', true);
                    }}
                  />
                </TableCell>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    {getFileIconSmall(doc.filename, isSelected)}
                    <Typography>{doc.filename}</Typography>
                    {doc.index_status && (
                      <Chip
                        label={doc.index_status}
                        size="small"
                        color={
                          doc.index_status === 'INDEXED' ? 'success' :
                          doc.index_status === 'INDEXING' ? 'primary' :
                          doc.index_status === 'ERROR' ? 'error' : 'default'
                        }
                        sx={{ ml: 1, height: 20, fontSize: '0.7rem' }}
                      />
                    )}
                  </Box>
                </TableCell>
                <TableCell>File</TableCell>
                <TableCell align="right">{formatBytes(doc.size)}</TableCell>
                <TableCell align="right">{formatDate(doc.uploaded_at)}</TableCell>
                <TableCell align="right">
                  <IconButton
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleContextMenu(e, doc, 'file');
                    }}
                  >
                    <MoreVertIcon fontSize="small" />
                  </IconButton>
                </TableCell>
              </TableRow>
            );
          })}

          {items.folders.length === 0 && items.documents.length === 0 && (
            <TableRow>
              <TableCell colSpan={6} align="center">
                <Typography variant="body2" color="text.secondary" sx={{ py: 4 }}>
                  This folder is empty. Create a folder or upload files to get started.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </TableContainer>
  );

  return (
    <Box
      sx={{ 
        height: '100%', 
        minHeight: 0, // Allow flex shrinking
        display: 'flex', 
        flexDirection: 'column',
        overflow: 'hidden', // Prevent outer overflow
      }}
      onDragEnter={handleFileDrag}
      onDragLeave={handleFileDrag}
      onDragOver={handleFileDrag}
      onDrop={handleFileDrop}
    >
      {/* Toolbar */}
      <Paper sx={{ p: 2, mb: 2 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Box sx={{ flex: 1 }}>
            {renderBreadcrumbs()}
          </Box>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            {/* View Toggle */}
            <ToggleButtonGroup
              value={viewMode}
              exclusive
              onChange={handleViewModeChange}
              size="small"
              sx={{ mr: 2 }}
            >
              <ToggleButton value="list">
                <Tooltip title="List View">
                  <ViewListIcon fontSize="small" />
                </Tooltip>
              </ToggleButton>
              <ToggleButton value="grid">
                <Tooltip title="Grid View">
                  <ViewModuleIcon fontSize="small" />
                </Tooltip>
              </ToggleButton>
            </ToggleButtonGroup>

            <Button
              variant="outlined"
              startIcon={<CreateNewFolderIcon />}
              onClick={handleCreateFolderClick}
              disabled={isOperationInProgress}
            >
              New Folder
            </Button>
            <Button
              variant="contained"
              startIcon={<UploadIcon />}
              onClick={handleUploadClick}
              disabled={isOperationInProgress}
            >
              Upload
            </Button>
          </Box>
        </Box>
      </Paper>

      {/* Main Content */}
      <Paper
        ref={contentAreaRef}
        sx={{
          flex: 1,
          overflow: 'auto',
          position: 'relative',
          border: dragActive ? '2px dashed' : '1px solid',
          borderColor: dragActive ? 'primary.main' : 'divider',
          userSelect: isSelecting ? 'none' : 'auto',
          cursor: 'default', // Ensure default cursor in empty areas
        }}
        onMouseDown={handleSelectionMouseDown}
        onContextMenu={(e) => {
          // Handle right-click on empty space
          // Check if we clicked on empty area (not on items)
          const clickedOnItem = e.target.closest('.MuiCard-root') ||
                                e.target.closest('.MuiTableRow-root') ||
                                e.target.closest('.react-grid-item');

          console.log('[FileManager Paper contextMenu]', {
            target: e.target,
            clickedOnItem: !!clickedOnItem,
          });

          if (!clickedOnItem) {
            console.log('[FileManager Paper] Triggering blank space context menu');
            // Call with just event to let defaults apply (item=null, type=null means blank space)
            handleContextMenu(e);
          }
        }}
      >
        {/* Selection rectangle overlay */}
        {isSelecting && selectionBox && (
          <Box
            sx={{
              position: 'absolute',
              left: Math.min(selectionBox.startX, selectionBox.currentX),
              top: Math.min(selectionBox.startY, selectionBox.currentY),
              width: Math.abs(selectionBox.currentX - selectionBox.startX),
              height: Math.abs(selectionBox.currentY - selectionBox.startY),
              bgcolor: (theme) => alpha(theme.palette.primary.main, 0.1),
              border: '1px solid',
              borderColor: 'primary.main',
              pointerEvents: 'none',
              zIndex: 1000,
            }}
          />
        )}

        {dragActive && (
          <Box
            sx={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              bgcolor: 'action.hover',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 1,
            }}
          >
            <Typography variant="h5" color="primary">
              Drop files here to upload
            </Typography>
          </Box>
        )}

        {loading && (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress />
          </Box>
        )}

        {/* Upload Progress */}
        {uploadProgress.total > 0 && (
          <Box sx={{ p: 2 }}>
            <Typography variant="body2" sx={{ mb: 1 }}>
              Uploading {uploadProgress.current} of {uploadProgress.total} files...
            </Typography>
            <LinearProgress 
              variant="determinate" 
              value={(uploadProgress.current / uploadProgress.total) * 100} 
              sx={{ mb: 1 }}
            />
            <Box sx={{ maxHeight: 200, overflow: 'auto' }}>
              {uploadProgress.files.map((file, idx) => (
                <Box key={idx} sx={{ display: 'flex', alignItems: 'center', gap: 1, py: 0.5 }}>
                  <Typography variant="caption" sx={{ flex: 1 }}>
                    {file.name}
                  </Typography>
                  {file.status === 'uploading' && <CircularProgress size={16} />}
                  {file.status === 'success' && (
                    <Chip label="Success" size="small" color="success" />
                  )}
                  {file.status === 'error' && (
                    <Chip label="Error" size="small" color="error" />
                  )}
                </Box>
              ))}
            </Box>
          </Box>
        )}

        {error && (
          <Alert severity="error" sx={{ m: 2 }}>
            {error}
          </Alert>
        )}

        {!loading && !error && (
          viewMode === 'grid' ? renderGridView() : renderListView()
        )}
      </Paper>

      {/* Selection status bar */}
      {selectedItems.size > 0 && (
        <Paper
          sx={{
            p: 1,
            mt: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            bgcolor: 'action.selected',
          }}
        >
          <Typography variant="body2">
            {selectedItems.size} item{selectedItems.size > 1 ? 's' : ''} selected
          </Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button
              size="small"
              startIcon={<SelectAllIcon />}
              onClick={selectAll}
              disabled={isOperationInProgress}
            >
              Select All
            </Button>
            <Button
              size="small"
              onClick={clearSelection}
              disabled={isOperationInProgress}
            >
              Clear
            </Button>
            <Button
              size="small"
              color="error"
              startIcon={<DeleteIcon />}
              onClick={handleDeleteSelected}
              disabled={isOperationInProgress}
            >
              Delete Selected
            </Button>
          </Box>
        </Paper>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={(e) => handleFileSelect(Array.from(e.target.files || []))}
      />

      {/* Confirmation Dialog */}
      <Dialog open={confirmDialog.open} onClose={handleConfirmDialogClose}>
        <DialogTitle>{confirmDialog.title}</DialogTitle>
        <DialogContent>
          <Typography style={{ whiteSpace: 'pre-line' }}>{confirmDialog.message}</Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleConfirmDialogClose} disabled={isOperationInProgress}>
            Cancel
          </Button>
          <Button 
            onClick={confirmDialog.onConfirm} 
            variant="contained" 
            color="error"
            disabled={isOperationInProgress}
            startIcon={isOperationInProgress ? <CircularProgress size={16} /> : null}
          >
            Confirm
          </Button>
        </DialogActions>
      </Dialog>

      {/* Context Menu */}
      <Menu
        open={contextMenu !== null}
        onClose={handleCloseContextMenu}
        anchorReference="anchorPosition"
        anchorPosition={
          contextMenu !== null
            ? { top: contextMenu.mouseY, left: contextMenu.mouseX }
            : undefined
        }
        onContextMenu={(e) => e.preventDefault()}
      >
        {!selectedItem && [
          // Blank space context menu
          <MenuItem key="paste" onClick={(e) => { e.stopPropagation(); handlePaste(); }} disabled={!clipboard}>
            <ListItemText>Paste</ListItemText>
          </MenuItem>,
          <MenuItem key="new-folder" onClick={(e) => { e.stopPropagation(); handleCreateFolderClick(); }}>
            <ListItemText>New Folder</ListItemText>
          </MenuItem>,
          <MenuItem key="upload" onClick={(e) => { e.stopPropagation(); handleUploadClick(); }}>
            <ListItemText>Upload Files</ListItemText>
          </MenuItem>,
          <MenuItem key="select-all" onClick={(e) => { e.stopPropagation(); selectAll(); handleCloseContextMenu(); }}>
            <ListItemText>Select All</ListItemText>
          </MenuItem>
        ]}
        {/* Multi-select context menu */}
        {selectedItems.size > 1 && selectedItem && [
          <MenuItem key="multi-info" disabled sx={{ opacity: 0.7 }}>
            <ListItemText>{selectedItems.size} items selected</ListItemText>
          </MenuItem>,
          <MenuItem key="delete-selected" onClick={(e) => { e.stopPropagation(); handleDeleteSelected(); }}>
            <ListItemIcon><DeleteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Delete Selected ({selectedItems.size})</ListItemText>
          </MenuItem>,
          <MenuItem key="clear-selection" onClick={(e) => { e.stopPropagation(); clearSelection(); handleCloseContextMenu(); }}>
            <ListItemText>Clear Selection</ListItemText>
          </MenuItem>
        ]}
        {/* Single folder context menu */}
        {selectedItems.size <= 1 && selectedItem?.itemType === 'folder' && [
          <MenuItem key="cut" onClick={(e) => { e.stopPropagation(); handleCut(); }}>
            <ListItemText>Cut</ListItemText>
          </MenuItem>,
          <MenuItem key="paste" onClick={(e) => { e.stopPropagation(); handlePaste(); }} disabled={!clipboard}>
            <ListItemText>Paste</ListItemText>
          </MenuItem>,
          <MenuItem key="color" disableRipple disableGutters>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 0.5 }}>
              <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                {FOLDER_COLOR_CHOICES.map((color) => (
                  <Box
                    key={color}
                    onClick={(e) => { e.stopPropagation(); handleFolderColorSelect(color); }}
                    sx={{
                      width: 18,
                      height: 18,
                      borderRadius: '50%',
                      backgroundColor: color,
                      border: (folderColors[selectedItem?.id] === color) ? '2px solid #000' : '1px solid #fff',
                      cursor: 'pointer',
                      boxShadow: 1,
                    }}
                    title={color}
                  />
                ))}
              </Box>
            </Box>
          </MenuItem>,
          <MenuItem key="rename" onClick={(e) => { e.stopPropagation(); handleRenameFolderClick(); }}>
            <ListItemText>Rename</ListItemText>
          </MenuItem>,
          <MenuItem key="properties" onClick={(e) => { e.stopPropagation(); handleProperties(); }}>
            <ListItemText>Properties</ListItemText>
          </MenuItem>,
          <MenuItem key="delete" onClick={(e) => { e.stopPropagation(); handleDeleteFolder(); }}>
            <ListItemText>Delete</ListItemText>
          </MenuItem>
        ]}
        {/* Single file context menu */}
        {selectedItems.size <= 1 && selectedItem?.itemType === 'file' && [
          <MenuItem key="cut" onClick={(e) => { e.stopPropagation(); handleCut(); }}>
            <ListItemText>Cut</ListItemText>
          </MenuItem>,
          <MenuItem key="paste" onClick={(e) => { e.stopPropagation(); handlePaste(); }} disabled={!clipboard}>
            <ListItemText>Paste</ListItemText>
          </MenuItem>,
          selectedItem?.filename?.toLowerCase().endsWith('.csv') && (
            <MenuItem key="spreadsheet" onClick={(e) => { e.stopPropagation(); handleOpenCSV(); }}>
              <ListItemText>Open in Spreadsheet</ListItemText>
            </MenuItem>
          ),
          <MenuItem key="download" onClick={(e) => { e.stopPropagation(); handleDownloadFile(); }}>
            <ListItemText>Download</ListItemText>
          </MenuItem>,
          <MenuItem key="rename" onClick={(e) => { e.stopPropagation(); handleRenameFileClick(); }}>
            <ListItemText>Rename</ListItemText>
          </MenuItem>,
          <MenuItem key="properties" onClick={(e) => { e.stopPropagation(); handleProperties(); }}>
            <ListItemText>Properties</ListItemText>
          </MenuItem>,
          <MenuItem key="delete" onClick={(e) => { e.stopPropagation(); handleDeleteFile(); }}>
            <ListItemText>Delete</ListItemText>
          </MenuItem>
        ]}
      </Menu>

      {/* Create Folder Dialog */}
      <Dialog open={createFolderOpen} onClose={() => !isOperationInProgress && setCreateFolderOpen(false)}>
        <DialogTitle>Create New Folder</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="Folder Name"
            fullWidth
            value={newFolderName}
            onChange={(e) => {
              setNewFolderName(e.target.value);
              if (createFolderError) setCreateFolderError('');
            }}
            error={!!createFolderError}
            helperText={createFolderError}
            disabled={isOperationInProgress}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isOperationInProgress) {
                e.preventDefault();
                handleCreateFolder();
              }
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateFolderOpen(false)} disabled={isOperationInProgress}>Cancel</Button>
          <Button 
            onClick={handleCreateFolder} 
            variant="contained"
            disabled={isOperationInProgress || !newFolderName.trim()}
            startIcon={isOperationInProgress ? <CircularProgress size={16} /> : null}
          >
            Create
          </Button>
        </DialogActions>
      </Dialog>

      {/* Rename Folder Dialog */}
      <Dialog open={renameFolderOpen} onClose={() => !isOperationInProgress && setRenameFolderOpen(false)}>
        <DialogTitle>Rename Folder</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="New Name"
            fullWidth
            value={newName}
            onChange={(e) => {
              setNewName(e.target.value);
              if (renameFolderError) setRenameFolderError('');
            }}
            error={!!renameFolderError}
            helperText={renameFolderError}
            disabled={isOperationInProgress}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isOperationInProgress) {
                e.preventDefault();
                handleRenameFolder();
              }
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameFolderOpen(false)} disabled={isOperationInProgress}>Cancel</Button>
          <Button 
            onClick={handleRenameFolder} 
            variant="contained"
            disabled={isOperationInProgress || !newName.trim()}
            startIcon={isOperationInProgress ? <CircularProgress size={16} /> : null}
          >
            Rename
          </Button>
        </DialogActions>
      </Dialog>

      {/* Rename File Dialog */}
      <Dialog open={renameFileOpen} onClose={() => !isOperationInProgress && setRenameFileOpen(false)}>
        <DialogTitle>Rename File</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="New Name"
            fullWidth
            value={newName}
            onChange={(e) => {
              setNewName(e.target.value);
              if (renameFileError) setRenameFileError('');
            }}
            error={!!renameFileError}
            helperText={renameFileError}
            disabled={isOperationInProgress}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !isOperationInProgress) {
                e.preventDefault();
                handleRenameFile();
              }
            }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameFileOpen(false)} disabled={isOperationInProgress}>Cancel</Button>
          <Button 
            onClick={handleRenameFile} 
            variant="contained"
            disabled={isOperationInProgress || !newName.trim()}
            startIcon={isOperationInProgress ? <CircularProgress size={16} /> : null}
          >
            Rename
          </Button>
        </DialogActions>
      </Dialog>

      {/* Properties Modal */}
      <FilePropertiesModal
        open={propertiesOpen}
        onClose={() => {
          setPropertiesOpen(false);
          setSelectedItem(null);
        }}
        fileData={selectedItem}
        onSave={async (id, payload) => {
          setIsOperationInProgress(true);
          try {
            await axios.put(`${API_BASE}/document/${id}/link`, payload);
            setPropertiesOpen(false);
            setSelectedItem(null);
            await fetchContents();
            showMessage('Properties updated successfully', 'success');
          } catch (err) {
            showMessage(err.response?.data?.message || 'Failed to update properties', 'error');
          } finally {
            setIsOperationInProgress(false);
          }
        }}
        onDelete={async (id) => {
          setIsOperationInProgress(true);
          try {
            await axios.delete(`${API_BASE}/document/${id}`);
            setPropertiesOpen(false);
            setSelectedItem(null);
            await fetchContents();
            showMessage('File deleted successfully', 'success');
          } catch (err) {
            showMessage(err.response?.data?.message || 'Failed to delete file', 'error');
          } finally {
            setIsOperationInProgress(false);
          }
        }}
        onReindex={async (id) => {
          setIsOperationInProgress(true);
          try {
            // Call the indexing API endpoint
            const apiBase = BASE_URL;
            const response = await axios.post(`${apiBase}/index/${id}`);
            if (response.data.success || response.status === 202) {
              showMessage('Document reindexing started successfully!', 'success');
              await fetchContents(); // Refresh to show updated status
            }
          } catch (err) {
            showMessage(
              err.response?.data?.message || err.response?.data?.error || 'Failed to reindex document',
              'error'
            );
          } finally {
            setIsOperationInProgress(false);
          }
        }}
      />

      {/* Folder Properties Modal */}
      <FolderPropertiesModal
        open={folderPropertiesOpen}
        onClose={() => {
          setFolderPropertiesOpen(false);
          setSelectedItem(null);
        }}
        folderData={selectedItem}
        onSave={handleFolderPropertiesSave}
        onDelete={async (id) => {
          setIsOperationInProgress(true);
          try {
            await axios.delete(`${API_BASE}/folder/${id}`);
            setFolderPropertiesOpen(false);
            setSelectedItem(null);
            await fetchContents();
            showMessage('Folder deleted successfully', 'success');
          } catch (err) {
            showMessage(err.response?.data?.message || 'Failed to delete folder', 'error');
          } finally {
            setIsOperationInProgress(false);
          }
        }}
      />

      {/* CSV Spreadsheet Viewer */}
      {csvViewerOpen && csvFileData && (
        <CSVSpreadsheetViewer
          fileData={csvFileData}
          onClose={handleCloseCSV}
          onSave={handleCSVSave}
        />
      )}
    </Box>
  );
};

export default FileManager;
