// Main desktop area for Documents page
// Shows folders/files as draggable icons on a desktop surface

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Box, CircularProgress, Alert } from '@mui/material';
import axios from 'axios';
import { useSnackbar } from '../common/SnackbarProvider';
import DesktopItemsGrid from './DesktopItemsGrid';
import { API_BASE, getItemKey } from './fileUtils.jsx';

const DocumentsDesktop = ({
  onFolderOpen,
  desktopLayout,
  onLayoutChange,
  folderColors,
  selectedItems,
  onSelectionChange,
  onContextMenu,
  openWindowFolderIds = [],
}) => {
  const { showMessage } = useSnackbar();
  const [items, setItems] = useState({ folders: [], files: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [draggedItem, setDraggedItem] = useState(null);
  const [draggedItems, setDraggedItems] = useState([]);
  const [dropTarget, setDropTarget] = useState(null);
  const hasGeneratedLayout = useRef(false);

  // Generate default layout for items if not already positioned
  const generateDefaultLayout = useCallback((folders, files) => {
    const ICON_GRID_WIDTH = 4;
    const ICON_GRID_HEIGHT = 4;
    const COLS = 48;

    const allItems = [
      ...folders.map(f => ({ ...f, itemType: 'folder' })),
      ...files.map(f => ({ ...f, itemType: 'file' })),
    ];

    const colsPerRow = Math.floor(COLS / ICON_GRID_WIDTH);

    return allItems.map((item, idx) => ({
      i: getItemKey(item, item.itemType),
      x: (idx % colsPerRow) * ICON_GRID_WIDTH,
      y: Math.floor(idx / colsPerRow) * ICON_GRID_HEIGHT,
      w: ICON_GRID_WIDTH,
      h: ICON_GRID_HEIGHT,
      isDraggable: true,
      isResizable: false,
    }));
  }, []);

  // Fetch root-level items
  const fetchItems = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await axios.get(`${API_BASE}/browse?path=/&fields=light`);
      const data = response.data.data; // API returns nested {data: {data: {...}}}

      const folders = (data.folders || []).map(f => ({ ...f, itemType: 'folder' }));
      const files = (data.documents || []).map(f => ({ ...f, filename: f.filename || f.name, itemType: 'file' }));

      setItems({ folders, files });

      // Generate default layout only on first load (use a ref to track)
      if (!hasGeneratedLayout.current && desktopLayout.length === 0 && (folders.length > 0 || files.length > 0)) {
        const defaultLayout = generateDefaultLayout(folders, files);
        onLayoutChange?.(defaultLayout);
        hasGeneratedLayout.current = true;
      }
    } catch (err) {
      setError('Failed to load desktop items');
      showMessage?.('Failed to load desktop items', 'error');
    } finally {
      setLoading(false);
    }
  }, [showMessage, generateDefaultLayout, onLayoutChange, desktopLayout.length]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  // Handle drag start
  const handleDragStart = (e, item, type) => {
    const key = getItemKey(item, type);
    const selected = Array.from(selectedItems).map(k => {
      const [itemType, id] = k.split('-');
      const allItems = [...items.folders, ...items.files];
      return allItems.find(i => i.id === parseInt(id) && i.itemType === itemType);
    }).filter(Boolean);

    if (selected.length > 1 && selectedItems.has(key)) {
      setDraggedItems(selected);
      e.dataTransfer.setData('text/plain', JSON.stringify(selected));
    } else {
      setDraggedItem({ ...item, itemType: type });
      e.dataTransfer.setData('text/plain', JSON.stringify([{ ...item, itemType: type }]));
    }
    e.dataTransfer.effectAllowed = 'move';
  };

  // Handle drag end
  const handleDragEnd = () => {
    setDraggedItem(null);
    setDraggedItems([]);
    setDropTarget(null);
  };

  // Handle drag over folder icon
  const handleFolderDragOver = (e, targetFolder) => {
    e.preventDefault();
    setDropTarget(targetFolder.id);
  };

  // Handle drag leave
  const handleDragLeave = () => {
    setDropTarget(null);
  };

  // Handle drop on folder icon
  const handleFolderDrop = async (e, targetFolder) => {
    e.preventDefault();

    try {
      const data = e.dataTransfer.getData('text/plain');
      if (!data) return;

      const itemsToMove = JSON.parse(data);

      // Move each item
      for (const item of itemsToMove) {
        const destinationPath = `${targetFolder.path}/${item.name || item.filename}`;

        if (item.itemType === 'folder') {
          // Move folder
          await axios.put(`${API_BASE}/folder/${item.id}`, {
            name: item.name,
            parent_path: targetFolder.path,
          });
        } else {
          // Move file
          await axios.post(`${API_BASE}/document/${item.id}/move`, {
            destination_path: targetFolder.path,
          });
        }
      }

      showMessage?.(`Moved ${itemsToMove.length} item(s) to ${targetFolder.name}`, 'success');
      fetchItems(); // Refresh
    } catch (err) {
      showMessage?.('Failed to move items', 'error');
    } finally {
      handleDragEnd();
    }
  };

  // Handle drop on desktop (move to root)
  const handleDrop = async (e) => {
    e.preventDefault();

    try {
      const data = e.dataTransfer.getData('text/plain');
      if (!data) return;

      const itemsToMove = JSON.parse(data);

      // Move each item to root
      for (const item of itemsToMove) {
        if (item.itemType === 'folder') {
          // Move folder to root
          await axios.put(`${API_BASE}/folder/${item.id}`, {
            name: item.name,
            parent_path: '/',
          });
        } else {
          // Move file to root
          await axios.post(`${API_BASE}/document/${item.id}/move`, {
            destination_path: '/',
          });
        }
      }

      showMessage?.(`Moved ${itemsToMove.length} item(s) to desktop`, 'success');
      fetchItems(); // Refresh
    } catch (err) {
      showMessage?.('Failed to move items', 'error');
    } finally {
      handleDragEnd();
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 400 }}>
        <CircularProgress />
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

  // Filter out folders that have open windows
  const visibleFolders = items.folders.filter(f => !openWindowFolderIds.includes(f.id));

  return (
    <Box sx={{ width: '100%', height: '100%', position: 'relative' }}>
      <DesktopItemsGrid
        folders={visibleFolders}
        files={items.files}
        layout={desktopLayout}
        onLayoutChange={onLayoutChange}
        onFolderOpen={onFolderOpen}
        onDragStart={handleDragStart}
        onDrop={handleDrop}
        onDragOver={handleFolderDragOver}
        onFolderDrop={handleFolderDrop}
        selectedItems={selectedItems}
        onSelectionChange={onSelectionChange}
        folderColors={folderColors}
        dropTarget={dropTarget}
      />
    </Box>
  );
};

export default DocumentsDesktop;
