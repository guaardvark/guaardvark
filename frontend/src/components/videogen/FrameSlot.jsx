// frontend/src/components/videogen/FrameSlot.jsx
//
// One picture slot for a frame the clip must hit (the end frame today): a
// thumbnail when filled, else a drop target with Upload and Add from Files.
// `value` is {ref, name, thumbnailUrl}; ref is a server path (an upload) or a
// library document id (a pick), both of which the generate routes resolve.

import React, { useRef, useState } from "react";
import { Box, IconButton, Tooltip, Typography } from "@mui/material";
import { Close as CloseIcon } from "@mui/icons-material";
import { ActionButton, Hint, Line } from "../settings/ui";
import MediaPickerDialog from "../common/MediaPickerDialog";
import FileDropOverlay from "../chat/FileDropOverlay";
import useFileDropZone from "../../hooks/useFileDropZone";

const FrameSlot = ({ label, value, onChange, onUploadFile, uploading = false, hint }) => {
  const [pickerOpen, setPickerOpen] = useState(false);
  const inputRef = useRef(null);
  const drop = useFileDropZone({
    enabled: !uploading,
    onFiles: (files) => {
      const image = files.find((f) => (f.type || "").startsWith("image/"));
      if (image) onUploadFile?.(image);
    },
  });

  return (
    <Box {...drop.dropProps} sx={{ position: "relative", width: 208 }}>
      <FileDropOverlay active={drop.isDragActive} label={`Drop the ${label.toLowerCase()} here`} compact />
      {value ? (
        <Box sx={{ position: "relative", borderRadius: "8px", overflow: "hidden", border: 1, borderColor: "divider" }}>
          <Box component="img" src={value.thumbnailUrl} alt={value.name} sx={{ width: "100%", height: 117, objectFit: "cover", display: "block" }} />
          <Box sx={{ px: 1, py: 0.5, display: "flex", alignItems: "center", gap: 0.5 }}>
            <Typography variant="caption" sx={{ fontWeight: 600 }}>{label}</Typography>
            <Typography variant="caption" color="text.secondary" noWrap title={value.name} sx={{ flex: 1, minWidth: 0 }}>
              {value.name}
            </Typography>
            <Tooltip title={`Remove the ${label.toLowerCase()}`}>
              <IconButton size="small" onClick={() => onChange(null)} aria-label={`Remove the ${label.toLowerCase()}`}>
                <CloseIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Box>
        </Box>
      ) : (
        <Box
          sx={{
            border: "2px dashed",
            borderColor: "divider",
            borderRadius: "8px",
            p: 1.5,
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Typography variant="caption" sx={{ fontWeight: 600 }}>{label}</Typography>
          {hint && <Hint>{hint}</Hint>}
          <Line>
            <ActionButton loading={uploading} onClick={() => inputRef.current?.click()}>Upload</ActionButton>
            <ActionButton onClick={() => setPickerOpen(true)}>Add from Files</ActionButton>
          </Line>
        </Box>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(e) => {
          if (e.target.files?.[0]) onUploadFile?.(e.target.files[0]);
          e.target.value = "";
        }}
      />
      <MediaPickerDialog
        open={pickerOpen}
        kind="image"
        max={1}
        title={`Choose the ${label.toLowerCase()}`}
        onPick={(docs) => {
          const doc = docs[0];
          if (doc) {
            onChange({
              ref: String(doc.id),
              name: doc.filename,
              thumbnailUrl: doc.thumbnail_url || `/api/files/document/${doc.id}/download`,
            });
          }
        }}
        onClose={() => setPickerOpen(false)}
      />
    </Box>
  );
};

export default FrameSlot;
