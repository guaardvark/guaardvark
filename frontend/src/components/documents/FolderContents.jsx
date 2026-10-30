// Folder contents component - displays files/subfolders inside a folder window
// Supports list and grid views, selection, and drag-drop

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Box,
  CircularProgress,
  Alert,
  Typography,
  Card,
  CardActionArea,
  CardContent,
  Tooltip,
  useTheme,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TableSortLabel,
} from '@mui/material';
import { Folder, Code } from 'lucide-react';
import axios from 'axios';
import { TableVirtuoso, VirtuosoGrid } from 'react-virtuoso';
import { useSnackbar } from '../common/SnackbarProvider';
import { API_BASE, getFileIcon, getFileIconSmall, FolderIndexIndicator, formatBytes, formatDate, getItemKey } from './fileUtils.jsx';
import MediaView from './MediaView';
import { folderHasVisualMedia } from './folderViewPolicy';
import { useUiTranslation } from '../../i18n';
import useDeferredClick from '../../hooks/useDeferredClick';
import { ThumbFileBody, ThumbFolderBody, useFileMetaLine } from './DesktopCards';

// Stable grid components (must be defined outside render to avoid Virtuoso remounts)
const GridItemWrapper = React.forwardRef(({ children, ...props }, ref) => (
  <Box ref={ref} {...props} sx={{ width: 120, p: 1 }}>
    {children}
  </Box>
));
GridItemWrapper.displayName = 'GridItemWrapper';

// The Thumbnails view's card size, as on the desktop.
const LargeGridItemWrapper = React.forwardRef(({ children, ...props }, ref) => (
  <Box ref={ref} {...props} sx={{ width: 240, p: 0.5 }}>
    {children}
  </Box>
));
LargeGridItemWrapper.displayName = 'LargeGridItemWrapper';

const GridListWrapper = React.forwardRef(({ children, ...props }, ref) => (
  <Box ref={ref} {...props} sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, p: 1 }}>
    {children}
  </Box>
));
GridListWrapper.displayName = 'GridListWrapper';

const GRID_COMPONENTS = { Item: GridItemWrapper, List: GridListWrapper };
const LARGE_GRID_COMPONENTS = { Item: LargeGridItemWrapper, List: GridListWrapper };

const FolderContents = ({
  folder,
  currentPath, // Current navigation path within folder window
  onNavigateToPath, // Callback to navigate to subfolder
  viewMode = 'list',
  selectedItems = new Set(),
  onSelectionChange,
  _onItemsMove,
  onContextMenu,
  onDragStart,
  onFolderOpen,
  onFileOpen,
  onFileClick,
  onDrop,
  onFocusContext,
  refreshKey = 0, // Refresh trigger - incrementing this will refresh contents
  folderColors = {}, // Folder ID → color map for nested color coding
  onMediaDetected, // Callback: fires true/false when folder contents are loaded
  mediaOnly = false, // only pictures, video and sound, and the folders holding them
  largeCards = false, // the grid draws the Thumbnails view's cards
}) => {
  const metaLine = useFileMetaLine();
  const { t } = useUiTranslation();
  const theme = useTheme();
  const { showMessage } = useSnackbar();
  const [items, setItems] = useState({ folders: [], files: [] });
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [_totalItems, setTotalItems] = useState(0);
  const PAGE_SIZE = 200;

  // Drag-to-select state
  const [selectionBox, setSelectionBox] = useState(null);
  const [isSelecting, setIsSelecting] = useState(false);
  const contentAreaRef = useRef(null);
  const itemRefs = useRef(new Map());
  const { schedule: scheduleFileClick, cancel: cancelFileClick } = useDeferredClick();
  const [isFocused, setIsFocused] = useState(false);
  const rafRef = useRef(null);

  // Sorting state - load from localStorage
  const [orderBy, setOrderBy] = useState(() => {
    const saved = localStorage.getItem('documentsPageListSort');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed.orderBy || 'name';
      } catch {
        return 'name';
      }
    }
    return 'name';
  });
  const [order, setOrder] = useState(() => {
    const saved = localStorage.getItem('documentsPageListSort');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed.order || 'asc';
      } catch {
        return 'asc';
      }
    }
    return 'asc';
  });

  // Map frontend sort columns to backend sort_by values
  const sortByMap = { name: 'name', date: 'date', size: 'size' };

  // Fetch folder contents based on currentPath (supports subfolder navigation and pagination)
  const fetchContents = useCallback(async (append = false) => {
    try {
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      setError(null);
      const pathToFetch = currentPath || folder.path;
      const currentOffset = append ? items.folders.length + items.files.length : 0;
      const params = new URLSearchParams({
        path: pathToFetch,
        fields: mediaOnly ? 'media' : 'light',
        offset: currentOffset,
        limit: PAGE_SIZE,
        sort_by: sortByMap[orderBy] || 'name',
        sort_dir: order,
      });
      if (mediaOnly) params.set('media_only', '1');
      const response = await axios.get(`${API_BASE}/browse?${params.toString()}`);
      const data = response.data.data;

      const newFolders = (data.folders || []).map(f => ({ ...f, itemType: 'folder' }));
      const newFiles = (data.documents || []).map(f => ({ ...f, filename: f.filename || f.name, itemType: 'file' }));

      if (append) {
        setItems(prev => ({
          folders: [...prev.folders, ...newFolders],
          files: [...prev.files, ...newFiles],
        }));
      } else {
        setItems({ folders: newFolders, files: newFiles });
      }

      setHasMore(data.has_more ?? false);
      setTotalItems((data.total_folders ?? 0) + (data.total_documents ?? 0));
    } catch (err) {
      setError('documents.folder.loadFailed');
      showMessage?.(t('documents.folder.loadFailed'), 'error');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [currentPath, folder.path, showMessage, orderBy, order, items.folders.length, items.files.length, mediaOnly]);

  // Initial fetch and refresh on path/sort/refreshKey changes
  const fetchInitial = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const pathToFetch = currentPath || folder.path;
      const params = new URLSearchParams({
        path: pathToFetch,
        fields: mediaOnly ? 'media' : 'light',
        offset: '0',
        limit: String(PAGE_SIZE),
        sort_by: sortByMap[orderBy] || 'name',
        sort_dir: order,
      });
      if (mediaOnly) params.set('media_only', '1');
      const response = await axios.get(`${API_BASE}/browse?${params.toString()}`);
      const data = response.data.data;

      const newFiles = (data.documents || []).map(f => ({ ...f, filename: f.filename || f.name, itemType: 'file' }));
      setItems({
        folders: (data.folders || []).map(f => ({ ...f, itemType: 'folder' })),
        files: newFiles,
      });
      setHasMore(data.has_more ?? false);
      setTotalItems((data.total_folders ?? 0) + (data.total_documents ?? 0));

      // Let parent know whether this folder has visual media (images/videos)
      if (onMediaDetected) {
        onMediaDetected(folderHasVisualMedia(newFiles));
      }
    } catch (err) {
      setError('documents.folder.loadFailed');
      showMessage?.(t('documents.folder.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [currentPath, folder.path, showMessage, orderBy, order, mediaOnly]);

  useEffect(() => {
    fetchInitial();
  }, [fetchInitial, refreshKey]);

  // Optimistic removal listener: instantly drop deleted items from this window's local list
  // when deletes happen anywhere (desktop or other windows). Keys use getItemKey format.
  useEffect(() => {
    const handleRemoved = (event) => {
      const { keys = [] } = event?.detail || {};
      if (!keys.length) return;
      setItems((prev) => {
        const keySet = new Set(keys);
        const filteredFolders = prev.folders.filter((f) => {
          const k1 = getItemKey(f, 'folder');
          const k2 = `folder-${f.id}`;
          return !keySet.has(k1) && !keySet.has(k2);
        });
        const filteredFiles = prev.files.filter((f) => {
          const k1 = getItemKey(f, 'file');
          const k2 = `file-${f.id}`;
          return !keySet.has(k1) && !keySet.has(k2);
        });
        // If selection included removed, parent will have cleared via its slot, but clean here too
        if (onSelectionChange && selectedItems.size > 0) {
          const stillValid = new Set(Array.from(selectedItems).filter((k) => !keySet.has(k)));
          if (stillValid.size !== selectedItems.size) {
            // Defer to avoid set during render
            setTimeout(() => onSelectionChange(stillValid), 0);
          }
        }
        return { folders: filteredFolders, files: filteredFiles };
      });
    };
    window.addEventListener('documents-items-removed', handleRemoved);
    return () => window.removeEventListener('documents-items-removed', handleRemoved);
  }, [selectedItems, onSelectionChange]);

  // Keyboard shortcuts - only active when this component has focus
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Only handle keyboard shortcuts when focused and not in an input field
      if (!isFocused) return;
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        return;
      }

      // Ctrl+A: Select all items
      if (e.ctrlKey && e.key === 'a') {
        e.preventDefault();
        const allKeys = new Set([
          ...items.folders.map(f => getItemKey(f, 'folder')),
          ...items.files.map(f => getItemKey(f, 'file'))
        ]);
        if (onSelectionChange) {
          onSelectionChange(allKeys);
        }
      }

      // Delete: Delete selected items (direct confirm via page handler + optimistic removal)
      if (e.key === 'Delete' && selectedItems.size > 0) {
        e.preventDefault();
        // Do not fake a context menu (that would require a second click on Delete).
        // The page-level key handler (DocumentsPage) will see non-empty activeSelection
        // (via refs) for this surface and open the confirm dialog. Once confirmed,
        // optimistic removal + bulk/parallel delete makes it instant.
        // Selection scoping + focus already happened on prior clicks.
      }

      // Escape: Clear selection
      if (e.key === 'Escape') {
        e.preventDefault();
        if (onSelectionChange) {
          onSelectionChange(new Set());
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [items, selectedItems, onSelectionChange, onContextMenu, isFocused]);

  // Drag-to-select handlers
  const handleSelectionMouseDown = useCallback((e) => {
    // Only start selection on left click on empty area
    if (e.button !== 0) return;
    // List-view rows (.MuiTableRow-root) need this too — without it, mousedown
    // on a row starts a rubber-band that collapses multi-selection right before
    // HTML5 dragstart fires, and only the dragged row ends up in dataTransfer.
    if (e.target.closest('.MuiListItem-root') || e.target.closest('.MuiCard-root') || e.target.closest('.MuiTableRow-root')) return;
    if (onFocusContext) {
      onFocusContext({ type: 'folder', path: currentPath || folder.path, folderId: folder.id });
    }

    const rect = contentAreaRef.current?.getBoundingClientRect();
    if (!rect) return;

    const startX = e.clientX - rect.left;
    const startY = e.clientY - rect.top;

    setIsSelecting(true);
    setSelectionBox({ startX, startY, currentX: startX, currentY: startY });
  }, [currentPath, folder.id, folder.path, onFocusContext]);

  const handleSelectionMouseMove = useCallback((e) => {
    if (!isSelecting || !selectionBox) return;

    const rect = contentAreaRef.current?.getBoundingClientRect();
    if (!rect) return;

    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;

    setSelectionBox(prev => ({ ...prev, currentX, currentY }));

    // Throttle intersection checks to ~60fps via requestAnimationFrame
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      const minX = Math.min(selectionBox.startX, currentX);
      const maxX = Math.max(selectionBox.startX, currentX);
      const minY = Math.min(selectionBox.startY, currentY);
      const maxY = Math.max(selectionBox.startY, currentY);

      const newSelection = new Set();
      itemRefs.current.forEach((element, key) => {
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

      if (onSelectionChange) {
        onSelectionChange(newSelection);
      }
    });
  }, [isSelecting, selectionBox, onSelectionChange]);

  const handleSelectionMouseUp = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    setIsSelecting(false);
    setSelectionBox(null);
  }, []);

  // Attach mouse move and up listeners when selecting
  useEffect(() => {
    if (isSelecting) {
      window.addEventListener('mousemove', handleSelectionMouseMove);
      window.addEventListener('mouseup', handleSelectionMouseUp);
      return () => {
        window.removeEventListener('mousemove', handleSelectionMouseMove);
        window.removeEventListener('mouseup', handleSelectionMouseUp);
      };
    }
  }, [isSelecting, handleSelectionMouseMove, handleSelectionMouseUp]);

  // Handle drag start
  const handleDragStart = (e, item, type) => {
    const key = getItemKey(item, type);
    const selected = Array.from(selectedItems).map(k => {
      const [itemType, id] = k.split('-');
      const allItems = [...items.folders, ...items.files];
      return allItems.find(i => i.id === parseInt(id) && i.itemType === itemType);
    }).filter(Boolean);

    // Create minimal transfer objects with only essential fields
    const createMinimalItem = (fullItem, itemType) => ({
      id: fullItem.id,
      itemType: itemType,
      name: fullItem.name || fullItem.filename,
      filename: fullItem.filename,
      path: fullItem.path,
    });

    let itemsToTransfer;
    if (selected.length > 1 && selectedItems.has(key)) {
      itemsToTransfer = selected.map(item => createMinimalItem(item, item.itemType));
    } else {
      itemsToTransfer = [createMinimalItem(item, type)];
    }

    e.dataTransfer.setData('text/plain', JSON.stringify(itemsToTransfer));
    e.dataTransfer.effectAllowed = 'move';

    if (onDragStart) {
      onDragStart(e, item, type);
    }
  };

  // Handle drop in window
  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (onDrop) {
      // Pass a target object with the current path (which may be a subfolder)
      const pathToUse = currentPath || folder.path;
      const targetFolder = {
        ...folder,
        path: pathToUse
      };
      onDrop(e, targetFolder);
    }
  };

  // Right-click on an item. A subfolder can be opened in this window from its menu.
  const handleItemContextMenu = (e, item, type) => {
    cancelFileClick();
    if (!onContextMenu) return;
    const extras = type === 'folder' && onNavigateToPath
      ? { openInPlace: () => onNavigateToPath(item.path) }
      : null;
    onContextMenu(e, item, type, extras);
  };

  // Right-click on empty space: the menu acts on the folder this window shows.
  const handleBackgroundContextMenu = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (onContextMenu) {
      onContextMenu(e, { ...folder, path: currentPath || folder.path }, 'folder-window');
    }
  };

  // Handle item click
  const handleItemClick = (e, item, type) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      if (onSelectionChange) {
        const key = getItemKey(item, type);
        const newSelection = new Set(selectedItems);
        if (newSelection.has(key)) {
          newSelection.delete(key);
        } else {
          newSelection.add(key);
        }
        onSelectionChange(newSelection);
      }
    } else {
      if (onSelectionChange) {
        onSelectionChange(new Set([getItemKey(item, type)]));
      }
      if (type === 'file' && onFileClick) {
        scheduleFileClick(() => onFileClick(item, items.files));
      }
    }
  };

  // Handle folder double-click - navigate within window for subfolders
  const handleFolderDoubleClick = (e, subfolder) => {
    e.preventDefault();
    e.stopPropagation();

    // If we have onNavigateToPath callback, use it for subfolder navigation
    if (onNavigateToPath) {
      onNavigateToPath(subfolder.path);
    } else if (onFolderOpen) {
      // Fallback for desktop folder icons (opens as new window)
      onFolderOpen(subfolder);
    }
  };

  // Handle sort request - triggers server-side re-sort via useEffect
  const handleSortRequest = (property) => {
    const isAsc = orderBy === property && order === 'asc';
    const newOrder = isAsc ? 'desc' : 'asc';
    setOrder(newOrder);
    setOrderBy(property);

    // Save to localStorage
    localStorage.setItem('documentsPageListSort', JSON.stringify({
      orderBy: property,
      order: newOrder,
    }));
    // fetchInitial will re-run via useEffect dependency on orderBy/order
  };

  // Refs for stable Virtuoso component callbacks (avoids recreating components every render)
  const handlersRef = useRef({});
  handlersRef.current = {
    handleItemClick, handleFolderDoubleClick, handleDragStart, handleDrop,
    handleItemContextMenu, handleBackgroundContextMenu, selectedItems, items,
    cancelFileClick,
  };

  // Stable TableRow component for TableVirtuoso — never recreated, reads current data via ref
  const stableTableRowComponent = useMemo(() => {
    const StableTableRow = ({ item: _item, ...props }) => {
      const h = handlersRef.current;
      const allItems = [...h.items.folders, ...h.items.files];
      const item = allItems[props['data-index']];
      if (!item) return <tr {...props} />;
      const key = getItemKey(item, item.itemType);
      const isSelected = h.selectedItems.has(key);
      const isFolder = item.itemType === 'folder';
      return (
        <TableRow
          {...props}
          hover
          selected={isSelected}
          ref={(el) => {
            if (el) itemRefs.current.set(key, el);
            else itemRefs.current.delete(key);
          }}
          onClick={(e) => h.handleItemClick(e, item, item.itemType)}
          onDoubleClick={(e) => {
            h.cancelFileClick();
            if (isFolder) h.handleFolderDoubleClick(e, item);
            else if (onFileOpen) onFileOpen(e, item, { siblings: h.items.files });
          }}
          draggable
          onDragStart={(e) => h.handleDragStart(e, item, item.itemType)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={h.handleDrop}
          onContextMenu={(e) => h.handleItemContextMenu(e, item, item.itemType)}
          sx={{
            cursor: 'pointer',
            height: 36,
            '& .MuiTableCell-root': { py: 0.25 },
            '&:hover': { backgroundColor: 'action.hover' },
          }}
        />
      );
    };
    return StableTableRow;
  }, []); // Never recreated — reads fresh data via handlersRef

  // Stable components object for TableVirtuoso (must be referentially stable)
  const virtuosoComponents = useMemo(() => ({
    Table: (props) => (
      <Table {...props} size="small"
        onDrop={(e) => handlersRef.current.handleDrop(e)}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (e.dataTransfer.types.includes('Files')) {
            e.dataTransfer.dropEffect = 'copy';
          }
        }}
        onContextMenu={(e) => {
          // Item rows open their own menu and stop the event; the header and
          // any space around the rows get the window's menu.
          if (!e.target.closest('.MuiTableRow-root[data-index]')) {
            handlersRef.current.handleBackgroundContextMenu(e);
          }
        }}
      />
    ),
    TableHead: React.forwardRef(function ForwardedTableHead(props, ref) { return <TableHead {...props} ref={ref} />; }),
    TableRow: stableTableRowComponent,
    TableBody: React.forwardRef(function ForwardedTableBody(props, ref) { return <TableBody {...props} ref={ref} />; }),
  }), [stableTableRowComponent]);

  // Load more items for infinite scroll
  const loadMore = useCallback(() => {
    if (!loadingMore && hasMore) {
      fetchContents(true);
    }
  }, [loadingMore, hasMore, fetchContents]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', p: 3 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="error">{t(error)}</Alert>
      </Box>
    );
  }

  if (items.folders.length === 0 && items.files.length === 0) {
    return (
      <Box
        sx={{ p: 3, textAlign: 'center', minHeight: '200px', cursor: 'default' }}
        onContextMenu={handleBackgroundContextMenu}
        onDrop={handleDrop}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (e.dataTransfer.types.includes('Files')) {
            e.dataTransfer.dropEffect = 'copy';
          }
        }}
      >
        <Typography variant="body2" color="text.secondary">
          {t('documents.folder.empty')}
        </Typography>
      </Box>
    );
  }

  // List view - Virtualized Sortable Table (data arrives pre-sorted from server)
  if (viewMode === 'list') {
    const allSortedItems = [...items.folders, ...items.files];

    return (
      <Box
        ref={contentAreaRef}
        tabIndex={0}
        onFocus={() => {
          setIsFocused(true);
          if (onFocusContext) {
            onFocusContext({ type: 'folder', path: currentPath || folder.path, folderId: folder.id });
          }
        }}
        onBlur={() => setIsFocused(false)}
        onClick={() => contentAreaRef.current?.focus()}
        sx={{
          position: 'relative',
          width: '100%',
          height: '100%',
          minHeight: '200px',
          userSelect: isSelecting ? 'none' : 'auto',
          outline: 'none',
          '&:focus': {
            outline: 'none',
          }
        }}
        onMouseDown={handleSelectionMouseDown}
        onContextMenu={(e) => {
          if (!e.target.closest('.MuiTableRow-root[data-index]')) handleBackgroundContextMenu(e);
        }}
      >
        {/* Selection box overlay */}
        {isSelecting && selectionBox && (
          <Box
            sx={{
              position: 'absolute',
              left: Math.min(selectionBox.startX, selectionBox.currentX),
              top: Math.min(selectionBox.startY, selectionBox.currentY),
              width: Math.abs(selectionBox.currentX - selectionBox.startX),
              height: Math.abs(selectionBox.currentY - selectionBox.startY),
              bgcolor: (theme) => theme.palette.primary.main + '1A',
              border: '1px solid',
              borderColor: 'primary.main',
              pointerEvents: 'none',
              zIndex: 1000,
            }}
          />
        )}

        <TableVirtuoso
          style={{ height: '100%', width: '100%', cursor: 'default' }}
          data={allSortedItems}
          overscan={50}
          fixedItemHeight={36}
          endReached={() => {
            if (hasMore && !loadingMore) loadMore();
          }}
          components={virtuosoComponents}
          fixedHeaderContent={() => (
            <TableRow>
              <TableCell>
                <TableSortLabel
                  active={orderBy === 'name'}
                  direction={orderBy === 'name' ? order : 'asc'}
                  onClick={() => handleSortRequest('name')}
                >
                  {t('documents.folder.columnName')}
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={orderBy === 'date'}
                  direction={orderBy === 'date' ? order : 'asc'}
                  onClick={() => handleSortRequest('date')}
                >
                  {t('documents.folder.columnDate')}
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={orderBy === 'size'}
                  direction={orderBy === 'size' ? order : 'asc'}
                  onClick={() => handleSortRequest('size')}
                >
                  {t('documents.folder.columnSize')}
                </TableSortLabel>
              </TableCell>
            </TableRow>
          )}
          itemContent={(index, item) => {
            const key = getItemKey(item, item.itemType);
            const isSelected = selectedItems.has(key);
            const isFolder = item.itemType === 'folder';

            return (
              <>
                <TableCell>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    {isFolder ? (
                      <Box sx={{ position: 'relative', display: 'inline-flex' }}>
                        <Folder
                          size={20}
                          color={isSelected ? theme.palette.primary.main : (folderColors[item.id] || theme.palette.action.active)}
                          strokeWidth={1.5}
                        />
                        <FolderIndexIndicator item={item} theme={theme} size={5} />
                      </Box>
                    ) : (
                      getFileIconSmall(item.filename, isSelected, theme, item.index_status, item.path)
                    )}
                    <Typography variant="body2" noWrap sx={{ flex: 1 }}>
                      {isFolder ? item.name : item.filename}
                    </Typography>
                    {isFolder && item.is_repository && (
                      <Tooltip title={t('documents.folder.codeRepository')}>
                        <Code size={14} color={theme.palette.primary.main} style={{ marginLeft: 4 }} />
                      </Tooltip>
                    )}
                  </Box>
                </TableCell>
                <TableCell align="right">
                  <Typography variant="body2" color="text.secondary">
                    {isFolder
                      ? formatDate(item.updated_at)
                      : formatDate(item.uploaded_at)}
                  </Typography>
                </TableCell>
                <TableCell align="right">
                  <Typography variant="body2" color="text.secondary">
                    {isFolder
                      ? t('documents.folder.itemCount', { count: (item.subfolder_count || 0) + (item.document_count || 0) })
                      : formatBytes(item.size)}
                  </Typography>
                </TableCell>
              </>
            );
          }}
        />
        {loadingMore && (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 1 }}>
            <CircularProgress size={20} />
          </Box>
        )}
      </Box>
    );
  }

  // Media view — large preview + thumbnail strip
  if (viewMode === 'media') {
    return (
      <MediaView
        items={items}
        folder={folder}
        onContextMenu={onContextMenu}
        onFileOpen={onFileOpen}
        onNavigateToPath={onNavigateToPath}
      />
    );
  }

  // Grid view - Virtualized
  const allGridItems = [...items.folders, ...items.files];

  return (
    <Box
      ref={contentAreaRef}
      tabIndex={0}
      onFocus={() => {
        setIsFocused(true);
        if (onFocusContext) {
          onFocusContext({ type: 'folder', path: currentPath || folder.path, folderId: folder.id });
        }
      }}
      onBlur={() => setIsFocused(false)}
      onClick={() => contentAreaRef.current?.focus()}
      sx={{
        position: 'relative',
        height: '100%',
        minHeight: '200px',
        cursor: 'default',
        userSelect: isSelecting ? 'none' : 'auto',
        outline: 'none',
        '&:focus': {
          outline: 'none',
        }
      }}
      onMouseDown={handleSelectionMouseDown}
      onDrop={handleDrop}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.dataTransfer.types.includes('Files')) {
          e.dataTransfer.dropEffect = 'copy';
        }
      }}
      onContextMenu={(e) => {
        if (!e.target.closest('.MuiCard-root')) handleBackgroundContextMenu(e);
      }}
    >
      {/* Selection box overlay */}
      {isSelecting && selectionBox && (
        <Box
          sx={{
            position: 'absolute',
            left: Math.min(selectionBox.startX, selectionBox.currentX),
            top: Math.min(selectionBox.startY, selectionBox.currentY),
            width: Math.abs(selectionBox.currentX - selectionBox.startX),
            height: Math.abs(selectionBox.currentY - selectionBox.startY),
            bgcolor: (theme) => theme.palette.primary.main + '1A',
            border: '1px solid',
            borderColor: 'primary.main',
            pointerEvents: 'none',
            zIndex: 1000,
          }}
        />
      )}

      <VirtuosoGrid
        style={{ height: '100%', width: '100%' }}
        totalCount={allGridItems.length}
        overscan={50}
        components={largeCards ? LARGE_GRID_COMPONENTS : GRID_COMPONENTS}
        endReached={() => {
          if (hasMore && !loadingMore) loadMore();
        }}
        itemContent={(index) => {
          const item = allGridItems[index];
          if (!item) return null;
          const isFolder = item.itemType === 'folder';
          const type = item.itemType;
          const key = getItemKey(item, type);
          const isSelected = selectedItems.has(key);

          return (
            <Card
              ref={(el) => {
                if (el) itemRefs.current.set(key, el);
                else itemRefs.current.delete(key);
              }}
              sx={{
                border: isSelected ? '2px solid' : '1px solid',
                borderColor: isSelected ? 'primary.main' : 'divider',
                bgcolor: isSelected ? 'action.selected' : 'background.paper',
                width: '100%',
              }}
            >
              <CardActionArea
                onClick={(e) => handleItemClick(e, item, type)}
                onDoubleClick={(e) => { cancelFileClick(); if (isFolder) handleFolderDoubleClick(e, item); else if (onFileOpen) onFileOpen(e, item, { siblings: items.files }); }}
                draggable
                onDragStart={(e) => handleDragStart(e, item, type)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                onContextMenu={(e) => handleItemContextMenu(e, item, type)}
              >
                <CardContent sx={{ textAlign: 'center', p: 1 }}>
                  {largeCards ? (isFolder ? (
                    <ThumbFolderBody
                      folder={item}
                      color={folderColors[item.id] || theme.palette.primary.main}
                      itemCountLabel=""
                    />
                  ) : (
                    <ThumbFileBody file={item} metaLine={metaLine} />
                  )) : isFolder ? (
                    <>
                      <Box sx={{ position: 'relative', display: 'inline-flex' }}>
                        <Folder
                          size={48}
                          color={isSelected ? theme.palette.primary.main : (folderColors[item.id] || theme.palette.action.active)}
                          strokeWidth={1.5}
                        />
                        <FolderIndexIndicator item={item} theme={theme} />
                      </Box>
                      {item.is_repository && (
                        <Box sx={{ position: 'absolute', top: 8, right: 8 }}>
                          <Tooltip title={t('documents.folder.codeRepository')}>
                            <Code size={16} color={theme.palette.primary.main} />
                          </Tooltip>
                        </Box>
                      )}
                      <Tooltip title={item.name}>
                        <Typography
                          variant="body2"
                          sx={{
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            wordBreak: 'break-word',
                            textAlign: 'center',
                            lineHeight: 1.2,
                            mt: 0.5,
                          }}
                        >
                          {item.name}
                        </Typography>
                      </Tooltip>
                    </>
                  ) : (
                    <>
                      {getFileIcon(item.filename, isSelected, theme, 48, item.index_status, item.path)}
                      <Tooltip title={item.filename}>
                        <Typography
                          variant="body2"
                          sx={{
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            wordBreak: 'break-word',
                            textAlign: 'center',
                            lineHeight: 1.2,
                            mt: 0.5,
                          }}
                        >
                          {item.filename}
                        </Typography>
                      </Tooltip>
                      <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.65rem' }}>
                        {formatBytes(item.size)}
                      </Typography>
                    </>
                  )}
                </CardContent>
              </CardActionArea>
            </Card>
          );
        }}
      />
      {loadingMore && (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 1 }}>
          <CircularProgress size={20} />
        </Box>
      )}
    </Box>
  );
};

export default FolderContents;
