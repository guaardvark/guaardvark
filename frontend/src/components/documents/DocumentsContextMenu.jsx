// Enhanced context menu for Documents page
// Provides different menus based on context: Desktop, Folder, or File

import React from 'react';
import { Menu, MenuItem, Divider, Box } from '@mui/material';
import { useTheme } from '@mui/material/styles';

const FOLDER_COLOR_CHOICES = ['#1976d2', '#9c27b0', '#d32f2f', '#f57c00', '#388e3c', '#455a64', '#7b1fa2', '#5d4037'];

const DocumentsContextMenu = ({
  anchorPosition,
  onClose,
  onNewFolder,
  onUpload,
  onCopy,
  onCut,
  onPaste,
  onDelete,
  onProperties,
  onRename,
  onDownload,
  onEdit,
  onColorChange,
  onIndex,
  onOpenWindow,
  onOpenInCodeEditor,
  hasClipboard = false,
  _hasSelection = false,
  contextType = 'desktop', // 'desktop', 'folder', 'file'
  _selectedItem = null,
  folderColor = null,
  isImage = false,
  isCode = false,
  isPdf = false,
}) => {
  const theme = useTheme();
  const open = Boolean(anchorPosition);

  const handleColorSelect = (color) => {
    if (onColorChange) {
      onColorChange(color);
    }
    onClose();
  };

  // Common menu styling for cleaner, more minimal appearance
  const menuStyles = {
    '& .MuiPaper-root': {
      minWidth: 180,
      boxShadow: '0 2px 8px rgba(0,0,0,0.15)',
      borderRadius: '6px',
      border: '1px solid rgba(0,0,0,0.08)',
    },
    '& .MuiMenuItem-root': {
      fontSize: '0.8125rem',
      py: 0.6,
      px: 1.5,
      minHeight: 'auto',
      '&:hover': {
        backgroundColor: 'rgba(0,0,0,0.04)',
      },
    },
    '& .MuiDivider-root': {
      my: 0.5,
    },
  };

  // Desktop context menu (blank space) OR Folder window context menu
  if (contextType === 'desktop' || contextType === 'folder-window') {
    return (
      <Menu
        open={open}
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={
          anchorPosition || { top: 0, left: 0 }
        }
        sx={menuStyles}
      >
        <MenuItem onClick={onNewFolder}>New Folder</MenuItem>
        <MenuItem onClick={onUpload}>Import Files</MenuItem>
        {hasClipboard && (
          <>
            <Divider />
            <MenuItem onClick={onPaste}>Paste</MenuItem>
          </>
        )}
      </Menu>
    );
  }

  // Folder context menu
  if (contextType === 'folder') {
    return (
      <Menu
        open={open}
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={
          anchorPosition || { top: 0, left: 0 }
        }
        sx={menuStyles}
      >
        {onOpenWindow && <MenuItem onClick={onOpenWindow}>Open in Window</MenuItem>}
        {onOpenWindow && <Divider />}
        {onCut && <MenuItem onClick={onCut}>Cut</MenuItem>}
        {onCopy && <MenuItem onClick={onCopy}>Copy</MenuItem>}
        <MenuItem onClick={onPaste} disabled={!hasClipboard}>Paste</MenuItem>
        <Divider />
        {onColorChange && (
          <MenuItem disableRipple disableGutters sx={{ px: 1.5, py: 0.6 }}>
            <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
              {FOLDER_COLOR_CHOICES.map((color) => (
                <Box
                  key={color}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleColorSelect(color);
                  }}
                  sx={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    backgroundColor: color,
                    border: (folderColor === color) ? `2px solid ${theme.palette.text.primary}` : `1px solid ${theme.palette.divider}`,
                    cursor: 'pointer',
                    boxShadow: '0 1px 3px rgba(0,0,0,0.12)',
                    transition: 'transform 0.1s',
                    '&:hover': {
                      transform: 'scale(1.15)',
                    },
                  }}
                  title={color}
                />
              ))}
            </Box>
          </MenuItem>
        )}
        {onRename && <MenuItem onClick={onRename}>Rename</MenuItem>}
        {onProperties && <MenuItem onClick={onProperties}>Properties</MenuItem>}
        {onIndex && (
          <>
            <Divider />
            <MenuItem onClick={onIndex}>Index Contents</MenuItem>
          </>
        )}
        {onDelete && (
          <>
            <Divider />
            <MenuItem onClick={onDelete}>Delete</MenuItem>
          </>
        )}
      </Menu>
    );
  }

  // File context menu
  if (contextType === 'file') {
    return (
      <Menu
        open={open}
        onClose={onClose}
        anchorReference="anchorPosition"
        anchorPosition={
          anchorPosition || { top: 0, left: 0 }
        }
        sx={menuStyles}
      >
        {onCut && <MenuItem onClick={onCut}>Cut</MenuItem>}
        {onCopy && <MenuItem onClick={onCopy}>Copy</MenuItem>}
        <MenuItem onClick={onPaste} disabled={!hasClipboard}>Paste</MenuItem>
        {onDownload && (
          <>
            <Divider />
            <MenuItem onClick={onDownload}>Download</MenuItem>
          </>
        )}
        {isImage && onEdit && <MenuItem onClick={onEdit}>Edit</MenuItem>}
        {isCode && onEdit && <MenuItem onClick={onEdit}>Edit</MenuItem>}
        {isPdf && onEdit && <MenuItem onClick={onEdit}>View</MenuItem>}
        {isCode && onOpenInCodeEditor && <MenuItem onClick={onOpenInCodeEditor}>Open in Code Editor</MenuItem>}
        {onRename && <MenuItem onClick={onRename}>Rename</MenuItem>}
        {onProperties && <MenuItem onClick={onProperties}>Properties</MenuItem>}
        {onIndex && (
          <>
            <Divider />
            <MenuItem onClick={onIndex}>Index</MenuItem>
          </>
        )}
        {onDelete && (
          <>
            <Divider />
            <MenuItem onClick={onDelete}>Delete</MenuItem>
          </>
        )}
      </Menu>
    );
  }

  return null;
};

export default DocumentsContextMenu;
