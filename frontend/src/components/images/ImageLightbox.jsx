// frontend/src/components/images/ImageLightbox.jsx
// Full-screen image lightbox with keyboard navigation (Escape, ArrowLeft, ArrowRight)

import React, { useEffect, useCallback } from 'react';
import { Box, IconButton, Typography } from '@mui/material';
import {
  Close as CloseIcon,
  ArrowBack as PrevIcon,
  ArrowForward as NextIcon,
  Download as DownloadIcon,
} from '@mui/icons-material';

const ImageLightbox = ({
  imageUrl,
  imageName = '',
  onClose,
  onPrev,
  onNext,
  onDownload,
  hasPrev = false,
  hasNext = false,
}) => {
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') onClose?.();
    else if (e.key === 'ArrowLeft' && hasPrev) onPrev?.();
    else if (e.key === 'ArrowRight' && hasNext) onNext?.();
  }, [onClose, onPrev, onNext, hasPrev, hasNext]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  if (!imageUrl) return null;

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
        <Typography variant="body2" sx={{ color: 'white', ml: 1, opacity: 0.8 }} noWrap>
          {imageName}
        </Typography>
        <Box>
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

      {/* Image */}
      <Box
        component="img"
        src={imageUrl}
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
