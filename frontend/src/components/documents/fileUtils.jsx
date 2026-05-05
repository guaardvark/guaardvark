// Shared utilities for file management
// Extracted from FileManager.jsx for reuse across desktop/window components

import React from 'react';
import {
  File as FileIcon,
  Image as ImageIcon,
  FileText as PdfIcon,
  Code2 as CodeIcon,
  FileText as DocumentIcon,
  Table as SpreadsheetIcon,
  Video as VideoIcon,
  Music as AudioIcon,
  Archive as ArchiveIcon,
  Braces as JsonIcon,
  Folder as FolderIcon,
} from 'lucide-react';
import { Box } from '@mui/material';

// Export folder icon for use in other components
export { FolderIcon };

// Constants
export const API_BASE = '/api/files'; // Use relative path so Vite proxy handles CORS
export const MAX_FILENAME_LENGTH = 255;
export const MAX_FILE_SIZE_MB = 100; // Maximum file size in MB
export const BYTES_PER_MB = 1024 * 1024;
// eslint-disable-next-line no-control-regex -- intentional: matches OS-illegal filename control chars
export const INVALID_FILENAME_CHARS = /[<>:"/\\|?*\x00-\x1f]/;

// Image file extensions that should show thumbnails
const IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff'];

// Video file extensions
const VIDEO_EXTENSIONS = ['mp4', 'webm', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'm4v'];

// Code file extensions
const CODE_EXTENSIONS = ['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'html', 'css', 'scss', 'less', 'vue', 'sh', 'bash', 'zsh', 'sql', 'json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'env', 'config', 'md', 'txt', 'csv', 'log'];

// Audio file extensions — those an HTML5 <audio> element can actually play
const AUDIO_EXTENSIONS = ['wav', 'mp3', 'ogg', 'flac', 'm4a', 'aac', 'opus'];

// Check if a filename is an image
export const isImageFile = (filename) => {
  if (!filename) return false;
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return IMAGE_EXTENSIONS.includes(ext);
};

// Check if a filename is a video
export const isVideoFile = (filename) => {
  if (!filename) return false;
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return VIDEO_EXTENSIONS.includes(ext);
};

// Check if a filename is an audio file the in-app player can handle
export const isAudioFile = (filename) => {
  if (!filename) return false;
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return AUDIO_EXTENSIONS.includes(ext);
};

// Check if a filename is any media type (image, video, or audio)
export const isMediaFile = (filename) => isImageFile(filename) || isVideoFile(filename) || isAudioFile(filename);

// Check if a filename is a code/text file that can be opened in an editor
export const isCodeFile = (filename) => {
  if (!filename) return false;
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return CODE_EXTENSIONS.includes(ext);
};

// Check if a filename is a PDF
export const isPdfFile = (filename) => {
  if (!filename) return false;
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  return ext === 'pdf';
};

// Helper component for index status indicator dot
const IndexStatusIndicator = ({ indexStatus, theme }) => {
  if (!indexStatus) return null;

  let dotColor = null;
  let tooltipText = '';

  if (indexStatus === 'INDEXED') {
    dotColor = theme.palette.success.main || '#4CAF50';
    tooltipText = 'Indexed';
  } else if (indexStatus === 'INDEXING' || indexStatus === 'PENDING') {
    dotColor = theme.palette.warning.main || '#FF9800';
    tooltipText = indexStatus === 'INDEXING' ? 'Indexing...' : 'Pending';
  } else if (indexStatus === 'ERROR') {
    dotColor = theme.palette.error.main || '#F44336';
    tooltipText = 'Error';
  } else {
    return null; // No indicator for other statuses
  }

  return (
    <Box
      sx={{
        position: 'absolute',
        top: 2,
        right: 2,
        width: 6,
        height: 6,
        borderRadius: '50%',
        backgroundColor: dotColor,
        border: '1.5px solid',
        borderColor: theme.palette.primary.main,
        zIndex: 1,
      }}
      title={tooltipText}
    />
  );
};

// Helper component for folder index status indicator dot
// Shows green if all docs indexed, yellow if partial, nothing if no docs or none indexed
export const FolderIndexIndicator = ({ item, theme, size = 6 }) => {
  const docCount = item.document_count || 0;
  const indexedCount = item.indexed_document_count || 0;
  if (docCount === 0 || indexedCount === 0) return null;

  const isFullyIndexed = indexedCount >= docCount;
  const dotColor = isFullyIndexed
    ? (theme.palette.success.main || '#4CAF50')
    : (theme.palette.warning.main || '#FF9800');
  const tooltipText = isFullyIndexed
    ? `Fully indexed (${indexedCount}/${docCount})`
    : `Partially indexed (${indexedCount}/${docCount})`;

  return (
    <Box
      sx={{
        position: 'absolute',
        bottom: 2,
        right: 2,
        width: size,
        height: size,
        borderRadius: '50%',
        backgroundColor: dotColor,
        border: '1px solid',
        borderColor: 'background.paper',
        zIndex: 1,
      }}
      title={tooltipText}
    />
  );
};

// File extension to icon mapping (large icons for grid view)
// filePath: optional document path — when provided for image files, renders a thumbnail instead of an icon
export const getFileIcon = (filename, isSelected, theme, size = 48, indexStatus = null, filePath = null) => {
  const ext = filename ? filename.split('.').pop()?.toLowerCase() || '' : '';

  // If this is an image or video file and we have a path, render a thumbnail
  if (filePath && (isImageFile(filename) || isVideoFile(filename))) {
    const thumbnailUrl = `${API_BASE}/thumbnail?path=${encodeURIComponent(filePath)}`;
    return (
      <Box sx={{
        position: 'relative',
        display: 'inline-flex',
        width: size + 16,
        height: size + 16,
        borderRadius: 1,
        overflow: 'hidden',
        backgroundColor: theme.palette.action.hover,
        alignItems: 'center',
        justifyContent: 'center',
        border: isSelected ? `2px solid ${theme.palette.primary.main}` : `1px solid ${theme.palette.divider}`,
        filter: isSelected ? `drop-shadow(0 0 6px ${theme.palette.primary.main}80)` : 'none',
        transform: isSelected ? 'scale(1.05)' : 'scale(1)',
        transition: 'all 0.15s ease-in-out',
      }}>
        <Box
          component="img"
          src={thumbnailUrl}
          alt={filename}
          loading="lazy"
          sx={{
            maxWidth: '100%',
            maxHeight: '100%',
            objectFit: 'cover',
            width: '100%',
            height: '100%',
          }}
          onError={(e) => {
            // On error, hide the img and show the fallback icon sibling
            e.target.style.display = 'none';
            if (e.target.nextSibling) e.target.nextSibling.style.display = 'flex';
          }}
        />
        <Box sx={{
          display: 'none', alignItems: 'center', justifyContent: 'center',
          width: '100%', height: '100%', position: 'absolute', top: 0, left: 0,
        }}>
          <ImageIcon size={size * 0.6} color={isSelected ? theme.palette.primary.main : '#4CAF50'} strokeWidth={1.5} />
        </Box>
        <IndexStatusIndicator indexStatus={indexStatus} theme={theme} />
      </Box>
    );
  }

  // Get color for icon
  const getIconColor = (override) => {
    if (override) {
      if (override === 'primary.main') return theme.palette.primary.main;
      return override;
    }
    return isSelected ? theme.palette.primary.main : theme.palette.action.active;
  };

  let IconComponent = FileIcon;
  let iconColorOverride = null;

  if (!filename) {
    IconComponent = FileIcon;
    iconColorOverride = null;
  } else {
    // Images (no path provided — show icon)
    if (IMAGE_EXTENSIONS.includes(ext)) {
      IconComponent = ImageIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#4CAF50';
    }
    // PDF
    else if (ext === 'pdf') {
      IconComponent = PdfIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#F44336';
    }
    // Code files
    else if (['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'html', 'css', 'scss', 'less', 'vue', 'sh', 'bash', 'zsh', 'sql'].includes(ext)) {
      IconComponent = CodeIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#2196F3';
    }
    // JSON/Config files
    else if (['json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'env', 'config'].includes(ext)) {
      IconComponent = JsonIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#FF9800';
    }
    // Spreadsheets
    else if (['csv', 'xls', 'xlsx', 'ods'].includes(ext)) {
      IconComponent = SpreadsheetIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#4CAF50';
    }
    // Documents
    else if (['doc', 'docx', 'txt', 'rtf', 'odt', 'md', 'markdown'].includes(ext)) {
      IconComponent = DocumentIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#2196F3';
    }
    // Video
    else if (['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'].includes(ext)) {
      IconComponent = VideoIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#9C27B0';
    }
    // Audio
    else if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma'].includes(ext)) {
      IconComponent = AudioIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#E91E63';
    }
    // Archives
    else if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'].includes(ext)) {
      IconComponent = ArchiveIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#795548';
    }
  }

  const iconColor = getIconColor(iconColorOverride);
  const iconSize = size;

  return (
    <Box sx={{
      position: 'relative',
      display: 'inline-flex',
      filter: isSelected ? `drop-shadow(0 0 6px ${theme.palette.primary.main}80)` : 'none',
      transform: isSelected ? 'scale(1.05)' : 'scale(1)',
      transition: 'all 0.15s ease-in-out',
    }}>
      <IconComponent size={iconSize} color={iconColor} strokeWidth={1.5} />
      <IndexStatusIndicator indexStatus={indexStatus} theme={theme} />
    </Box>
  );
};

// Small file icon for list view
// filePath: optional document path — when provided for image files, renders a small thumbnail
export const getFileIconSmall = (filename, isSelected, theme, indexStatus = null, filePath = null) => {
  if (!theme) {
    // Fallback if theme not provided
    return <FileIcon size={20} color="#666" strokeWidth={1.5} />;
  }

  const ext = filename ? filename.split('.').pop()?.toLowerCase() || '' : '';

  // If this is an image or video file and we have a path, render a small thumbnail
  if (filePath && (isImageFile(filename) || isVideoFile(filename))) {
    const thumbnailUrl = `${API_BASE}/thumbnail?path=${encodeURIComponent(filePath)}`;
    return (
      <Box sx={{
        position: 'relative',
        display: 'inline-flex',
        width: 24,
        height: 24,
        borderRadius: 0.5,
        overflow: 'hidden',
        flexShrink: 0,
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <Box
          component="img"
          src={thumbnailUrl}
          alt={filename}
          loading="lazy"
          sx={{ width: 24, height: 24, objectFit: 'cover', borderRadius: 0.5 }}
          onError={(e) => {
            e.target.style.display = 'none';
            if (e.target.nextSibling) e.target.nextSibling.style.display = 'flex';
          }}
        />
        <Box sx={{
          display: 'none', alignItems: 'center', justifyContent: 'center',
          width: '100%', height: '100%', position: 'absolute', top: 0, left: 0,
        }}>
          <ImageIcon size={16} color={isSelected ? theme.palette.primary.main : '#4CAF50'} strokeWidth={1.5} />
        </Box>
        <IndexStatusIndicator indexStatus={indexStatus} theme={theme} />
      </Box>
    );
  }

  // Get color for icon
  const getIconColor = (override) => {
    if (override) {
      if (override === 'primary.main') return theme.palette.primary.main;
      return override;
    }
    return isSelected ? theme.palette.primary.main : theme.palette.action.active;
  };

  let IconComponent = FileIcon;
  let iconColorOverride = null;

  if (!filename) {
    IconComponent = FileIcon;
    iconColorOverride = null;
  } else {
    // Images (no path provided — show icon)
    if (IMAGE_EXTENSIONS.includes(ext)) {
      IconComponent = ImageIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#4CAF50';
    }
    // PDF
    else if (ext === 'pdf') {
      IconComponent = PdfIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#F44336';
    }
    // Code files
    else if (['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'cs', 'go', 'rs', 'rb', 'php', 'swift', 'kt', 'scala', 'html', 'css', 'scss', 'less', 'vue', 'sh', 'bash', 'zsh', 'sql'].includes(ext)) {
      IconComponent = CodeIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#2196F3';
    }
    // JSON/Config files
    else if (['json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'env', 'config'].includes(ext)) {
      IconComponent = JsonIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#FF9800';
    }
    // Spreadsheets
    else if (['csv', 'xls', 'xlsx', 'ods'].includes(ext)) {
      IconComponent = SpreadsheetIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#4CAF50';
    }
    // Documents
    else if (['doc', 'docx', 'txt', 'rtf', 'odt', 'md', 'markdown'].includes(ext)) {
      IconComponent = DocumentIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#2196F3';
    }
    // Video
    else if (['mp4', 'avi', 'mov', 'mkv', 'webm', 'flv', 'wmv'].includes(ext)) {
      IconComponent = VideoIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#9C27B0';
    }
    // Audio
    else if (['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'wma'].includes(ext)) {
      IconComponent = AudioIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#E91E63';
    }
    // Archives
    else if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2', 'xz'].includes(ext)) {
      IconComponent = ArchiveIcon;
      iconColorOverride = isSelected ? 'primary.main' : '#795548';
    }
    else {
      IconComponent = FileIcon;
      iconColorOverride = null;
    }
  }

  const iconColor = getIconColor(iconColorOverride);

  return (
    <Box sx={{ position: 'relative', display: 'inline-flex' }}>
      <IconComponent size={20} color={iconColor} strokeWidth={1.5} />
      <IndexStatusIndicator indexStatus={indexStatus} theme={theme} />
    </Box>
  );
};

// Format bytes to human-readable size
export const formatBytes = (bytes) => {
  if (!bytes) return '0 B';
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return Math.round(bytes / Math.pow(1024, i)) + ' ' + sizes[i];
};

// Format date to locale string
export const formatDate = (dateString) => {
  if (!dateString) return '';
  const date = new Date(dateString);
  return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
};

// Validate file/folder name
export const validateName = (name, type = 'name') => {
  if (!name || !name.trim()) {
    return `${type === 'folder' ? 'Folder' : 'File'} name cannot be empty`;
  }
  const trimmedName = name.trim();
  if (INVALID_FILENAME_CHARS.test(trimmedName)) {
    return `${type === 'folder' ? 'Folder' : 'File'} name contains invalid characters. Please use only letters, numbers, spaces, hyphens, underscores${type === 'file' ? ', and dots' : ''}.`;
  }
  if (trimmedName.length > MAX_FILENAME_LENGTH) {
    return `${type === 'folder' ? 'Folder' : 'File'} name is too long. Maximum length is ${MAX_FILENAME_LENGTH} characters.`;
  }
  return null;
};

// Generate unique key for item
export const getItemKey = (item, type) => {
  return `${type}-${item.id}`;
};
