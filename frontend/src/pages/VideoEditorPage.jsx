// frontend/src/pages/VideoEditorPage.jsx
//
// Bin-driven Video Editor — drop B-roll into the Bin, pick a song, hit Plan,
// the Art Director arranges everything, Render produces a .mlt + .mp4.
// Refine in Shotcut if needed. See plans/video-editor-bin-autoedit-vision.md.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Box,
  Typography,
  Stack,
  Button,
  IconButton,
  Chip,
  CircularProgress,
  Alert,
  Tooltip,
} from "@mui/material";
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  AutoFixHigh as RenderIcon,
  MovieFilter as VideoIcon,
  AutoAwesome as PlanIcon,
  OpenInNew as ShotcutIcon,
  RocketLaunch as QuickRenderIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import MediaLibraryPanel from "../components/videoeditor/MediaLibraryPanel";
import OverlayLayer from "../components/videoeditor/OverlayLayer";
import BinPanel from "../components/videoeditor/BinPanel";
import ArrangementPreview from "../components/videoeditor/ArrangementPreview";
import OptionsPanel from "../components/videoeditor/OptionsPanel";
import { usePlanJob } from "../components/videoeditor/usePlanJob";
import { useTimelineHistory } from "../components/videoeditor/useTimelineHistory";
import { normalizeTimeline } from "../components/videoeditor/normalizeTimeline";
import { listVideoDocuments, listAudioDocuments, listImageDocuments } from "../api/videoOverlayService";
import { listStyleRecipes, renderArrangement, openInShotcut, rescanClip, getClipHash } from "../api/videoEditorService";
import { getJobsGate } from "../api/jobsService";
import ReactGridLayout, { WidthProvider } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import DashboardCardWrapper from "../components/dashboard/DashboardCardWrapper";
import { useLayout, useDashboardWidth } from "../contexts/LayoutContext";

const GridLayout = WidthProvider(ReactGridLayout);

// Bumped whenever DEFAULT_VE_LAYOUT changes shape — a saved layout from an older
// version is discarded so everyone picks up the improved tiling once (otherwise
// a stale, messy hand-dragged layout shadows the default forever).
const LAYOUT_VERSION = 3;

// Default card placement on a ~175-col grid (10px units). Three across the top
// (library / preview / properties) and three across the bottom (bin / controls /
// arrangement) — the familiar NLE layout, but every card is draggable/resizable.
// Each row's widths sum to COLS_COUNT (175) so there's no right-edge gap, and the
// two row heights fill the ~calc(100vh-96px) canvas.
const DEFAULT_VE_LAYOUT = [
  { i: "media",       x: 0,   y: 0,  w: 32,  h: 46, minW: 16, minH: 14 },
  { i: "preview",     x: 32,  y: 0,  w: 111, h: 46, minW: 30, minH: 14 },
  { i: "options",     x: 143, y: 0,  w: 32,  h: 46, minW: 16, minH: 14 },
  { i: "bin",         x: 0,   y: 46, w: 70,  h: 46, minW: 26, minH: 12 },
  { i: "arrangement", x: 70,  y: 46, w: 105, h: 46, minW: 26, minH: 12 },
];
const VE_CARD_TITLES = {
  media: "Media Library",
  preview: "Preview",
  options: "Options",
  bin: "Bin",
  arrangement: "Arrangement",
};

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Initial blank timeline state. Bin holds clips for THIS project; song is
// the master soundtrack. Text overlays are kept for A2+ — currently they
// don't flow through the Plan pipeline.
const _emptyTimeline = () => ({
  // BinClip[]: { clipId, documentId, filename, kind, keptRanges, durationSeconds,
  //              isMasterSong?, volume? }. The master soundtrack is the one audio
  //              clip flagged isMasterSong — there is no separate song slot.
  bin: [],
  textElements: [],
});

const VideoEditorPage = () => {
  const { timeline, commitTimeline, handleUndo } = useTimelineHistory(_emptyTimeline());
  const videoElRef = useRef(null);
  const [mediaLibrary, setMediaLibrary] = useState([]);
  const [audioLibrary, setAudioLibrary] = useState([]);
  const [imageLibrary, setImageLibrary] = useState([]);
  const [loadingMedia, setLoadingMedia] = useState(false);
  const [, setVideoDuration] = useState(0);  // for visual trim slider
  const [selectedItem, setSelectedItem] = useState(null);  // {type, id} for properties panel
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [renderResult, setRenderResult] = useState(null);   // { mlt_path, rendered_mp4, documents }
  // Plan pipeline state.
  const [scanMode, setScanMode] = useState("both-and");
  const [styleRecipeName, setStyleRecipeName] = useState("Default");
  const [recipes, setRecipes] = useState([]);
  const planJob = usePlanJob();
  const [, setGate] = useState(null);
  const [error, setError] = useState(null);

  // Director's Notes overrides — keyed by clip_id. Local until next Plan.
  // Each value is a Partial<ClipAnalysis> patch the user has applied on top
  // of the Art Director's output.
  const [clipOverrides, setClipOverrides] = useState({});

  // Quick Render: when true, the next planJob.result triggers a Render
  // automatically. Cleared on completion or error.
  const [quickRenderPending, setQuickRenderPending] = useState(false);

  // --- Card grid layout (resizable/draggable windows, same system as the Code
  // Editor & Documents pages). Persisted to /api/state/video-editor. ---
  const { gridSettings } = useLayout();
  const { COLS_COUNT, ROW_HEIGHT_PX, CARD_MARGIN_PX, CONTAINER_PADDING_PX } = gridSettings;
  const gridWidth = useDashboardWidth();
  const [layout, setLayout] = useState(DEFAULT_VE_LAYOUT);
  const [cardColors, setCardColors] = useState({});
  const [minimizedCards, setMinimizedCards] = useState({});
  const layoutLoadedRef = useRef(false);
  const MIN_ROW = Math.max(1, Math.round(48 / ROW_HEIGHT_PX));

  // SaveState indicator (same UX as CodeEditor/Dashboard/Documents): a "Saving…/
  // Saved" chip backed by both the layout save and the session (content) save.
  const [isSaving, setIsSaving] = useState(false);
  const [lastSaveTime, setLastSaveTime] = useState(null);
  const sessionLoadedRef = useRef(false);
  const lastSavedSessionRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/state/video-editor")
      .then((r) => (r.ok ? r.json() : null))
      .then((saved) => {
        if (cancelled) return;
        if (saved && saved.version === LAYOUT_VERSION && Array.isArray(saved.layout) && saved.layout.length) {
          // Same version: restore it, but drop any card id no longer in the
          // default set and add any new default card the save predates.
          const defaultIds = new Set(DEFAULT_VE_LAYOUT.map((d) => d.i));
          const kept = saved.layout.filter((l) => defaultIds.has(l.i));
          const keptIds = new Set(kept.map((l) => l.i));
          setLayout([...kept, ...DEFAULT_VE_LAYOUT.filter((d) => !keptIds.has(d.i))]);
        }
        // Older/absent version → keep DEFAULT_VE_LAYOUT (discard stale layout).
        if (saved?.cardColors) setCardColors(saved.cardColors);
        if (saved?.minimizedCards) setMinimizedCards(saved.minimizedCards);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) layoutLoadedRef.current = true; });
    return () => { cancelled = true; };
  }, []);

  const saveLayoutState = useCallback(async (nextLayout, nextColors, nextMin) => {
    if (!layoutLoadedRef.current) return;  // don't clobber saved state on first paint
    setIsSaving(true);
    try {
      await fetch("/api/state/video-editor", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          version: LAYOUT_VERSION,
          layout: nextLayout,
          cardColors: nextColors,
          minimizedCards: nextMin,
          lastSaved: new Date().toISOString(),
        }),
      });
      setLastSaveTime(new Date());
    } catch (e) {
      console.warn("video-editor layout save failed:", e);
    } finally {
      setIsSaving(false);
    }
  }, []);

  // Restore the working session (bin / song / overlays / scan mode / recipe /
  // overrides) on mount, then auto-save it (debounced) whenever it changes.
  useEffect(() => {
    let cancelled = false;
    fetch("/api/state/video-editor/session")
      .then((r) => (r.ok ? r.json() : null))
      .then((s) => {
        if (cancelled || !s) return;
        if (s.timeline) commitTimeline(() => normalizeTimeline(s.timeline));
        if (s.scanMode) setScanMode(s.scanMode);
        if (s.styleRecipeName) setStyleRecipeName(s.styleRecipeName);
        if (s.clipOverrides) setClipOverrides(s.clipOverrides);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) sessionLoadedRef.current = true; });
    return () => { cancelled = true; };
  }, []);  // deps intentionally limited

  useEffect(() => {
    if (!sessionLoadedRef.current) return;
    const payload = { timeline, scanMode, styleRecipeName, clipOverrides };
    const serialized = JSON.stringify(payload);
    if (lastSavedSessionRef.current === serialized) return;  // nothing changed
    const t = setTimeout(() => {
      setIsSaving(true);
      fetch("/api/state/video-editor/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, lastSaved: new Date().toISOString() }),
      })
        .then((r) => { if (r.ok) { lastSavedSessionRef.current = serialized; setLastSaveTime(new Date()); } })
        .catch((e) => console.warn("video-editor session save failed:", e))
        .finally(() => setIsSaving(false));
    }, 800);
    return () => clearTimeout(t);
  }, [timeline, scanMode, styleRecipeName, clipOverrides]);

  const onLayoutChange = useCallback((next) => {
    if (!layoutLoadedRef.current) return;
    // A minimized card reports its collapsed height — preserve its stored
    // (expanded) height so un-minimizing restores the real size.
    const prevById = Object.fromEntries(layout.map((l) => [l.i, l]));
    const merged = next.map((l) =>
      minimizedCards[l.i] && prevById[l.i] ? { ...l, h: prevById[l.i].h } : l,
    );
    setLayout(merged);
    saveLayoutState(merged, cardColors, minimizedCards);
  }, [layout, minimizedCards, cardColors, saveLayoutState]);

  const handleCardColorChange = useCallback((cardId, color) => {
    const next = { ...cardColors, [cardId]: color };
    setCardColors(next);
    saveLayoutState(layout, next, minimizedCards);
  }, [cardColors, layout, minimizedCards, saveLayoutState]);

  const handleToggleMinimize = useCallback((cardId) => {
    const next = { ...minimizedCards, [cardId]: !minimizedCards[cardId] };
    setMinimizedCards(next);
    saveLayoutState(layout, cardColors, next);
  }, [minimizedCards, layout, cardColors, saveLayoutState]);

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

  // Click a media item in the library → add it to the project Bin (any kind).
  // (Drag also works; this handles the click affordance.)
  const handleAddMedia = (mediaItem, kind = "video") => {
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
            kind,
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

  // Master soundtrack = the one audio bin clip flagged isMasterSong. Toggling
  // one on clears the others (single-flag invariant); Plan reads the flagged clip.
  const handleSetMasterSong = useCallback((clipId, on) => {
    commitTimeline((prev) => ({
      ...prev,
      bin: prev.bin.map((c) =>
        c.clipId === clipId
          ? { ...c, isMasterSong: on }
          : on ? { ...c, isMasterSong: false } : c,
      ),
    }));
  }, [commitTimeline]);

  const handleSetClipVolume = useCallback((clipId, volume) => {
    commitTimeline((prev) => ({
      ...prev,
      bin: prev.bin.map((c) => (c.clipId === clipId ? { ...c, volume } : c)),
    }));
  }, [commitTimeline]);

  // Click on the preview to add a text element at that point. Phase 3 of
  // the editor plan adds drag/resize/rotate handles via react-rnd or
  // react-moveable; for now click-to-place + edit-in-properties.
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

  // Bin-tile warnings: parse planJob.result.warnings and route to the right clip.
  // Warnings come in the shape "filename: rest of message" — match by basename.
  const warningsByClipId = useMemo(() => {
    const map = {};
    const warnings = planJob.result?.warnings || [];
    if (!warnings.length) return map;
    for (const c of timeline.bin) {
      const base = (c.filename || "").split("/").pop();
      if (!base) continue;
      const hit = warnings.find((w) => w.startsWith(base + ":"));
      if (hit) map[c.clipId] = hit.slice(base.length + 1).trim();
    }
    return map;
  }, [planJob.result, timeline.bin]);

  // The merged analysis for the selected bin clip (AI output + local overrides).
  const selectedClipAnalysis = useMemo(() => {
    if (selectedItem?.type !== "bin") return null;
    const analyses = planJob.result?.clip_analyses || [];
    const base = analyses.find((a) => a.clip_id === selectedItem.id);
    if (!base) return null;
    return { ...base, ...(clipOverrides[selectedItem.id] || {}) };
  }, [selectedItem, planJob.result, clipOverrides]);

  const handleClipOverride = useCallback((patch) => {
    if (!selectedItem || selectedItem.type !== "bin") return;
    setClipOverrides((prev) => ({
      ...prev,
      [selectedItem.id]: { ...(prev[selectedItem.id] || {}), ...patch },
    }));
  }, [selectedItem]);

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
  }, [planJob.result]);  // deps intentionally limited

  // Master soundtrack = the flagged audio bin clip. Plan arranges the VIDEO
  // clips against it; audio/image clips aren't part of the auto-edit material.
  const masterSong = timeline.bin.find((c) => c.kind === "audio" && c.isMasterSong) || null;
  const canPlan = timeline.bin.some((c) => c.kind === "video") && !!masterSong && !planJob.planning;

  const handlePlan = useCallback(() => {
    if (!canPlan) return;
    setError(null);
    setRenderResult(null);
    planJob.start({
      bin_clips: timeline.bin
        .filter((c) => c.kind === "video")
        .map((c) => ({ clip_id: c.clipId, document_id: c.documentId })),
      song_document_id: masterSong.documentId,
      scan_mode: scanMode,
      style_recipe_name: styleRecipeName,
      seed: Math.floor(Math.random() * 1_000_000),
      clip_overrides: clipOverrides,
    });
  }, [canPlan, planJob, timeline.bin, masterSong, scanMode, styleRecipeName, clipOverrides]);

  const handleQuickRender = useCallback(() => {
    if (!canPlan) return;
    setQuickRenderPending(true);
    handlePlan();
  }, [canPlan, handlePlan]);

  // Re-analyze a single clip: drops cache, re-samples frames, fresh vision pass.
  // The new analysis replaces both the cached value and the current planJob's
  // clip_analyses entry so the UI updates without a full re-Plan.
  const [rescanInFlight, setRescanInFlight] = useState(null);  // clip_id currently being re-analyzed
  const handleReanalyze = useCallback(async () => {
    if (!selectedItem || selectedItem.type !== "bin") return;
    const clip = timeline.bin.find((c) => c.clipId === selectedItem.id);
    if (!clip?.documentId) return;
    setRescanInFlight(clip.clipId);
    setError(null);
    try {
      const res = await rescanClip({
        document_id: clip.documentId,
        style_recipe_name: styleRecipeName,
      });
      // Clear any local override since it'd shadow the fresh AI read.
      setClipOverrides((prev) => {
        const next = { ...prev };
        delete next[clip.clipId];
        return next;
      });
      // Patch the planJob result so the panel updates immediately. We don't
      // have a setter from the hook, so this is intentionally optimistic:
      // the next Plan run picks up the new cache anyway.
      if (planJob.result?.clip_analyses) {
        const idx = planJob.result.clip_analyses.findIndex((a) => a.clip_id === clip.clipId);
        if (idx >= 0) {
          planJob.result.clip_analyses[idx] = {
            ...res.analysis,
            clip_id: clip.clipId,
            source_path: planJob.result.clip_analyses[idx].source_path,
          };
        }
      }
    } catch (e) {
      console.error("rescan failed:", e);
      setError(e.response?.data?.error?.message || e.message || "Re-analyze failed");
    } finally {
      setRescanInFlight(null);
    }
  }, [selectedItem, timeline.bin, styleRecipeName, planJob.result]);

  // Resolve the clip hash for the selected bin clip so DirectorsNotesPanel
  // can build frame-thumbnail URLs. Cached per documentId.
  const [clipHashByDocId, setClipHashByDocId] = useState({});
  useEffect(() => {
    if (selectedItem?.type !== "bin") return;
    const clip = timeline.bin.find((c) => c.clipId === selectedItem.id);
    if (!clip?.documentId || clipHashByDocId[clip.documentId]) return;
    getClipHash({ document_id: clip.documentId })
      .then((h) => h && setClipHashByDocId((prev) => ({ ...prev, [clip.documentId]: h })))
      .catch((e) => console.warn("clip-hash lookup failed:", e));
  }, [selectedItem, timeline.bin, clipHashByDocId]);

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
        song_document_id: masterSong?.documentId,
        audio_volume: masterSong?.volume ?? 1.0,
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
  }, [planJob.result, masterSong]);

  // Quick Render: when planJob lands with a result and we're pending, chain into Render.
  useEffect(() => {
    if (!quickRenderPending) return;
    if (planJob.result && !planJob.planning && !rendering) {
      setQuickRenderPending(false);
      handleRender();
    } else if (planJob.error) {
      setQuickRenderPending(false);
    }
  }, [quickRenderPending, planJob.result, planJob.planning, planJob.error, rendering]);  // deps intentionally limited

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
  // kind so BinPanel knows what kind of media is being dropped.
  const handleDragStartMedia = (e, mediaItem, kind) => {
    e.dataTransfer.setData("application/json", JSON.stringify({ id: mediaItem.id, kind, filename: mediaItem.filename }));
    e.dataTransfer.effectAllowed = "copy";
  };

  // Each card's body. The card chrome (title bar, drag handle, minimize, color)
  // comes from DashboardCardWrapper — these just fill the card.
  const renderCardBody = (cardId) => {
    switch (cardId) {
      case "media":
        return (
          <MediaLibraryPanel
            videos={mediaLibrary}
            audios={audioLibrary}
            images={imageLibrary}
            loading={loadingMedia}
            onItemClick={(item, kind) => handleAddMedia(item, kind)}
            onItemDragStart={handleDragStartMedia}
          />
        );

      case "preview":
        return (
          <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
            <Box sx={{ position: "relative", flex: 1, minHeight: 0, bgcolor: "#000", display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden", borderRadius: 1 }}>
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
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ pt: 1, mt: 1, borderTop: 1, borderColor: "divider" }}>
              <IconButton onClick={() => setPreviewPlaying(!previewPlaying)} size="small" className="non-draggable">
                {previewPlaying ? <PauseIcon /> : <PlayIcon />}
              </IconButton>
              {(isSaving || lastSaveTime) && (
                <Tooltip title={isSaving ? "Saving…" : `Last saved: ${lastSaveTime?.toLocaleTimeString?.() || ""}`}>
                  <Chip
                    label={isSaving ? "Saving…" : "Saved"}
                    size="small"
                    color={isSaving ? "primary" : "success"}
                    className="non-draggable"
                    sx={{ fontSize: "0.6rem", height: 18, "& .MuiChip-label": { px: 0.75, py: 0 } }}
                  />
                </Tooltip>
              )}
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
                    {planJob.planning && !quickRenderPending
                      ? `Planning... ${Math.round((planJob.job?.progress || 0) * 100)}%`
                      : "Plan"}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title={planJob.result ? "Render the arrangement to .mlt + .mp4" : "Hit Plan first"}>
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    startIcon={rendering ? <CircularProgress size={18} color="inherit" /> : <RenderIcon />}
                    onClick={handleRender}
                    disabled={!planJob.result || rendering || quickRenderPending}
                  >
                    {rendering ? "Rendering..." : "Render"}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title="Plan + Render in one click. The Batch Video Generator pattern — set it and forget it.">
                <span>
                  <Button
                    size="small"
                    variant="contained"
                    color="secondary"
                    startIcon={
                      (planJob.planning || rendering) && quickRenderPending
                        ? <CircularProgress size={18} color="inherit" />
                        : <QuickRenderIcon />
                    }
                    onClick={handleQuickRender}
                    disabled={!canPlan || rendering || quickRenderPending}
                  >
                    {quickRenderPending
                      ? (rendering ? "Rendering..." : `Planning... ${Math.round((planJob.job?.progress || 0) * 100)}%`)
                      : "Quick Render"}
                  </Button>
                </span>
              </Tooltip>
              {renderResult?.mlt_path && (
                <Tooltip title="Open the rendered project in Shotcut for refinement">
                  <Button size="small" variant="text" startIcon={<ShotcutIcon />} onClick={handleOpenInShotcut}>
                    Shotcut
                  </Button>
                </Tooltip>
              )}
            </Stack>
          </Box>
        );

      case "options": {
        const selectedClip = selectedItem?.type === "bin"
          ? timeline.bin.find((c) => c.clipId === selectedItem.id) || null
          : null;
        return (
          <OptionsPanel
            selectedItem={selectedItem}
            selectedClip={selectedClip}
            selectedClipAnalysis={selectedClipAnalysis}
            selectedText={selectedText}
            scanMode={scanMode}
            setScanMode={setScanMode}
            styleRecipeName={styleRecipeName}
            setStyleRecipeName={setStyleRecipeName}
            recipes={recipes}
            planning={planJob.planning}
            onClipOverride={handleClipOverride}
            onReanalyze={handleReanalyze}
            rescanning={rescanInFlight === selectedItem?.id}
            clipHash={selectedClip?.documentId ? clipHashByDocId[selectedClip.documentId] : null}
            onSetMasterSong={handleSetMasterSong}
            onSetVolume={handleSetClipVolume}
            onUpdateText={handleUpdateText}
            onDeleteText={handleDeleteText}
            error={error}
            planError={planJob.error}
          />
        );
      }

      case "bin":
        return (
          <BinPanel
            binClips={timeline.bin}
            selectedClipId={selectedItem?.type === "bin" ? selectedItem.id : null}
            onSelect={(id) => setSelectedItem({ type: "bin", id })}
            onAdd={handleBinAdd}
            onAddMany={handleBinAddMany}
            onRemove={handleBinRemove}
            warningsByClipId={warningsByClipId}
          />
        );

      case "arrangement":
        return (
          <Box sx={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
            {planJob.planning && (
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
                <CircularProgress size={14} />
                <Typography variant="caption" color="text.secondary">Planning…</Typography>
              </Stack>
            )}
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
        );

      default:
        return null;
    }
  };

  return (
    <PageLayout title="Video Editor" subtitle="Compose videos with overlays, audio, and text">
      {/* Window/card system — drag by the title bar, resize from any edge,
          double-click a header to minimize. Layout persists per-machine via
          /api/state/video-editor. Same pattern as the Documents & Code Editor pages. */}
      <Box sx={{ height: "calc(100vh - 96px)", overflow: "auto", p: 0.5 }}>
        <GridLayout
          className="layout"
          layout={layout}
          cols={COLS_COUNT}
          rowHeight={ROW_HEIGHT_PX}
          width={gridWidth}
          containerPadding={[CONTAINER_PADDING_PX / 10, CONTAINER_PADDING_PX / 10]}
          margin={[CARD_MARGIN_PX / 20, CARD_MARGIN_PX / 20]}
          isDraggable
          isResizable
          compactType={null}
          preventCollision={false}
          useCSSTransforms={false}
          allowOverlap={true}
          draggableHandle=".card-header-buttons"
          draggableCancel="button, input, textarea, select, option, .non-draggable"
          onLayoutChange={onLayoutChange}
          resizeHandles={["s", "w", "e", "n", "sw", "nw", "se", "ne"]}
        >
          {layout.map((item) => {
            const cardId = item.i;
            const isMin = !!minimizedCards[cardId];
            const dataGrid = isMin
              ? { ...item, h: MIN_ROW, minH: MIN_ROW, maxH: MIN_ROW, isResizable: false }
              : { ...item, isResizable: true };
            return (
              <div key={cardId} data-grid={dataGrid}>
                <DashboardCardWrapper
                  id={cardId}
                  title={VE_CARD_TITLES[cardId] || cardId}
                  cardColor={cardColors[cardId]}
                  onCardColorChange={(c) => handleCardColorChange(cardId, c)}
                  isMinimized={isMin}
                  onToggleMinimize={() => handleToggleMinimize(cardId)}
                >
                  {renderCardBody(cardId)}
                </DashboardCardWrapper>
              </div>
            );
          })}
        </GridLayout>
      </Box>
    </PageLayout>
  );
};

export default VideoEditorPage;
