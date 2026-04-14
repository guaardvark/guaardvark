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
import { API_BASE, getFileIcon, getFileIconSmall, FolderIndexIndicator, formatBytes, formatDate, getItemKey, isMediaFile } from './fileUtils.jsx';
import MediaView from './MediaView';

// Stable grid components (must be defined outside render to avoid Virtuoso remounts)
const GridItemWrapper = React.forwardRef(({ children, ...props }, ref) => (
  <Box ref={ref} {...props} sx={{ width: 120, p: 1 }}>
    {children}
  </Box>
));
GridItemWrapper.displayName = 'GridItemWrapper';

const GridListWrapper = React.forwardRef(({ children, ...props }, ref) => (
  <Box ref={ref} {...props} sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, p: 1 }}>
    {children}
  </Box>
));
GridListWrapper.displayName = 'GridListWrapper';

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
  onDrop,
  onFocusContext,
  refreshKey = 0, // Refresh trigger - incrementing this will refresh contents
  folderColors = {}, // Folder ID → color map for nested color coding
  onMediaDetected, // Callback: fires true/false when folder contents are loaded
}) => {
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
        fields: 'light',
        offset: currentOffset,
        limit: PAGE_SIZE,
        sort_by: sortByMap[orderBy] || 'name',
        sort_dir: order,
      });
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
      setError('Failed to load folder contents');
      showMessage?.('Failed to load folder contents', 'error');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [currentPath, folder.path, showMessage, orderBy, order, items.folders.length, items.files.length]);

  // Initial fetch and refresh on path/sort/refreshKey changes
  const fetchInitial = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const pathToFetch = currentPath || folder.path;
      const params = new URLSearchParams({
        path: pathToFetch,
        fields: 'light',
        offset: '0',
        limit: String(PAGE_SIZE),
        sort_by: sortByMap[orderBy] || 'name',
        sort_dir: order,
      });
      const response = await axios.get(`${API_BASE}/browse?${params.toString()}`);
      const data = response.data.data;

      const newFiles = (data.documents || []).map(f => ({ ...f, filename: f.filename || f.name, itemType: 'file' }));
      setItems({
        folders: (data.folders || []).map(f => ({ ...f, itemType: 'folder' })),
        files: newFiles,
      });
      setHasMore(data.has_more ?? false);
      setTotalItems((data.total_folders ?? 0) + (data.total_documents ?? 0));

      // Let parent know whether this folder has media content
      if (onMediaDetected) {
        const hasMedia = newFiles.some(f => isMediaFile(f.filename));
        onMediaDetected(hasMedia);
      }
    } catch (err) {
      setError('Failed to load folder contents');
      showMessage?.('Failed to load folder contents', 'error');
    } finally {
      setLoading(false);
    }
  }, [currentPath, folder.path, showMessage, orderBy, order]);

  useEffect(() => {
    fetchInitial();
  }, [fetchInitial, refreshKey]);

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

      // Delete: Delete selected items
      if (e.key === 'Delete' && selectedItems.size > 0) {
        e.preventDefault();
        // Show context menu at center of screen for delete confirmation
        const fakeEvent = {
          clientX: window.innerWidth / 2,
          clientY: window.innerHeight / 2,
          preventDefault: () => { },
          stopPropagation: () => { },
        };
        if (onContextMenu) {
          // Trigger context menu which has delete option
          onContextMenu(fakeEvent, null, 'folder-window');
        }
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
    if (e.target.closest('.MuiListItem-root') || e.target.closest('.MuiCard-root')) return;
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
    onContextMenu, selectedItems, items,
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
            if (isFolder) h.handleFolderDoubleClick(e, item);
            else if (onFileOpen) onFileOpen(e, item);
          }}
          draggable
          onDragStart={(e) => h.handleDragStart(e, item, item.itemType)}
          onDragOver={(e) => e.preventDefault()}
          onDrop={h.handleDrop}
          onContextMenu={(e) => h.onContextMenu && h.onContextMenu(e, item, item.itemType)}
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
          const clickedOnItem = e.target.closest('.MuiTableRow-root');
          if (!clickedOnItem) {
            e.preventDefault();
            e.stopPropagation();
            // delegated via ref so always fresh
          }
        }}
      />
    ),
    TableHead: React.forwardRef((props, ref) => <TableHead {...props} ref={ref} />),
    TableRow: stableTableRowComponent,
    TableBody: React.forwardRef((props, ref) => <TableBody {...props} ref={ref} />),
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
        <Alert severity="error">{error}</Alert>
      </Box>
    );
  }

  if (items.folders.length === 0 && items.files.length === 0) {
    return (
      <Box
        sx={{ p: 3, textAlign: 'center', minHeight: '200px', cursor: 'default' }}
        onContextMenu={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (onContextMenu) {
            // Pass folder with current path so DocumentsPage knows the context
            const pathToUse = currentPath || folder.path;
            const targetFolder = { ...folder, path: pathToUse };
            onContextMenu(e, targetFolder, 'folder-window');
          }
        }}
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
          This folder is empty
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
                  Name
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={orderBy === 'date'}
                  direction={orderBy === 'date' ? order : 'asc'}
                  onClick={() => handleSortRequest('date')}
                >
                  Date
                </TableSortLabel>
              </TableCell>
              <TableCell align="right">
                <TableSortLabel
                  active={orderBy === 'size'}
                  direction={orderBy === 'size' ? order : 'asc'}
                  onClick={() => handleSortRequest('size')}
                >
                  Size
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
                      <Tooltip title="Code Repository">
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
                      ? `${(item.subfolder_count || 0) + (item.document_count || 0)} items`
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
        const clickedOnItem = e.target.closest('.MuiCard-root');
        if (!clickedOnItem) {
          e.preventDefault();
          e.stopPropagation();
          if (onContextMenu) {
            const pathToUse = currentPath || folder.path;
            const targetFolder = { ...folder, path: pathToUse };
            onContextMenu(e, targetFolder, 'folder-window');
          }
        }
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
        components={{
          Item: GridItemWrapper,
          List: GridListWrapper,
        }}
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
                onDoubleClick={(e) => { if (isFolder) handleFolderDoubleClick(e, item); else if (onFileOpen) onFileOpen(e, item); }}
                draggable
                onDragStart={(e) => handleDragStart(e, item, type)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                onContextMenu={(e) => onContextMenu && onContextMenu(e, item, type)}
              >
                <CardContent sx={{ textAlign: 'center', p: 1 }}>
                  {isFolder ? (
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
                          <Tooltip title="Code Repository">
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
