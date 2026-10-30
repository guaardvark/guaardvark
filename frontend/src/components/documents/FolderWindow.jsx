// Complete folder window component
// Combines FolderWindowWrapper (chrome) and FolderContents (file list)

import React, { useState, useEffect, useCallback } from 'react';
import PropTypes from 'prop-types';
import { ToggleButtonGroup, ToggleButton, Tooltip, Box } from '@mui/material';
import { ViewList as ViewListIcon, ViewModule as ViewModuleIcon, ViewComfy as ViewComfyIcon } from '@mui/icons-material';
import FolderWindowWrapper from './FolderWindowWrapper';
import FolderContents from './FolderContents';
import CodeRepoDashboard from './CodeRepoDashboard';
import BreadcrumbNav from '../filesystem/BreadcrumbNav';
import { persistListingView, readStoredListingView } from './folderViewPolicy';
import { useUiTranslation } from '../../i18n';

const FolderWindow = React.forwardRef(({
  _id,
  folder,
  isMinimized,
  onToggleMinimize,
  onClose,
  selectedItems,
  onSelectionChange,
  onItemsMove,
  onContextMenu,
  onTitleContextMenu,
  onDragStart,
  onDrop,
  onFolderOpen,
  onFileOpen,
  // A plain click on a file (after the double-click window), e.g. the Media page's details.
  onFileClick,
  onFocusContext,
  refreshKey = 0,
  folderColors = {},
  // The Media page's windows list only media; Thumbnails draws large cards.
  mediaOnly = false,
  largeCards = false,
  ...gridLayoutProps
}, ref) => {
  const { t } = useUiTranslation();
  // Track current path within this folder window for subfolder navigation
  const [currentPath, setCurrentPath] = useState(folder.path);
  // Whether this folder's contents contain visual media (images/videos)
  const [hasMedia, setHasMedia] = useState(false);
  // Per-window view mode — seeded from the last listing preference. Gallery is
  // opt-in for this window only and is never written back as a default.
  const [viewMode, setViewMode] = useState(() => readStoredListingView());

  const fallBackFromGallery = useCallback(() => {
    setHasMedia(false);
    setViewMode((prev) => (prev === 'media' ? readStoredListingView() : prev));
  }, []);

  // Reset path and leave gallery when the opened folder identity changes
  useEffect(() => {
    setCurrentPath(folder.path);
    fallBackFromGallery();
  }, [folder.id, folder.path, fallBackFromGallery]);

  // Detection only drives the Media toggle. Content never hijacks the view.
  const handleMediaDetected = useCallback((detected) => {
    setHasMedia(detected);
    if (!detected) {
      setViewMode((prev) => (prev === 'media' ? readStoredListingView() : prev));
    }
  }, []);

  // User toggle — persist list/grid as the default for future windows.
  // Gallery stays local to this window.
  const handleViewModeToggle = (_event, newViewMode) => {
    if (newViewMode === null) return;
    if (newViewMode === 'media') {
      setViewMode('media');
      return;
    }
    setViewMode(persistListingView(newViewMode));
  };

  // Handle navigation within folder window (for subfolder double-clicks and breadcrumb clicks)
  const handleNavigateToPath = (newPath) => {
    if (newPath === currentPath) return;
    setCurrentPath(newPath);
    fallBackFromGallery();
  };

  const titleBarActions = (
    <>
      <CodeRepoDashboard folder={folder} />
      <ToggleButtonGroup
        value={viewMode}
        exclusive
        onChange={handleViewModeToggle}
        size="small"
        sx={{ mr: 0.5 }}
      >
        <ToggleButton value="list" size="small" sx={{ minWidth: 'auto', px: 0.5 }}>
          <Tooltip title={t("documents.folderWindow.listView")}>
            <ViewListIcon sx={{ fontSize: '14px' }} />
          </Tooltip>
        </ToggleButton>
        <ToggleButton value="grid" size="small" sx={{ minWidth: 'auto', px: 0.5 }}>
          <Tooltip title={t("documents.folderWindow.gridView")}>
            <ViewModuleIcon sx={{ fontSize: '14px' }} />
          </Tooltip>
        </ToggleButton>
        {hasMedia && (
          <ToggleButton value="media" size="small" sx={{ minWidth: 'auto', px: 0.5 }}>
            <Tooltip title={t("documents.folderWindow.mediaView")}>
              <ViewComfyIcon sx={{ fontSize: '14px' }} />
            </Tooltip>
          </ToggleButton>
        )}
      </ToggleButtonGroup>
    </>
  );

  return (
    <FolderWindowWrapper
      ref={ref}
      title={currentPath.split('/').filter(Boolean).pop() || folder.name}
      isMinimized={isMinimized}
      onToggleMinimize={onToggleMinimize}
      onClose={onClose}
      onTitleContextMenu={onTitleContextMenu}
      folderColor={folderColors[folder.id]}
      onDrop={onDrop}
      onDragOver={(e) => e.preventDefault()}
      titleBarActions={titleBarActions}
      isRepository={folder.is_repository}
      {...gridLayoutProps}
    >
      {/* Breadcrumb navigation for folder hierarchy */}
      {!isMinimized && (
        <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
          <BreadcrumbNav
            currentPath={currentPath}
            rootPath={folder.path}
            onNavigate={handleNavigateToPath}
          />
        </Box>
      )}

      <Box
        data-folder-path={currentPath}
        onMouseDown={() => onFocusContext && onFocusContext({ type: 'folder', path: currentPath, folderId: folder.id })}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onDrop={onDrop}
        sx={{ height: '100%', width: '100%', overflow: 'auto' }}
      >
        <FolderContents
          folder={folder}
          currentPath={currentPath}
          onNavigateToPath={handleNavigateToPath}
          viewMode={viewMode}
          selectedItems={selectedItems}
          onSelectionChange={onSelectionChange}
          onItemsMove={onItemsMove}
          onContextMenu={onContextMenu}
          onDragStart={onDragStart}
          onDrop={onDrop}
          onFolderOpen={onFolderOpen}
          onFileOpen={onFileOpen}
          onFileClick={onFileClick}
          onFocusContext={onFocusContext}
          refreshKey={refreshKey}
          folderColors={folderColors}
          onMediaDetected={handleMediaDetected}
          mediaOnly={mediaOnly}
          largeCards={largeCards}
        />
      </Box>
    </FolderWindowWrapper>
  );
});

FolderWindow.displayName = 'FolderWindow';

FolderWindow.propTypes = {
  id: PropTypes.string.isRequired,
  folder: PropTypes.shape({
    name: PropTypes.string.isRequired,
    path: PropTypes.string.isRequired,
    is_repository: PropTypes.bool,
  }).isRequired,
  isMinimized: PropTypes.bool,
  onToggleMinimize: PropTypes.func,
  onClose: PropTypes.func.isRequired,
  selectedItems: PropTypes.instanceOf(Set),
  onSelectionChange: PropTypes.func,
  onItemsMove: PropTypes.func,
  onContextMenu: PropTypes.func,
  onTitleContextMenu: PropTypes.func,
  onDragStart: PropTypes.func,
  onDrop: PropTypes.func,
  onFolderOpen: PropTypes.func,
  onFocusContext: PropTypes.func,
  refreshKey: PropTypes.number,
};

export default FolderWindow;
