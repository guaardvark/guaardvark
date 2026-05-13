// Master soundtrack picker. One song per project. Accepts drag from
// MediaLibrary's Audio tab AND from the OS file browser (uploads as Document).
// Visually a compact horizontal slot, not a full panel.

import React from "react";
import { Box, Stack, Typography, IconButton, LinearProgress, Tooltip } from "@mui/material";
import { GraphicEq as AudioIcon, Close as CloseIcon } from "@mui/icons-material";
import { useExternalDrop } from "./useExternalDrop";

const SongSlot = ({ song, onSet, onClear }) => {
  const { onDrop: osDrop, onDragOver, uploading, progress } = useExternalDrop({
    folderName: "Audio",
    onUploaded: (docs) => {
      if (docs[0]) {
        onSet({
          documentId: docs[0].id,
          filename: docs[0].filename || docs[0].name || "(unnamed)",
          volume: 1.0,
        });
      }
    },
  });

  const handleDrop = (event) => {
    try {
      const raw = event.dataTransfer.getData("application/json");
      if (!raw) {
        osDrop(event);
        return;
      }
      const data = JSON.parse(raw);
      if (data.kind !== "audio") return;
      event.preventDefault();
      onSet({
        documentId: data.id,
        filename: data.filename,
        volume: 1.0,
      });
    } catch {
      osDrop(event);
    }
  };

  const isFilled = !!song;

  return (
    <Box
      onDrop={handleDrop}
      onDragOver={onDragOver}
      sx={{
        border: 2,
        borderStyle: "dashed",
        borderColor: isFilled ? "secondary.main" : "divider",
        borderRadius: 1,
        p: 1,
        backgroundColor: "background.paper",
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1}>
        <AudioIcon fontSize="small" color={isFilled ? "secondary" : "disabled"} />
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          {isFilled ? (
            <Typography variant="body2" sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={song.filename}>
              {song.filename}
            </Typography>
          ) : (
            <Typography variant="caption" color="text.secondary">
              Drag a song from the Library or your file browser
            </Typography>
          )}
        </Box>
        {isFilled && (
          <Tooltip title="Remove song">
            <IconButton size="small" onClick={onClear}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}
      </Stack>
      {uploading && (
        <Box sx={{ mt: 0.5 }}>
          <LinearProgress variant="determinate" value={progress} />
        </Box>
      )}
    </Box>
  );
};

export default SongSlot;
