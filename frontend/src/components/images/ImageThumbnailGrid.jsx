// frontend/src/components/images/ImageThumbnailGrid.jsx
// Displays folder contents as a thumbnail grid with selection, drag, and context menu support.
// Used inside folder windows on the ImagesPage.

import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Box, Typography, CircularProgress, useTheme, TableSortLabel,
} from '@mui/material';
import { Folder as FolderIcon, BrokenImage as BrokenImageIcon } from '@mui/icons-material';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || '';
const THUMB_SIZE = 160;
const GRID_GAP = 8;

const ImageThumbnailGrid = ({
  folder,
  currentPath,
  onNavigateToPath,
  viewMode = 'grid',
  selectedItems = new Set(),
  onSelectionChange,
  onContextMenu,
  onDragStart,
  onFolderOpen,
  onDrop,
  onImageDoubleClick,
  refreshKey = 0,
}) => {
  const theme = useTheme();
  const [items, setItems] = useState({ folders: [], files: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('name');
  const [sortDir, setSortDir] = useState('asc');
  const containerRef = useRef(null);
  const lastClickedIndex = useRef(null);

  // Fetch folder contents
  const fetchContents = useCallback(async () => {
    if (!currentPath) return;
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API_BASE}/api/files/browse`, {
        params: { path: currentPath, fields: 'light', limit: 500 },
      });
      const data = res.data?.data || res.data;
      setItems({
        folders: data.folders || [],
        files: data.files || data.documents || [],
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [currentPath]);

  useEffect(() => { fetchContents(); }, [fetchContents, refreshKey]);

  // Sort helper
  const sortItems = useCallback((arr) => {
    const sorted = [...arr];
    sorted.sort((a, b) => {
      let cmp = 0;
      const nameA = (a.name || a.filename || '').toLowerCase();
      const nameB = (b.name || b.filename || '').toLowerCase();
      if (sortBy === 'name') {
        cmp = nameA.localeCompare(nameB);
      } else if (sortBy === 'date') {
        cmp = (a.updated_at || a.uploaded_at || '').localeCompare(b.updated_at || b.uploaded_at || '');
      } else if (sortBy === 'size') {
        cmp = (a.file_size || a.size || 0) - (b.file_size || b.size || 0);
      }
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return sorted;
  }, [sortBy, sortDir]);

  // All items combined for selection indexing (folders first, then files, both sorted)
  const allItems = useMemo(() => [
    ...sortItems(items.folders).map(f => ({ ...f, itemType: 'folder', key: `folder-${f.id}` })),
    ...sortItems(items.files).map(f => ({ ...f, itemType: 'file', key: `file-${f.id}` })),
  ], [items, sortItems]);

  // Only file items (for lightbox navigation)
  const fileItems = useMemo(() => allItems.filter(i => i.itemType === 'file'), [allItems]);

  const handleItemClick = useCallback((e, item, index) => {
    e.stopPropagation();
    const key = item.key;

    if (e.shiftKey && lastClickedIndex.current !== null) {
      const start = Math.min(lastClickedIndex.current, index);
      const end = Math.max(lastClickedIndex.current, index);
      const newSelection = new Set(selectedItems);
      for (let i = start; i <= end; i++) {
        newSelection.add(allItems[i].key);
      }
      onSelectionChange?.(newSelection);
    } else if (e.ctrlKey || e.metaKey) {
      const newSelection = new Set(selectedItems);
      if (newSelection.has(key)) newSelection.delete(key);
      else newSelection.add(key);
      onSelectionChange?.(newSelection);
    } else {
      onSelectionChange?.(new Set([key]));
    }
    lastClickedIndex.current = index;
  }, [selectedItems, onSelectionChange, allItems]);

  const handleItemDoubleClick = useCallback((e, item) => {
    e.stopPropagation();
    if (item.itemType === 'folder') {
      onNavigateToPath?.(item.path);
    } else {
      // Image double-click -> lightbox
      const fileIndex = fileItems.findIndex(f => f.key === item.key);
      onImageDoubleClick?.(item, fileIndex, fileItems);
    }
  }, [onNavigateToPath, onImageDoubleClick, fileItems]);

  const handleItemContextMenu = useCallback((e, item) => {
    e.preventDefault();
    e.stopPropagation();
    if (!selectedItems.has(item.key)) {
      onSelectionChange?.(new Set([item.key]));
    }
    onContextMenu?.(e, item, item.itemType === 'folder' ? 'folder' : 'image');
  }, [selectedItems, onSelectionChange, onContextMenu]);

  const handleBackgroundContextMenu = useCallback((e) => {
    if (e.target === containerRef.current || e.target.dataset?.background) {
      e.preventDefault();
      onSelectionChange?.(new Set());
      onContextMenu?.(e, null, 'folder-window');
    }
  }, [onContextMenu, onSelectionChange]);

  const handleDragStartItem = useCallback((e, item) => {
    const dragItems = selectedItems.has(item.key)
      ? allItems.filter(i => selectedItems.has(i.key))
      : [item];
    e.dataTransfer.setData('application/json', JSON.stringify(
      dragItems.map(i => ({ id: i.id, type: i.itemType, path: i.path, name: i.name || i.filename }))
    ));
    e.dataTransfer.effectAllowed = 'move';
    onDragStart?.(e, item);
  }, [selectedItems, allItems, onDragStart]);

  const handleContainerDrop = useCallback((e) => {
    e.preventDefault();
    onDrop?.(e);
  }, [onDrop]);

  const handleDragOver = useCallback((e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const handleBackgroundClick = useCallback((e) => {
    if (e.target === containerRef.current || e.target.dataset?.background) {
      onSelectionChange?.(new Set());
      lastClickedIndex.current = null;
    }
  }, [onSelectionChange]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', minHeight: 200 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ p: 2, textAlign: 'center' }}>
        <Typography color="error">{error}</Typography>
      </Box>
    );
  }

  // List view mode
  if (viewMode === 'list') {
    return (
      <Box
        ref={containerRef}
        data-background="true"
        onClick={handleBackgroundClick}
        onContextMenu={handleBackgroundContextMenu}
        onDrop={handleContainerDrop}
        onDragOver={handleDragOver}
        sx={{ overflow: 'auto', height: '100%', minHeight: 200 }}
      >
        {/* Sort header row */}
        <Box sx={{
          display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.25,
          borderBottom: 1, borderColor: 'divider', position: 'sticky', top: 0,
          backgroundColor: theme.palette.background.paper, zIndex: 1,
        }}>
          <Box sx={{ width: 28, flexShrink: 0 }} />
          <TableSortLabel
            active={sortBy === 'name'}
            direction={sortBy === 'name' ? sortDir : 'asc'}
            onClick={() => { setSortDir(sortBy === 'name' && sortDir === 'asc' ? 'desc' : 'asc'); setSortBy('name'); }}
            sx={{ flex: 1 }}
          >
            <Typography variant="caption" color="text.secondary">Name</Typography>
          </TableSortLabel>
          <TableSortLabel
            active={sortBy === 'date'}
            direction={sortBy === 'date' ? sortDir : 'asc'}
            onClick={() => { setSortDir(sortBy === 'date' && sortDir === 'asc' ? 'desc' : 'asc'); setSortBy('date'); }}
          >
            <Typography variant="caption" color="text.secondary">Date</Typography>
          </TableSortLabel>
          <TableSortLabel
            active={sortBy === 'size'}
            direction={sortBy === 'size' ? sortDir : 'asc'}
            onClick={() => { setSortDir(sortBy === 'size' && sortDir === 'asc' ? 'desc' : 'asc'); setSortBy('size'); }}
            sx={{ minWidth: 60 }}
          >
            <Typography variant="caption" color="text.secondary">Size</Typography>
          </TableSortLabel>
        </Box>
        {allItems.length === 0 && (
          <Box data-background="true" sx={{ width: '100%', textAlign: 'center', py: 4 }}>
            <Typography color="text.secondary" data-background="true">Empty folder</Typography>
          </Box>
        )}
        {allItems.map((item, index) => {
          const isSelected = selectedItems.has(item.key);
          const isFolder = item.itemType === 'folder';
          return (
            <Box
              key={item.key}
              draggable
              onDragStart={(e) => handleDragStartItem(e, item)}
              onClick={(e) => handleItemClick(e, item, index)}
              onDoubleClick={(e) => handleItemDoubleClick(e, item)}
              onContextMenu={(e) => handleItemContextMenu(e, item)}
              sx={{
                display: 'flex', alignItems: 'center', gap: 1, px: 1.5, py: 0.5,
                cursor: 'pointer', userSelect: 'none',
                borderRadius: 0.5,
                backgroundColor: isSelected ? theme.palette.action.selected : 'transparent',
                '&:hover': { backgroundColor: theme.palette.action.hover },
              }}
            >
              {isFolder ? (
                <FolderIcon sx={{ fontSize: 20, color: item.color || '#90CAF9' }} />
              ) : (
                <Box
                  component="img"
                  src={`${API_BASE}/api/files/thumbnail?path=${encodeURIComponent(item.path)}`}
                  alt={item.filename}
                  loading="lazy"
                  sx={{ width: 28, height: 28, objectFit: 'cover', borderRadius: 0.5, flexShrink: 0 }}
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
              )}
              <Typography variant="body2" noWrap sx={{ flex: 1 }}>
                {isFolder ? item.name : item.filename}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ minWidth: 70, textAlign: 'right', flexShrink: 0 }}>
                {isFolder
                  ? formatDate(item.updated_at)
                  : formatDate(item.uploaded_at)}
              </Typography>
              {!isFolder && (item.size || item.file_size) ? (
                <Typography variant="caption" color="text.secondary" sx={{ minWidth: 60, textAlign: 'right', flexShrink: 0 }}>
                  {formatBytes(item.size || item.file_size)}
                </Typography>
              ) : (
                <Typography variant="caption" color="text.secondary" sx={{ minWidth: 60, textAlign: 'right', flexShrink: 0 }}>
                  {isFolder ? `${(item.document_count || 0) + (item.subfolder_count || 0)} items` : ''}
                </Typography>
              )}
            </Box>
          );
        })}
      </Box>
    );
  }

  // Grid view mode (default)
  return (
    <Box
      ref={containerRef}
      data-background="true"
      onClick={handleBackgroundClick}
      onContextMenu={handleBackgroundContextMenu}
      onDrop={handleContainerDrop}
      onDragOver={handleDragOver}
      sx={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: `${GRID_GAP}px`,
        p: 1,
        minHeight: 200,
        alignContent: 'flex-start',
        overflow: 'auto',
        height: '100%',
      }}
    >
      {allItems.length === 0 && (
        <Box data-background="true" sx={{ width: '100%', textAlign: 'center', py: 4 }}>
          <Typography color="text.secondary" data-background="true">Empty folder</Typography>
        </Box>
      )}

      {allItems.map((item, index) => {
        const isSelected = selectedItems.has(item.key);
        const isFolder = item.itemType === 'folder';

        return (
          <Box
            key={item.key}
            draggable
            onDragStart={(e) => handleDragStartItem(e, item)}
            onClick={(e) => handleItemClick(e, item, index)}
            onDoubleClick={(e) => handleItemDoubleClick(e, item)}
            onContextMenu={(e) => handleItemContextMenu(e, item)}
            sx={{
              width: THUMB_SIZE,
              cursor: 'pointer',
              borderRadius: 1,
              border: isSelected
                ? `2px solid ${theme.palette.primary.main}`
                : '2px solid transparent',
              backgroundColor: isSelected
                ? theme.palette.action.selected
                : 'transparent',
              '&:hover': {
                backgroundColor: theme.palette.action.hover,
              },
              p: 0.5,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              userSelect: 'none',
            }}
          >
            {isFolder ? (
              <FolderIcon sx={{ fontSize: 64, color: item.color || '#90CAF9' }} />
            ) : (
              <Box
                sx={{
                  width: THUMB_SIZE - 8,
                  height: THUMB_SIZE - 8,
                  borderRadius: 0.5,
                  overflow: 'hidden',
                  backgroundColor: theme.palette.action.hover,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: `1px solid ${theme.palette.divider}`,
                  position: 'relative',
                }}
              >
                <Box
                  component="img"
                  src={`${API_BASE}/api/files/thumbnail?path=${encodeURIComponent(item.path)}`}
                  alt={item.filename}
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
                <Box sx={{ display: 'none', alignItems: 'center', justifyContent: 'center', width: '100%', height: '100%', position: 'absolute', top: 0, left: 0 }}>
                  <BrokenImageIcon sx={{ fontSize: 32, color: 'text.secondary' }} />
                </Box>
              </Box>
            )}
            <Typography
              variant="caption"
              sx={{
                mt: 0.5,
                textAlign: 'center',
                width: '100%',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                fontSize: '0.7rem',
              }}
            >
              {isFolder ? item.name : item.filename}
            </Typography>
          </Box>
        );
      })}
    </Box>
  );
};

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return '';
  }
}

export default ImageThumbnailGrid;
