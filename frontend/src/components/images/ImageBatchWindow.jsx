// ImageBatchWindow.jsx
// Window wrapper for viewing a batch of images.
// Uses FolderWindowWrapper for consistent window chrome.

import React, { useCallback } from 'react';
import {
  IconButton,
  Tooltip,
  Chip,
  Box,
} from '@mui/material';
import {
  Download as DownloadIcon,
  Refresh as RefreshIcon,
} from '@mui/icons-material';
import FolderWindowWrapper from '../documents/FolderWindowWrapper';
import ImageBatchContents from './ImageBatchContents';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const ImageBatchWindow = ({
  id,
  batch,
  windowColor,
  onWindowColorChange,
  isMinimized,
  onToggleMinimize,
  onClose,
  onFeedback,
  ...layoutProps
}) => {
  const handleDownload = useCallback(async () => {
    if (!batch?.batch_id) return;
    try {
      const response = await fetch(`${API_BASE}/batch-image/download/${batch.batch_id}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `batch_${batch.batch_id}_results.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      onFeedback?.(`Download started for ${batch.display_name || batch.batch_id}`, 'success');
    } catch (err) {
      onFeedback?.(`Download failed: ${err.message}`, 'error');
    }
  }, [batch, onFeedback]);

  const statusColor = batch?.status === 'completed' ? 'success'
    : batch?.status === 'running' ? 'warning'
    : batch?.status === 'failed' ? 'error'
    : 'default';

  const titleBarActions = (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
      <Chip
        label={`${batch?.completed_images || 0}/${batch?.total_images || 0}`}
        size="small"
        color={statusColor}
        sx={{ height: 18, fontSize: '0.6rem', '& .MuiChip-label': { px: 0.5 } }}
      />
      {batch?.status === 'completed' && (
        <Tooltip title="Download batch">
          <IconButton size="small" onClick={handleDownload} className="non-draggable" sx={{ width: 20, height: 20 }}>
            <DownloadIcon sx={{ fontSize: 14 }} />
          </IconButton>
        </Tooltip>
      )}
    </Box>
  );

  const title = batch?.display_name || batch?.batch_id || 'Batch';

  return (
    <FolderWindowWrapper
      title={title}
      windowColor={windowColor}
      onWindowColorChange={onWindowColorChange}
      isMinimized={isMinimized}
      onToggleMinimize={onToggleMinimize}
      onClose={onClose}
      titleBarActions={titleBarActions}
      {...layoutProps}
    >
      <ImageBatchContents batch={batch} onFeedback={onFeedback} />
    </FolderWindowWrapper>
  );
};

export default ImageBatchWindow;
