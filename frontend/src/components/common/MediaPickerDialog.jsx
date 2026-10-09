// frontend/src/components/common/MediaPickerDialog.jsx
//
// Pick one or more images, videos or audio files from the library (Files),
// or upload new ones from disk into it. Uploads land in the Files folder for
// their kind (Images, Videos, Audio) as Documents, so whatever is picked here
// is a Document either way and is handed back as one.
//
//   <MediaPickerDialog open kind="video" max={2} onPick={(docs) => ...} onClose={...} />

import React, { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import {
  Box,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Grid,
  TextField,
  Typography,
} from "@mui/material";
import { ActionButton, Hint } from "../settings/ui";
import MediaTile from "../videoeditor/MediaTile";
import { uploadDroppedMedia } from "../videoeditor/useExternalDrop";
import { listAudioDocuments, listImageDocuments, listVideoDocuments } from "../../api/videoOverlayService";

const LISTERS = { image: listImageDocuments, video: listVideoDocuments, audio: listAudioDocuments };
const NOUNS = { image: "pictures", video: "clips", audio: "audio files" };
const ACCEPT = { image: "image/*", video: "video/*", audio: "audio/*" };

const MediaPickerDialog = ({ open, kind = "image", max = 1, title, onPick, onClose }) => {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState([]);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    setPicked([]);
    setFilter("");
    setError("");
    setLoading(true);
    (LISTERS[kind] || listImageDocuments)()
      .then((docs) => { if (alive) setItems(docs || []); })
      .catch((e) => { if (alive) setError(e.message || "Could not list the library."); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open, kind]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return q ? items.filter((d) => (d.filename || "").toLowerCase().includes(q)) : items;
  }, [items, filter]);

  const toggle = (doc) => {
    setPicked((prev) => {
      if (prev.some((d) => d.id === doc.id)) return prev.filter((d) => d.id !== doc.id);
      if (max === 1) return [doc];
      return prev.length >= max ? prev : [...prev, doc];
    });
  };

  const handleUpload = async (files) => {
    const list = Array.from(files || []).slice(0, Math.max(1, max));
    if (!list.length) return;
    setUploading(true);
    setError("");
    try {
      const docs = await uploadDroppedMedia(list, { http: axios });
      const ofKind = docs.filter((d) => d.kind === kind);
      if (ofKind.length < docs.length) setError(`Only ${NOUNS[kind]} go here; the other files were added to Files.`);
      if (ofKind.length) {
        onPick?.(ofKind);
        onClose?.();
      }
    } catch (e) {
      setError(e.response?.data?.error?.message || e.message || "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const pickedIds = new Set(picked.map((d) => d.id));

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>{title || `Choose ${NOUNS[kind]}`}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", mb: 1.5, flexWrap: "wrap" }}>
          <TextField
            size="small"
            placeholder="Filter by name"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            sx={{ flex: 1, minWidth: 180 }}
          />
          <ActionButton loading={uploading} onClick={() => inputRef.current?.click()}>
            Upload from disk
          </ActionButton>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPT[kind]}
            multiple={max > 1}
            hidden
            onChange={(e) => {
              handleUpload(e.target.files);
              e.target.value = "";
            }}
          />
        </Box>
        {error && (
          <Typography variant="caption" color="error" sx={{ display: "block", mb: 1 }}>
            {error}
          </Typography>
        )}
        {loading ? (
          <CircularProgress size={24} />
        ) : shown.length === 0 ? (
          <Hint>
            No {NOUNS[kind]} in Files yet{filter ? " match that name" : ""}. Upload one, or generate one in the Studio.
          </Hint>
        ) : (
          <Grid container spacing={1}>
            {shown.map((doc) => (
              <Grid item xs={6} sm={4} md={3} key={doc.id}>
                <MediaTile item={doc} kind={kind} variant="grid" selected={pickedIds.has(doc.id)} onClick={() => toggle(doc)} />
              </Grid>
            ))}
          </Grid>
        )}
      </DialogContent>
      <DialogActions>
        <Hint sx={{ mr: "auto", pl: 1 }}>
          {max > 1 ? `Up to ${max} more.` : "Pick one."}
        </Hint>
        <ActionButton onClick={onClose}>Cancel</ActionButton>
        <ActionButton
          kind={picked.length ? "primary" : "neutral"}
          disabled={!picked.length}
          onClick={() => {
            onPick?.(picked);
            onClose?.();
          }}
        >
          {picked.length > 1 ? `Use ${picked.length}` : "Use"}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default MediaPickerDialog;
