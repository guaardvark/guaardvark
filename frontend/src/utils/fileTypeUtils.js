// frontend/src/utils/fileTypeUtils.js
// File Type Enhancement Utility - Phase 1
// Provides intelligent file type categorization, human-readable labels, and icons

/**
 * File Type Categories and Mappings
 */
export const FILE_TYPE_CATEGORIES = {
  DOCUMENT: {
    label: "Documents",
    color: "#1976D2", // Blue
    priority: 1
  },
  CODE: {
    label: "Code Files", 
    color: "#388E3C", // Green
    priority: 2
  },
  IMAGE: {
    label: "Images",
    color: "#F57C00", // Orange
    priority: 3
  },
  AUDIO: {
    label: "Audio",
    color: "#7B1FA2", // Purple
    priority: 4
  },
  VIDEO: {
    label: "Video", 
    color: "#C62828", // Red
    priority: 5
  },
  ARCHIVE: {
    label: "Archives",
    color: "#5D4037", // Brown
    priority: 6
  },
  DATA: {
    label: "Data Files",
    color: "#455A64", // Blue Grey
    priority: 7
  },
  OTHER: {
    label: "Other",
    color: "#757575", // Grey
    priority: 99
  }
};

/**
 * Comprehensive File Type Mapping
 */
export const FILE_TYPE_MAPPING = {
  // Documents
  'pdf': { label: 'PDF Document', category: 'DOCUMENT', icon: 'description' },
  'doc': { label: 'Word Document', category: 'DOCUMENT', icon: 'description' },
  'docx': { label: 'Word Document', category: 'DOCUMENT', icon: 'description' },
  'txt': { label: 'Text File', category: 'DOCUMENT', icon: 'article' },
  'rtf': { label: 'Rich Text', category: 'DOCUMENT', icon: 'description' },
  'odt': { label: 'OpenDocument Text', category: 'DOCUMENT', icon: 'description' },
  'xls': { label: 'Excel Spreadsheet', category: 'DATA', icon: 'table_chart' },
  'xlsx': { label: 'Excel Spreadsheet', category: 'DATA', icon: 'table_chart' },
  'csv': { label: 'CSV Data', category: 'DATA', icon: 'table_chart' },
  'ppt': { label: 'PowerPoint', category: 'DOCUMENT', icon: 'slideshow' },
  'pptx': { label: 'PowerPoint', category: 'DOCUMENT', icon: 'slideshow' },

  // Code Files - Frontend
  'js': { label: 'JavaScript', category: 'CODE', icon: 'code' },
  'jsx': { label: 'React Component', category: 'CODE', icon: 'code' },
  'ts': { label: 'TypeScript', category: 'CODE', icon: 'code' },
  'tsx': { label: 'React TypeScript', category: 'CODE', icon: 'code' },
  'html': { label: 'HTML', category: 'CODE', icon: 'web' },
  'htm': { label: 'HTML', category: 'CODE', icon: 'web' },
  'css': { label: 'CSS Stylesheet', category: 'CODE', icon: 'style' },
  'scss': { label: 'SASS Stylesheet', category: 'CODE', icon: 'style' },
  'sass': { label: 'SASS Stylesheet', category: 'CODE', icon: 'style' },
  'less': { label: 'LESS Stylesheet', category: 'CODE', icon: 'style' },
  'vue': { label: 'Vue Component', category: 'CODE', icon: 'code' },
  'svelte': { label: 'Svelte Component', category: 'CODE', icon: 'code' },

  // Code Files - Backend
  'py': { label: 'Python Script', category: 'CODE', icon: 'code' },
  'java': { label: 'Java Class', category: 'CODE', icon: 'code' },
  'php': { label: 'PHP Script', category: 'CODE', icon: 'code' },
  'rb': { label: 'Ruby Script', category: 'CODE', icon: 'code' },
  'go': { label: 'Go Source', category: 'CODE', icon: 'code' },
  'rs': { label: 'Rust Source', category: 'CODE', icon: 'code' },
  'cpp': { label: 'C++ Source', category: 'CODE', icon: 'code' },
  'c': { label: 'C Source', category: 'CODE', icon: 'code' },
  'h': { label: 'C Header', category: 'CODE', icon: 'code' },
  'cs': { label: 'C# Source', category: 'CODE', icon: 'code' },
  'kt': { label: 'Kotlin Source', category: 'CODE', icon: 'code' },
  'swift': { label: 'Swift Source', category: 'CODE', icon: 'code' },

  // Config & Data
  'json': { label: 'JSON Data', category: 'DATA', icon: 'data_object' },
  'xml': { label: 'XML Data', category: 'DATA', icon: 'code' },
  'yaml': { label: 'YAML Config', category: 'DATA', icon: 'settings' },
  'yml': { label: 'YAML Config', category: 'DATA', icon: 'settings' },
  'toml': { label: 'TOML Config', category: 'DATA', icon: 'settings' },
  'ini': { label: 'INI Config', category: 'DATA', icon: 'settings' },
  'env': { label: 'Environment', category: 'DATA', icon: 'settings' },
  'sql': { label: 'SQL Script', category: 'DATA', icon: 'storage' },

  // Images
  'jpg': { label: 'JPEG Image', category: 'IMAGE', icon: 'image' },
  'jpeg': { label: 'JPEG Image', category: 'IMAGE', icon: 'image' },
  'png': { label: 'PNG Image', category: 'IMAGE', icon: 'image' },
  'gif': { label: 'GIF Image', category: 'IMAGE', icon: 'image' },
  'bmp': { label: 'Bitmap Image', category: 'IMAGE', icon: 'image' },
  'svg': { label: 'SVG Vector', category: 'IMAGE', icon: 'vector_image' },
  'webp': { label: 'WebP Image', category: 'IMAGE', icon: 'image' },
  'ico': { label: 'Icon File', category: 'IMAGE', icon: 'image' },

  // Audio
  'mp3': { label: 'MP3 Audio', category: 'AUDIO', icon: 'audio_file' },
  'wav': { label: 'WAV Audio', category: 'AUDIO', icon: 'audio_file' },
  'flac': { label: 'FLAC Audio', category: 'AUDIO', icon: 'audio_file' },
  'ogg': { label: 'OGG Audio', category: 'AUDIO', icon: 'audio_file' },
  'aac': { label: 'AAC Audio', category: 'AUDIO', icon: 'audio_file' },
  'webm': { label: 'WebM Audio', category: 'AUDIO', icon: 'audio_file' },

  // Video
  'mp4': { label: 'MP4 Video', category: 'VIDEO', icon: 'video_file' },
  'avi': { label: 'AVI Video', category: 'VIDEO', icon: 'video_file' },
  'mov': { label: 'QuickTime Video', category: 'VIDEO', icon: 'video_file' },
  'wmv': { label: 'WMV Video', category: 'VIDEO', icon: 'video_file' },
  'mkv': { label: 'MKV Video', category: 'VIDEO', icon: 'video_file' },

  // Archives
  'zip': { label: 'ZIP Archive', category: 'ARCHIVE', icon: 'folder_zip' },
  'rar': { label: 'RAR Archive', category: 'ARCHIVE', icon: 'folder_zip' },
  'tar': { label: 'TAR Archive', category: 'ARCHIVE', icon: 'folder_zip' },
  'gz': { label: 'GZIP Archive', category: 'ARCHIVE', icon: 'folder_zip' },
  '7z': { label: '7-Zip Archive', category: 'ARCHIVE', icon: 'folder_zip' },

  // Other
  'md': { label: 'Markdown', category: 'DOCUMENT', icon: 'article' },
  'log': { label: 'Log File', category: 'DATA', icon: 'list_alt' },
  'license': { label: 'License File', category: 'DOCUMENT', icon: 'gavel' },
  'readme': { label: 'Readme File', category: 'DOCUMENT', icon: 'info' },
};

/**
 * Get enhanced file type information
 */
export const getFileTypeInfo = (extension) => {
  if (!extension) return null;
  
  const ext = extension.toLowerCase().replace('.', '');
  const typeInfo = FILE_TYPE_MAPPING[ext];
  
  if (!typeInfo) {
    return {
      label: ext.toUpperCase(),
      category: 'OTHER',
      icon: 'insert_drive_file',
      isUnknown: true
    };
  }
  
  return {
    ...typeInfo,
    categoryInfo: FILE_TYPE_CATEGORIES[typeInfo.category],
    isUnknown: false
  };
};

/**
 * Get file type category statistics
 */
export const getFileTypeStats = (documents) => {
  const stats = {};
  
  documents.forEach(doc => {
    const typeInfo = getFileTypeInfo(doc.type);
    const category = typeInfo?.category || 'OTHER';
    
    if (!stats[category]) {
      stats[category] = {
        count: 0,
        categoryInfo: FILE_TYPE_CATEGORIES[category]
      };
    }
    stats[category].count++;
  });
  
  return stats;
};

/**
 * Get human-readable file type display
 */
export const getFileTypeDisplay = (extension) => {
  const typeInfo = getFileTypeInfo(extension);
  return typeInfo?.label || (extension ? extension.toUpperCase() : 'Unknown');
};

/**
 * Get file type category color
 */
export const getFileTypeCategoryColor = (extension) => {
  const typeInfo = getFileTypeInfo(extension);
  return typeInfo?.categoryInfo?.color || FILE_TYPE_CATEGORIES.OTHER.color;
};

/**
 * Check if file type is supported for indexing
 */
export const isIndexableFileType = (extension) => {
  const typeInfo = getFileTypeInfo(extension);
  const category = typeInfo?.category;
  
  // Generally indexable categories
  return ['DOCUMENT', 'CODE', 'DATA'].includes(category);
};

/**
 * Get file type filter options for UI
 */
export const getFileTypeFilterOptions = () => {
  return Object.entries(FILE_TYPE_CATEGORIES)
    .sort((a, b) => a[1].priority - b[1].priority)
    .map(([key, info]) => ({
      value: key,
      label: info.label,
      color: info.color
    }));
};

// Utility function to properly handle UTC timestamps from backend
export const formatTimestamp = (timestamp) => {
  if (!timestamp) return 'N/A';
  
  // If the timestamp doesn't have timezone info, assume it's UTC
  const dateStr = timestamp.includes('Z') || timestamp.includes('+') || timestamp.includes('T') 
    ? timestamp 
    : timestamp.replace(' ', 'T') + 'Z';
  
  try {
    return new Date(dateStr).toLocaleString();
  } catch (error) {
    console.warn('Invalid timestamp format:', timestamp);
    return timestamp;
  }
}; 