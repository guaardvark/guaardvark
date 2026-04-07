// Media View — large preview on top, thumbnail strip below
// Handles both images and videos in the same layout.
// Modeled after the batch video player in ImagesPage.

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Button,
  useTheme,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import DownloadIcon from '@mui/icons-material/Download';
import ImageIcon from '@mui/icons-material/Image';
import VideocamIcon from '@mui/icons-material/Videocam';
import { API_BASE, isImageFile, isVideoFile } from './fileUtils';

const MediaView = ({ items, folder, onContextMenu, onFileOpen }) => {
  const theme = useTheme();
  const [currentIndex, setCurrentIndex] = useState(0);

  // Collect all media files (images + videos), skip folders
  const mediaFiles = useMemo(() => {
    const files = items?.files || [];
    return files.filter(f => isImageFile(f.filename) || isVideoFile(f.filename));
  }, [items]);

  // Non-media files (shown in a compact list below thumbnails)
  const otherFiles = useMemo(() => {
    const files = items?.files || [];
    return files.filter(f => !isImageFile(f.filename) && !isVideoFile(f.filename));
  }, [items]);

  // Subfolders
  const subfolders = useMemo(() => items?.folders || [], [items]);

  // Clamp index when media list changes
  useEffect(() => {
    if (currentIndex >= mediaFiles.length && mediaFiles.length > 0) {
      setCurrentIndex(mediaFiles.length - 1);
    }
  }, [mediaFiles.length, currentIndex]);

  const currentFile = mediaFiles[currentIndex] || null;
  const isVideo = currentFile ? isVideoFile(currentFile.filename) : false;
  const isImage = currentFile ? isImageFile(currentFile.filename) : false;

  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < mediaFiles.length - 1;
  const navigateTo = useCallback((idx) => {
    if (idx >= 0 && idx < mediaFiles.length) setCurrentIndex(idx);
  }, [mediaFiles.length]);

  // Build URLs for the current file
  const fileUrl = currentFile
    ? `${API_BASE}/document/${currentFile.id}/download?v=${currentFile.updated_at || Date.now()}`
    : null;
  const thumbnailUrl = currentFile
    ? `${API_BASE}/thumbnail?path=${encodeURIComponent(currentFile.path)}`
    : null;

  if (mediaFiles.length === 0) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'text.secondary', gap: 1, py: 4 }}>
        <ImageIcon sx={{ fontSize: 48, opacity: 0.3 }} />
        <Typography variant="body2">No media files in this folder</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%', bgcolor: 'grey.900', borderRadius: 1, overflow: 'hidden' }}>
      {/* Large preview area */}
      <Box sx={{ position: 'relative', flex: 1, minHeight: 0, bgcolor: 'black', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        {isVideo && fileUrl && (
          <video
            key={fileUrl}
            src={fileUrl}
            controls
            autoPlay
            loop
            style={{ width: '100%', height: '100%', maxHeight: '100%', objectFit: 'contain', display: 'block' }}
          />
        )}
        {isImage && fileUrl && (
          <Box
            component="img"
            src={fileUrl}
            alt={currentFile.filename}
            sx={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            onError={(e) => { e.target.style.display = 'none'; }}
          />
        )}

        {/* Prev overlay */}
        {hasPrev && (
          <IconButton
            onClick={() => navigateTo(currentIndex - 1)}
            sx={{
              position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
              bgcolor: 'rgba(0,0,0,0.5)', color: 'white', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' },
            }}
          >
            <PlayArrowIcon sx={{ transform: 'rotate(180deg)' }} />
          </IconButton>
        )}
        {/* Next overlay */}
        {hasNext && (
          <IconButton
            onClick={() => navigateTo(currentIndex + 1)}
            sx={{
              position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
              bgcolor: 'rgba(0,0,0,0.5)', color: 'white', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' },
            }}
          >
            <PlayArrowIcon />
          </IconButton>
        )}
      </Box>

      {/* Thumbnail strip */}
      <Box sx={{
        display: 'flex', gap: 0.5, px: 1, py: 1,
        overflowX: 'auto', bgcolor: 'grey.900', flexShrink: 0,
        '&::-webkit-scrollbar': { height: 4 },
        '&::-webkit-scrollbar-thumb': { bgcolor: 'grey.700', borderRadius: 2 },
      }}>
        {mediaFiles.map((file, idx) => {
          const isVid = isVideoFile(file.filename);
          const thumbSrc = `${API_BASE}/thumbnail?path=${encodeURIComponent(file.path)}`;
          return (
            <Box
              key={file.id || idx}
              onClick={() => navigateTo(idx)}
              onContextMenu={(e) => onContextMenu?.(e, file, 'file')}
              sx={{
                flexShrink: 0, width: 80, height: 45,
                borderRadius: 1, overflow: 'hidden', cursor: 'pointer',
                border: 2, borderColor: idx === currentIndex ? 'primary.main' : 'transparent',
                opacity: idx === currentIndex ? 1 : 0.6,
                transition: 'opacity 0.2s, border-color 0.2s',
                '&:hover': { opacity: 1 },
                bgcolor: 'grey.800',
                position: 'relative',
              }}
            >
              <Box component="img" src={thumbSrc} alt={file.filename}
                sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                onError={(e) => { e.target.style.display = 'none'; }}
              />
              {/* Small video indicator badge */}
              {isVid && (
                <VideocamIcon sx={{
                  position: 'absolute', bottom: 2, right: 2,
                  fontSize: 12, color: 'white',
                  filter: 'drop-shadow(0 0 2px rgba(0,0,0,0.8))',
                }} />
              )}
            </Box>
          );
        })}
      </Box>

      {/* Action bar — filename, index, open/download */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', px: 2, py: 0.75, bgcolor: 'grey.900', borderTop: 1, borderColor: 'grey.800', flexShrink: 0 }}>
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <Button size="small" disabled={!hasPrev} onClick={() => navigateTo(currentIndex - 1)} sx={{ color: 'grey.400', minWidth: 'auto' }}>
            Previous
          </Button>
          <Button size="small" disabled={!hasNext} onClick={() => navigateTo(currentIndex + 1)} sx={{ color: 'grey.400', minWidth: 'auto' }}>
            Next
          </Button>
          <Typography variant="caption" sx={{ color: 'grey.500', ml: 1 }}>
            {currentFile?.filename}
            <Typography component="span" variant="caption" sx={{ ml: 1, color: 'grey.600' }}>
              {currentIndex + 1} / {mediaFiles.length}
            </Typography>
          </Typography>
        </Box>
        <Box sx={{ display: 'flex', gap: 1 }}>
          <Button size="small" onClick={() => fileUrl && window.open(fileUrl, '_blank')} startIcon={<OpenInNewIcon />} sx={{ color: 'grey.400' }}>
            Open
          </Button>
          <Button size="small" onClick={() => {
            if (!fileUrl) return;
            const a = document.createElement('a');
            a.href = fileUrl;
            a.download = currentFile.filename;
            a.click();
          }} startIcon={<DownloadIcon />} sx={{ color: 'grey.400' }}>
            Download
          </Button>
        </Box>
      </Box>
    </Box>
  );
};

export default MediaView;
