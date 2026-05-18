// frontend/src/components/videoeditor/MediaLibraryList.jsx
//
// Compact list presenter — folders first (when at the panel root), then
// items, both sorted by most-recent timestamp. Folders-first ordering
// here (vs. the grid's strict timestamp interleave) matches what most
// file managers do in detail view: containers float to the top so users
// can see the structure at a glance before scrolling into the contents.
import React from "react";
import { Stack, Typography } from "@mui/material";
import FolderTile from "./FolderTile";
import MediaTile from "./MediaTile";

const MediaLibraryList = ({ grouping, kind, onItemClick, onItemDragStart, onFolderOpen }) => {
  const { folders, ungrouped } = grouping;

  if (folders.length === 0 && ungrouped.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ p: 1, display: "block" }}>
        Nothing here yet. Generate via the Studio or import from Documents.
      </Typography>
    );
  }

  const sortedFolders = [...folders].sort((a, b) =>
    (b.latest_timestamp || "").localeCompare(a.latest_timestamp || ""),
  );
  const sortedItems = [...ungrouped].sort((a, b) =>
    (b.updated_at || b.uploaded_at || "").localeCompare(a.updated_at || a.uploaded_at || ""),
  );

  return (
    <Stack spacing={0.5}>
      {sortedFolders.map((g) => (
        <FolderTile
          key={`folder-${g.folder.id}`}
          group={g}
          variant="list"
          onOpen={onFolderOpen}
        />
      ))}
      {sortedItems.map((it) => (
        <MediaTile
          key={`${kind}-${it.id}`}
          item={it}
          kind={kind}
          variant="list"
          onClick={onItemClick}
          onDragStart={onItemDragStart}
        />
      ))}
    </Stack>
  );
};

export default MediaLibraryList;
