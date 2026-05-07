// frontend/src/components/videoeditor/MediaLibraryPanel.jsx
//
// Owns the Video Editor's left-column Media Library:
//   - tab selection (Video / Audio / Image)
//   - view-mode (grid | list, persisted to localStorage)
//   - folder-drill state (per-tab)
//
// Data flows in from VideoEditorPage (which still does the fetching).
// Drag-start handlers come in as props so the timeline drop-targets
// stay wired exactly the way the page already expects.
import React, { useEffect, useMemo, useState } from "react";
import {
  Box,
  Paper,
  Tabs,
  Tab,
  Stack,
  CircularProgress,
  IconButton,
  Tooltip,
  Typography,
  Breadcrumbs,
  Link as MuiLink,
} from "@mui/material";
import {
  MovieFilter as VideoIcon,
  GraphicEq as AudioIcon,
  Image as ImageIcon,
  GridView as GridIcon,
  ViewList as ListIcon,
  ArrowBack as ArrowBackIcon,
} from "@mui/icons-material";
import groupByFolder from "./groupByFolder";
import MediaLibraryGrid from "./MediaLibraryGrid";
import MediaLibraryList from "./MediaLibraryList";

const STORAGE_KEY = "videoEditor.mediaLibraryView";

// Read the persisted view-mode safely. Defaults to grid for first-time
// users — that's what they got before this work landed, so the change
// is invisible until they opt into list mode.
const _readPersistedView = () => {
  try {
    return localStorage.getItem(STORAGE_KEY) === "list" ? "list" : "grid";
  } catch {
    // localStorage can throw in private mode / sandboxed iframes
    return "grid";
  }
};

const _persistView = (mode) => {
  try {
    localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // Best-effort — losing persistence isn't fatal
  }
};

const MediaLibraryPanel = ({
  videos = [],
  audios = [],
  images = [],
  loading = false,
  onItemClick,
  onItemDragStart,
}) => {
  const [tabIndex, setTabIndex] = useState(0); // 0=video, 1=audio, 2=image
  const [viewMode, setViewMode] = useState(_readPersistedView);
  // currentFolderId is per-tab; switching tabs resets the drill-in.
  const [currentFolderId, setCurrentFolderId] = useState(null);

  useEffect(() => {
    _persistView(viewMode);
  }, [viewMode]);

  // Reset drill-in when the user switches tabs — the active folder
  // belongs to the previous tab's content kind, not this one.
  useEffect(() => {
    setCurrentFolderId(null);
  }, [tabIndex]);

  // Pick the active list + kind based on tab index. Single source of
  // truth for which list+kind we're rendering.
  const { activeList, activeKind } = useMemo(() => {
    if (tabIndex === 1) return { activeList: audios, activeKind: "audio" };
    if (tabIndex === 2) return { activeList: images, activeKind: "image" };
    return { activeList: videos, activeKind: "video" };
  }, [tabIndex, videos, audios, images]);

  // Group once per (active list, drill state) — feeds both presenters.
  // When drilled in, restrict the grouping to the items in that folder
  // so subfolders inside it (if any) become tiles at the same level.
  const grouping = useMemo(() => {
    if (currentFolderId == null) {
      return groupByFolder(activeList);
    }
    const inFolder = activeList.filter((it) => it?.folder_id === currentFolderId);
    return groupByFolder(inFolder);
  }, [activeList, currentFolderId]);

  // Auto-drill-out guard: if the drilled-into folder no longer has any
  // items in the active list (deleted upstream, list refreshed), pop
  // the breadcrumb back to root rather than render an empty drilldown
  // forever. Fires once per stale-folder event.
  useEffect(() => {
    if (currentFolderId == null) return;
    const stillExists = activeList.some((it) => it?.folder_id === currentFolderId);
    if (!stillExists) setCurrentFolderId(null);
  }, [activeList, currentFolderId]);

  const currentFolder = useMemo(() => {
    if (currentFolderId == null) return null;
    const item = activeList.find((it) => it?.folder_id === currentFolderId && it?.folder);
    return item?.folder ?? null;
  }, [activeList, currentFolderId]);

  const handleFolderOpen = (group) => {
    setCurrentFolderId(group?.folder?.id ?? null);
  };

  const handleBreadcrumbHome = (e) => {
    e?.preventDefault?.();
    setCurrentFolderId(null);
  };

  return (
    <Paper elevation={2} sx={{ width: 280, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <Tabs
        value={tabIndex}
        onChange={(_, v) => setTabIndex(v)}
        variant="fullWidth"
        sx={{ borderBottom: 1, borderColor: "divider", minHeight: 40 }}
      >
        <Tab
          icon={<VideoIcon fontSize="small" />}
          label={`Video (${videos.length})`}
          sx={{ minHeight: 40, textTransform: "none", fontSize: "0.75rem" }}
        />
        <Tab
          icon={<AudioIcon fontSize="small" />}
          label={`Audio (${audios.length})`}
          sx={{ minHeight: 40, textTransform: "none", fontSize: "0.75rem" }}
        />
        <Tab
          icon={<ImageIcon fontSize="small" />}
          label={`Images (${images.length})`}
          sx={{ minHeight: 40, textTransform: "none", fontSize: "0.75rem" }}
        />
      </Tabs>

      {/* Sub-header: breadcrumb (only when drilled in) + view toggle */}
      <Stack
        direction="row"
        alignItems="center"
        sx={{ px: 1, py: 0.5, borderBottom: 1, borderColor: "divider", gap: 1 }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {currentFolder ? (
            <Breadcrumbs separator="›" sx={{ fontSize: "0.7rem" }}>
              <MuiLink
                href="#"
                onClick={handleBreadcrumbHome}
                underline="hover"
                sx={{ fontSize: "0.7rem", display: "flex", alignItems: "center", gap: 0.25 }}
              >
                <ArrowBackIcon sx={{ fontSize: 14 }} />
                Library
              </MuiLink>
              <Typography
                variant="caption"
                sx={{
                  fontSize: "0.7rem",
                  fontWeight: 600,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  maxWidth: 140,
                }}
                title={currentFolder.name}
              >
                {currentFolder.name}
              </Typography>
            </Breadcrumbs>
          ) : (
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
              Drag items to the timeline
            </Typography>
          )}
        </Box>
        <Tooltip title="Grid view">
          <IconButton
            size="small"
            color={viewMode === "grid" ? "primary" : "default"}
            onClick={() => setViewMode("grid")}
            aria-label="Grid view"
          >
            <GridIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="List view">
          <IconButton
            size="small"
            color={viewMode === "list" ? "primary" : "default"}
            onClick={() => setViewMode("list")}
            aria-label="List view"
          >
            <ListIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      <Box sx={{ flex: 1, p: 1, overflow: "auto" }}>
        {loading && <CircularProgress size={20} />}
        {!loading && viewMode === "grid" && (
          <MediaLibraryGrid
            grouping={grouping}
            kind={activeKind}
            onItemClick={onItemClick}
            onItemDragStart={onItemDragStart}
            onFolderOpen={handleFolderOpen}
          />
        )}
        {!loading && viewMode === "list" && (
          <MediaLibraryList
            grouping={grouping}
            kind={activeKind}
            onItemClick={onItemClick}
            onItemDragStart={onItemDragStart}
            onFolderOpen={handleFolderOpen}
          />
        )}
      </Box>
    </Paper>
  );
};

export default MediaLibraryPanel;
