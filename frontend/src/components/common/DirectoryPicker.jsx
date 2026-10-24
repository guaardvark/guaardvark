import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
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
  Refresh as RefreshIcon,
} from "@mui/icons-material";
import { BASE_URL, handleResponse } from "../../api/apiClient";
import { rootLabel } from "../../utils/displayPath";
import { translate, useUiTranslation } from "../../i18n";

const RECENT_PATHS_KEY = "guaardvark_recentDirectoryPaths";
const MAX_RECENT_PATHS = 5;

const getFileIcon = (extension) => {
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico'];
  const docExts = ['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt'];
  const codeExts = ['js', 'jsx', 'ts', 'tsx', 'py', 'java', 'cpp', 'c', 'h', 'css', 'html', 'php', 'rb', 'go', 'rs'];
  const dataExts = ['json', 'jsonl', 'xml', 'yaml', 'yml', 'csv', 'sql'];
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

/** "dir/name" -> { dir, name }; a bare name's folder is ".". */
const splitPath = (path) => {
  const trimmed = path.replace(/\/+$/, "");
  const cut = trimmed.lastIndexOf("/");
  if (cut < 0) return { dir: ".", name: trimmed };
  return { dir: trimmed.slice(0, cut) || "/", name: trimmed.slice(cut + 1) };
};

const joinPath = (dir, name) => (dir === "/" ? `/${name}` : `${dir}/${name}`);

/**
 * Browse the server's folders and pick a folder, a file, or either.
 *
 * Paths shown and returned are the server's resolved paths. A starting path
 * that is a file (in a file mode) opens its folder with the file selected; one
 * that does not exist falls back to `fallbackPath`.
 *
 * @param {boolean}  open
 * @param {function} onClose
 * @param {function} onSelect        (path, { kind: "folder" | "file" })
 * @param {string}   [initialPath]   where to open; "~" is the server user's home
 * @param {string}   [fallbackPath]  where to open when initialPath is missing
 * @param {string}   [title]
 * @param {boolean}  [showFiles]     folder mode: list files (not selectable)
 * @param {"folder"|"file"|"fileOrFolder"} [mode]
 * @param {string[]} [fileExtensions] file modes: the extensions that can be picked, e.g. [".jsonl"]
 * @param {string}   [purpose]       "training" also lets the picker browse the training datasets folder
 */
const DirectoryPicker = ({
  open,
  onClose,
  onSelect,
  initialPath = "~",
  fallbackPath = "~",
  title = translate("layoutCommon.directoryPicker.title"),
  showFiles = false,
  mode = "folder",
  fileExtensions = null,
  purpose = null,
}) => {
  const { t, language } = useUiTranslation();
  const pickFiles = mode !== "folder";
  const [currentPath, setCurrentPath] = useState("");
  const [parentPath, setParentPath] = useState(null);
  const [inputPath, setInputPath] = useState(initialPath);
  const [directories, setDirectories] = useState([]);
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [recentPaths, setRecentPaths] = useState([]);
  const [showRecentPaths, setShowRecentPaths] = useState(false);
  const [includeFiles, setIncludeFiles] = useState(showFiles || pickFiles);
  // The folders the server lets this picker browse (its home folder first);
  // it names them in every answer, refusals included.
  const [roots, setRoots] = useState([]);
  const [home, setHome] = useState(null);
  // Only the newest request may update the list; an older answer arriving
  // late would otherwise replace the folder the person just opened.
  const requestId = useRef(0);

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

  const canPickFile = useCallback(
    (file) => {
      if (!pickFiles) return false;
      if (!fileExtensions || fileExtensions.length === 0) return true;
      const name = file.name.toLowerCase();
      return fileExtensions.some((ext) => name.endsWith(ext.toLowerCase()));
    },
    [pickFiles, fileExtensions],
  );

  /** List one folder. Resolves to "ok", or to the error for a path that is
   *  missing (404) or not a folder (400); other failures are shown. */
  const load = useCallback(
    async (path, { selectName = null, listFiles = includeFiles } = {}) => {
      const id = ++requestId.current;
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          path,
          include_files: listFiles ? "true" : "false",
        });
        if (purpose) params.set("purpose", purpose);
        const response = await fetch(`${BASE_URL}/files/browse-server?${params}`);
        const data = await handleResponse(response, { quiet: true });
        if (id !== requestId.current) return "stale";
        const dirs = (data.directories || []).map((d) =>
          typeof d === "string" ? { name: d, item_count: -1 } : d,
        );
        if (Array.isArray(data.roots)) setRoots(data.roots);
        if (data.home) setHome(data.home);
        setCurrentPath(data.path);
        setInputPath(data.path);
        setParentPath(data.parent_path ?? null);
        setDirectories(dirs);
        setFiles(data.files || []);
        setSelectedFile(selectName);
        setSearchQuery("");
        return "ok";
      } catch (err) {
        if (id !== requestId.current) return "stale";
        if (Array.isArray(err.data?.roots)) setRoots(err.data.roots);
        if (err.data?.home) setHome(err.data.home);
        if (err.status === 400 || err.status === 404) return err;
        setError(err.message || t("layoutCommon.directoryPicker.loadFailed"));
        setDirectories([]);
        setFiles([]);
        return "failed";
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [includeFiles, purpose],
  );

  /** Open a typed or starting path: a folder as it is, a file (in a file mode)
   *  as its folder with the file selected, anything missing as `fallback`. */
  const openPath = useCallback(
    async (path, fallback = null) => {
      setNotice(null);
      const result = await load(path);
      if (typeof result === "string") return;
      if (result.status === 400 && pickFiles) {
        const { dir, name } = splitPath(path);
        const inFolder = await load(dir, { selectName: name });
        if (typeof inFolder === "string") return;
      }
      if (fallback && fallback !== path) {
        setNotice(
          fallback === "~"
            ? t("layoutCommon.directoryPicker.notFoundShowingHome", { path })
            : t("layoutCommon.directoryPicker.notFoundShowing", { path, fallback }),
        );
        const again = await load(fallback);
        if (typeof again !== "string") setError(again.message || t("layoutCommon.directoryPicker.folderNotFound"));
        return;
      }
      setError(result.message || t("layoutCommon.directoryPicker.folderNotFound"));
    },
    [load, pickFiles],
  );

  useEffect(() => {
    if (open) {
      setSelectedFile(null);
      setSearchQuery("");
      openPath(initialPath || fallbackPath, fallbackPath);
    }
    // Only opening, or a new starting point, restarts the browse; the
    // switches reload the folder already open.
  }, [open, initialPath]);

  const reload = (overrides = {}) => {
    if (open && currentPath) load(currentPath, { selectName: selectedFile, ...overrides });
  };

  const handleNavigate = (newPath) => {
    setNotice(null);
    load(newPath);
  };

  const typedPathOpen = inputPath === currentPath;
  const selectedPath = selectedFile ? joinPath(currentPath, selectedFile) : currentPath;
  const selectionKind = selectedFile ? "file" : "folder";
  const canSelect =
    Boolean(currentPath) &&
    !loading &&
    !error &&
    typedPathOpen &&
    (mode === "file" ? Boolean(selectedFile) : true);

  const handleSelect = (path = selectedPath, kind = selectionKind) => {
    if (!canSelect && path === selectedPath) return;
    saveToRecentPaths(currentPath);
    if (onSelect) {
      onSelect(path, { kind });
    }
    onClose();
  };

  const handleGoToPath = () => {
    const typed = inputPath.trim();
    if (typed && typed !== currentPath) {
      openPath(typed);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      handleGoToPath();
    }
  };

  const handleRecentPathClick = (path) => {
    handleNavigate(path);
    setShowRecentPaths(false);
  };

  const breadcrumbs = useMemo(() => {
    // Crumbs start at the deepest allowed root holding the folder; the
    // folders above it could only be refused.
    const root = roots
      .map((r) => r.replace(/\/+$/, ""))
      .filter((r) => r && (currentPath === r || currentPath.startsWith(`${r}/`)))
      .sort((a, b) => b.length - a.length)[0];
    const base = root || "";
    const rootName = base ? rootLabel(base, home).split("/").pop() : t("layoutCommon.directoryPicker.root");
    const crumbs = [{ path: base || "/", name: rootName }];
    currentPath.slice(base.length).split("/").filter(Boolean).forEach((part, i, parts) => {
      crumbs.push({ path: `${base}/${parts.slice(0, i + 1).join("/")}`, name: part });
    });
    return crumbs;
  }, [currentPath, roots, home, language]);

  const filteredDirectories = useMemo(() => {
    if (!searchQuery) return directories;
    const query = searchQuery.toLowerCase();
    return directories.filter(dir => dir.name.toLowerCase().includes(query));
  }, [directories, searchQuery]);

  const filteredFiles = useMemo(() => {
    if (!searchQuery) return files;
    const query = searchQuery.toLowerCase();
    return files.filter(file => file.name.toLowerCase().includes(query));
  }, [files, searchQuery]);

  const selectLabel = selectedFile || currentPath.split('/').pop() || t("layoutCommon.directoryPicker.root");

  // "3 folders, 2 files matching "x" · .jsonl files can be picked"
  const folderCount = t("layoutCommon.directoryPicker.folderCount", { count: filteredDirectories.length });
  let countSummary = includeFiles
    ? t("layoutCommon.directoryPicker.foldersAndFiles", {
        folders: folderCount,
        files: t("layoutCommon.directoryPicker.fileCount", { count: filteredFiles.length }),
      })
    : folderCount;
  if (searchQuery) countSummary = t("layoutCommon.directoryPicker.summaryMatching", { summary: countSummary, query: searchQuery });
  if (pickFiles && fileExtensions?.length > 0) {
    countSummary = t("layoutCommon.directoryPicker.summaryPickable", { summary: countSummary, extensions: fileExtensions.join(", ") });
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ pb: 1 }}>
        <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
          <Stack direction="row" spacing={1} alignItems="center">
            <FolderOpenIcon color="primary" />
            <Typography variant="h6">{title}</Typography>
          </Stack>
          <Stack direction="row" spacing={1} alignItems="center">
            <Tooltip title={t("common.refresh")}>
              <span>
                <IconButton size="small" onClick={() => reload()} disabled={loading || !currentPath}>
                  <RefreshIcon fontSize="small" />
                </IconButton>
              </span>
            </Tooltip>
            <Tooltip title={showRecentPaths ? t("layoutCommon.directoryPicker.hideRecent") : t("layoutCommon.directoryPicker.showRecent")}>
              <IconButton size="small" onClick={() => setShowRecentPaths(!showRecentPaths)}>
                <HistoryIcon fontSize="small" color={showRecentPaths ? "primary" : "inherit"} />
              </IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </DialogTitle>
      <DialogContent sx={{ pb: 1 }}>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <Collapse in={showRecentPaths && recentPaths.length > 0}>
            <Box sx={{ p: 1.5, bgcolor: 'action.hover', borderRadius: 1, mb: 1 }}>
              <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
                {t("layoutCommon.directoryPicker.recentPaths")}
              </Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                {recentPaths.map((path, index) => (
                  <Chip
                    key={index}
                    label={path.split('/').pop() || t("layoutCommon.directoryPicker.root")}
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

          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            <Tooltip title={t("layoutCommon.directoryPicker.goHome")}>
              <IconButton size="small" aria-label={t("layoutCommon.directoryPicker.homeFolder")} onClick={() => handleNavigate("~")}>
                <HomeIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Breadcrumbs separator="/" sx={{ flex: 1 }} maxItems={4} itemsAfterCollapse={2}>
              {breadcrumbs.map((item, index) => {
                const isLast = index === breadcrumbs.length - 1;
                return (
                  <Link
                    key={item.path}
                    component="button"
                    variant="body2"
                    onClick={() => !isLast && handleNavigate(item.path)}
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

          <TextField
            label={t("layoutCommon.directoryPicker.path")}
            value={inputPath}
            onChange={(e) => setInputPath(e.target.value)}
            onKeyPress={handleKeyPress}
            fullWidth
            size="small"
            helperText={!typedPathOpen ? t("layoutCommon.directoryPicker.openPathFirst") : " "}
            InputProps={{
              startAdornment: parentPath && (
                <InputAdornment position="start">
                  <IconButton size="small" aria-label={t("layoutCommon.directoryPicker.parentFolder")} onClick={() => handleNavigate(parentPath)}>
                    <ArrowBackIcon fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ),
              endAdornment: (
                <InputAdornment position="end">
                  <Button size="small" onClick={handleGoToPath} disabled={typedPathOpen}>
                    {t("layoutCommon.directoryPicker.go")}
                  </Button>
                </InputAdornment>
              ),
            }}
          />

          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              placeholder={t("layoutCommon.directoryPicker.searchPlaceholder")}
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
            {!pickFiles && (
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={includeFiles}
                    onChange={(e) => {
                      setIncludeFiles(e.target.checked);
                      reload({ listFiles: e.target.checked });
                    }}
                  />
                }
                label={<Typography variant="body2">{t("layoutCommon.directoryPicker.showFiles")}</Typography>}
              />
            )}
          </Stack>

          {roots.length > 0 && (
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap alignItems="center">
              <Typography variant="caption" color="text.secondary">
                {t("layoutCommon.directoryPicker.browse")}:
              </Typography>
              {roots.map((root) => (
                <Chip
                  key={root}
                  label={rootLabel(root, home)}
                  size="small"
                  variant={root === currentPath ? "filled" : "outlined"}
                  onClick={() => handleNavigate(root)}
                />
              ))}
              <Typography variant="caption" color="text.secondary">
                {t("layoutCommon.directoryPicker.hiddenFolders")}
              </Typography>
            </Stack>
          )}

          {notice && <Alert severity="info">{notice}</Alert>}

          {loading ? (
            <Box sx={{ p: 3, textAlign: "center" }}>
              <CircularProgress size={28} />
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {t("common.loading")}
              </Typography>
            </Box>
          ) : error ? (
            <Alert severity="error">{error}</Alert>
          ) : filteredDirectories.length === 0 && filteredFiles.length === 0 ? (
            <Alert severity="info">
              {searchQuery
                ? t("layoutCommon.directoryPicker.noMatches")
                : pickFiles
                  ? t("layoutCommon.directoryPicker.emptyFolder")
                  : t("layoutCommon.directoryPicker.noSubdirectories")}
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
              {filteredDirectories.map((dir) => (
                <ListItemButton
                  key={dir.name}
                  onClick={() => handleNavigate(joinPath(currentPath, dir.name))}
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
                    primary={dir.name}
                    secondary={dir.item_count >= 0 ? t("layoutCommon.directoryPicker.itemCount", { count: dir.item_count }) : null}
                    primaryTypographyProps={{ variant: 'body2' }}
                    secondaryTypographyProps={{ variant: 'caption' }}
                  />
                </ListItemButton>
              ))}

              {includeFiles && filteredFiles.length > 0 && (
                <>
                  {filteredDirectories.length > 0 && <Divider sx={{ my: 0.5 }} />}
                  {filteredFiles.map((file) => {
                    const pickable = canPickFile(file);
                    return (
                      <ListItemButton
                        key={file.name}
                        disabled={!pickable}
                        selected={pickable && selectedFile === file.name}
                        onClick={() => setSelectedFile(selectedFile === file.name ? null : file.name)}
                        onDoubleClick={() => handleSelect(joinPath(currentPath, file.name), "file")}
                        sx={pickable ? undefined : { opacity: 0.7, cursor: 'default' }}
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
                    );
                  })}
                </>
              )}
            </List>
          )}

          {!loading && !error && (
            <Typography variant="caption" color="text.secondary">
              {countSummary}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose}>{t("common.cancel")}</Button>
        <Button
          onClick={() => handleSelect()}
          variant="contained"
          startIcon={selectedFile ? <FileIcon /> : <FolderOpenIcon />}
          disabled={!canSelect}
        >
          {mode === "file" && !selectedFile ? t("layoutCommon.directoryPicker.chooseFile") : t("layoutCommon.directoryPicker.selectNamed", { name: selectLabel })}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default DirectoryPicker;
