// frontend/src/pages/VideoEditorPage.jsx
//
// Shotcut-lite editor — preview window with overlay layer + media library
// panel + multi-track timeline + properties panel. Plan reference:
// plans/2026-04-29-video-editor.md.
//
// Phase 5 ships the layout + state model + click-to-add wiring. Phase 6
// adds drag-and-drop. Phase 7 wires the render-timeline endpoint.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  Paper,
  Typography,
  Stack,
  Button,
  IconButton,
  TextField,
  Slider,
  Chip,
  Divider,
  CircularProgress,
  Alert,
} from "@mui/material";
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  AutoFixHigh as RenderIcon,
  Delete as DeleteIcon,
  TextFields as TextIcon,
  MovieFilter as VideoIcon,
  GraphicEq as AudioIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import { listVideoDocuments, listAudioDocuments, renderTimeline } from "../api/videoOverlayService";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Track lane colors — keep video / text / audio visually distinct.
const TRACK_COLORS = {
  video: "#2196f3",
  text: "#ff9800",
  audio: "#9c27b0",
};

// Initial blank timeline state. Phase 6 will let users drop multiple items
// in here from the media library; Phase 7 sends this to /api/video-overlay/render-timeline.
const _emptyTimeline = () => ({
  video: null,            // { documentId, trimStart, trimEnd }
  textElements: [],       // [{ id, text, fontSize, fontColor, x, y, rotation, startSeconds, endSeconds }]
  audio: null,            // { documentId, volume, startOffset }
});

const VideoEditorPage = () => {
  const [timeline, setTimeline] = useState(_emptyTimeline());
  const [mediaLibrary, setMediaLibrary] = useState([]);
  const [audioLibrary, setAudioLibrary] = useState([]);
  const [loadingMedia, setLoadingMedia] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);  // {type, id} for properties panel
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderResult, setRenderResult] = useState(null);
  const [error, setError] = useState(null);

  // Pull video + audio Documents into the media library. Phase 6 of the
  // editor plan — both lists feed the drag-drop palette.
  useEffect(() => {
    let cancelled = false;
    setLoadingMedia(true);
    Promise.all([listVideoDocuments(), listAudioDocuments()])
      .then(([videos, audios]) => {
        if (!cancelled) {
          setMediaLibrary(videos);
          setAudioLibrary(audios);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          console.error("VideoEditorPage: media list failed:", e);
          setError("Could not load media library.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingMedia(false);
      });
    return () => { cancelled = true; };
  }, []);

  // Click a media item → add it to the timeline. Phase 6 replaces this
  // with drag-and-drop into specific track lanes.
  const handleAddMedia = (mediaItem) => {
    setTimeline((prev) => ({
      ...prev,
      video: {
        documentId: mediaItem.id,
        documentFilename: mediaItem.filename,
        trimStart: 0,
        trimEnd: null,  // null = use full duration
      },
    }));
  };

  // Click on the preview to add a text element at that point. Phase 3 of
  // the editor plan adds drag/resize/rotate handles via react-rnd or
  // react-moveable; for now click-to-place + edit-in-properties.
  const handleAddText = useCallback((x, y) => {
    const newId = `text_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    setTimeline((prev) => ({
      ...prev,
      textElements: [
        ...prev.textElements,
        {
          id: newId,
          text: "Sample text",
          fontSize: 48,
          fontColor: "white",
          x: x ?? 320,
          y: y ?? 240,
          rotation: 0,
          startSeconds: 0,
          endSeconds: null,
        },
      ],
    }));
    setSelectedItem({ type: "text", id: newId });
  }, []);

  const handleDeleteText = (textId) => {
    setTimeline((prev) => ({
      ...prev,
      textElements: prev.textElements.filter((t) => t.id !== textId),
    }));
    if (selectedItem?.type === "text" && selectedItem.id === textId) {
      setSelectedItem(null);
    }
  };

  const handleUpdateText = (textId, patch) => {
    setTimeline((prev) => ({
      ...prev,
      textElements: prev.textElements.map((t) =>
        t.id === textId ? { ...t, ...patch } : t,
      ),
    }));
  };

  const previewVideoUrl = useMemo(
    () => (timeline.video?.documentId
      ? `${API_BASE}/files/document/${timeline.video.documentId}/download`
      : null),
    [timeline.video],
  );

  const selectedText = useMemo(() => {
    if (selectedItem?.type !== "text") return null;
    return timeline.textElements.find((t) => t.id === selectedItem.id) || null;
  }, [selectedItem, timeline.textElements]);

  const handleRender = async () => {
    if (!timeline.video) {
      setError("Add a video to the timeline first.");
      return;
    }
    setRendering(true);
    setError(null);
    setRenderResult(null);
    try {
      const payload = {
        video_document_id: timeline.video.documentId,
        video_trim_start: timeline.video.trimStart || 0,
        video_trim_end: timeline.video.trimEnd,  // null = full duration
        text_elements: timeline.textElements.map((t) => ({
          text: t.text,
          fontSize: t.fontSize,
          fontColor: t.fontColor,
          x: t.x,
          y: t.y,
          rotation: t.rotation,
          startSeconds: t.startSeconds,
          endSeconds: t.endSeconds,
        })),
        audio_document_id: timeline.audio?.documentId || null,
        audio_volume: timeline.audio?.volume ?? 1.0,
      };
      const newDoc = await renderTimeline(payload);
      setRenderResult({
        ...newDoc,
        full_url: `${API_BASE}/files/document/${newDoc.id}/download`,
      });
    } catch (e) {
      console.error("VideoEditorPage: render failed:", e);
      setError(e.response?.data?.error?.message || e.response?.data?.message || e.message || "Render failed");
    } finally {
      setRendering(false);
    }
  };

  // HTML5 drag-and-drop. dataTransfer carries the media-library row id +
  // kind ('video' or 'audio') so the timeline drop handler knows where to put it.
  const handleDragStartMedia = (e, mediaItem, kind) => {
    e.dataTransfer.setData("application/json", JSON.stringify({ id: mediaItem.id, kind, filename: mediaItem.filename }));
    e.dataTransfer.effectAllowed = "copy";
  };

  const handleDropOnVideoTrack = (e) => {
    e.preventDefault();
    try {
      const data = JSON.parse(e.dataTransfer.getData("application/json"));
      if (data.kind !== "video") return;
      setTimeline((prev) => ({
        ...prev,
        video: { documentId: data.id, documentFilename: data.filename, trimStart: 0, trimEnd: null },
      }));
    } catch {}
  };

  const handleDropOnAudioTrack = (e) => {
    e.preventDefault();
    try {
      const data = JSON.parse(e.dataTransfer.getData("application/json"));
      if (data.kind !== "audio") return;
      setTimeline((prev) => ({
        ...prev,
        audio: { documentId: data.id, documentFilename: data.filename, volume: 1.0, startOffset: 0 },
      }));
    } catch {}
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  };

  return (
    <PageLayout title="Video Editor" subtitle="Compose videos with overlays, audio, and text">
      <Box sx={{ display: "flex", flexDirection: "column", height: "calc(100vh - 96px)", p: 2, gap: 2 }}>
        {/* Top half: preview + media library + properties */}
        <Box sx={{ display: "flex", gap: 2, flex: 1, minHeight: 0 }}>
          {/* Media library — left */}
          <Paper elevation={2} sx={{ width: 240, p: 2, overflow: "auto" }}>
            <Typography variant="subtitle2" fontWeight="bold" mb={1}>Media Library</Typography>
            {loadingMedia && <CircularProgress size={20} />}
            {!loadingMedia && mediaLibrary.length === 0 && (
              <Typography variant="caption" color="text.secondary">
                No video files yet. Generate one or import from Documents.
              </Typography>
            )}
            <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>Videos</Typography>
            <Stack spacing={1} mb={2}>
              {mediaLibrary.map((m) => (
                <Paper
                  key={`v-${m.id}`}
                  variant="outlined"
                  draggable
                  onDragStart={(e) => handleDragStartMedia(e, m, "video")}
                  sx={{ p: 1, cursor: "grab", "&:hover": { bgcolor: "action.hover" }, "&:active": { cursor: "grabbing" } }}
                  onClick={() => handleAddMedia(m)}
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <VideoIcon fontSize="small" color="primary" />
                    <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                      <Typography variant="caption" sx={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {m.filename}
                      </Typography>
                      {m.size != null && (
                        <Typography variant="caption" color="text.secondary">
                          {(m.size / 1024 / 1024).toFixed(1)} MB
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                </Paper>
              ))}
            </Stack>

            <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>Audio</Typography>
            <Stack spacing={1}>
              {audioLibrary.length === 0 && !loadingMedia && (
                <Typography variant="caption" color="text.secondary">
                  No audio files yet. Generate via Audio Studio.
                </Typography>
              )}
              {audioLibrary.map((a) => (
                <Paper
                  key={`a-${a.id}`}
                  variant="outlined"
                  draggable
                  onDragStart={(e) => handleDragStartMedia(e, a, "audio")}
                  sx={{ p: 1, cursor: "grab", "&:hover": { bgcolor: "action.hover" } }}
                >
                  <Stack direction="row" spacing={1} alignItems="center">
                    <AudioIcon fontSize="small" sx={{ color: TRACK_COLORS.audio }} />
                    <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                      <Typography variant="caption" sx={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {a.filename}
                      </Typography>
                      {a.size != null && (
                        <Typography variant="caption" color="text.secondary">
                          {(a.size / 1024 / 1024).toFixed(1)} MB
                        </Typography>
                      )}
                    </Box>
                  </Stack>
                </Paper>
              ))}
            </Stack>
          </Paper>

          {/* Preview window — center */}
          <Paper elevation={4} sx={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
            <Box sx={{ position: "relative", flex: 1, bgcolor: "#000", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
              {previewVideoUrl ? (
                <video
                  src={previewVideoUrl}
                  controls
                  style={{ maxWidth: "100%", maxHeight: "100%", display: "block" }}
                />
              ) : (
                <Stack spacing={1} alignItems="center" sx={{ color: "rgba(255,255,255,0.5)" }}>
                  <VideoIcon sx={{ fontSize: 48 }} />
                  <Typography variant="caption">Click a video in the library to begin</Typography>
                </Stack>
              )}

              {/* Text overlays — absolutely positioned. Phase 3 of editor plan
                  will make these draggable/resizable via react-rnd. */}
              {timeline.textElements.map((t) => (
                <Box
                  key={t.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedItem({ type: "text", id: t.id });
                  }}
                  sx={{
                    position: "absolute",
                    left: `${t.x}px`,
                    top: `${t.y}px`,
                    transform: `rotate(${t.rotation}deg)`,
                    color: t.fontColor,
                    fontSize: `${t.fontSize}px`,
                    fontWeight: 700,
                    textShadow: "1px 1px 3px rgba(0,0,0,0.8)",
                    cursor: "pointer",
                    border: selectedItem?.type === "text" && selectedItem.id === t.id
                      ? "1px dashed yellow" : "1px dashed transparent",
                    padding: "2px 4px",
                    userSelect: "none",
                  }}
                >
                  {t.text}
                </Box>
              ))}
            </Box>

            {/* Toolbar under the preview */}
            <Stack direction="row" spacing={1} alignItems="center" sx={{ p: 1, borderTop: 1, borderColor: "divider" }}>
              <IconButton onClick={() => setPreviewPlaying(!previewPlaying)} size="small">
                {previewPlaying ? <PauseIcon /> : <PlayIcon />}
              </IconButton>
              <Button size="small" variant="outlined" startIcon={<TextIcon />} onClick={() => handleAddText()}>
                Add Text
              </Button>
              <Box sx={{ flexGrow: 1 }} />
              <Button
                variant="contained"
                startIcon={rendering ? <CircularProgress size={20} color="inherit" /> : <RenderIcon />}
                onClick={handleRender}
                disabled={!timeline.video || rendering}
              >
                {rendering ? "Rendering..." : "Render"}
              </Button>
              {renderResult && (
                <Button
                  size="small"
                  variant="text"
                  onClick={() => window.open(renderResult.full_url, "_blank")}
                >
                  Download {renderResult.filename}
                </Button>
              )}
            </Stack>
          </Paper>

          {/* Properties panel — right */}
          <Paper elevation={2} sx={{ width: 280, p: 2, overflow: "auto" }}>
            <Typography variant="subtitle2" fontWeight="bold" mb={1}>Properties</Typography>
            {!selectedItem && (
              <Typography variant="caption" color="text.secondary">
                Select a text overlay or timeline clip.
              </Typography>
            )}
            {selectedText && (
              <Stack spacing={2}>
                <TextField
                  size="small"
                  fullWidth
                  label="Text"
                  value={selectedText.text}
                  onChange={(e) => handleUpdateText(selectedText.id, { text: e.target.value })}
                />
                <Box>
                  <Typography variant="caption">Font size: {selectedText.fontSize}</Typography>
                  <Slider
                    value={selectedText.fontSize}
                    min={12}
                    max={144}
                    onChange={(_e, v) => handleUpdateText(selectedText.id, { fontSize: v })}
                  />
                </Box>
                <TextField
                  size="small"
                  fullWidth
                  label="Color"
                  value={selectedText.fontColor}
                  onChange={(e) => handleUpdateText(selectedText.id, { fontColor: e.target.value })}
                />
                <Box>
                  <Typography variant="caption">Rotation: {selectedText.rotation}°</Typography>
                  <Slider
                    value={selectedText.rotation}
                    min={-180}
                    max={180}
                    onChange={(_e, v) => handleUpdateText(selectedText.id, { rotation: v })}
                  />
                </Box>
                <Stack direction="row" spacing={1}>
                  <TextField
                    size="small"
                    label="X (px)"
                    type="number"
                    value={selectedText.x}
                    onChange={(e) => handleUpdateText(selectedText.id, { x: Number(e.target.value) || 0 })}
                  />
                  <TextField
                    size="small"
                    label="Y (px)"
                    type="number"
                    value={selectedText.y}
                    onChange={(e) => handleUpdateText(selectedText.id, { y: Number(e.target.value) || 0 })}
                  />
                </Stack>
                <Button
                  variant="outlined"
                  color="error"
                  size="small"
                  startIcon={<DeleteIcon />}
                  onClick={() => handleDeleteText(selectedText.id)}
                >
                  Delete element
                </Button>
              </Stack>
            )}
          </Paper>
        </Box>

        {/* Timeline — bottom */}
        <Paper elevation={2} sx={{ p: 2, minHeight: 180 }}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}>
            <Typography variant="subtitle2" fontWeight="bold">Timeline</Typography>
            {error && <Alert severity="info" sx={{ py: 0 }}>{error}</Alert>}
          </Stack>

          <Stack spacing={1}>
            {/* Video track */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Chip size="small" label="V" sx={{ bgcolor: TRACK_COLORS.video, color: "white", minWidth: 28 }} />
              <Box
                onDragOver={handleDragOver}
                onDrop={handleDropOnVideoTrack}
                sx={{ flex: 1, height: 32, border: 1, borderColor: "divider", borderRadius: 1, display: "flex", alignItems: "center", px: 1 }}
              >
                {timeline.video ? (
                  <Chip
                    size="small"
                    icon={<VideoIcon fontSize="small" />}
                    label={timeline.video.documentFilename || `Document #${timeline.video.documentId}`}
                    onDelete={() => setTimeline((p) => ({ ...p, video: null }))}
                  />
                ) : (
                  <Typography variant="caption" color="text.secondary">Drag a video here</Typography>
                )}
              </Box>
            </Box>

            {/* Text track */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Chip size="small" label="T" sx={{ bgcolor: TRACK_COLORS.text, color: "white", minWidth: 28 }} />
              <Box sx={{ flex: 1, height: 32, border: 1, borderColor: "divider", borderRadius: 1, display: "flex", alignItems: "center", px: 1, gap: 0.5, overflow: "auto" }}>
                {timeline.textElements.length === 0 ? (
                  <Typography variant="caption" color="text.secondary">Add Text to place an overlay</Typography>
                ) : (
                  timeline.textElements.map((t) => (
                    <Chip
                      key={t.id}
                      size="small"
                      icon={<TextIcon fontSize="small" />}
                      label={t.text.slice(0, 20)}
                      color={selectedItem?.id === t.id ? "primary" : "default"}
                      onClick={() => setSelectedItem({ type: "text", id: t.id })}
                      onDelete={() => handleDeleteText(t.id)}
                    />
                  ))
                )}
              </Box>
            </Box>

            {/* Audio track */}
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Chip size="small" label="A" sx={{ bgcolor: TRACK_COLORS.audio, color: "white", minWidth: 28 }} />
              <Box
                onDragOver={handleDragOver}
                onDrop={handleDropOnAudioTrack}
                sx={{ flex: 1, height: 32, border: 1, borderColor: "divider", borderRadius: 1, display: "flex", alignItems: "center", px: 1 }}
              >
                {timeline.audio ? (
                  <Chip
                    size="small"
                    icon={<AudioIcon fontSize="small" />}
                    label={timeline.audio.documentFilename || `Audio #${timeline.audio.documentId}`}
                    onDelete={() => setTimeline((p) => ({ ...p, audio: null }))}
                  />
                ) : (
                  <Typography variant="caption" color="text.secondary">Drag an audio file here</Typography>
                )}
              </Box>
            </Box>
          </Stack>

          <Divider sx={{ my: 1 }} />
          <Typography variant="caption" color="text.secondary">
            v1 — drag-and-drop, render endpoint, and orchestrator integration land in subsequent phases.
          </Typography>
        </Paper>
      </Box>
    </PageLayout>
  );
};

export default VideoEditorPage;
