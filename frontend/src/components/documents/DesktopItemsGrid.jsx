// React-grid-layout wrapper for desktop file/folder icons
// Provides free-form draggable positioning for desktop items

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Box, Card, CardActionArea, CardContent, Typography, useTheme, alpha } from '@mui/material';
import { Folder as FolderIcon } from '@mui/icons-material';
import ReactGridLayoutLib, { WidthProvider } from 'react-grid-layout';
import { useLayout } from '../../contexts/LayoutContext';
import { getFileIcon, getItemKey } from './fileUtils.jsx';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';

const DesktopGridLayout = WidthProvider(ReactGridLayoutLib);

// Grid configuration (same as FileManager folder grid)
const DESKTOP_COLS = 48;
const DESKTOP_ROW_HEIGHT = 30;
const ICON_GRID_WIDTH = 4;  // ~145px
const ICON_GRID_HEIGHT = 4; // ~120px

const DesktopItemsGrid = ({
  folders = [],
  files = [],
  layout = [],
  onLayoutChange,
  onFolderOpen,
  onDragStart,
  onDrop,
  onDragOver,
  onFolderDrop,
  selectedItems = new Set(),
  onSelectionChange,
  folderColors = {},
  dropTarget = null,
}) => {
  const theme = useTheme();
  const { gridSettings } = useLayout();
  const { RGL_WIDTH_PROP_PX, CONTAINER_PADDING_PX } = gridSettings;

  // Drag-to-select state
  const [selectionBox, setSelectionBox] = useState(null);
  const [isSelecting, setIsSelecting] = useState(false);
  const contentAreaRef = useRef(null);
  const itemRefs = useRef(new Map());
  const [isFocused, setIsFocused] = useState(false);

  // Handle item click (selection)
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

  // Handle folder double-click (open)
  const handleFolderDoubleClick = (e, folder) => {
    e.preventDefault();
    e.stopPropagation();
    if (onFolderOpen) {
      onFolderOpen(folder);
    }
  };

  // Handle drag over folder
  const handleFolderDragOver = (e, folder) => {
    e.preventDefault();
    e.stopPropagation();
    if (onDragOver) {
      onDragOver(e, folder);
    }
  };

  // Handle drop on folder
  const handleFolderDrop = (e, folder) => {
    e.preventDefault();
    e.stopPropagation();
    if (onFolderDrop) {
      onFolderDrop(e, folder);
    }
  };

  // Keyboard shortcuts - only active when this component has focus
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Only handle keyboard shortcuts when focused and not in an input field
      if (!isFocused) return;
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
        return;
      }

      const allItems = [
        ...folders.map(f => ({ ...f, itemType: 'folder' })),
        ...files.map(f => ({ ...f, itemType: 'file' })),
      ];

      // Ctrl+A: Select all items
      if (e.ctrlKey && e.key === 'a') {
        e.preventDefault();
        const allKeys = new Set(allItems.map(i => getItemKey(i, i.itemType)));
        if (onSelectionChange) {
          onSelectionChange(allKeys);
        }
      }

      // Delete: Delete selected items (trigger context menu for confirmation)
      if (e.key === 'Delete' && selectedItems.size > 0) {
        e.preventDefault();
        // Note: Desktop delete would be handled by parent component's context menu
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
  }, [folders, files, selectedItems, onSelectionChange, isFocused]);

  // Drag-to-select handlers
  const handleSelectionMouseDown = useCallback((e) => {
    // Only start selection on left click on empty area
    if (e.button !== 0) return;
    if (e.target.closest('.MuiCard-root')) return;

    const rect = contentAreaRef.current?.getBoundingClientRect();
    if (!rect) return;

    const startX = e.clientX - rect.left;
    const startY = e.clientY - rect.top;

    setIsSelecting(true);
    setSelectionBox({ startX, startY, currentX: startX, currentY: startY });
  }, []);

  const handleSelectionMouseMove = useCallback((e) => {
    if (!isSelecting || !selectionBox) return;

    const rect = contentAreaRef.current?.getBoundingClientRect();
    if (!rect) return;

    const currentX = e.clientX - rect.left;
    const currentY = e.clientY - rect.top;

    setSelectionBox(prev => {
      if (!prev) return prev;

      const newBox = { ...prev, currentX, currentY };

      // Check intersection with items using the updated box
      const minX = Math.min(newBox.startX, currentX);
      const maxX = Math.max(newBox.startX, currentX);
      const minY = Math.min(newBox.startY, currentY);
      const maxY = Math.max(newBox.startY, currentY);

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

        // Check if selection box intersects with item
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

      return newBox;
    });
  }, [isSelecting, selectionBox, onSelectionChange]);

  const handleSelectionMouseUp = useCallback(() => {
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

  // Render folder card
  const renderFolderCard = (folder) => {
    const key = getItemKey(folder, 'folder');
    const isSelected = selectedItems.has(key);
    const folderColor = folderColors[folder.id] || theme.palette.primary.main;
    const isDropTarget = dropTarget === folder.id;

    return (
      <div
        key={key}
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
          sx={{
            width: '100%',
            height: '100%',
            cursor: 'grab',
            border: isSelected ? '2px solid' : isDropTarget ? '2px solid' : '1px solid',
            borderColor: isSelected ? 'primary.main' : isDropTarget ? 'primary.main' : 'divider',
            bgcolor: isSelected ? 'action.selected' : isDropTarget ? 'action.hover' : 'background.paper',
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
            className="desktop-icon-drag-handle"
            onClick={(e) => handleItemClick(e, folder, 'folder')}
            onDoubleClick={(e) => handleFolderDoubleClick(e, folder)}
            draggable
            onDragStart={(e) => onDragStart && onDragStart(e, folder, 'folder')}
            onDragOver={(e) => handleFolderDragOver(e, folder)}
            onDrop={(e) => handleFolderDrop(e, folder)}
            sx={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              alignItems: 'center',
              p: 0.5,
            }}
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
                {folder.name}
              </Typography>
            </CardContent>
          </CardActionArea>
        </Card>
      </div>
    );
  };

  // Render file card
  const renderFileCard = (file) => {
    const key = getItemKey(file, 'file');
    const isSelected = selectedItems.has(key);

    return (
      <div
        key={key}
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
          sx={{
            width: '100%',
            height: '100%',
            cursor: 'grab',
            border: isSelected ? '2px solid' : '1px solid',
            borderColor: isSelected ? 'primary.main' : 'divider',
            bgcolor: isSelected ? 'action.selected' : 'background.paper',
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
            className="desktop-icon-drag-handle"
            onClick={(e) => handleItemClick(e, file, 'file')}
            draggable
            onDragStart={(e) => onDragStart && onDragStart(e, file, 'file')}
            sx={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'center',
              alignItems: 'center',
              p: 0.5,
            }}
          >
            <CardContent sx={{ textAlign: 'center', position: 'relative', width: '100%', p: 0.5, '&:last-child': { pb: 0.5 } }}>
              {getFileIcon(file.filename, isSelected, theme)}
              <Typography variant="caption" noWrap sx={{ mt: 0.5, px: 0.5, fontSize: '0.75rem' }}>
                {file.filename}
              </Typography>
            </CardContent>
          </CardActionArea>
        </Card>
      </div>
    );
  };

  // Combine folders and files into single layout
  const allItems = [
    ...folders.map(folder => ({ ...folder, itemType: 'folder' })),
    ...files.map(file => ({ ...file, itemType: 'file' })),
  ];

  return (
    <Box
      ref={contentAreaRef}
      tabIndex={0}
      onFocus={() => setIsFocused(true)}
      onBlur={() => setIsFocused(false)}
      onClick={() => contentAreaRef.current?.focus()}
      sx={{
        width: '100%',
        minHeight: 400,
        position: 'relative',
        userSelect: isSelecting ? 'none' : 'auto',
        outline: 'none', // Remove focus outline
        '&:focus': {
          outline: 'none', // Ensure no focus ring
        }
      }}
      onMouseDown={handleSelectionMouseDown}
      onDrop={onDrop}
      onDragOver={(e) => e.preventDefault()}
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
            bgcolor: (theme) => theme.palette.primary.main + '1A', // 10% opacity
            border: '1px solid',
            borderColor: 'primary.main',
            pointerEvents: 'none',
            zIndex: 1000,
          }}
        />
      )}

      <DesktopGridLayout
        layout={layout}
        cols={DESKTOP_COLS}
        rowHeight={DESKTOP_ROW_HEIGHT}
        width={RGL_WIDTH_PROP_PX}
        margin={[8, 8]}
        containerPadding={[CONTAINER_PADDING_PX / 10, CONTAINER_PADDING_PX / 10]}
        isDraggable={true}
        isResizable={false}
        compactType={null}  // Free-form placement
        preventCollision={false}
        allowOverlap={true}
        useCSSTransforms={false}
        draggableHandle=".desktop-icon-drag-handle"
        draggableCancel="button, input, textarea, select, option"
        onLayoutChange={onLayoutChange}
      >
        {layout.map((layoutItem) => {
          const item = allItems.find(i => getItemKey(i, i.itemType) === layoutItem.i);
          if (!item) return null;

          return item.itemType === 'folder' ? renderFolderCard(item) : renderFileCard(item);
        })}
      </DesktopGridLayout>
    </Box>
  );
};

export default DesktopItemsGrid;
