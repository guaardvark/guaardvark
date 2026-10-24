// Gallery layout — large preview on top, then every folder item.
// Visual media drive the preview; folders and other files stay visible and clickable.

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Button,
  Tooltip,
  useTheme,
} from '@mui/material';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import DownloadIcon from '@mui/icons-material/Download';
import ImageIcon from '@mui/icons-material/Image';
import VideocamIcon from '@mui/icons-material/Videocam';
import { Folder } from 'lucide-react';
import { API_BASE, getFileIconSmall, isImageFile, isVideoFile } from './fileUtils';
import { isVisualMediaFile } from './folderViewPolicy';
import { useUiTranslation } from '../../i18n';

const MediaView = ({
  items,
  _folder,
  onContextMenu,
  onFileOpen,
  onNavigateToPath,
  initialFileId = null,
}) => {
  const { t } = useUiTranslation();
  const theme = useTheme();
  const [currentIndex, setCurrentIndex] = useState(0);

  const mediaFiles = useMemo(() => {
    const files = items?.files || [];
    return files.filter((f) => isVisualMediaFile(f.filename || f.name));
  }, [items]);

  const otherFiles = useMemo(() => {
    const files = items?.files || [];
    return files.filter((f) => !isVisualMediaFile(f.filename || f.name));
  }, [items]);

  const subfolders = useMemo(() => items?.folders || [], [items]);

  useEffect(() => {
    if (initialFileId == null) return;
    const idx = mediaFiles.findIndex((f) => f.id === initialFileId);
    if (idx >= 0) setCurrentIndex(idx);
  }, [mediaFiles, initialFileId]);

  useEffect(() => {
    if (currentIndex >= mediaFiles.length && mediaFiles.length > 0) {
      setCurrentIndex(mediaFiles.length - 1);
    }
  }, [mediaFiles.length, currentIndex]);

  const currentFile = mediaFiles[currentIndex] || null;
  const isVideo = currentFile ? isVideoFile(currentFile.filename || currentFile.name) : false;
  const isImage = currentFile ? isImageFile(currentFile.filename || currentFile.name) : false;

  const hasPrev = currentIndex > 0;
  const hasNext = currentIndex < mediaFiles.length - 1;
  const navigateTo = useCallback((idx) => {
    if (idx >= 0 && idx < mediaFiles.length) setCurrentIndex(idx);
  }, [mediaFiles.length]);

  const fileUrl = currentFile
    ? `${API_BASE}/document/${currentFile.id}/download?v=${currentFile.updated_at || Date.now()}`
    : null;

  const hasOtherItems = subfolders.length > 0 || otherFiles.length > 0;
  const isEmpty = mediaFiles.length === 0 && !hasOtherItems;

  if (isEmpty) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'text.secondary', gap: 1, py: 4 }}>
        <ImageIcon sx={{ fontSize: 48, opacity: 0.3 }} />
        <Typography variant="body2">{t('documents.folder.empty')}</Typography>
      </Box>
    );
  }

  const handleOtherFileClick = (e, file) => {
    onFileOpen?.(e, file, { siblings: items?.files || [] });
  };

  const handleFolderClick = (e, folder) => {
    e.preventDefault();
    e.stopPropagation();
    onNavigateToPath?.(folder.path);
  };

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
            alt={currentFile.filename || currentFile.name}
            sx={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            onError={(e) => { e.target.style.display = 'none'; }}
          />
        )}
        {!currentFile && (
          <Typography variant="body2" sx={{ color: 'grey.500' }}>
            {t('documents.mediaView.selectFile')}
          </Typography>
        )}

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

      {/* Visual-media thumbnail strip */}
      {mediaFiles.length > 0 && (
        <Box sx={{
          display: 'flex', gap: 0.5, px: 1, py: 1,
          overflowX: 'auto', bgcolor: 'grey.900', flexShrink: 0,
          '&::-webkit-scrollbar': { height: 4 },
          '&::-webkit-scrollbar-thumb': { bgcolor: 'grey.700', borderRadius: 2 },
        }}>
          {mediaFiles.map((file, idx) => {
            const isVid = isVideoFile(file.filename || file.name);
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
                <Box component="img" src={thumbSrc} alt={file.filename || file.name}
                  sx={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  onError={(e) => { e.target.style.display = 'none'; }}
                />
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
      )}

      {/* Folders + non-visual files — always listed, never hidden by gallery */}
      {hasOtherItems && (
        <Box sx={{
          display: 'flex', gap: 0.75, px: 1, py: 0.75,
          overflowX: 'auto', flexShrink: 0,
          bgcolor: 'grey.800',
          borderTop: 1, borderColor: 'grey.700',
          '&::-webkit-scrollbar': { height: 4 },
          '&::-webkit-scrollbar-thumb': { bgcolor: 'grey.600', borderRadius: 2 },
        }}>
          {subfolders.map((folder) => (
            <Tooltip key={folder.id || folder.path} title={folder.name}>
              <Box
                onClick={(e) => handleFolderClick(e, folder)}
                onDoubleClick={(e) => handleFolderClick(e, folder)}
                onContextMenu={(e) => onContextMenu?.(e, folder, 'folder')}
                sx={{
                  flexShrink: 0, minWidth: 88, maxWidth: 120,
                  display: 'flex', flexDirection: 'column', alignItems: 'center',
                  gap: 0.25, px: 0.75, py: 0.5, borderRadius: 1, cursor: 'pointer',
                  '&:hover': { bgcolor: 'grey.700' },
                }}
              >
                <Folder size={22} color={theme.palette.warning.light} strokeWidth={1.5} />
                <Typography variant="caption" noWrap sx={{ color: 'grey.200', width: '100%', textAlign: 'center' }}>
                  {folder.name}
                </Typography>
              </Box>
            </Tooltip>
          ))}
          {otherFiles.map((file) => {
            const name = file.filename || file.name;
            return (
              <Tooltip key={file.id || name} title={name}>
                <Box
                  onClick={(e) => handleOtherFileClick(e, file)}
                  onDoubleClick={(e) => handleOtherFileClick(e, file)}
                  onContextMenu={(e) => onContextMenu?.(e, file, 'file')}
                  sx={{
                    flexShrink: 0, minWidth: 88, maxWidth: 120,
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    gap: 0.25, px: 0.75, py: 0.5, borderRadius: 1, cursor: 'pointer',
                    '&:hover': { bgcolor: 'grey.700' },
                  }}
                >
                  {getFileIconSmall(name, false, theme, file.index_status, file.path)}
                  <Typography variant="caption" noWrap sx={{ color: 'grey.200', width: '100%', textAlign: 'center' }}>
                    {name}
                  </Typography>
                </Box>
              </Tooltip>
            );
          })}
        </Box>
      )}

      {/* Action bar — filename, index, open/download */}
      {currentFile && (
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', px: 2, py: 0.75, bgcolor: 'grey.900', borderTop: 1, borderColor: 'grey.800', flexShrink: 0 }}>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <Button size="small" disabled={!hasPrev} onClick={() => navigateTo(currentIndex - 1)} sx={{ color: 'grey.400', minWidth: 'auto' }}>
              {t('documents.mediaView.previous')}
            </Button>
            <Button size="small" disabled={!hasNext} onClick={() => navigateTo(currentIndex + 1)} sx={{ color: 'grey.400', minWidth: 'auto' }}>
              {t('documents.mediaView.next')}
            </Button>
            <Typography variant="caption" sx={{ color: 'grey.500', ml: 1 }}>
              {currentFile?.filename || currentFile?.name}
              <Typography component="span" variant="caption" sx={{ ml: 1, color: 'grey.600' }}>
                {currentIndex + 1} / {mediaFiles.length}
              </Typography>
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" onClick={() => fileUrl && window.open(fileUrl, '_blank')} startIcon={<OpenInNewIcon />} sx={{ color: 'grey.400' }}>
              {t('documents.mediaView.open')}
            </Button>
            <Button size="small" onClick={() => {
              if (!fileUrl) return;
              const a = document.createElement('a');
              a.href = fileUrl;
              a.download = currentFile.filename || currentFile.name;
              a.click();
            }} startIcon={<DownloadIcon />} sx={{ color: 'grey.400' }}>
              {t('documents.mediaView.download')}
            </Button>
          </Box>
        </Box>
      )}
    </Box>
  );
};

export default MediaView;
