import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Box,
  Alert,
  CircularProgress,
  IconButton,
  Breadcrumbs,
  Link,
  Stack,
  Typography,
  Chip,
  InputAdornment,
  Tooltip,
  Divider,
  FormControlLabel,
  Switch,
  Collapse,
} from "@mui/material";
import {
  Folder as FolderIcon,
  ArrowBack as ArrowBackIcon,
  FolderOpen as FolderOpenIcon,
  Search as SearchIcon,
  Clear as ClearIcon,
  Home as HomeIcon,
  History as HistoryIcon,
  InsertDriveFile as FileIcon,
  Image as ImageIcon,
  Description as DocIcon,
  Code as CodeIcon,
  DataObject as JsonIcon,
  TextSnippet as TextIcon,
  VideoFile as VideoIcon,
  AudioFile as AudioIcon,
  Archive as ArchiveIcon,
  ExpandMore as ExpandMoreIcon,
  ExpandLess as ExpandLessIcon,
  CreateNewFolder as CreateNewFolderIcon,
  Refresh as RefreshIcon,
} from "@mui/icons-material";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const RECENT_PATHS_KEY = "guaardvark_recentDirectoryPaths";
const MAX_RECENT_PATHS = 5;

const getFileIcon = (extension) => {
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'];
  const docExts = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt'];
  const codeExts = ['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'cpp', 'c', 'h', 'css', 'html', 'php', 'rb', 'go', 'rs'];
  const dataExts = ['json', 'xml', 'yaml', 'yml', 'csv', 'sql'];
  const textExts = ['txt', 'md', 'log', 'ini', 'cfg', 'conf'];
  const videoExts = ['mp4', 'avi', 'mkv', 'mov', 'wmv', 'webm'];
  const audioExts = ['mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a'];
  const archiveExts = ['zip', 'tar', 'gz', 'rar', '7z', 'bz2'];

  if (imageExts.includes(extension)) return <ImageIcon fontSize="small" sx={{ color: 'success.main' }} />;
  if (docExts.includes(extension)) return <DocIcon fontSize="small" sx={{ color: 'error.main' }} />;
  if (codeExts.includes(extension)) return <CodeIcon fontSize="small" sx={{ color: 'info.main' }} />;
  if (dataExts.includes(extension)) return <JsonIcon fontSize="small" sx={{ color: 'warning.main' }} />;
  if (textExts.includes(extension)) return <TextIcon fontSize="small" sx={{ color: 'text.secondary' }} />;
  if (videoExts.includes(extension)) return <VideoIcon fontSize="small" sx={{ color: 'secondary.main' }} />;
  if (audioExts.includes(extension)) return <AudioIcon fontSize="small" sx={{ color: 'primary.main' }} />;
  if (archiveExts.includes(extension)) return <ArchiveIcon fontSize="small" sx={{ color: 'text.disabled' }} />;
  return <FileIcon fontSize="small" sx={{ color: 'text.disabled' }} />;
};

const formatFileSize = (bytes) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
};

const DirectoryPicker = ({
  open,
  onClose,
  onSelect,
  initialPath = "/",
  title = "Select Directory",
  showFiles = false,
}) => {
  const [currentPath, setCurrentPath] = useState(initialPath);
  const [inputPath, setInputPath] = useState(initialPath);
  const [directories, setDirectories] = useState([]);
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [recentPaths, setRecentPaths] = useState([]);
  const [showRecentPaths, setShowRecentPaths] = useState(false);
  const [includeFiles, setIncludeFiles] = useState(showFiles);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(RECENT_PATHS_KEY);
      if (saved) {
        setRecentPaths(JSON.parse(saved));
      }
    } catch (e) {
      console.warn("Failed to load recent paths:", e);
    }
  }, []);

  const saveToRecentPaths = useCallback((path) => {
    try {
      const newRecent = [path, ...recentPaths.filter(p => p !== path)].slice(0, MAX_RECENT_PATHS);
      setRecentPaths(newRecent);
      localStorage.setItem(RECENT_PATHS_KEY, JSON.stringify(newRecent));
    } catch (e) {
      console.warn("Failed to save recent path:", e);
    }
  }, [recentPaths]);

  useEffect(() => {
    if (open) {
      setCurrentPath(initialPath);
      setInputPath(initialPath);
      setSearchQuery("");
      loadDirectories(initialPath);
    }
  }, [open, initialPath]);

  const loadDirectories = async (path) => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        path: path,
        include_files: includeFiles ? "true" : "false"
      });
      const response = await fetch(`${API_BASE}/files/browse-server?${params}`);
      const data = await response.json();
      if (response.ok) {
        if (data.directories && data.directories.length > 0) {
          if (typeof data.directories[0] === 'string') {
            setDirectories(data.directories.map(name => ({ name, item_count: -1 })));
          } else {
            setDirectories(data.directories);
          }
        } else {
          setDirectories([]);
        }
        setFiles(data.files || []);
      } else {
        setError(data.error || "Failed to load directories");
        setDirectories([]);
        setFiles([]);
      }
    } catch (err) {
      console.error("Error loading directories:", err);
      setError("Failed to load directories");
      setDirectories([]);
      setFiles([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open && currentPath) {
      loadDirectories(currentPath);
    }
  }, [includeFiles]);

  const handleNavigate = (newPath) => {
    setCurrentPath(newPath);
    setInputPath(newPath);
    setSearchQuery("");
    loadDirectories(newPath);
  };

  const handleSelect = () => {
    saveToRecentPaths(currentPath);
    if (onSelect) {
      onSelect(currentPath);
    }
    onClose();
  };

  const handleGoToPath = () => {
    if (inputPath && inputPath !== currentPath) {
      handleNavigate(inputPath);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      handleGoToPath();
    }
  };

  const handleBreadcrumbClick = (path) => {
    handleNavigate(path);
  };

  const handleRecentPathClick = (path) => {
    handleNavigate(path);
    setShowRecentPaths(false);
  };

  const getBreadcrumbs = () => {
    if (currentPath === "/") return [{ path: "/", name: "Root" }];
    const parts = currentPath.split("/").filter(Boolean);
    const breadcrumbs = [{ path: "/", name: "Root" }];
    for (let i = 0; i < parts.length; i++) {
      breadcrumbs.push({
        path: "/" + parts.slice(0, i + 1).join("/"),
        name: parts[i]
      });
    }
    return breadcrumbs;
  };

  const filteredDirectories = useMemo(() => {
    if (!searchQuery) return directories;
    const query = searchQuery.toLowerCase();
    return directories.filter(dir =>
      (typeof dir === 'string' ? dir : dir.name).toLowerCase().includes(query)
    );
  }, [directories, searchQuery]);

  const filteredFiles = useMemo(() => {
    if (!searchQuery) return files;
    const query = searchQuery.toLowerCase();
    return files.filter(file => file.name.toLowerCase().includes(query));
  }, [files, searchQuery]);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
          <Stack direction="row" spacing={1} alignItems="center">
            <FolderOpenIcon color="primary" />
            <Typography variant="h6">{title}</Typography>
          </Stack>
          <Stack direction="row" spacing={1} alignItems="center">
            <Tooltip title="Refresh">
              <IconButton size="small" onClick={() => loadDirectories(currentPath)} disabled={loading}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title={showRecentPaths ? "Hide recent" : "Recent paths"}>
              <IconButton size="small" onClick={() => setShowRecentPaths(!showRecentPaths)}>
                <HistoryIcon fontSize="small" color={showRecentPaths ? "primary" : "inherit"} />
              </IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ pb: 1 }}>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {}
          <Collapse in={showRecentPaths && recentPaths.length > 0}>
            <Box sx={{ p: 1.5, bgcolor: 'action.hover', borderRadius: 1, mb: 1 }}>
              <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
                Recent Paths
              </Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                {recentPaths.map((path, index) => (
                  <Chip
                    key={index}
                    label={path.split('/').pop() || 'Root'}
                    size="small"
                    onClick={() => handleRecentPathClick(path)}
                    onDelete={() => {
                      const newRecent = recentPaths.filter((_, i) => i !== index);
                      setRecentPaths(newRecent);
                      localStorage.setItem(RECENT_PATHS_KEY, JSON.stringify(newRecent));
                    }}
                    title={path}
                    sx={{ mb: 0.5 }}
                  />
                ))}
              </Stack>
            </Box>
          </Collapse>

          {}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Tooltip title="Go to home directory">
              <IconButton size="small" onClick={() => handleNavigate(initialPath || "/")}>
                <HomeIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Breadcrumbs separator="/" sx={{ flex: 1 }} maxItems={4} itemsAfterCollapse={2}>
              {getBreadcrumbs().map((item, index) => {
                const isLast = index === getBreadcrumbs().length - 1;
                return (
                  <Link
                    key={item.path}
                    component="button"
                    variant="body2"
                    onClick={() => !isLast && handleBreadcrumbClick(item.path)}
                    sx={{
                      cursor: isLast ? "default" : "pointer",
                      textDecoration: "none",
                      color: isLast ? "text.primary" : "primary.main",
                      fontWeight: isLast ? 600 : 400,
                      "&:hover": {
                        textDecoration: isLast ? "none" : "underline"
                      }
                    }}
                  >
                    {item.name}
                  </Link>
                );
              })}
            </Breadcrumbs>
          </Box>

          {}
          <TextField
            label="Path"
            value={inputPath}
            onChange={(e) => setInputPath(e.target.value)}
            onKeyPress={handleKeyPress}
            fullWidth
            size="small"
            InputProps={{
              startAdornment: currentPath !== "/" && (
                <InputAdornment position="start">
                  <IconButton
                    size="small"
                    onClick={() => {
                      const parentPath = currentPath.split("/").slice(0, -1).join("/") || "/";
                      handleNavigate(parentPath);
                    }}
                  >
                    <ArrowBackIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ),
              endAdornment: (
                <InputAdornment position="end">
                  <Button size="small" onClick={handleGoToPath} disabled={inputPath === currentPath}>
                    Go
                  </Button>
                </InputAdornment>
              ),
            }}
          />

          {}
          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              placeholder="Search in current directory..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              size="small"
              sx={{ flex: 1 }}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" color="action" />
                  </InputAdornment>
                ),
                endAdornment: searchQuery && (
                  <InputAdornment position="end">
                    <IconButton size="small" onClick={() => setSearchQuery("")}>
                      <ClearIcon fontSize="small" />
                    </IconButton>
                  </InputAdornment>
                ),
              }}
            />
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={includeFiles}
                  onChange={(e) => setIncludeFiles(e.target.checked)}
                />
              }
              label={<Typography variant="body2">Show files</Typography>}
            />
          </Stack>

          {}
          {loading ? (
            <Box sx={{ p: 3, textAlign: "center" }}>
              <CircularProgress size={28} />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                Loading...
              </Typography>
            </Box>
          ) : error ? (
            <Alert severity="error">{error}</Alert>
          ) : filteredDirectories.length === 0 && filteredFiles.length === 0 ? (
            <Alert severity="info">
              {searchQuery
                ? "No matching items found."
                : "No subdirectories found. You can select this directory."}
            </Alert>
          ) : (
            <List
              sx={{
                maxHeight: 350,
                overflow: "auto",
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                bgcolor: 'background.paper'
              }}
              dense
            >
              {}
              {filteredDirectories.map((dir) => {
                const dirName = typeof dir === 'string' ? dir : dir.name;
                const itemCount = typeof dir === 'object' ? dir.item_count : -1;
                const newPath = currentPath === "/" ? `/${dirName}` : `${currentPath}/${dirName}`;
                return (
                  <ListItemButton
                    key={dirName}
                    onClick={() => handleNavigate(newPath)}
                    sx={{
                      '&:hover': {
                        bgcolor: 'action.hover'
                      }
                    }}
                  >
                    <ListItemIcon sx={{ minWidth: 36 }}>
                      <FolderIcon color="primary" />
                    </ListItemIcon>
                    <ListItemText
                      primary={dirName}
                      secondary={itemCount >= 0 ? `${itemCount} items` : null}
                      primaryTypographyProps={{ variant: 'body2' }}
                      secondaryTypographyProps={{ variant: 'caption' }}
                    />
                  </ListItemButton>
                );
              })}

              {}
              {includeFiles && filteredFiles.length > 0 && (
                <>
                  {filteredDirectories.length > 0 && <Divider sx={{ my: 0.5 }} />}
                  {filteredFiles.map((file) => (
                    <ListItemButton
                      key={file.name}
                      disabled
                      sx={{ opacity: 0.7, cursor: 'default' }}
                    >
                      <ListItemIcon sx={{ minWidth: 36 }}>
                        {getFileIcon(file.extension)}
                      </ListItemIcon>
                      <ListItemText
                        primary={file.name}
                        secondary={formatFileSize(file.size)}
                        primaryTypographyProps={{ variant: 'body2' }}
                        secondaryTypographyProps={{ variant: 'caption' }}
                      />
                    </ListItemButton>
                  ))}
                </>
              )}
            </List>
          )}

          {}
          {!loading && !error && (
            <Typography variant="caption" color="text.secondary">
              {filteredDirectories.length} folder{filteredDirectories.length !== 1 ? 's' : ''}
              {includeFiles && `, ${filteredFiles.length} file${filteredFiles.length !== 1 ? 's' : ''}`}
              {searchQuery && ` matching "${searchQuery}"`}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          onClick={handleSelect}
          variant="contained"
          startIcon={<FolderOpenIcon />}
        >
          Select: {currentPath.split('/').pop() || 'Root'}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DirectoryPicker;
