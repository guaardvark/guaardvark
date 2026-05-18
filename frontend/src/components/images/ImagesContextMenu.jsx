// frontend/src/components/images/ImagesContextMenu.jsx
// Context menu for ImagesPage - adapted from DocumentsContextMenu
// Supports: desktop, folder, folder-window, image context types

import React from 'react';
import { Menu, MenuItem, ListItemIcon, ListItemText, Divider, Box, Tooltip } from '@mui/material';
import {
  CreateNewFolder as NewFolderIcon,
  ContentCut as CutIcon,
  ContentCopy as CopyIcon,
  ContentPaste as PasteIcon,
  Delete as DeleteIcon,
  DriveFileRenameOutline as RenameIcon,
  Download as DownloadIcon,
  SelectAll as SelectAllIcon,
  Sort as SortIcon,
  ViewModule as ArrangeIcon,
  Fullscreen as ViewIcon,
  Edit as EditIcon,
  Check as CheckIcon,
} from '@mui/icons-material';

const FOLDER_COLORS = [
  '#90CAF9', '#A5D6A7', '#FFCC80', '#EF9A9A',
  '#CE93D8', '#80DEEA', '#FFAB91', '#B0BEC5',
];

const ImagesContextMenu = ({
  anchorPosition,
  onClose,
  onNewFolder,
  onCut,
  onCopy,
  onPaste,
  onDelete,
  onRename,
  onDownload,
  onViewFullSize,
  onEdit,
  onColorChange,
  onSelectAll,
  onSortBy,
  onArrangeIcons,
  onArrangeWindows,
  hasClipboard = false,
  _hasSelection = false,
  contextType = 'desktop',
  _selectedItem = null,
  folderColor = null,
}) => {
  if (!anchorPosition) return null;

  const handleSortBy = (field) => {
    onSortBy?.(field);
    onClose();
  };

  return (
    <Menu
      open={Boolean(anchorPosition)}
      onClose={onClose}
      anchorReference="anchorPosition"
      anchorPosition={anchorPosition}
      slotProps={{ paper: { sx: { minWidth: 200, maxWidth: 280 } } }}
    >
      {/* Desktop context */}
      {contextType === 'desktop' && [
        <MenuItem key="new-folder" onClick={() => { onNewFolder?.(); onClose(); }}>
          <ListItemIcon><NewFolderIcon fontSize="small" /></ListItemIcon>
          <ListItemText>New Folder</ListItemText>
        </MenuItem>,
        <MenuItem key="select-all" onClick={() => { onSelectAll?.(); onClose(); }}>
          <ListItemIcon><SelectAllIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Select All</ListItemText>
        </MenuItem>,
        <Divider key="d1" />,
        <MenuItem key="sort-name" onClick={() => handleSortBy('name')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Sort by Name</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-date" onClick={() => handleSortBy('date')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Sort by Date</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-size" onClick={() => handleSortBy('size')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Sort by Size</ListItemText>
        </MenuItem>,
        <Divider key="d2" />,
        <MenuItem key="arrange-icons" onClick={() => { onArrangeIcons?.(); onClose(); }}>
          <ListItemIcon><ArrangeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Arrange Icons</ListItemText>
        </MenuItem>,
        <MenuItem key="arrange-windows" onClick={() => { onArrangeWindows?.(); onClose(); }}>
          <ListItemIcon><ArrangeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Arrange Windows</ListItemText>
        </MenuItem>,
        hasClipboard ? [
          <Divider key="paste-div" />,
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Paste</ListItemText>
          </MenuItem>,
        ] : null,
      ]}

      {/* Folder context (icon or window) */}
      {(contextType === 'folder' || contextType === 'folder-window') && [
        <MenuItem key="cut" onClick={() => { onCut?.(); onClose(); }}>
          <ListItemIcon><CutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Cut</ListItemText>
        </MenuItem>,
        <MenuItem key="copy" onClick={() => { onCopy?.(); onClose(); }}>
          <ListItemIcon><CopyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Copy</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Paste</ListItemText>
          </MenuItem>
        ),
        <Divider key="d1" />,
        <MenuItem key="color" disableRipple disableGutters sx={{ px: 1.5, py: 0.6 }}>
          <ListItemText sx={{ mr: 1 }}>Color</ListItemText>
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
            {FOLDER_COLORS.map((color) => (
              <Tooltip key={color} title={color}>
                <Box
                  onClick={(e) => { e.stopPropagation(); onColorChange?.(color); onClose(); }}
                  sx={{
                    width: 20, height: 20, borderRadius: '50%',
                    backgroundColor: color, cursor: 'pointer',
                    border: folderColor === color ? '2px solid white' : '1px solid rgba(255,255,255,0.3)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    '&:hover': { transform: 'scale(1.2)' },
                  }}
                >
                  {folderColor === color && <CheckIcon sx={{ fontSize: 12, color: 'white' }} />}
                </Box>
              </Tooltip>
            ))}
          </Box>
        </MenuItem>,
        <Divider key="d2" />,
        <MenuItem key="rename" onClick={() => { onRename?.(); onClose(); }}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>,
        <MenuItem key="delete" onClick={() => { onDelete?.(); onClose(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Delete</ListItemText>
        </MenuItem>,
      ]}

      {/* Image context */}
      {contextType === 'image' && [
        <MenuItem key="view" onClick={() => { onViewFullSize?.(); onClose(); }}>
          <ListItemIcon><ViewIcon fontSize="small" /></ListItemIcon>
          <ListItemText>View Full Size</ListItemText>
        </MenuItem>,
        <MenuItem key="edit" onClick={() => { onEdit?.(); onClose(); }}>
          <ListItemIcon><EditIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Edit</ListItemText>
        </MenuItem>,
        <Divider key="d0" />,
        <MenuItem key="cut" onClick={() => { onCut?.(); onClose(); }}>
          <ListItemIcon><CutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Cut</ListItemText>
        </MenuItem>,
        <MenuItem key="copy" onClick={() => { onCopy?.(); onClose(); }}>
          <ListItemIcon><CopyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Copy</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>Paste</ListItemText>
          </MenuItem>
        ),
        <Divider key="d1" />,
        <MenuItem key="download" onClick={() => { onDownload?.(); onClose(); }}>
          <ListItemIcon><DownloadIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Download</ListItemText>
        </MenuItem>,
        <MenuItem key="rename" onClick={() => { onRename?.(); onClose(); }}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>,
        <MenuItem key="delete" onClick={() => { onDelete?.(); onClose(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>Delete</ListItemText>
        </MenuItem>,
      ]}
    </Menu>
  );
};

export default ImagesContextMenu;
