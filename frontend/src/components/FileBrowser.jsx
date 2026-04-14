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

const FileBrowser = ({ onFileSelect, _onClose }) => {
  const [currentPath, setCurrentPath] = useState('');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
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
      setError(err.message || 'Failed to load directory');
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
      setError(err.message || 'Failed to load file');
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
      setError(err.message || 'Search failed');
      console.error('Search error:', err);
    } finally {
      setSearching(false);
    }
  };

  // Copy file content to clipboard
  const copyToClipboard = async () => {
    try {
      await navigator.clipboard.writeText(fileContent);
      // Could add a success notification here
    } catch (err) {
      console.error('Failed to copy to clipboard:', err);
    }
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
      const revisionInstructions = prompt("Enter instructions for the file revision:", "");
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
        alert(`File revision generated successfully!\n\nNew file: ${data.data.revision_name}\nLocation: ${data.data.revision_path}\nSize: ${data.data.file_size} bytes`);
        // Refresh the current directory to show the new file
        loadDirectory(currentPath);
      } else {
        alert(`Failed to generate revision: ${data.message || 'Unknown error'}`);
      }
    } catch (err) {
      console.error('Revision generation error:', err);
      alert(`Error generating revision: ${err.message}`);
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
      { name: 'Root', path: '' }
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
          File Browser
        </Typography>
        
        {/* Search */}
        <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
          <TextField
            size="small"
            placeholder="Search files..."
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
            Search
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
          {error}
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
                        {new Date(item.modified * 1000).toLocaleDateString()}
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
              label={selectedFile?.content_type || 'unknown'}
              size="small"
              color="primary"
            />
          </Box>
        </DialogTitle>
        <DialogContent>
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" color="text.secondary">
              Path: {selectedFile?.path}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Size: {selectedFile?.size} bytes
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
            Close
          </Button>
          <Tooltip title="Copy content to clipboard">
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
            Generate Revision
          </Button>
          <Button
            variant="contained"
            onClick={handleFileSelect}
            startIcon={<InfoIcon />}
          >
            Use for LLM
          </Button>
        </DialogActions>
      </Dialog>
    </Paper>
  );
};

export default FileBrowser; 