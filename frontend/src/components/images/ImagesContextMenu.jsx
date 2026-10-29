// frontend/src/components/images/ImagesContextMenu.jsx
// Context menu for ImagesPage - adapted from DocumentsContextMenu
// Supports: desktop, folder, folder-window, folder-background (empty space in a window), image.
// The image menu leads with Details, Regenerate and Adjust & Retry when the page passes them.

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
  IosShare as ShareIcon,
  InfoOutlined as DetailsIcon,
  Autorenew as RegenerateIcon,
  Tune as AdjustIcon,
} from '@mui/icons-material';
import { translate, useUiTranslation } from '../../i18n';

// The stored value is the hex; the name is what the swatch tooltip says.
const folderColors = () => [
  { hex: '#90CAF9', name: translate('images.contextMenu.colors.blue') },
  { hex: '#A5D6A7', name: translate('images.contextMenu.colors.green') },
  { hex: '#FFCC80', name: translate('images.contextMenu.colors.orange') },
  { hex: '#EF9A9A', name: translate('images.contextMenu.colors.red') },
  { hex: '#CE93D8', name: translate('images.contextMenu.colors.purple') },
  { hex: '#80DEEA', name: translate('images.contextMenu.colors.cyan') },
  { hex: '#FFAB91', name: translate('images.contextMenu.colors.coral') },
  { hex: '#B0BEC5', name: translate('images.contextMenu.colors.grey') },
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
  onPublish,
  // How the image was made, and making it again (MediaDetailsModal).
  onDetails,
  onRegenerate,
  onAdjustRetry,
  onColorChange,
  onSelectAll,
  onSortBy,
  onArrangeIcons,
  onArrangeWindows,
  hasClipboard = false,
  hasOpenWindows = false,
  _hasSelection = false,
  contextType = 'desktop',
  _selectedItem = null,
  folderColor = null,
}) => {
  const { t } = useUiTranslation();
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
          <ListItemText>{t('images.contextMenu.newFolder')}</ListItemText>
        </MenuItem>,
        <MenuItem key="select-all" onClick={() => { onSelectAll?.(); onClose(); }}>
          <ListItemIcon><SelectAllIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.selectAll')}</ListItemText>
        </MenuItem>,
        <Divider key="d1" />,
        <MenuItem key="sort-name" onClick={() => handleSortBy('name')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.sortByName')}</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-date" onClick={() => handleSortBy('date')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.sortByDate')}</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-size" onClick={() => handleSortBy('size')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.sortBySize')}</ListItemText>
        </MenuItem>,
        <Divider key="d2" />,
        <MenuItem key="arrange-icons" onClick={() => { onArrangeIcons?.(); onClose(); }}>
          <ListItemIcon><ArrangeIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.shared.arrangeIcons')}</ListItemText>
        </MenuItem>,
        hasOpenWindows && (
          <MenuItem key="arrange-windows" onClick={() => { onArrangeWindows?.(); onClose(); }}>
            <ListItemIcon><ArrangeIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.shared.arrangeWindows')}</ListItemText>
          </MenuItem>
        ),
        hasClipboard ? [
          <Divider key="paste-div" />,
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.contextMenu.paste')}</ListItemText>
          </MenuItem>,
        ] : null,
      ]}

      {/* Empty space inside a folder window: acts on the folder it shows */}
      {contextType === 'folder-background' && [
        <MenuItem key="new-folder" onClick={() => { onNewFolder?.(); onClose(); }}>
          <ListItemIcon><NewFolderIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.newFolder')}</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.contextMenu.pasteIntoFolder')}</ListItemText>
          </MenuItem>
        ),
        <MenuItem key="select-all" onClick={() => { onSelectAll?.(); onClose(); }}>
          <ListItemIcon><SelectAllIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.selectAll')}</ListItemText>
        </MenuItem>,
        <Divider key="d1" />,
        <MenuItem key="sort-name" onClick={() => handleSortBy('name')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.sortByName')}</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-date" onClick={() => handleSortBy('date')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.sortByDate')}</ListItemText>
        </MenuItem>,
        <MenuItem key="sort-size" onClick={() => handleSortBy('size')}>
          <ListItemIcon><SortIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.sortBySize')}</ListItemText>
        </MenuItem>,
      ]}

      {/* Folder context (icon or window) */}
      {(contextType === 'folder' || contextType === 'folder-window') && [
        <MenuItem key="cut" onClick={() => { onCut?.(); onClose(); }}>
          <ListItemIcon><CutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.cut')}</ListItemText>
        </MenuItem>,
        <MenuItem key="copy" onClick={() => { onCopy?.(); onClose(); }}>
          <ListItemIcon><CopyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.copy')}</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.contextMenu.paste')}</ListItemText>
          </MenuItem>
        ),
        <Divider key="d1" />,
        <MenuItem key="color" disableRipple disableGutters sx={{ px: 1.5, py: 0.6 }}>
          <ListItemText sx={{ mr: 1 }}>{t('images.contextMenu.color')}</ListItemText>
          <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap' }}>
            {folderColors().map(({ hex, name }) => (
              <Tooltip key={hex} title={name}>
                <Box
                  onClick={(e) => { e.stopPropagation(); onColorChange?.(hex); onClose(); }}
                  sx={{
                    width: 20, height: 20, borderRadius: '50%',
                    backgroundColor: hex, cursor: 'pointer',
                    border: folderColor === hex ? '2px solid white' : '1px solid rgba(255,255,255,0.3)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    '&:hover': { transform: 'scale(1.2)' },
                  }}
                >
                  {folderColor === hex && <CheckIcon sx={{ fontSize: 12, color: 'white' }} />}
                </Box>
              </Tooltip>
            ))}
          </Box>
        </MenuItem>,
        <Divider key="d2" />,
        <MenuItem key="rename" onClick={() => { onRename?.(); onClose(); }}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.shared.rename')}</ListItemText>
        </MenuItem>,
        <MenuItem key="delete" onClick={() => { onDelete?.(); onClose(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>{t('images.shared.delete')}</ListItemText>
        </MenuItem>,
      ]}

      {/* Image context */}
      {contextType === 'image' && [
        onDetails && (
          <MenuItem key="details" onClick={() => { onDetails(); onClose(); }}>
            <ListItemIcon><DetailsIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.contextMenu.details')}</ListItemText>
          </MenuItem>
        ),
        onRegenerate && (
          <MenuItem key="regenerate" onClick={() => { onRegenerate(); onClose(); }}>
            <ListItemIcon><RegenerateIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.details.regenerate')}</ListItemText>
          </MenuItem>
        ),
        onAdjustRetry && (
          <MenuItem key="adjust" onClick={() => { onAdjustRetry(); onClose(); }}>
            <ListItemIcon><AdjustIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.batchCard.adjustRetry')}</ListItemText>
          </MenuItem>
        ),
        onDetails && <Divider key="d-details" />,
        <MenuItem key="view" onClick={() => { onViewFullSize?.(); onClose(); }}>
          <ListItemIcon><ViewIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.viewFullSize')}</ListItemText>
        </MenuItem>,
        <MenuItem key="edit" onClick={() => { onEdit?.(); onClose(); }}>
          <ListItemIcon><EditIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.edit')}</ListItemText>
        </MenuItem>,
        <MenuItem key="publish" onClick={() => { onPublish?.(); onClose(); }}>
          <ListItemIcon><ShareIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.publish')}</ListItemText>
        </MenuItem>,
        <Divider key="d0" />,
        <MenuItem key="cut" onClick={() => { onCut?.(); onClose(); }}>
          <ListItemIcon><CutIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.cut')}</ListItemText>
        </MenuItem>,
        <MenuItem key="copy" onClick={() => { onCopy?.(); onClose(); }}>
          <ListItemIcon><CopyIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.contextMenu.copy')}</ListItemText>
        </MenuItem>,
        hasClipboard && (
          <MenuItem key="paste" onClick={() => { onPaste?.(); onClose(); }}>
            <ListItemIcon><PasteIcon fontSize="small" /></ListItemIcon>
            <ListItemText>{t('images.contextMenu.paste')}</ListItemText>
          </MenuItem>
        ),
        <Divider key="d1" />,
        <MenuItem key="download" onClick={() => { onDownload?.(); onClose(); }}>
          <ListItemIcon><DownloadIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.shared.download')}</ListItemText>
        </MenuItem>,
        <MenuItem key="rename" onClick={() => { onRename?.(); onClose(); }}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>{t('images.shared.rename')}</ListItemText>
        </MenuItem>,
        <MenuItem key="delete" onClick={() => { onDelete?.(); onClose(); }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText sx={{ color: 'error.main' }}>{t('images.shared.delete')}</ListItemText>
        </MenuItem>,
      ]}
    </Menu>
  );
};

export default ImagesContextMenu;
