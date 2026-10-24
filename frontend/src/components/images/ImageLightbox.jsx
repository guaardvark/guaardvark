// frontend/src/components/images/ImageLightbox.jsx
// Full-screen image lightbox with keyboard navigation and edit mode.

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Box, IconButton, Typography, Tooltip } from '@mui/material';
import {
  Close as CloseIcon,
  ArrowBack as PrevIcon,
  ArrowForward as NextIcon,
  Download as DownloadIcon,
  Edit as EditIcon,
  IosShare as ShareIcon,
  Casino as SeedIcon,
} from '@mui/icons-material';
import ImageEditor from './ImageEditor';
import { guardedMediaSrc } from '../../utils/assetGuard';
import { useUiTranslation } from '../../i18n';

const ImageLightbox = ({
  imageUrl,
  imageName = '',
  // Small line under the name, e.g. the prompt a rewrite sent to the model.
  caption = '',
  documentId,
  onClose,
  onPrev,
  onNext,
  onDownload,
  onPublish,
  onImageEdited,
  // The seed this image rendered with; with onUseSeed the toolbar offers to reuse it.
  seed = null,
  onUseSeed,
  hasPrev = false,
  hasNext = false,
  initialEditMode = false,
}) => {
  const { t } = useUiTranslation();
  const [editMode, setEditMode] = useState(initialEditMode);

  const handleKeyDown = useCallback((e) => {
    if (editMode) return; // Editor handles its own keys
    if (e.key === 'Escape') onClose?.();
    else if (e.key === 'ArrowLeft' && hasPrev) onPrev?.();
    else if (e.key === 'ArrowRight' && hasNext) onNext?.();
    else if (e.key === 'e') setEditMode(true);
    else if (e.key === 's') onPublish?.();
  }, [onClose, onPrev, onNext, onPublish, hasPrev, hasNext, editMode]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  // Reset edit mode on image change (skip initial mount to preserve initialEditMode)
  const prevImageUrlRef = useRef(imageUrl);
  useEffect(() => {
    if (prevImageUrlRef.current !== imageUrl) {
      prevImageUrlRef.current = imageUrl;
      setEditMode(false);
    }
  }, [imageUrl]);

  const handleSaved = useCallback((savedData) => {
    setEditMode(false);
    onImageEdited?.(savedData);
  }, [onImageEdited]);

  if (!imageUrl) return null;

  // Editor overlay
  if (editMode) {
    return (
      <ImageEditor
        imageUrl={imageUrl}
        imageName={imageName}
        documentId={documentId}
        onClose={() => setEditMode(false)}
        onSaved={handleSaved}
      />
    );
  }

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
        cursor: 'pointer',
      }}
    >
      {/* Top bar */}
      <Box
        onClick={(e) => e.stopPropagation()}
        sx={{
          position: 'absolute', top: 0, left: 0, right: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          p: 1, background: 'linear-gradient(rgba(0,0,0,0.7), transparent)',
          zIndex: 1,
        }}
      >
        <Box sx={{ minWidth: 0, flex: 1, mr: 1 }}>
          <Typography variant="body2" sx={{ color: 'white', ml: 1, opacity: 0.8 }} noWrap>
            {imageName}
          </Typography>
          {caption && (
            <Typography
              variant="caption"
              title={caption}
              sx={{
                color: 'white', ml: 1, opacity: 0.6, display: '-webkit-box',
                WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
              }}
            >
              {caption}
            </Typography>
          )}
        </Box>
        <Box sx={{ flexShrink: 0 }}>
          {documentId && (
            <Tooltip title={t('images.lightbox.edit')}>
              <IconButton onClick={() => setEditMode(true)} sx={{ color: 'white' }} size="small">
                <EditIcon />
              </IconButton>
            </Tooltip>
          )}
          {onPublish && (
            <Tooltip title={t('images.lightbox.publish')}>
              <IconButton onClick={onPublish} sx={{ color: 'white' }} size="small">
                <ShareIcon />
              </IconButton>
            </Tooltip>
          )}
          {seed != null && onUseSeed && (
            <Tooltip title={t('images.lightbox.useSeedTooltip', { seed })}>
              <IconButton onClick={() => onUseSeed(seed)} sx={{ color: 'white' }} size="small" aria-label={t('images.lightbox.useSeed', { seed })}>
                <SeedIcon />
              </IconButton>
            </Tooltip>
          )}
          {onDownload && (
            <IconButton onClick={onDownload} sx={{ color: 'white' }} size="small">
              <DownloadIcon />
            </IconButton>
          )}
          <IconButton onClick={onClose} sx={{ color: 'white' }} size="small">
            <CloseIcon />
          </IconButton>
        </Box>
      </Box>

      {/* Previous arrow */}
      {hasPrev && (
        <IconButton
          onClick={(e) => { e.stopPropagation(); onPrev?.(); }}
          sx={{
            position: 'absolute', left: 16, color: 'white',
            backgroundColor: 'rgba(0,0,0,0.5)',
            '&:hover': { backgroundColor: 'rgba(0,0,0,0.7)' },
            zIndex: 1,
          }}
        >
          <PrevIcon fontSize="large" />
        </IconButton>
      )}

      {/* Next arrow */}
      {hasNext && (
        <IconButton
          onClick={(e) => { e.stopPropagation(); onNext?.(); }}
          sx={{
            position: 'absolute', right: 16, color: 'white',
            backgroundColor: 'rgba(0,0,0,0.5)',
            '&:hover': { backgroundColor: 'rgba(0,0,0,0.7)' },
            zIndex: 1,
          }}
        >
          <NextIcon fontSize="large" />
        </IconButton>
      )}

      {/* Image — offline-first: a remote (non-local) URL is swapped for a blocked
          placeholder so opening the lightbox never issues an outbound request. */}
      <Box
        component="img"
        src={guardedMediaSrc(imageUrl)}
        alt={imageName}
        onClick={(e) => e.stopPropagation()}
        sx={{
          maxWidth: '90vw',
          maxHeight: '90vh',
          objectFit: 'contain',
          cursor: 'default',
          borderRadius: 1,
        }}
      />
    </Box>
  );
};

export default ImageLightbox;
