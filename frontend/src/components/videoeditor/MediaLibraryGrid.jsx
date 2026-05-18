// frontend/src/components/videoeditor/MediaLibraryGrid.jsx
//
// Grid presenter — folders intermixed with items, all sorted by most-
// recent timestamp descending. Exists as a sibling to MediaLibraryList
// so MediaLibraryPanel can swap presenters without touching state.
import React from "react";
import { Grid, Typography } from "@mui/material";
import FolderTile from "./FolderTile";
import MediaTile from "./MediaTile";
import { mergeTiles } from "./groupByFolder";

const MediaLibraryGrid = ({ grouping, kind, onItemClick, onItemDragStart, onFolderOpen }) => {
  const tiles = mergeTiles(grouping);

  if (tiles.length === 0) {
    return (
      <Typography variant="caption" color="text.secondary" sx={{ p: 1, display: "block" }}>
        Nothing here yet. Generate via the Studio or import from Documents.
      </Typography>
    );
  }

  return (
    <Grid container spacing={1}>
      {tiles.map((tile) =>
        tile.kind === "folder" ? (
          <Grid item xs={6} key={`folder-${tile.group.folder.id}`}>
            <FolderTile group={tile.group} variant="grid" onOpen={onFolderOpen} />
          </Grid>
        ) : (
          <Grid item xs={6} key={`${kind}-${tile.item.id}`}>
            <MediaTile
              item={tile.item}
              kind={kind}
              variant="grid"
              onClick={onItemClick}
              onDragStart={onItemDragStart}
            />
          </Grid>
        ),
      )}
    </Grid>
  );
};

export default MediaLibraryGrid;
