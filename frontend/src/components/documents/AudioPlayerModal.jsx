// In-app audio preview modal — opens when an .wav/.mp3/etc row is clicked
// in DocumentsPage. Native HTML5 <audio controls> for now; a static-waveform
// canvas can replace the audio element later without changing the surrounding
// modal shell or the click-handler contract in DocumentsPage.

import React from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Box,
  Typography,
  IconButton,
  Chip,
  Stack,
  Tooltip,
} from "@mui/material";
import { Close as CloseIcon, MusicNote as AudioIcon } from "@mui/icons-material";

const API_BASE = "/api/files";

// Pull whichever metadata fields the backend ships with the file. Different
// generators (audio_foundry, future imports, manual uploads) populate
// different keys, so we look in a couple of likely shapes.
const getMeta = (file) => {
  if (!file) return {};
  // Document.to_dict() returns parsed JSON under `metadata`.
  return file.metadata || file.file_metadata || {};
};

const formatDuration = (seconds) => {
  if (!seconds && seconds !== 0) return null;
  const total = Math.round(Number(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
};

const formatSampleRate = (sr) => {
  if (!sr) return null;
  return sr >= 1000 ? `${(sr / 1000).toFixed(sr % 1000 === 0 ? 0 : 1)} kHz` : `${sr} Hz`;
};

const AudioPlayerModal = ({ open, onClose, file }) => {
  if (!open || !file) return null;

  const filename = file.filename || file.name || "audio";
  const meta = getMeta(file);
  // SAO/voice/music backends each store a prompt-like field; fall back through
  // the most likely names so we don't have to special-case per backend here.
  const prompt = meta.prompt || meta.text || meta.style_prompt || meta.lyrics;
  const backend = meta.backend || meta.model;
  const seed = meta.seed ?? null;
  const duration = formatDuration(meta.actual_duration_s ?? meta.duration_s ?? meta.requested_duration_s);
  const sampleRate = formatSampleRate(meta.sample_rate);

  const audioUrl = `${API_BASE}/document/${file.id}/download?v=${file.updated_at || Date.now()}`;
  const handleDownload = () => {
    // Pop a fresh tab; the inline-allowlist only kicks in for the player —
    // a "Save As" right-click in the new tab still works as expected.
    window.open(audioUrl, "_blank");
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pr: 6, display: "flex", alignItems: "center", gap: 1 }}>
        <AudioIcon fontSize="small" />
        <Box sx={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {filename}
        </Box>
        <Tooltip title="Close">
          <IconButton
            aria-label="close"
            onClick={onClose}
            sx={{ position: "absolute", right: 8, top: 8 }}
          >
            <CloseIcon />
          </IconButton>
        </Tooltip>
      </DialogTitle>

      <DialogContent dividers>
        {prompt && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ mb: 2, fontStyle: "italic" }}
          >
            “{prompt}”
          </Typography>
        )}

        <Box sx={{ display: "flex", justifyContent: "center", py: 1 }}>
          <audio
            // Safe to autoplay — user clicked to open this modal, so the
            // browser's autoplay policy treats it as user-gesture-initiated.
            autoPlay
            controls
            preload="auto"
            src={audioUrl}
            style={{ width: "100%" }}
          />
        </Box>

        {(backend || seed !== null || duration || sampleRate) && (
          <Stack direction="row" spacing={1} sx={{ mt: 2, flexWrap: "wrap", gap: 1 }}>
            {duration && <Chip size="small" label={duration} />}
            {sampleRate && <Chip size="small" label={sampleRate} />}
            {backend && <Chip size="small" label={backend} variant="outlined" />}
            {seed !== null && <Chip size="small" label={`seed: ${seed}`} variant="outlined" />}
          </Stack>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleDownload}>Open in new tab</Button>
        <Button onClick={onClose} variant="contained">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default AudioPlayerModal;
