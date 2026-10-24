// frontend/src/components/FileBrowser.jsx
// Version: Read-only file browser for LLM testing

import React, { useState, useEffect } from 'react';
import {
  Box,
  Paper,
  Typography,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  IconButton,
  Breadcrumbs,
  Link,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Alert,
  CircularProgress,
  Tooltip,
} from '@mui/material';
import {
  Folder as FolderIcon,
  InsertDriveFile as FileIcon,
  NavigateNext as NavigateNextIcon,
  Search as SearchIcon,
  ContentCopy as CopyIcon,
  Info as InfoIcon,
} from '@mui/icons-material';
import { BASE_URL, handleResponse } from '../api/apiClient';
import copyText from '../utils/copyText';
import { useUiTranslation, uiLocale } from '../i18n';

const FileBrowser = ({ onFileSelect, _onClose }) => {
  const { t } = useUiTranslation();
  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  // { message: the error's own text, failed: what failed }; our fallback is worded at render.
  const [error, setError] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileContent, setFileContent] = useState('');
  const [showFileDialog, setShowFileDialog] = useState(false);
  const [searchPattern, setSearchPattern] = useState('');
  const [_searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);

  // Load directory contents
  const loadDirectory = async (path = '') => {
    setLoading(true);
    setError(null);
    try {
      const url = `${BASE_URL}/files/browse${path ? `?path=${encodeURIComponent(path)}&fields=light` : '?fields=light'}`;
      const response = await fetch(url);
      const data = await handleResponse(response);
      setItems(data.data?.items || []);
      setCurrentPath(path);
    } catch (err) {
      setError({ message: err.message, failed: 'directory' });
      console.error('File browser error:', err);
    } finally {
      setLoading(false);
    }
  };

  // Load file content
  const loadFileContent = async (filePath) => {
    try {
      const url = `${BASE_URL}/files/read?path=${encodeURIComponent(filePath)}`;
      const response = await fetch(url);
      const data = await handleResponse(response);
      setFileContent(data.data?.content || '');
      setSelectedFile(data.data || data);
      setShowFileDialog(true);
    } catch (err) {
      setError({ message: err.message, failed: 'file' });
      console.error('File read error:', err);
    }
  };

  // Search files
  const searchFiles = async () => {
    if (!searchPattern.trim()) return;
    
    setSearching(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        pattern: searchPattern,
        path: currentPath
      });
      const url = `${BASE_URL}/files/search?${params}`;
      const response = await fetch(url);
      const data = await handleResponse(response);
      setSearchResults(data.data?.results || []);
    } catch (err) {
      setError({ message: err.message, failed: 'search' });
      console.error('Search error:', err);
    } finally {
      setSearching(false);
    }
  };

  // Copy file content to clipboard
  const copyToClipboard = async () => {
    // copyText falls back to execCommand: navigator.clipboard is absent over plain http on the LAN.
    const ok = await copyText(fileContent);
    // Could add a success notification here
    if (!ok) console.error('Failed to copy to clipboard');
  };

  // Handle file selection for LLM
  const handleFileSelect = () => {
    if (selectedFile && onFileSelect) {
      onFileSelect({
        path: selectedFile.path,
        content: fileContent,
        name: selectedFile.name,
        size: selectedFile.size,
        content_type: selectedFile.content_type
      });
      setShowFileDialog(false);
    }
  };

  // Generate file revision
  const generateRevision = async () => {
    if (!selectedFile) return;
    
    try {
      const revisionInstructions = prompt(t('files.browser.revisionPrompt'), "");
      if (!revisionInstructions) return;
      
      const response = await fetch(`${BASE_URL}/files/generate-revision`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          original_path: selectedFile.path,
          instructions: revisionInstructions,
          llm_context: `File: ${selectedFile.name}\nContent Type: ${selectedFile.content_type}\nSize: ${selectedFile.size} bytes`
        })
      });
      
      const data = await handleResponse(response);
      
      if (data.success) {
        alert(t('files.browser.revisionGenerated', {
          name: data.data.revision_name,
          path: data.data.revision_path,
          size: data.data.file_size,
        }));
        // Refresh the current directory to show the new file
        loadDirectory(currentPath);
      } else {
        alert(t('files.browser.revisionFailed', { message: data.message || t('files.unknownError') }));
      }
    } catch (err) {
      console.error('Revision generation error:', err);
      alert(t('files.browser.revisionError', { message: err.message }));
    }
  };

  // Navigate to directory
  const navigateTo = (item) => {
    if (item.is_directory) {
      loadDirectory(item.path);
    } else {
      loadFileContent(item.path);
    }
  };

  // Navigate breadcrumb
  const navigateBreadcrumb = (path) => {
    loadDirectory(path);
  };

  // Generate breadcrumb items
  const getBreadcrumbItems = () => {
    const parts = currentPath.split('/').filter(Boolean);
    const items = [
      { name: t('files.browser.root'), path: '' }
    ];
    
    let currentPathPart = '';
    parts.forEach(part => {
      currentPathPart += (currentPathPart ? '/' : '') + part;
      items.push({ name: part, path: currentPathPart });
    });
    
    return items;
  };

  // Load initial directory
  useEffect(() => {
    loadDirectory();
  }, []);

  return (
    <Paper sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
        <Typography variant="h6" gutterBottom>
          {t('files.browser.title')}
        </Typography>
        
        {/* Search */}
        <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
          <TextField
            size="small"
            placeholder={t('files.browser.searchPlaceholder')}
            value={searchPattern}
            onChange={(e) => setSearchPattern(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && searchFiles()}
            sx={{ flexGrow: 1 }}
          />
          <Button
            variant="outlined"
            onClick={searchFiles}
            disabled={!searchPattern.trim() || searching}
            startIcon={searching ? <CircularProgress size={16} /> : <SearchIcon />}
          >
            {t('files.browser.search')}
          </Button>
        </Box>

        {/* Breadcrumbs */}
        <Breadcrumbs separator={<NavigateNextIcon fontSize="small" />}>
          {getBreadcrumbItems().map((item, index) => (
            <Link
              key={index}
              color="inherit"
              onClick={() => navigateBreadcrumb(item.path)}
              sx={{ cursor: 'pointer' }}
            >
              {item.name}
            </Link>
          ))}
        </Breadcrumbs>
      </Box>

      {/* Error Alert */}
      {error && (
        <Alert severity="error" sx={{ m: 2 }}>
          {error.message || {
            directory: t('files.browser.loadDirectoryFailed'),
            file: t('files.browser.loadFileFailed'),
            search: t('files.browser.searchFailed'),
          }[error.failed]}
        </Alert>
      )}

      {/* Content */}
      <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
        {loading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
            <CircularProgress />
          </Box>
        ) : (
          <List>
            {items.map((item) => (
              <ListItem
                key={item.path}
                button
                onClick={() => navigateTo(item)}
                sx={{ cursor: 'pointer' }}
              >
                <ListItemIcon>
                  {item.is_directory ? <FolderIcon color="primary" /> : <FileIcon />}
                </ListItemIcon>
                <ListItemText
                  primary={item.name}
                  secondary={
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      {!item.is_directory && (
                        <Chip
                          label={`${(item.size / 1024).toFixed(1)} KB`}
                          size="small"
                          variant="outlined"
                        />
                      )}
                      <Typography variant="caption" color="text.secondary">
                        {new Date(item.modified * 1000).toLocaleDateString(uiLocale())}
                      </Typography>
                    </Box>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </Box>

      {/* File Content Dialog */}
      <Dialog
        open={showFileDialog}
        onClose={() => setShowFileDialog(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <FileIcon />
            {selectedFile?.name}
            <Chip
              label={selectedFile?.content_type || t('files.browser.unknownType')}
              size="small"
              color="primary"
            />
          </Box>
        </DialogTitle>
        <DialogContent>
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" color="text.secondary">
              {t('files.browser.path', { path: selectedFile?.path })}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t('files.browser.size', { size: selectedFile?.size })}
            </Typography>
          </Box>
          
          <TextField
            fullWidth
            multiline
            rows={20}
            value={fileContent}
            InputProps={{
              readOnly: true,
              style: { fontFamily: 'monospace', fontSize: '0.875rem' }
            }}
            variant="outlined"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowFileDialog(false)}>
            {t('files.close')}
          </Button>
          <Tooltip title={t('files.browser.copyContent')}>
            <IconButton onClick={copyToClipboard}>
              <CopyIcon />
            </IconButton>
          </Tooltip>
          <Button
            variant="outlined"
            onClick={generateRevision}
            startIcon={<InfoIcon />}
            sx={{ mr: 1 }}
          >
            {t('files.browser.generateRevision')}
          </Button>
          <Button
            variant="contained"
            onClick={handleFileSelect}
            startIcon={<InfoIcon />}
          >
            {t('files.browser.useForLlm')}
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

export default FileBrowser; 