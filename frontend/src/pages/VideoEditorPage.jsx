// frontend/src/pages/VideoEditorPage.jsx
//
// Bin-driven Video Editor — drop B-roll into the Bin, pick a song, hit Plan,
// the Art Director arranges everything, Render produces a .mlt + .mp4.
// Refine in Shotcut if needed. See plans/video-editor-bin-autoedit-vision.md.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Tooltip,
} from "@mui/material";
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  AutoFixHigh as RenderIcon,
  Delete as DeleteIcon,
  TextFields as TextIcon,
  MovieFilter as VideoIcon,
  GraphicEq as AudioIcon,
  AutoAwesome as PlanIcon,
  OpenInNew as ShotcutIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import MediaLibraryPanel from "../components/videoeditor/MediaLibraryPanel";
import OverlayLayer from "../components/videoeditor/OverlayLayer";
import BinPanel from "../components/videoeditor/BinPanel";
import SongSlot from "../components/videoeditor/SongSlot";
import ScanModeSelector from "../components/videoeditor/ScanModeSelector";
import ArrangementPreview from "../components/videoeditor/ArrangementPreview";
import { usePlanJob } from "../components/videoeditor/usePlanJob";
import { useTimelineHistory } from "../components/videoeditor/useTimelineHistory";
import { listVideoDocuments, listAudioDocuments, listImageDocuments } from "../api/videoOverlayService";
import { listStyleRecipes, renderArrangement, openInShotcut } from "../api/videoEditorService";
import { getJobsGate } from "../api/jobsService";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Track lane colors — keep video / text / audio visually distinct.
const TRACK_COLORS = {
  video: "#2196f3",
  text: "#ff9800",
  audio: "#9c27b0",
};

// Initial blank timeline state. Bin holds clips for THIS project; song is
// the master soundtrack. Text overlays are kept for A2+ — currently they
// don't flow through the Plan pipeline.
const _emptyTimeline = () => ({
  bin: [],                // BinClip[]: { clipId, documentId, filename, keptRanges, durationSeconds }
  song: null,             // { documentId, filename, volume }
  textElements: [],
});

const VideoEditorPage = () => {
  const { timeline, commitTimeline, handleUndo, pendingSnapshotRef } = useTimelineHistory(_emptyTimeline());
  const videoElRef = useRef(null);
  const [mediaLibrary, setMediaLibrary] = useState([]);
  const [audioLibrary, setAudioLibrary] = useState([]);
  const [imageLibrary, setImageLibrary] = useState([]);
  const [loadingMedia, setLoadingMedia] = useState(false);
  const [videoDuration, setVideoDuration] = useState(0);  // for visual trim slider
  const [selectedItem, setSelectedItem] = useState(null);  // {type, id} for properties panel
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderResult, setRenderResult] = useState(null);   // { mlt_path, rendered_mp4, documents }
  // Plan pipeline state.
  const [scanMode, setScanMode] = useState("both-and");
  const [styleRecipeName, setStyleRecipeName] = useState("Default");
  const [recipes, setRecipes] = useState([]);
  const planJob = usePlanJob();
  const [gate, setGate] = useState(null);
  const [error, setError] = useState(null);

  // Phase 8 — poll the JobOperationGate so the Render button knows whether
  // another exclusive job (training, etc.) is mid-flight. Refreshes every
  // 5s; cheap call, returns a small JSON snapshot.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const snapshot = await getJobsGate();
        if (!cancelled) setGate(snapshot);
      } catch (e) {
        if (!cancelled) setGate(null);
      }
    };
    tick();
    const t = setInterval(tick, 5000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  // Pull video + audio + image Documents into the media library. Three
  // tabs in the panel each render their own icon-grid.
  useEffect(() => {
    let cancelled = false;
    setLoadingMedia(true);
    Promise.all([listVideoDocuments(), listAudioDocuments(), listImageDocuments()])
      .then(([videos, audios, images]) => {
        if (!cancelled) {
          setMediaLibrary(videos);
          setAudioLibrary(audios);
          setImageLibrary(images);
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

  // Click a video in the library → add it to the project Bin.
  // (Drag also works; this handles the click affordance.)
  const handleAddMedia = (mediaItem) => {
    commitTimeline((prev) => {
      // Don't double-add — silently skip if already in bin.
      if (prev.bin.some((c) => c.documentId === mediaItem.id)) return prev;
      return {
        ...prev,
        bin: [
          ...prev.bin,
          {
            clipId: `doc${mediaItem.id}`,
            documentId: mediaItem.id,
            filename: mediaItem.filename,
            keptRanges: null,
            durationSeconds: null,
          },
        ],
      };
    });
  };

  // Bin operations — used by BinPanel.
  const handleBinAdd = useCallback((clip) => {
    commitTimeline((prev) => {
      if (prev.bin.some((c) => c.clipId === clip.clipId)) return prev;
      return { ...prev, bin: [...prev.bin, clip] };
    });
  }, [commitTimeline]);

  const handleBinAddMany = useCallback((clips) => {
    commitTimeline((prev) => {
      const existing = new Set(prev.bin.map((c) => c.clipId));
      const fresh = clips.filter((c) => !existing.has(c.clipId));
      return { ...prev, bin: [...prev.bin, ...fresh] };
    });
  }, [commitTimeline]);

  const handleBinRemove = useCallback((clipId) => {
    commitTimeline((prev) => ({ ...prev, bin: prev.bin.filter((c) => c.clipId !== clipId) }));
  }, [commitTimeline]);

  const handleSongSet = useCallback((song) => {
    commitTimeline((prev) => ({ ...prev, song }));
  }, [commitTimeline]);

  const handleSongClear = useCallback(() => {
    commitTimeline((prev) => ({ ...prev, song: null }));
  }, [commitTimeline]);

  // Click on the preview to add a text element at that point. Phase 3 of
  // the editor plan adds drag/resize/rotate handles via react-rnd or
  // react-moveable; for now click-to-place + edit-in-properties.
  const handleAddText = useCallback((x, y) => {
    const newId = `text_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    commitTimeline((prev) => {
      return {
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
      };
    });
    setSelectedItem({ type: "text", id: newId });
  }, []);

  const handleDeleteText = (textId) => {
    commitTimeline((prev) => {
      return {
        ...prev,
        textElements: prev.textElements.filter((t) => t.id !== textId),
      };
    });
    if (selectedItem?.type === "text" && selectedItem.id === textId) {
      setSelectedItem(null);
    }
  };



  // Keyboard shortcuts: space toggles play/pause on the preview video,
  // Delete removes the selected text element, Cmd/Ctrl+Z undoes the last
  // mutation. Skipped when the user is typing in an input/textarea so
  // shortcuts don't fight form-field editing.
  useEffect(() => {
    const handleKeyDown = (e) => {
      const target = e.target;
      const isFormField = target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable;
      if (isFormField) return;

      if (e.key === " " || e.code === "Space") {
        e.preventDefault();
        const v = videoElRef.current;
        if (v) {
          if (v.paused) v.play(); else v.pause();
        }
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (selectedItem?.type === "text") {
          e.preventDefault();
          handleDeleteText(selectedItem.id);
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        handleUndo();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedItem, handleUndo]);

  const handleUpdateText = (textId, patch) => {
    commitTimeline((prev) => ({
      ...prev,
      textElements: prev.textElements.map((t) =>
        t.id === textId ? { ...t, ...patch } : t,
      ),
    }));
  };

  // Preview shows the rendered MP4 if we have one, else the first bin clip
  // (so the user gets *something* to see before Plan/Render).
  const previewVideoUrl = useMemo(() => {
    if (renderResult?.rendered_mp4_doc_id) {
      return `${API_BASE}/files/document/${renderResult.rendered_mp4_doc_id}/download`;
    }
    const first = timeline.bin?.[0];
    if (first?.documentId) {
      return `${API_BASE}/files/document/${first.documentId}/download`;
    }
    return null;
  }, [renderResult, timeline.bin]);

  const selectedText = useMemo(() => {
    if (selectedItem?.type !== "text") return null;
    return timeline.textElements.find((t) => t.id === selectedItem.id) || null;
  }, [selectedItem, timeline.textElements]);

  // Load style recipes once on mount.
  useEffect(() => {
    listStyleRecipes()
      .then(setRecipes)
      .catch((e) => console.warn("recipes load failed:", e));
  }, []);

  // Patch each bin clip with kept-ranges + duration from the Plan result so
  // BinClipTile can render the kept-vs-cut strip.
  useEffect(() => {
    const result = planJob.result;
    if (!result) return;
    const kept = result.kept_ranges_by_clip || {};
    const songDuration = result.song?.duration_seconds || null;
    commitTimeline((prev) => ({
      ...prev,
      bin: prev.bin.map((c) => ({
        ...c,
        keptRanges: kept[c.clipId] || null,
        // For the strip, we need the SOURCE clip's duration, not the song's.
        // We don't have it yet (would need an ffprobe round-trip); use the
        // last kept-range endpoint as a pessimistic upper bound for display.
        durationSeconds: kept[c.clipId]?.length
          ? Math.max(...kept[c.clipId].map((r) => r[1]))
          : c.durationSeconds,
      })),
    }));
  }, [planJob.result]);  // eslint-disable-line react-hooks/exhaustive-deps

  const canPlan = timeline.bin.length > 0 && !!timeline.song && !planJob.planning;

  const handlePlan = useCallback(() => {
    if (!canPlan) return;
    setError(null);
    setRenderResult(null);
    planJob.start({
      bin_clips: timeline.bin.map((c) => ({
        clip_id: c.clipId,
        document_id: c.documentId,
      })),
      song_document_id: timeline.song.documentId,
      scan_mode: scanMode,
      style_recipe_name: styleRecipeName,
      seed: Math.floor(Math.random() * 1_000_000),
    });
  }, [canPlan, planJob, timeline.bin, timeline.song, scanMode, styleRecipeName]);

  // A2 render: full multi-clip arrangement with per-clip filters + transitions.
  // Plugin synthesizes the .mlt and renders to .mp4 in one synchronous call.
  const handleRender = useCallback(async () => {
    const arr = planJob.result?.arrangement;
    if (!arr || arr.clips.length === 0) {
      setError("Hit Plan first — no arrangement to render yet.");
      return;
    }
    setRendering(true);
    setError(null);
    try {
      const res = await renderArrangement({
        arrangement: arr,
        song_document_id: timeline.song?.documentId,
        audio_volume: timeline.song?.volume ?? 1.0,
        song_duration_seconds: planJob.result?.song?.duration_seconds,
        render_mp4: true,
      });
      setRenderResult(res);
    } catch (e) {
      console.error("render failed:", e);
      setError(e.response?.data?.error?.message || e.message || "Render failed");
    } finally {
      setRendering(false);
    }
  }, [planJob.result, timeline.song]);

  const handleOpenInShotcut = useCallback(async () => {
    if (!renderResult?.mlt_path) return;
    try {
      await openInShotcut(renderResult.mlt_path);
    } catch (e) {
      console.error("openInShotcut failed:", e);
      setError(e.response?.data?.error?.message || e.message || "Could not launch Shotcut");
    }
  }, [renderResult]);

  // HTML5 drag-and-drop. dataTransfer carries the media-library row id +
  // kind so BinPanel / SongSlot know whether to accept the drop.
  const handleDragStartMedia = (e, mediaItem, kind) => {
    e.dataTransfer.setData("application/json", JSON.stringify({ id: mediaItem.id, kind, filename: mediaItem.filename }));
    e.dataTransfer.effectAllowed = "copy";
  };

  return (
    <PageLayout title="Video Editor" subtitle="Compose videos with overlays, audio, and text">
      <Box sx={{ display: "flex", flexDirection: "column", height: "calc(100vh - 96px)", p: 2, gap: 2 }}>
        {/* Top half: preview + media library + properties */}
        <Box sx={{ display: "flex", gap: 2, flex: 1, minHeight: 0 }}>
          {/* Media library — left. Owns its own tab/view-mode/drill state.
              Item click + drag handlers come from the page so the timeline
              drop-targets stay wired the way they always were. */}
          <MediaLibraryPanel
            videos={mediaLibrary}
            audios={audioLibrary}
            images={imageLibrary}
            loading={loadingMedia}
            onItemClick={(item, kind) => {
              if (kind === "video") handleAddMedia(item);
            }}
            onItemDragStart={handleDragStartMedia}
          />

          {/* Preview window — center */}
          <Paper elevation={4} sx={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
            <Box sx={{ position: "relative", flex: 1, bgcolor: "#000", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden" }}>
              {previewVideoUrl ? (
                <video
                  ref={videoElRef}
                  src={previewVideoUrl}
                  controls
                  onPlay={() => setPreviewPlaying(true)}
                  onPause={() => setPreviewPlaying(false)}
                  onLoadedMetadata={(e) => {
                    const dur = e.target.duration;
                    if (dur && isFinite(dur)) setVideoDuration(dur);
                  }}
                  style={{ maxWidth: "100%", maxHeight: "100%", display: "block" }}
                />
              ) : (
                <Stack spacing={1} alignItems="center" sx={{ color: "rgba(255,255,255,0.5)" }}>
                  <VideoIcon sx={{ fontSize: 48 }} />
                  <Typography variant="caption">Drop clips into the Bin to begin</Typography>
                </Stack>
              )}

              {/* Text overlays — drag to reposition. */}
              <OverlayLayer
                textElements={timeline.textElements}
                selectedTextId={selectedItem?.type === "text" ? selectedItem.id : null}
                onSelectText={(id) => setSelectedItem({ type: "text", id })}
                onMoveText={(id, x, y) => commitTimeline((prev) => ({
                  ...prev,
                  textElements: prev.textElements.map(t => t.id === id ? { ...t, x, y } : t)
                }))}
              />
            </Box>

            {/* Toolbar under the preview — Plan / Render / Open in Shotcut */}
            <Stack direction="row" spacing={1} alignItems="center" sx={{ p: 1, borderTop: 1, borderColor: "divider" }}>
              <IconButton onClick={() => setPreviewPlaying(!previewPlaying)} size="small">
                {previewPlaying ? <PauseIcon /> : <PlayIcon />}
              </IconButton>
              <Box sx={{ flexGrow: 1 }} />
              <Tooltip title="Run the auto-edit + Art Director pipeline. Cheap to re-run (vision is cached).">
                <span>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={planJob.planning ? <CircularProgress size={16} /> : <PlanIcon />}
                    onClick={handlePlan}
                    disabled={!canPlan}
                  >
                    {planJob.planning ? `Planning... ${Math.round((planJob.job?.progress || 0) * 100)}%` : "Plan"}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title={planJob.result ? "Render the arrangement to .mlt + .mp4" : "Hit Plan first"}>
                <span>
                  <Button
                    variant="contained"
                    startIcon={rendering ? <CircularProgress size={20} color="inherit" /> : <RenderIcon />}
                    onClick={handleRender}
                    disabled={!planJob.result || rendering}
                  >
                    {rendering ? "Rendering..." : "Render"}
                  </Button>
                </span>
              </Tooltip>
              {renderResult?.mlt_path && (
                <Tooltip title="Open the rendered project in Shotcut for refinement">
                  <Button size="small" variant="text" startIcon={<ShotcutIcon />} onClick={handleOpenInShotcut}>
                    Open in Shotcut
                  </Button>
                </Tooltip>
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

        {/* Project Bin + Song + Plan controls — bottom */}
        <Paper elevation={2} sx={{ p: 2, minHeight: 220, display: "flex", gap: 2 }}>
          {/* Left: Bin */}
          <Box sx={{ width: 320, minWidth: 280, display: "flex", flexDirection: "column", borderRight: 1, borderColor: "divider", pr: 2 }}>
            <BinPanel
              binClips={timeline.bin}
              selectedClipId={selectedItem?.type === "bin" ? selectedItem.id : null}
              onSelect={(id) => setSelectedItem({ type: "bin", id })}
              onAdd={handleBinAdd}
              onAddMany={handleBinAddMany}
              onRemove={handleBinRemove}
            />
          </Box>

          {/* Middle: Song + Controls */}
          <Box sx={{ width: 320, minWidth: 260, display: "flex", flexDirection: "column", gap: 1.5 }}>
            <Box>
              <Typography variant="caption" color="text.secondary">Master soundtrack</Typography>
              <SongSlot song={timeline.song} onSet={handleSongSet} onClear={handleSongClear} />
            </Box>
            <ScanModeSelector value={scanMode} onChange={setScanMode} disabled={planJob.planning} />
            <Box>
              <Typography variant="caption" color="text.secondary">Style recipe</Typography>
              <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mt: 0.5 }}>
                {(recipes.length === 0 ? [{ name: "Default" }] : recipes).map((r) => (
                  <Chip
                    key={r.name}
                    label={r.name}
                    size="small"
                    color={styleRecipeName === r.name ? "primary" : "default"}
                    onClick={() => setStyleRecipeName(r.name)}
                    variant={styleRecipeName === r.name ? "filled" : "outlined"}
                    sx={{ mb: 0.5 }}
                  />
                ))}
              </Stack>
            </Box>
            {error && <Alert severity="error" sx={{ py: 0 }}>{error}</Alert>}
            {planJob.error && <Alert severity="error" sx={{ py: 0 }}>{planJob.error}</Alert>}
          </Box>

          {/* Right: Arrangement preview */}
          <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
            <Stack direction="row" alignItems="center" spacing={1} mb={1}>
              <Typography variant="subtitle2" fontWeight="bold">Arrangement</Typography>
              {planJob.planning && <CircularProgress size={14} />}
            </Stack>
            <Box sx={{ flex: 1, overflow: "auto" }}>
              <ArrangementPreview arrangement={planJob.result?.arrangement} />
              {planJob.result?.warnings?.length > 0 && (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  {planJob.result.warnings.slice(0, 3).map((w, i) => (
                    <div key={i}>{w}</div>
                  ))}
                </Alert>
              )}
            </Box>
          </Box>
        </Paper>
      </Box>
    </PageLayout>
  );
};

export default VideoEditorPage;
