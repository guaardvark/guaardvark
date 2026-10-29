// Fullscreen preview for image/video files opened from list, grid, or desktop.

import React, { useEffect, useRef } from 'react';
import { Box, IconButton } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import MediaView from './MediaView';
import { isVisualMediaFile } from './folderViewPolicy';
import { useUiTranslation } from '../../i18n';

const MediaPreviewOverlay = ({ file, siblings = [], onClose, onFileOpen, onContextMenu }) => {
  const { t } = useUiTranslation();
  const files = (siblings.length ? siblings : (file ? [file] : []))
    .filter((f) => isVisualMediaFile(f.filename || f.name));

  // One listener for the overlay's life, reading the latest onClose. A
  // listener re-added on every render can be swapped out while Escape is
  // still being dispatched (the page's own handler re-renders first), and the
  // browser skips a listener removed mid-dispatch, so Escape did nothing.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onCloseRef.current?.();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  if (!file) return null;

  return (
    <Box
      onClick={onClose}
      sx={{
        position: 'fixed',
        top: 0, left: 0, right: 0, bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.92)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        p: 2,
      }}
    >
      <IconButton
        onClick={onClose}
        sx={{ position: 'absolute', top: 8, right: 8, color: 'white', zIndex: 1 }}
        size="small"
        aria-label={t('documents.mediaView.closePreview')}
      >
        <CloseIcon />
      </IconButton>
      <Box
        onClick={(e) => e.stopPropagation()}
        sx={{ width: 'min(1100px, 96vw)', height: 'min(820px, 92vh)' }}
      >
        <MediaView
          items={{ folders: [], files }}
          initialFileId={file.id}
          onFileOpen={onFileOpen}
          onContextMenu={onContextMenu}
        />
      </Box>
    </Box>
  );
};

export default MediaPreviewOverlay;
