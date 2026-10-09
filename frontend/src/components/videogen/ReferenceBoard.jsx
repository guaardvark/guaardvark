// frontend/src/components/videogen/ReferenceBoard.jsx
//
// The References input of the Video Generator: rows of pictures, clips and
// audio for a model that declares ref2v, sized from its ref_limits. Each tile
// shows the tag the prompt can name it by, a role in plain words and a name;
// names typed here are matched in the prompt when it is compiled. Files come
// from the library (MediaPickerDialog), a drop on the row, or an upload; all
// three end as Documents in Files.
//
// `onChange` must accept an updater function (a useState setter does): the
// clip probe resolves after the board may have changed again.

import React, { useCallback, useState } from "react";
import axios from "axios";
import {
  Box,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Close as CloseIcon,
  ChevronLeft as ChevronLeftIcon,
  ChevronRight as ChevronRightIcon,
  GraphicEq as AudioIcon,
} from "@mui/icons-material";
import { ActionButton, Cluster, Hint, Line } from "../settings/ui";
import MediaPickerDialog from "../common/MediaPickerDialog";
import FileDropOverlay from "../chat/FileDropOverlay";
import useFileDropZone from "../../hooks/useFileDropZone";
import { kindForFile, uploadDroppedMedia } from "../videoeditor/useExternalDrop";
import {
  KIND_OF_ROW,
  REF_KINDS,
  ROLE_OPTIONS,
  ROW_LABELS,
  boardFileCount,
  boardNames,
  boardTags,
  clipFramesAllowed,
  defaultClipAudio,
  entryFromDocument,
  roomInRow,
} from "./referenceBoardModel";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

const ROW_HELP = {
  images: "People, places, products or a look to keep. Give pictures of the same person the same name.",
  videos: "A clip to keep someone from, copy the motion of, edit, or continue. 2 to 15 seconds.",
  audios: "A voice for a character, music to use as is, or a sound to match.",
};

const TagChip = ({ tag, onInsert }) => (
  <Tooltip title="Insert into the prompt">
    <Box
      component="button"
      type="button"
      onClick={() => onInsert?.(tag)}
      sx={(theme) => ({
        border: 0,
        cursor: "pointer",
        font: "inherit",
        fontSize: "0.7rem",
        fontFamily: "monospace",
        px: 0.75,
        py: 0.25,
        borderRadius: "6px",
        color: theme.palette.primary.light,
        bgcolor: theme.palette.action.hover,
        "&:hover": { bgcolor: theme.palette.action.selected },
      })}
    >
      {tag}
    </Box>
  </Tooltip>
);

const Preview = ({ entry, row }) => {
  const sx = { width: "100%", height: 96, objectFit: "cover", display: "block", bgcolor: "action.hover" };
  if (row === "images") {
    return <Box component="img" src={entry.thumbnailUrl || entry.previewUrl} alt={entry.fileName} sx={sx} />;
  }
  if (row === "videos") {
    return (
      <Box
        component="video"
        src={entry.previewUrl}
        poster={entry.thumbnailUrl || undefined}
        muted
        preload="metadata"
        controls
        sx={sx}
      />
    );
  }
  return (
    <Box sx={{ ...sx, height: "auto", p: 1, display: "flex", flexDirection: "column", gap: 0.5, alignItems: "center" }}>
      <AudioIcon sx={{ color: "#9c27b0" }} />
      <Box component="audio" src={entry.previewUrl} controls preload="none" sx={{ width: "100%", height: 28 }} />
    </Box>
  );
};

const ReferenceTile = ({ row, entry, index, count, tags, names, limits, readS, onPatch, onMove, onRemove, onInsertTag, onPickSoundtrack }) => {
  const tag = tags[entry.key];
  const soundTag = tags[`${entry.key}:audio`];
  const shortest = limits?.video_seconds?.[0];
  const tooShort = row === "videos" && shortest && entry.durationS != null && entry.durationS < shortest;
  const separate = typeof entry.audio === "object" && entry.audio !== null;
  return (
    <Paper variant="outlined" sx={{ width: 208, overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <Preview entry={entry} row={row} />
      <Box sx={{ p: 1, display: "flex", flexDirection: "column", gap: 0.75 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <TagChip tag={tag} onInsert={onInsertTag} />
          <Typography variant="caption" color="text.secondary" noWrap title={entry.fileName} sx={{ flex: 1, minWidth: 0 }}>
            {entry.fileName}
          </Typography>
        </Box>
        <FormControl size="small" fullWidth>
          <InputLabel>Use it to</InputLabel>
          <Select
            value={entry.role}
            label="Use it to"
            onChange={(e) => onPatch(row === "videos" && !entry.audioPicked
              ? { role: e.target.value, audio: defaultClipAudio(e.target.value) }
              : { role: e.target.value })}
          >
            {ROLE_OPTIONS[row].map((o) => (
              <MenuItem key={o.value} value={o.value}>{o.label}</MenuItem>
            ))}
          </Select>
        </FormControl>
        {row !== "audios" && entry.role !== "first_frame" && (
          <TextField
            size="small"
            label="Who or what is this?"
            placeholder={row === "images" ? "e.g. Maya, the red car" : "optional"}
            value={entry.name}
            onChange={(e) => onPatch({ name: e.target.value })}
          />
        )}
        {row === "images" && entry.role === "detail" && (
          <TextField
            size="small"
            label="Which detail?"
            placeholder="e.g. only the leather jacket"
            value={entry.note}
            onChange={(e) => onPatch({ note: e.target.value })}
          />
        )}
        {row === "audios" && entry.role === "voice" && (
          <TextField
            size="small"
            label="Whose voice?"
            placeholder={names[0] ? `e.g. ${names[0]}` : "a name from the pictures"}
            value={entry.speaker}
            onChange={(e) => onPatch({ speaker: e.target.value })}
            helperText={names.length && entry.speaker && !names.some((n) => n.toLowerCase() === entry.speaker.trim().toLowerCase())
              ? `Not a name on the board (${names.join(", ")})` : undefined}
          />
        )}
        {row === "videos" && (
          <>
            <FormControl size="small" fullWidth>
              <InputLabel>Sound</InputLabel>
              <Select
                label="Sound"
                value={separate ? "separate" : entry.audio || "own"}
                onChange={(e) => {
                  if (e.target.value === "separate") onPickSoundtrack();
                  else onPatch({ audio: e.target.value, audioPicked: true });
                }}
              >
                <MenuItem value="own">Its own sound</MenuItem>
                <MenuItem value="none">No sound</MenuItem>
                <MenuItem value="separate">{separate ? `Track: ${entry.audio.fileName}` : "A separate track…"}</MenuItem>
              </Select>
            </FormControl>
            <Hint>
              {entry.durationS != null ? `${entry.durationS.toFixed(1)} s` : "Length unknown"}
              {entry.audio === "own" && entry.hasAudio === false ? " · this clip has no sound" : ""}
              {soundTag ? ` · its sound is ${soundTag}` : ""}
              {readS && entry.durationS != null && entry.durationS > readS + 0.05
                ? ` · the model reads its first ${readS.toFixed(1)} s` : ""}
            </Hint>
            {tooShort && <Typography variant="caption" color="error">Clips need at least {shortest} s.</Typography>}
          </>
        )}
        <Box sx={{ display: "flex", alignItems: "center" }}>
          <Tooltip title="Earlier (renumbers the tags)">
            <span>
              <IconButton size="small" disabled={index === 0} onClick={() => onMove(-1)} aria-label="Move earlier">
                <ChevronLeftIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Later (renumbers the tags)">
            <span>
              <IconButton size="small" disabled={index === count - 1} onClick={() => onMove(1)} aria-label="Move later">
                <ChevronRightIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
          <Box sx={{ flex: 1 }} />
          <Tooltip title="Remove">
            <IconButton size="small" onClick={onRemove} aria-label="Remove reference">
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>
    </Paper>
  );
};

const ReferenceRow = ({ row, board, limits, readS, tags, names, onAdd, onUpdate, onOpenPicker, onInsertTag, onError, uploading }) => {
  const kind = KIND_OF_ROW[row];
  const entries = board[row] || [];
  const cap = limits?.[row];
  const room = roomInRow(board, row, limits);
  const drop = useFileDropZone({
    enabled: room > 0 && !uploading,
    onFiles: (files) => {
      const fit = files.filter((f) => kindForFile(f.name, "") === kind);
      if (fit.length < files.length) onError?.(`Only ${ROW_LABELS[row].toLowerCase()} go in this row.`);
      if (fit.length) onAdd(row, fit.slice(0, room));
    },
  });
  const patch = (key, change) => onUpdate((b) => ({ ...b, [row]: b[row].map((e) => (e.key === key ? { ...e, ...change } : e)) }));
  const move = (i, delta) => onUpdate((b) => {
    const next = [...b[row]];
    const [item] = next.splice(i, 1);
    next.splice(i + delta, 0, item);
    return { ...b, [row]: next };
  });
  const remove = (key) => onUpdate((b) => ({ ...b, [row]: b[row].filter((e) => e.key !== key) }));

  return (
    <Cluster label={`${ROW_LABELS[row]} ${entries.length}${cap != null ? `/${cap}` : ""}`} help={ROW_HELP[row]}>
      <Box {...drop.dropProps} sx={{ position: "relative", display: "flex", gap: 1, flexWrap: "wrap", alignItems: "stretch" }}>
        <FileDropOverlay active={drop.isDragActive} label={`Drop ${ROW_LABELS[row].toLowerCase()} here`} compact />
        {entries.map((entry, i) => (
          <ReferenceTile
            key={entry.key}
            row={row}
            entry={entry}
            index={i}
            count={entries.length}
            tags={tags}
            names={names}
            limits={limits}
            readS={readS}
            onPatch={(change) => patch(entry.key, change)}
            onMove={(delta) => move(i, delta)}
            onRemove={() => remove(entry.key)}
            onInsertTag={onInsertTag}
            onPickSoundtrack={() => onOpenPicker({ row: "audios", soundtrackFor: entry.key, max: 1 })}
          />
        ))}
        {room > 0 && (
          <Box
            sx={{
              width: 208,
              minHeight: entries.length ? 96 : 64,
              border: "2px dashed",
              borderColor: "divider",
              borderRadius: "8px",
              p: 1.5,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              gap: 1,
            }}
          >
            <Hint>Drop {ROW_LABELS[row].toLowerCase()} here, or</Hint>
            <Line>
              <ActionButton loading={uploading} onClick={() => onOpenPicker({ row, max: room })}>
                Add from Files
              </ActionButton>
            </Line>
          </Box>
        )}
      </Box>
    </Cluster>
  );
};

/**
 * @param {object} [fit] {budget, renderFrames, width, height, fps}: the card's
 *   measured board budget (tier_defaults.ref_token_budget) and the render's
 *   length and size, so each clip shows how much of it the model reads.
 */
const ReferenceBoard = ({ value, onChange, limits, fit, onInsertTag, onError }) => {
  const [picker, setPicker] = useState(null); // {row, max, soundtrackFor?}
  const [uploadingRow, setUploadingRow] = useState(null);
  const board = value || { images: [], videos: [], audios: [] };
  const tags = boardTags(board);
  const names = boardNames(board);
  const longest = limits?.video_seconds?.[1] ?? null;
  const shareFrames = clipFramesAllowed(fit?.budget, fit?.renderFrames, fit?.width, fit?.height,
    board.images.length, board.videos.length);
  const share = shareFrames == null ? null : shareFrames / (fit?.fps || 24);
  const readS = [longest, share].filter((x) => x != null && x > 0).reduce((a, b) => Math.min(a, b), Infinity);

  // Length and sound of each new clip, so the tags and the 2 s floor are right
  // before anything is queued.
  const probeClips = useCallback((entries) => {
    entries.forEach(async (entry) => {
      try {
        const res = await axios.post(`${API_BASE}/batch-video/references/probe`, { ref: entry.ref, kind: "video" });
        const info = res.data?.data || {};
        onChange((b) => ({
          ...b,
          videos: b.videos.map((v) => (v.key === entry.key
            ? { ...v, durationS: info.duration_s ?? null, hasAudio: info.has_audio ?? null }
            : v)),
        }));
      } catch (e) {
        onError?.(e.response?.data?.error?.message || e.response?.data?.message || `Could not read ${entry.fileName}.`);
      }
    });
  }, [onChange, onError]);

  const addDocuments = useCallback((row, docs) => {
    const entries = docs.map((d) => entryFromDocument(d, row));
    onChange((b) => ({ ...b, [row]: [...b[row], ...entries].slice(0, (b[row].length + roomInRow(b, row, limits))) }));
    if (row === "videos") probeClips(entries);
  }, [onChange, limits, probeClips]);

  const addFiles = useCallback(async (row, files) => {
    setUploadingRow(row);
    try {
      const docs = await uploadDroppedMedia(files, { http: axios });
      addDocuments(row, docs.filter((d) => d.kind === KIND_OF_ROW[row]));
    } catch (e) {
      onError?.(e.response?.data?.error?.message || e.message || "Upload failed.");
    } finally {
      setUploadingRow(null);
    }
  }, [addDocuments, onError]);

  const handlePick = (docs) => {
    if (!picker || !docs.length) return;
    if (picker.soundtrackFor) {
      const doc = docs[0];
      onChange((b) => ({
        ...b,
        videos: b.videos.map((v) => (v.key === picker.soundtrackFor
          ? { ...v, audio: { ref: String(doc.id), fileName: doc.filename }, audioPicked: true }
          : v)),
      }));
      return;
    }
    addDocuments(picker.row, docs);
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {REF_KINDS.map((row) => (
        <ReferenceRow
          key={row}
          row={row}
          board={board}
          limits={limits}
          readS={Number.isFinite(readS) ? readS : null}
          tags={tags}
          names={names}
          onAdd={addFiles}
          onUpdate={onChange}
          onOpenPicker={setPicker}
          onInsertTag={onInsertTag}
          onError={onError}
          uploading={uploadingRow === row}
        />
      ))}
      {limits?.files != null && (
        <Hint>{boardFileCount(board)} of {limits.files} files. Each clip&apos;s separate track counts as one.</Hint>
      )}
      <MediaPickerDialog
        open={!!picker}
        kind={picker ? KIND_OF_ROW[picker.row] : "image"}
        max={picker?.max || 1}
        title={picker?.soundtrackFor ? "Choose the clip's soundtrack" : undefined}
        onPick={handlePick}
        onClose={() => setPicker(null)}
      />
    </Box>
  );
};

export default ReferenceBoard;
