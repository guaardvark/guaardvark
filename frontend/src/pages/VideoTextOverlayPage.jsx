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
import { formatUiError } from "../utils/uiError";
import { useUiTranslation, translate } from "../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Same nine positions the backend supports. Labels are friendlier than the
// raw enum values; the value strings match _VALID_POSITIONS server-side.
const positionOptions = () => [
  { value: "top-left",      label: translate("videoTextOverlay.positions.topLeft") },
  { value: "top-center",    label: translate("videoTextOverlay.positions.topCenter") },
  { value: "top-right",     label: translate("videoTextOverlay.positions.topRight") },
  { value: "middle-left",   label: translate("videoTextOverlay.positions.middleLeft") },
  { value: "center",        label: translate("videoTextOverlay.positions.center") },
  { value: "middle-right",  label: translate("videoTextOverlay.positions.middleRight") },
  { value: "bottom-left",   label: translate("videoTextOverlay.positions.bottomLeft") },
  { value: "bottom-center", label: translate("videoTextOverlay.positions.bottomCenter") },
  { value: "bottom-right",  label: translate("videoTextOverlay.positions.bottomRight") },
];

const colorPresets = () => [
  { value: "white",   label: translate("videoTextOverlay.colors.white") },
  { value: "black",   label: translate("videoTextOverlay.colors.black") },
  { value: "yellow",  label: translate("videoTextOverlay.colors.yellow") },
  { value: "red",     label: translate("videoTextOverlay.colors.red") },
  { value: "#22c55e", label: translate("videoTextOverlay.colors.green") },
  { value: "#3b82f6", label: translate("videoTextOverlay.colors.blue") },
  { value: "#f97316", label: translate("videoTextOverlay.colors.orange") },
];

const VideoTextOverlayPage = () => {
  const { t } = useUiTranslation();
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
      setError(t("videoTextOverlay.loadFailed"));
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
      setError(t("videoTextOverlay.pickFirst"));
      return;
    }
    if (!text.trim()) {
      setError(t("videoTextOverlay.typeSomething"));
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
        t("videoTextOverlay.renderFailed"),
      );
    } finally {
      setRendering(false);
    }
  };

  return (
    <PageLayout title={t("videoTextOverlay.title")} subtitle={t("videoTextOverlay.subtitle")}>
      <Box sx={{ maxWidth: 1200, mx: "auto", mt: 2, px: 2 }}>
        <Grid container spacing={3}>
          {/* Left: controls */}
          <Grid item xs={12} md={6}>
            <Paper elevation={4} sx={{ p: 3, borderRadius: 3 }}>
              <Stack spacing={2.5}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <TextFieldsIcon color="primary" />
                  <Typography variant="h6" fontWeight="bold">{t("videoTextOverlay.sourceVideo")}</Typography>
                </Box>

                <FormControl variant="filled" fullWidth size="small">
                  <InputLabel>{t("videoTextOverlay.pickVideo")}</InputLabel>
                  <Select
                    value={selectedDocId}
                    onChange={(e) => { setSelectedDocId(e.target.value); setResult(null); }}
                    disabled={loadingVideos || rendering}
                    sx={{ borderRadius: 2 }}
                    MenuProps={{ PaperProps: { sx: { maxHeight: 360 } } }}
                  >
                    {videos.length === 0 && (
                      <MenuItem value="" disabled>
                        {loadingVideos ? t("videoTextOverlay.loading") : t("videoTextOverlay.noVideos")}
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

                <Typography variant="h6" fontWeight="bold">{t("videoTextOverlay.text")}</Typography>
                <TextField
                  fullWidth
                  variant="filled"
                  multiline
                  minRows={2}
                  placeholder={t("videoTextOverlay.textPlaceholder")}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  inputProps={{ maxLength: 500 }}
                  helperText={`${text.length} / 500`}
                  sx={{ "& .MuiFilledInput-root": { borderRadius: 2 } }}
                />

                <Typography variant="h6" fontWeight="bold">{t("videoTextOverlay.style")}</Typography>

                <Box>
                  <Typography variant="caption" fontWeight="bold">{t("videoTextOverlay.fontSize", { size: fontSize })}</Typography>
                  <Slider
                    value={fontSize}
                    min={16}
                    max={144}
                    step={2}
                    onChange={(_e, v) => setFontSize(v)}
                  />
                </Box>

                <FormControl variant="filled" fullWidth size="small">
                  <InputLabel>{t("videoTextOverlay.color")}</InputLabel>
                  <Select value={fontColor} onChange={(e) => setFontColor(e.target.value)} sx={{ borderRadius: 2 }}>
                    {colorPresets().map((c) => (
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
                  <InputLabel>{t("videoTextOverlay.position")}</InputLabel>
                  <Select value={position} onChange={(e) => setPosition(e.target.value)} sx={{ borderRadius: 2 }}>
                    {positionOptions().map((p) => (
                      <MenuItem key={p.value} value={p.value}>{p.label}</MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <Box sx={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
                  <FormControlLabel
                    control={<Switch checked={border} onChange={(e) => setBorder(e.target.checked)} size="small" />}
                    label={<Typography variant="caption">{t("videoTextOverlay.outline")}</Typography>}
                  />
                  <FormControlLabel
                    control={<Switch checked={boxBackground} onChange={(e) => setBoxBackground(e.target.checked)} size="small" />}
                    label={<Typography variant="caption">{t("videoTextOverlay.backgroundBox")}</Typography>}
                  />
                </Box>

                {error && <Alert severity="error" onClose={() => setError(null)}>{formatUiError(error)}</Alert>}

                <Button
                  variant="contained"
                  size="large"
                  startIcon={rendering ? <CircularProgress size={20} color="inherit" /> : <RenderIcon />}
                  disabled={rendering || !selectedDocId || !text.trim()}
                  onClick={handleRender}
                  sx={{ py: 1.5, borderRadius: 2 }}
                >
                  {rendering ? t("videoTextOverlay.rendering") : t("videoTextOverlay.render")}
                </Button>
              </Stack>
            </Paper>
          </Grid>

          {/* Right: preview */}
          <Grid item xs={12} md={6}>
            <Paper elevation={4} sx={{ p: 3, borderRadius: 3, minHeight: 360 }}>
              <Typography variant="h6" fontWeight="bold" mb={2}>
                {result ? t("videoTextOverlay.result") : selectedVideo ? t("videoTextOverlay.source") : t("videoTextOverlay.preview")}
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
                      {t("videoTextOverlay.download")}
                    </Button>
                    <Button
                      variant="text"
                      size="small"
                      onClick={() => { setResult(null); }}
                    >
                      {t("videoTextOverlay.renderAgain")}
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
                    {t("videoTextOverlay.originalPreserved")}
                  </Typography>
                </Stack>
              ) : (
                <Typography variant="body2" sx={{ opacity: 0.5 }}>
                  {t("videoTextOverlay.previewHint")}
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
