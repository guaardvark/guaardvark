// Enhanced context menu for Documents page
// Provides different menus based on context: Desktop, Folder, or File

import React from 'react';
import { Menu, MenuItem, Divider, Box, ListItemIcon } from '@mui/material';
import { Check as CheckIcon } from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import { useUiTranslation, translate } from '../../i18n';

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
  onToggleMinimize,
  onCloseWindow,
  onOpenInCodeEditor,
  onReviewWithAgent,
  onBulkImportHere,
  // The page's own file actions, first in a file's menu: [{label, onClick}].
  extraActions = null,
  // Desktop only: Sort by (name | date | size, with the current one ticked) and Arrange Icons.
  onSortBy,
  sortField = null,
  onArrangeIcons,
  hasClipboard = false,
  _hasSelection = false,
  contextType = 'desktop', // 'desktop', 'folder', 'file'
  _selectedItem = null,
  folderColor = null,
  isImage = false,
  isCode = false,
  isPdf = false,
  openLabel = translate('documents.contextMenu.openInWindow'),
  minimizeLabel = translate('documents.contextMenu.minimize'),
}) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const open = Boolean(anchorPosition);

  // Run the action with no arguments, then close. The click event never
  // reaches a handler, and the right-clicked item does not outlive the menu.
  const select = (handler) => () => {
    if (handler) handler();
    onClose();
  };

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
        <MenuItem onClick={select(onNewFolder)}>{t("documents.contextMenu.newFolder")}</MenuItem>
        <MenuItem onClick={select(onUpload)}>{t("documents.contextMenu.importFiles")}</MenuItem>
        {hasClipboard && (
          <>
            <Divider />
            <MenuItem onClick={select(onPaste)}>{t("documents.contextMenu.paste")}</MenuItem>
          </>
        )}
        {onSortBy && <Divider />}
        {onSortBy && ['name', 'date', 'size'].map((field) => (
          <MenuItem key={field} onClick={() => { onSortBy(field); onClose(); }}>
            <ListItemIcon sx={{ minWidth: 28 }}>
              {sortField === field && <CheckIcon fontSize="small" />}
            </ListItemIcon>
            {t(`documents.contextMenu.sortBy.${field}`)}
          </MenuItem>
        ))}
        {onArrangeIcons && <MenuItem onClick={select(onArrangeIcons)}>{t("documents.contextMenu.arrangeIcons")}</MenuItem>}
      </Menu>
    );
  }

  // Folder context menu: a folder icon, or the title bar of the folder's open
  // window, which adds the window's own Minimize and Close.
  if (contextType === 'folder') {
    const hasWindowActions = Boolean(onOpenWindow || onToggleMinimize || onCloseWindow);
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
        {onOpenWindow && <MenuItem onClick={select(onOpenWindow)}>{openLabel}</MenuItem>}
        {onToggleMinimize && <MenuItem onClick={select(onToggleMinimize)}>{minimizeLabel}</MenuItem>}
        {onCloseWindow && <MenuItem onClick={select(onCloseWindow)}>{t("documents.contextMenu.closeWindow")}</MenuItem>}
        {onReviewWithAgent && <MenuItem onClick={select(onReviewWithAgent)}>{t("documents.contextMenu.reviewWithAgent")}</MenuItem>}
        {hasWindowActions && <Divider />}
        {onCut && <MenuItem onClick={select(onCut)}>{t("documents.contextMenu.cut")}</MenuItem>}
        {onCopy && <MenuItem onClick={select(onCopy)}>{t("documents.contextMenu.copy")}</MenuItem>}
        <MenuItem onClick={select(onPaste)} disabled={!hasClipboard}>{t("documents.contextMenu.paste")}</MenuItem>
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
        {onRename && <MenuItem onClick={select(onRename)}>{t("documents.contextMenu.rename")}</MenuItem>}
        {onProperties && <MenuItem onClick={select(onProperties)}>{t("documents.contextMenu.properties")}</MenuItem>}
        {onBulkImportHere && <MenuItem onClick={select(onBulkImportHere)}>{t("documents.contextMenu.bulkImportHere")}</MenuItem>}
        {onIndex && (
          <>
            <Divider />
            <MenuItem onClick={select(onIndex)}>{t("documents.contextMenu.indexContents")}</MenuItem>
          </>
        )}
        {onDelete && (
          <>
            <Divider />
            <MenuItem onClick={select(onDelete)}>{t("documents.contextMenu.delete")}</MenuItem>
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
        {extraActions?.length > 0 && extraActions.filter(Boolean).map((action) => (
          <MenuItem key={action.label} onClick={select(action.onClick)}>{action.label}</MenuItem>
        ))}
        {extraActions?.length > 0 && <Divider />}
        {onCut && <MenuItem onClick={select(onCut)}>{t("documents.contextMenu.cut")}</MenuItem>}
        {onCopy && <MenuItem onClick={select(onCopy)}>{t("documents.contextMenu.copy")}</MenuItem>}
        <MenuItem onClick={select(onPaste)} disabled={!hasClipboard}>{t("documents.contextMenu.paste")}</MenuItem>
        {onDownload && (
          <>
            <Divider />
            <MenuItem onClick={select(onDownload)}>{t("documents.contextMenu.download")}</MenuItem>
          </>
        )}
        {isImage && onEdit && <MenuItem onClick={select(onEdit)}>{t("documents.contextMenu.edit")}</MenuItem>}
        {isCode && onEdit && <MenuItem onClick={select(onEdit)}>{t("documents.contextMenu.edit")}</MenuItem>}
        {isPdf && onEdit && <MenuItem onClick={select(onEdit)}>{t("documents.contextMenu.view")}</MenuItem>}
        {isCode && onOpenInCodeEditor && <MenuItem onClick={select(onOpenInCodeEditor)}>{t("documents.contextMenu.openInCodeEditor")}</MenuItem>}
        {onReviewWithAgent && <MenuItem onClick={select(onReviewWithAgent)}>{t("documents.contextMenu.reviewWithAgent")}</MenuItem>}
        {onRename && <MenuItem onClick={select(onRename)}>{t("documents.contextMenu.rename")}</MenuItem>}
        {onProperties && <MenuItem onClick={select(onProperties)}>{t("documents.contextMenu.properties")}</MenuItem>}
        {onIndex && (
          <>
            <Divider />
            <MenuItem onClick={select(onIndex)}>{t("documents.contextMenu.index")}</MenuItem>
          </>
        )}
        {onDelete && (
          <>
            <Divider />
            <MenuItem onClick={select(onDelete)}>{t("documents.contextMenu.delete")}</MenuItem>
          </>
        )}
      </Menu>
    );
  }

  return null;
};

export default DocumentsContextMenu;
