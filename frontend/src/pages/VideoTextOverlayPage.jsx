// frontend/src/pages/VideoTextOverlayPage.jsx
//
// Pick a video Document, type some text, hit Render — backend runs ffmpeg
// drawtext and registers the result as a new Document. The original is left
// alone so the user can iterate on copy without losing their source.
//
// Light by design: 9 named positions, font size, color, optional outline +
// translucent background. If we end up needing kinetic typography or timed
// captions, the natural follow-on is the ASS-subtitles option from the plan.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  Typography,
  Paper,
  Stack,
  TextField,
  Button,
  Slider,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  FormControlLabel,
  Switch,
  Alert,
  Grid,
} from "@mui/material";
import {
  TextFields as TextFieldsIcon,
  AutoFixHigh as RenderIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import { listVideoDocuments, overlayText } from "../api/videoOverlayService";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Same nine positions the backend supports. Labels are friendlier than the
// raw enum values; the value strings match _VALID_POSITIONS server-side.
const POSITION_OPTIONS = [
  { value: "top-left",      label: "Top Left" },
  { value: "top-center",    label: "Top Center" },
  { value: "top-right",     label: "Top Right" },
  { value: "middle-left",   label: "Middle Left" },
  { value: "center",        label: "Center" },
  { value: "middle-right",  label: "Middle Right" },
  { value: "bottom-left",   label: "Bottom Left" },
  { value: "bottom-center", label: "Bottom Center" },
  { value: "bottom-right",  label: "Bottom Right" },
];

const COLOR_PRESETS = [
  { value: "white",   label: "White" },
  { value: "black",   label: "Black" },
  { value: "yellow",  label: "Yellow" },
  { value: "red",     label: "Red" },
  { value: "#22c55e", label: "Green" },
  { value: "#3b82f6", label: "Blue" },
  { value: "#f97316", label: "Orange" },
];

const VideoTextOverlayPage = () => {
  const [videos, setVideos] = useState([]);
  const [loadingVideos, setLoadingVideos] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [text, setText] = useState("");
  const [fontSize, setFontSize] = useState(48);
  const [fontColor, setFontColor] = useState("white");
  const [position, setPosition] = useState("bottom-center");
  const [border, setBorder] = useState(true);
  const [boxBackground, setBoxBackground] = useState(false);

  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const refreshVideos = useCallback(async () => {
    setLoadingVideos(true);
    try {
      const list = await listVideoDocuments();
      setVideos(list);
    } catch (e) {
      console.error("Could not list videos:", e);
      setError("Could not load video list. Backend may be offline.");
    } finally {
      setLoadingVideos(false);
    }
  }, []);

  useEffect(() => { refreshVideos(); }, [refreshVideos]);

  const selectedVideo = useMemo(
    () => videos.find((v) => v.id === selectedDocId),
    [videos, selectedDocId],
  );

  const inputPreviewUrl = useMemo(
    () => (selectedDocId ? `${API_BASE}/files/document/${selectedDocId}/download` : null),
    [selectedDocId],
  );

  const handleRender = async () => {
    if (!selectedDocId) {
      setError("Pick a video first.");
      return;
    }
    if (!text.trim()) {
      setError("Type something to render.");
      return;
    }

    setRendering(true);
    setError(null);
    setResult(null);
    try {
      const newDoc = await overlayText({
        documentId: selectedDocId,
        text: text.trim(),
        fontSize,
        fontColor,
        position,
        border,
        boxBackground,
      });
      setResult({
        ...newDoc,
        full_url: `${API_BASE}/files/document/${newDoc.id}/download`,
      });
      refreshVideos();  // pick up the new doc so user can chain overlays
    } catch (e) {
      console.error("Render failed:", e);
      setError(
        e.response?.data?.error?.message ||
        e.response?.data?.message ||
        e.message ||
        "Render failed",
      );
    } finally {
      setRendering(false);
    }
  };

  return (
    <PageLayout title="Video Text Overlay" subtitle="Burn captions, titles, and watermarks into existing videos">
      <Box sx={{ maxWidth: 1200, mx: "auto", mt: 2, px: 2 }}>
        <Grid container spacing={3}>
          {/* Left: controls */}
          <Grid item xs={12} md={6}>
            <Paper elevation={4} sx={{ p: 3, borderRadius: 3 }}>
              <Stack spacing={2.5}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <TextFieldsIcon color="primary" />
                  <Typography variant="h6" fontWeight="bold">Source video</Typography>
                </Box>

                <FormControl variant="filled" fullWidth size="small">
                  <InputLabel>Pick a video</InputLabel>
                  <Select
                    value={selectedDocId}
                    onChange={(e) => { setSelectedDocId(e.target.value); setResult(null); }}
                    disabled={loadingVideos || rendering}
                    sx={{ borderRadius: 2 }}
                    MenuProps={{ PaperProps: { sx: { maxHeight: 360 } } }}
                  >
                    {videos.length === 0 && (
                      <MenuItem value="" disabled>
                        {loadingVideos ? "Loading..." : "No videos found — generate one first"}
                      </MenuItem>
                    )}
                    {videos.map((v) => (
                      <MenuItem key={v.id} value={v.id}>
                        <Box sx={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
                          <span>{v.filename}</span>
                          {v.size != null && (
                            <Typography component="span" variant="caption" sx={{ opacity: 0.5, ml: 2 }}>
                              {(v.size / 1024 / 1024).toFixed(1)} MB
                            </Typography>
                          )}
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <Typography variant="h6" fontWeight="bold">Text</Typography>
                <TextField
                  fullWidth
                  variant="filled"
                  multiline
                  minRows={2}
                  placeholder="Hello, world"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  inputProps={{ maxLength: 500 }}
                  helperText={`${text.length} / 500`}
                  sx={{ "& .MuiFilledInput-root": { borderRadius: 2 } }}
                />

                <Typography variant="h6" fontWeight="bold">Style</Typography>

                <Box>
                  <Typography variant="caption" fontWeight="bold">Font size: {fontSize}px</Typography>
                  <Slider
                    value={fontSize}
                    min={16}
                    max={144}
                    step={2}
                    onChange={(_e, v) => setFontSize(v)}
                  />
                </Box>

                <FormControl variant="filled" fullWidth size="small">
                  <InputLabel>Color</InputLabel>
                  <Select value={fontColor} onChange={(e) => setFontColor(e.target.value)} sx={{ borderRadius: 2 }}>
                    {COLOR_PRESETS.map((c) => (
                      <MenuItem key={c.value} value={c.value}>
                        <Box sx={{ display: "flex", alignItems: "center", gap: 1.5 }}>
                          <Box
                            sx={{
                              width: 18, height: 18, borderRadius: "4px",
                              backgroundColor: c.value, border: "1px solid rgba(255,255,255,0.2)",
                            }}
                          />
                          {c.label}
                        </Box>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <FormControl variant="filled" fullWidth size="small">
                  <InputLabel>Position</InputLabel>
                  <Select value={position} onChange={(e) => setPosition(e.target.value)} sx={{ borderRadius: 2 }}>
                    {POSITION_OPTIONS.map((p) => (
                      <MenuItem key={p.value} value={p.value}>{p.label}</MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
                  <FormControlLabel
                    control={<Switch checked={border} onChange={(e) => setBorder(e.target.checked)} size="small" />}
                    label={<Typography variant="caption">Outline</Typography>}
                  />
                  <FormControlLabel
                    control={<Switch checked={boxBackground} onChange={(e) => setBoxBackground(e.target.checked)} size="small" />}
                    label={<Typography variant="caption">Background box</Typography>}
                  />
                </Box>

                {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

                <Button
                  variant="contained"
                  size="large"
                  startIcon={rendering ? <CircularProgress size={20} color="inherit" /> : <RenderIcon />}
                  disabled={rendering || !selectedDocId || !text.trim()}
                  onClick={handleRender}
                  sx={{ py: 1.5, borderRadius: 2 }}
                >
                  {rendering ? "Rendering..." : "Render"}
                </Button>
              </Stack>
            </Paper>
          </Grid>

          {/* Right: preview */}
          <Grid item xs={12} md={6}>
            <Paper elevation={4} sx={{ p: 3, borderRadius: 3, minHeight: 360 }}>
              <Typography variant="h6" fontWeight="bold" mb={2}>
                {result ? "Result" : selectedVideo ? "Source" : "Preview"}
              </Typography>

              {result ? (
                <Stack spacing={1.5}>
                  <Typography variant="body2" sx={{ opacity: 0.7 }}>{result.filename}</Typography>
                  <Box sx={{ borderRadius: 2, overflow: "hidden", bgcolor: "#000" }}>
                    <video
                      key={result.id}
                      src={result.full_url}
                      controls
                      autoPlay
                      style={{ width: "100%", display: "block" }}
                    />
                  </Box>
                  <Box sx={{ display: "flex", gap: 1 }}>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={() => window.open(result.full_url, "_blank")}
                    >
                      Download
                    </Button>
                    <Button
                      variant="text"
                      size="small"
                      onClick={() => { setResult(null); }}
                    >
                      Render again
                    </Button>
                  </Box>
                </Stack>
              ) : selectedVideo && inputPreviewUrl ? (
                <Stack spacing={1.5}>
                  <Typography variant="body2" sx={{ opacity: 0.7 }}>{selectedVideo.filename}</Typography>
                  <Box sx={{ borderRadius: 2, overflow: "hidden", bgcolor: "#000" }}>
                    <video
                      key={selectedVideo.id}
                      src={inputPreviewUrl}
                      controls
                      style={{ width: "100%", display: "block" }}
                    />
                  </Box>
                  <Typography variant="caption" sx={{ opacity: 0.6 }}>
                    Original is preserved — Render writes a new file.
                  </Typography>
                </Stack>
              ) : (
                <Typography variant="body2" sx={{ opacity: 0.5 }}>
                  Pick a video on the left to preview it here. The result will appear here after Render.
                </Typography>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Box>
    </PageLayout>
  );
};

export default VideoTextOverlayPage;
