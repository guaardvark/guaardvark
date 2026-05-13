// Project Bin — multi-clip pool for the auto-edit. Accepts drops from
// MediaLibraryPanel (videos) and from the OS file browser. Drag-out is not
// supported; the bin owns its clips. Remove via the X on each tile.

import React from "react";
import { Box, Stack, Typography, LinearProgress, Alert, IconButton, Tooltip } from "@mui/material";
import { VideoLibrary as VideoIcon, FolderOpen as OpenFolderIcon } from "@mui/icons-material";
import BinClipTile from "./BinClipTile";
import { useExternalDrop } from "./useExternalDrop";

const BinPanel = ({
  binClips,
  selectedClipId,
  onSelect,
  onAdd,        // (BinClip) => void   — single clip add (from library drag)
  onAddMany,    // (BinClip[]) => void — bulk add (from OS upload)
  onRemove,
  warningsByClipId = {},  // {clipId: warning text}
}) => {
  // OS file drop: upload → Document → bin tile.
  const { onDrop, onDragOver, uploading, progress, error } = useExternalDrop({
    folderName: "Videos",
    onUploaded: (docs) => {
      const newClips = docs.map((d) => ({
        clipId: `doc${d.id}`,
        documentId: d.id,
        filename: d.filename || d.name || "(unnamed)",
        keptRanges: null,
        durationSeconds: null,
      }));
      onAddMany(newClips);
    },
  });

  // MediaLibrary drag drop: dataTransfer carries { id, kind, filename }.
  const handleLibraryDrop = (event) => {
    try {
      const raw = event.dataTransfer.getData("application/json");
      if (!raw) {
        // Maybe it's an OS file drop — let useExternalDrop handle it
        onDrop(event);
        return;
      }
      const data = JSON.parse(raw);
      if (data.kind !== "video") return; // bin is video-only for now
      event.preventDefault();
      onAdd({
        clipId: `doc${data.id}`,
        documentId: data.id,
        filename: data.filename,
        keptRanges: null,
        durationSeconds: null,
      });
    } catch {
      // Not JSON — treat as OS file drop
      onDrop(event);
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1, py: 0.75, borderBottom: 1, borderColor: "divider" }}>
        <VideoIcon fontSize="small" />
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>Project Bin</Typography>
        <Typography variant="caption" color="text.secondary">{binClips.length} clip{binClips.length !== 1 ? "s" : ""}</Typography>
      </Stack>

      <Box
        onDrop={handleLibraryDrop}
        onDragOver={onDragOver}
        sx={{
          flexGrow: 1,
          overflow: "auto",
          p: 1,
          backgroundColor: "background.default",
          border: 2,
          borderColor: "transparent",
          borderStyle: "dashed",
          "&.drag-over": { borderColor: "primary.main" },
        }}
      >
        {uploading && (
          <Box sx={{ mb: 1 }}>
            <Typography variant="caption">Uploading...</Typography>
            <LinearProgress variant="determinate" value={progress} />
          </Box>
        )}
        {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
        {binClips.length === 0 ? (
          <Stack alignItems="center" justifyContent="center" sx={{ height: "100%", color: "text.secondary" }}>
            <OpenFolderIcon sx={{ fontSize: 40, opacity: 0.4 }} />
            <Typography variant="caption">Drag clips from the Library or your file browser</Typography>
          </Stack>
        ) : (
          <Stack spacing={1}>
            {binClips.map((c) => (
              <BinClipTile
                key={c.clipId}
                clip={c}
                selected={selectedClipId === c.clipId}
                onSelect={onSelect}
                onRemove={onRemove}
                warning={warningsByClipId[c.clipId]}
              />
            ))}
          </Stack>
        )}
      </Box>
    </Box>
  );
};

export default BinPanel;
