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
import { Box, useTheme } from '@mui/material';

// Export folder icon for use in other components
export { FolderIcon };

// Constants
export const API_BASE = '/api/files'; // Use relative path so Vite proxy handles CORS
export const MAX_FILENAME_LENGTH = 255;
export const MAX_FILE_SIZE_MB = 100; // Maximum file size in MB
export const BYTES_PER_MB = 1024 * 1024;
export const INVALID_FILENAME_CHARS = /[<>:"/\\|?*\x00-\x1f]/;

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

// File extension to icon mapping (large icons for grid view)
export const getFileIcon = (filename, isSelected, theme, size = 48, indexStatus = null) => {
  const ext = filename ? filename.split('.').pop()?.toLowerCase() || '' : '';
  
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
    // Images
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff'].includes(ext)) {
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
export const getFileIconSmall = (filename, isSelected, theme, indexStatus = null) => {
  if (!theme) {
    // Fallback if theme not provided
    return <FileIcon size={20} color="#666" strokeWidth={1.5} />;
  }

  const ext = filename ? filename.split('.').pop()?.toLowerCase() || '' : '';
  
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
    // Images
    if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico', 'tiff'].includes(ext)) {
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
