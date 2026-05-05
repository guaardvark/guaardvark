// frontend/src/pages/AudioFoundryPage.jsx
import React, { useCallback, useEffect, useState } from "react";
import {
  Box,
  Typography,
  Paper,
  Tabs,
  Tab,
  Grid,
  TextField,
  Button,
  Stack,
  Slider,
  CircularProgress,
  Card,
  CardContent,
  Chip,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  ListSubheader,
  Switch,
  FormControlLabel,
} from "@mui/material";
import {
  RecordVoiceOver as VoiceIcon,
  MusicNote as MusicIcon,
  GraphicEq as FxIcon,
  AutoFixHigh as MagicIcon,
  Save as SaveIcon,
  PlayArrow as PlayIcon,
  CloudUpload as UploadIcon,
  Close as CloseIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import WaveformPlayer from "../components/audio/WaveformPlayer";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Hardcoded fallback if /api/audio-foundry/voices is unreachable (e.g. plugin
// is stopped). The live source of truth is the GET /voices endpoint, which the
// frontend fetches on mount. The two should stay roughly aligned.
const FALLBACK_VOICES = [
  { label: "American Female", voices: [
    { id: "af_heart",   label: "Heart (default)" },
    { id: "af_bella",   label: "Bella" },
    { id: "af_nicole",  label: "Nicole" },
    { id: "af_sarah",   label: "Sarah" },
    { id: "af_sky",     label: "Sky" },
    { id: "af_alloy",   label: "Alloy" },
    { id: "af_aoede",   label: "Aoede" },
    { id: "af_jessica", label: "Jessica" },
    { id: "af_kore",    label: "Kore" },
    { id: "af_nova",    label: "Nova" },
    { id: "af_river",   label: "River" },
  ]},
  { label: "American Male", voices: [
    { id: "am_adam",    label: "Adam" },
    { id: "am_michael", label: "Michael" },
    { id: "am_eric",    label: "Eric" },
    { id: "am_echo",    label: "Echo" },
    { id: "am_fenrir",  label: "Fenrir" },
    { id: "am_liam",    label: "Liam" },
    { id: "am_onyx",    label: "Onyx" },
    { id: "am_puck",    label: "Puck" },
    { id: "am_santa",   label: "Santa" },
  ]},
  { label: "British Female", voices: [
    { id: "bf_emma",     label: "Emma" },
    { id: "bf_isabella", label: "Isabella" },
    { id: "bf_alice",    label: "Alice" },
    { id: "bf_lily",     label: "Lily" },
  ]},
  { label: "British Male", voices: [
    { id: "bm_george",  label: "George" },
    { id: "bm_lewis",   label: "Lewis" },
    { id: "bm_daniel",  label: "Daniel" },
    { id: "bm_fable",   label: "Fable" },
  ]},
  { label: "Spanish Female", voices: [
    { id: "ef_dora",    label: "Dora" },
  ]},
  { label: "Spanish Male", voices: [
    { id: "em_alex",    label: "Alex" },
    { id: "em_santa",   label: "Santa" },
  ]},
];

// Suno-style tag palette for the Music tab. These are also the vocabulary
// the LLM rewriter is taught to prefer (see music_prompt_rewriter.py), so
// keeping the two lists rough cousins helps the user's mental model.
const MUSIC_GENRES = [
  "Cinematic", "Synthwave", "Lo-fi", "Ambient", "Classical", "Orchestral",
  "Jazz", "Electronic", "Hip-hop", "Rock", "Folk", "Cyberpunk",
];
const MUSIC_MOODS = [
  "Dark", "Uplifting", "Melancholy", "Energetic",
  "Calm", "Epic", "Romantic", "Tense", "Dreamy",
];
const MUSIC_INSTRUMENTS = [
  "Piano", "Cello", "Violin", "Synth", "Electric guitar",
  "Acoustic guitar", "Drums", "Strings", "Brass", "Choir",
];

// Compose chip selections + free text into the LLM rewriter's input. The
// rewriter expects natural-ish input (it was trained on prose-to-tags), so
// we just join with commas and let it sort the vocabulary out.
const composeMusicIntent = (genres, moods, instruments, extraText) => {
  const parts = [
    ...genres,
    ...moods,
    ...instruments,
  ].map((s) => s.toLowerCase());
  const extra = (extraText || "").trim();
  if (extra) parts.push(extra);
  return parts.join(", ");
};

const AudioFoundryPage = () => {
  const [activeTab, setActiveTab] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  // Form States
  const [voiceText, setVoiceText] = useState("");
  const [fxPrompt, setFxPrompt] = useState("");
  const [duration, setDuration] = useState(10);

  // Music tab — Suno-style chip composition + LLM polish.
  // `musicGenres/Moods/Instruments` are arrays of selected chip labels.
  // `musicExtra` is the free-text "additional details" box.
  // `musicInstrumental` defaults true to match ACE-Step's most common use.
  // `musicPolish` controls whether we route through the LLM rewriter before generating.
  // `musicPreview` holds the rewriter's output (or null if not previewed yet).
  // `musicPolishing` is a separate spinner state for the rewrite call so we
  //   can show "Polishing..." distinct from the longer "Composing..." gen call.
  const [musicGenres, setMusicGenres] = useState([]);
  const [musicMoods, setMusicMoods] = useState([]);
  const [musicInstruments, setMusicInstruments] = useState([]);
  const [musicExtra, setMusicExtra] = useState("");
  const [musicInstrumental, setMusicInstrumental] = useState(true);
  const [musicPolish, setMusicPolish] = useState(true);
  const [musicPreview, setMusicPreview] = useState(null);
  const [musicPolishing, setMusicPolishing] = useState(false);
  const [voiceBackend, setVoiceBackend] = useState("auto");
  const [voiceId, setVoiceId] = useState("af_heart");
  const [voiceGroups, setVoiceGroups] = useState(FALLBACK_VOICES);

  // Chatterbox reference clips for zero-shot voice cloning. `referenceClip`
  // holds the currently-selected clip object {id, filename, path}; null means
  // "use Chatterbox's default voice". `voiceClipLibrary` is the list of
  // previously-uploaded clips fetched from /voice-clips.
  const [referenceClip, setReferenceClip] = useState(null);
  const [voiceClipLibrary, setVoiceClipLibrary] = useState([]);
  const [uploadingClip, setUploadingClip] = useState(false);

  // Pull library of existing reference clips. Refreshes after every successful
  // upload/delete so the picker stays current.
  const refreshVoiceClips = useCallback(() => {
    axios.get(`${API_BASE}/audio-foundry/voice-clips`)
      .then((res) => setVoiceClipLibrary(res.data?.clips || []))
      .catch(() => { /* plugin offline; leave library empty */ });
  }, []);
  useEffect(() => { refreshVoiceClips(); }, [refreshVoiceClips]);

  const handleClipUpload = async (file) => {
    if (!file) return;
    setUploadingClip(true);
    setError(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("name", file.name);
      const res = await axios.post(
        `${API_BASE}/audio-foundry/voice-clips/upload`,
        fd,
        { headers: { "Content-Type": "multipart/form-data" } },
      );
      setReferenceClip(res.data);
      refreshVoiceClips();
    } catch (err) {
      console.error("Voice clip upload failed:", err);
      setError(err.response?.data?.error || "Import failed.");
    } finally {
      setUploadingClip(false);
    }
  };

  // (Backend exposes DELETE /voice-clips/<id> for future delete-from-library UI;
  //  not yet wired here — user can manage clips from the filesystem if needed.)

  // Pull the live voice catalog from the backend on mount. Falls back to the
  // hardcoded FALLBACK_VOICES if the audio_foundry plugin is offline. This
  // way new Kokoro voices appear without a frontend redeploy.
  useEffect(() => {
    let cancelled = false;
    axios.get(`${API_BASE}/audio-foundry/voices`)
      .then((res) => {
        if (cancelled) return;
        const groups = res.data?.kokoro?.groups;
        if (Array.isArray(groups) && groups.length > 0) {
          setVoiceGroups(groups);
        }
      })
      .catch(() => {
        // audio_foundry plugin offline — quietly use FALLBACK_VOICES.
      });
    return () => { cancelled = true; };
  }, []);

  const handleTabChange = (event, newValue) => {
    setActiveTab(newValue);
    setResult(null);
    setError(null);
  };

  const generateAudio = async (type) => {
    setLoading(true);
    setError(null);
    try {
      let endpoint = "";
      let payload = {};

      if (type === "voice") {
        endpoint = "/generate/voice";
        payload = { text: voiceText, backend: voiceBackend };
        // Kokoro uses voice_id; Chatterbox ignores it (zero-shot voice cloning
        // takes a reference clip instead). Send for auto + kokoro so the
        // dispatcher's Kokoro-fallback path also picks up the selected voice.
        if (voiceBackend !== "chatterbox") {
          payload.voice_id = voiceId;
        }
        // Chatterbox: pass the absolute reference clip path if the user
        // selected one. Without it Chatterbox falls back to its default voice.
        // For "auto" we also forward the clip — if Chatterbox runs, it uses
        // the clip; if it falls back to Kokoro, the clip is silently ignored.
        if (referenceClip && voiceBackend !== "kokoro") {
          payload.reference_clip_path = referenceClip.path;
        }
      } else if (type === "fx") {
        endpoint = "/generate/fx";
        payload = { prompt: fxPrompt, duration_s: duration };
      }

      const res = await axios.post(`${API_BASE}/audio-foundry${endpoint}`, payload);
      // The audio_foundry service registers the file as a Document and returns
      // its `document_id`. The browser plays it back via the Documents download
      // route (which serves WAV/MP3 inline so <audio> can stream it). The raw
      // filesystem `path` in the response is for backend logs/debug only — it's
      // never reachable from the browser.
      const docId = res.data.document_id;
      if (!docId) {
        throw new Error(
          "Audio was generated but couldn't be registered as a Document. " +
          "Check audio_foundry.log — the registration POST to /api/outputs/register may have failed."
        );
      }
      setResult({
        ...res.data,
        full_url: `${API_BASE}/files/document/${docId}/download`,
      });
    } catch (err) {
      console.error("Audio generation failed:", err);
      setError(err.response?.data?.detail || "Generation failed. Please check backend logs.");
    } finally {
      setLoading(false);
    }
  };

  const handleDownload = (url) => {
    window.open(url, "_blank");
  };

  // ----- Music tab helpers -------------------------------------------------

  // Toggle a chip in/out of one of the three category arrays. Auto-clears any
  // existing preview because the preview is keyed to the inputs that produced
  // it — letting it linger past an input change would let the user generate
  // music that doesn't match the chips visible on screen.
  const toggleMusicChip = (category, value) => {
    setMusicPreview(null);
    const setter = {
      genre: setMusicGenres,
      mood: setMusicMoods,
      instrument: setMusicInstruments,
    }[category];
    if (!setter) return;
    setter((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );
  };

  // Same auto-invalidate logic for the free-text box.
  const handleMusicExtraChange = (value) => {
    setMusicPreview(null);
    setMusicExtra(value);
  };

  const handleMusicInstrumentalToggle = (value) => {
    setMusicPreview(null);
    setMusicInstrumental(value);
  };

  // Ask the backend to translate the chip+text composition into a clean
  // ACE-Step tag prompt + negative_prompt via the local LLM. On any failure
  // (Ollama down, model refused, JSON gibberish), the endpoint returns a
  // `fallback: true` payload with the user's raw text — we surface a one-line
  // warning and let them continue with the un-polished prompt.
  const polishMusicPrompt = async () => {
    const intent = composeMusicIntent(musicGenres, musicMoods, musicInstruments, musicExtra);
    if (!intent) {
      setError("Pick at least one genre/mood/instrument or add a description first.");
      return;
    }
    setMusicPolishing(true);
    setError(null);
    try {
      const res = await axios.post(`${API_BASE}/audio-foundry/rewrite-music-prompt`, {
        text: intent,
        instrumental: musicInstrumental,
      });
      const { fallback, reason, style_prompt, negative_prompt, tags_used } = res.data || {};
      setMusicPreview({
        fallback: !!fallback,
        reason: reason || "",
        style_prompt: style_prompt || intent,
        negative_prompt: negative_prompt || "",
        tags_used: Array.isArray(tags_used) ? tags_used : [],
      });
    } catch (err) {
      console.error("Music prompt polish failed:", err);
      // Network-level failure — synthesize a fallback preview so the user can
      // still generate without the LLM in the loop.
      setMusicPreview({
        fallback: true,
        reason: err.response?.data?.error || "Polish service unreachable",
        style_prompt: intent,
        negative_prompt: "",
        tags_used: [],
      });
    } finally {
      setMusicPolishing(false);
    }
  };

  // Final generation: send {style_prompt, negative_prompt, duration_s,
  // instrumental_only} to /generate/music. Resolves the prompt from the
  // preview if one exists, otherwise from the raw chip+text composition.
  const generateMusic = async () => {
    const intent = composeMusicIntent(musicGenres, musicMoods, musicInstruments, musicExtra);
    if (!intent && !musicPreview) {
      setError("Pick at least one genre/mood/instrument or add a description first.");
      return;
    }

    let stylePrompt = intent;
    let negativePrompt = "";
    if (musicPreview) {
      stylePrompt = musicPreview.style_prompt || intent;
      negativePrompt = musicPreview.negative_prompt || "";
    }

    setLoading(true);
    setError(null);
    try {
      const payload = {
        style_prompt: stylePrompt,
        duration_s: duration,
        instrumental_only: musicInstrumental,
      };
      if (negativePrompt) payload.negative_prompt = negativePrompt;

      const res = await axios.post(`${API_BASE}/audio-foundry/generate/music`, payload);
      const docId = res.data.document_id;
      if (!docId) {
        throw new Error(
          "Audio was generated but couldn't be registered as a Document. " +
          "Check audio_foundry.log — the registration POST to /api/outputs/register may have failed."
        );
      }
      setResult({
        ...res.data,
        full_url: `${API_BASE}/files/document/${docId}/download`,
      });
    } catch (err) {
      console.error("Music generation failed:", err);
      setError(err.response?.data?.detail || err.response?.data?.error || "Generation failed. Please check backend logs.");
    } finally {
      setLoading(false);
    }
  };

  const getTabColor = () => {
    if (activeTab === 0) return "#9c27b0"; // Voice: Purple
    if (activeTab === 1) return "#2196f3"; // Music: Blue
    return "#ff9800"; // FX: Orange
  };

  return (
    <PageLayout title="Audio Studio" subtitle="Professional AI Audio Production">
      <Box sx={{ maxWidth: 1200, mx: "auto", mt: 2, px: 2 }}>
        <Grid container spacing={3}>
          {/* Left Panel: Controls */}
          <Grid item xs={12} md={5}>
            <Paper elevation={4} sx={{ borderRadius: 3, overflow: "hidden", border: "1px solid rgba(255,255,255,0.05)" }}>
              <Tabs
                value={activeTab}
                onChange={handleTabChange}
                variant="fullWidth"
                sx={{ 
                  borderBottom: 1, 
                  borderColor: "divider",
                  "& .MuiTabs-indicator": { backgroundColor: getTabColor() }
                }}
              >
                <Tab icon={<VoiceIcon />} label="Voice" sx={{ "&.Mui-selected": { color: "#9c27b0" } }} />
                <Tab icon={<MusicIcon />} label="Music" sx={{ "&.Mui-selected": { color: "#2196f3" } }} />
                <Tab icon={<FxIcon />} label="FX Lab" sx={{ "&.Mui-selected": { color: "#ff9800" } }} />
              </Tabs>

              <Box sx={{ p: 4, minHeight: 450 }}>
                {activeTab === 0 && (
                  <Stack spacing={3}>
                    <Typography variant="h6" fontWeight="bold">Neural Voiceover</Typography>
                    <TextField
                      multiline
                      rows={8}
                      fullWidth
                      variant="filled"
                      placeholder="Enter script for narration..."
                      value={voiceText}
                      onChange={(e) => setVoiceText(e.target.value)}
                      sx={{ "& .MuiFilledInput-root": { borderRadius: 2 } }}
                    />
                    <Stack direction="row" spacing={1}>
                      {["auto", "chatterbox", "kokoro"].map((b) => (
                        <Chip
                          key={b}
                          label={b.toUpperCase()}
                          clickable
                          sx={{
                            fontWeight: "bold",
                            backgroundColor: voiceBackend === b ? "#9c27b0" : "rgba(255,255,255,0.05)",
                            color: "white",
                            "&:hover": { backgroundColor: "#7b1fa2" }
                          }}
                          onClick={() => setVoiceBackend(b)}
                        />
                      ))}
                    </Stack>
                    {voiceBackend !== "chatterbox" && (
                      <FormControl variant="filled" fullWidth size="small">
                        <InputLabel>Voice {voiceBackend === "auto" ? "(used by Kokoro path)" : ""}</InputLabel>
                        <Select
                          value={voiceId}
                          onChange={(e) => setVoiceId(e.target.value)}
                          MenuProps={{ PaperProps: { sx: { maxHeight: 360 } } }}
                          sx={{ borderRadius: 2 }}
                        >
                          {voiceGroups.flatMap((group) => [
                            <ListSubheader key={group.label}>{group.label}</ListSubheader>,
                            ...group.voices.map((v) => (
                              <MenuItem key={v.id} value={v.id}>
                                {v.label} <Typography component="span" variant="caption" sx={{ ml: 1, opacity: 0.6 }}>{v.id}</Typography>
                              </MenuItem>
                            )),
                          ])}
                        </Select>
                      </FormControl>
                    )}
                    {voiceBackend !== "kokoro" && (
                      <Stack spacing={1.5}>
                        <Typography variant="body2" sx={{ opacity: 0.85 }}>
                          {voiceBackend === "chatterbox"
                            ? "Reference clip (5–10s of clean speech in the voice you want to clone). Optional — leave empty to use Chatterbox's default voice."
                            : "Optional Chatterbox reference clip. Used only if Chatterbox runs (auto mode)."}
                        </Typography>

                        {referenceClip ? (
                          <Card variant="outlined" sx={{ p: 1.5, borderRadius: 2 }}>
                            <Stack direction="row" spacing={1} alignItems="center">
                              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                                <Typography variant="body2" fontWeight="bold" noWrap>
                                  {referenceClip.display_name || referenceClip.filename}
                                </Typography>
                                <audio
                                  controls
                                  src={`${API_BASE}/audio-foundry/voice-clips/${referenceClip.id}/download`}
                                  style={{ width: "100%", height: 32, marginTop: 4 }}
                                />
                              </Box>
                              <Button
                                size="small"
                                onClick={() => setReferenceClip(null)}
                                startIcon={<CloseIcon />}
                                sx={{ flexShrink: 0 }}
                              >
                                Clear
                              </Button>
                            </Stack>
                          </Card>
                        ) : (
                          <Button
                            component="label"
                            variant="outlined"
                            startIcon={uploadingClip ? <CircularProgress size={16} /> : <UploadIcon />}
                            disabled={uploadingClip}
                            sx={{ borderRadius: 2, justifyContent: "flex-start" }}
                          >
                            {uploadingClip ? "Importing..." : "Import reference clip"}
                            <input
                              hidden
                              type="file"
                              accept="audio/*,.wav,.mp3,.ogg,.flac,.m4a"
                              onChange={(e) => handleClipUpload(e.target.files?.[0])}
                            />
                          </Button>
                        )}

                        {voiceClipLibrary.length > 0 && !referenceClip && (
                          <FormControl variant="filled" size="small" fullWidth>
                            <InputLabel>...or pick a previously imported clip</InputLabel>
                            <Select
                              value=""
                              onChange={(e) => {
                                const c = voiceClipLibrary.find((x) => x.id === e.target.value);
                                if (c) setReferenceClip(c);
                              }}
                              MenuProps={{ PaperProps: { sx: { maxHeight: 300 } } }}
                              sx={{ borderRadius: 2 }}
                            >
                              {voiceClipLibrary.map((c) => (
                                <MenuItem key={c.id} value={c.id}>
                                  <Box sx={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
                                    <span>{c.filename}</span>
                                    <Typography component="span" variant="caption" sx={{ opacity: 0.5, ml: 2 }}>
                                      {(c.size_bytes / 1024).toFixed(0)} KB
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        )}
                      </Stack>
                    )}
                    <Button
                      variant="contained"
                      size="large"
                      startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <MagicIcon />}
                      disabled={loading || !voiceText}
                      onClick={() => generateAudio("voice")}
                      sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#9c27b0", "&:hover": { backgroundColor: "#7b1fa2" } }}
                    >
                      {loading ? "Synthesizing..." : "Generate Voiceover"}
                    </Button>
                  </Stack>
                )}

                {activeTab === 1 && (
                  <Stack spacing={2.5}>
                    <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <Typography variant="h6" fontWeight="bold">Music Composer</Typography>
                      <FormControlLabel
                        control={
                          <Switch
                            checked={musicPolish}
                            onChange={(e) => { setMusicPolish(e.target.checked); setMusicPreview(null); }}
                            size="small"
                          />
                        }
                        label={<Typography variant="caption">Polish with AI</Typography>}
                        sx={{ m: 0 }}
                      />
                    </Box>

                    {/* Genre row — pick the anchor that keeps ACE-Step from drifting to country */}
                    <Box>
                      <Typography variant="caption" fontWeight="bold" color="text.secondary">Genre</Typography>
                      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
                        {MUSIC_GENRES.map((g) => (
                          <Chip
                            key={g}
                            label={g}
                            size="small"
                            color={musicGenres.includes(g) ? "primary" : "default"}
                            variant={musicGenres.includes(g) ? "filled" : "outlined"}
                            onClick={() => toggleMusicChip("genre", g)}
                            clickable
                          />
                        ))}
                      </Box>
                    </Box>

                    {/* Mood row */}
                    <Box>
                      <Typography variant="caption" fontWeight="bold" color="text.secondary">Mood</Typography>
                      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
                        {MUSIC_MOODS.map((m) => (
                          <Chip
                            key={m}
                            label={m}
                            size="small"
                            color={musicMoods.includes(m) ? "secondary" : "default"}
                            variant={musicMoods.includes(m) ? "filled" : "outlined"}
                            onClick={() => toggleMusicChip("mood", m)}
                            clickable
                          />
                        ))}
                      </Box>
                    </Box>

                    {/* Instrument row */}
                    <Box>
                      <Typography variant="caption" fontWeight="bold" color="text.secondary">Instruments</Typography>
                      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
                        {MUSIC_INSTRUMENTS.map((i) => (
                          <Chip
                            key={i}
                            label={i}
                            size="small"
                            color={musicInstruments.includes(i) ? "info" : "default"}
                            variant={musicInstruments.includes(i) ? "filled" : "outlined"}
                            onClick={() => toggleMusicChip("instrument", i)}
                            clickable
                          />
                        ))}
                      </Box>
                    </Box>

                    {/* Free-text "additional details" — anything the chips don't cover */}
                    <TextField
                      fullWidth
                      variant="filled"
                      multiline
                      minRows={2}
                      placeholder="Additional details (optional): 'driving rhythm for a chase scene', 'with rain', etc."
                      value={musicExtra}
                      onChange={(e) => handleMusicExtraChange(e.target.value)}
                      sx={{ "& .MuiFilledInput-root": { borderRadius: 2 } }}
                    />

                    <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <FormControlLabel
                        control={
                          <Switch
                            checked={musicInstrumental}
                            onChange={(e) => handleMusicInstrumentalToggle(e.target.checked)}
                            size="small"
                          />
                        }
                        label={<Typography variant="caption">Instrumental only</Typography>}
                      />
                    </Box>

                    <Box>
                      <Typography variant="caption" fontWeight="bold">Duration: {duration} seconds</Typography>
                      <Slider
                        value={duration}
                        min={10}
                        max={240}
                        step={10}
                        onChange={(e, v) => setDuration(v)}
                        sx={{ color: "#2196f3" }}
                      />
                    </Box>

                    {/* Polish preview — shown only when Polish is on AND we have a result */}
                    {musicPolish && musicPreview && (
                      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "action.hover" }}>
                        <Stack spacing={1.25}>
                          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <Typography variant="caption" fontWeight="bold" color="primary">
                              {musicPreview.fallback ? "Polished prompt (fallback — Ollama unavailable)" : "Polished prompt"}
                            </Typography>
                            <Button
                              size="small"
                              onClick={() => setMusicPreview(null)}
                              sx={{ minWidth: 0, textTransform: "none" }}
                            >
                              Discard
                            </Button>
                          </Box>
                          {musicPreview.fallback && musicPreview.reason && (
                            <Alert severity="warning" variant="outlined" sx={{ py: 0 }}>
                              {musicPreview.reason}
                            </Alert>
                          )}
                          <Box>
                            <Typography variant="caption" color="text.secondary">Style</Typography>
                            <Typography variant="body2">{musicPreview.style_prompt}</Typography>
                          </Box>
                          {musicPreview.negative_prompt && (
                            <Box>
                              <Typography variant="caption" color="text.secondary">Avoid</Typography>
                              <Typography variant="body2" sx={{ opacity: 0.75 }}>
                                {musicPreview.negative_prompt}
                              </Typography>
                            </Box>
                          )}
                        </Stack>
                      </Paper>
                    )}

                    {/* Two-button flow:
                        Polish=ON without preview → "Polish & Preview" calls the rewriter.
                        Polish=ON with preview, OR Polish=OFF → "Compose Music" generates. */}
                    {musicPolish && !musicPreview ? (
                      <Button
                        variant="contained"
                        size="large"
                        startIcon={musicPolishing ? <CircularProgress size={20} color="inherit" /> : <MagicIcon />}
                        disabled={musicPolishing || loading}
                        onClick={polishMusicPrompt}
                        sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#2196f3", "&:hover": { backgroundColor: "#1976d2" } }}
                      >
                        {musicPolishing ? "Polishing..." : "Polish & Preview"}
                      </Button>
                    ) : (
                      <Button
                        variant="contained"
                        size="large"
                        startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <MusicIcon />}
                        disabled={
                          loading ||
                          (!composeMusicIntent(musicGenres, musicMoods, musicInstruments, musicExtra) && !musicPreview)
                        }
                        onClick={generateMusic}
                        sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#2196f3", "&:hover": { backgroundColor: "#1976d2" } }}
                      >
                        {loading ? "Composing..." : "Compose Music"}
                      </Button>
                    )}
                  </Stack>
                )}

                {activeTab === 2 && (
                  <Stack spacing={3}>
                    <Typography variant="h6" fontWeight="bold">FX & Foley Generator</Typography>
                    <TextField
                      fullWidth
                      variant="filled"
                      placeholder="Sound: 'A lightsaber igniting in a vacuum'..."
                      value={fxPrompt}
                      onChange={(e) => setFxPrompt(e.target.value)}
                      sx={{ "& .MuiFilledInput-root": { borderRadius: 2 } }}
                    />
                    <Box>
                      <Typography variant="caption" fontWeight="bold">Duration: {duration > 47 ? 47 : duration}s (Max 47s)</Typography>
                      <Slider
                        value={duration > 47 ? 47 : duration}
                        min={1}
                        max={47}
                        onChange={(e, v) => setDuration(v)}
                        sx={{ color: "#ff9800" }}
                      />
                    </Box>
                    <Button
                      variant="contained"
                      size="large"
                      startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <FxIcon />}
                      disabled={loading || !fxPrompt}
                      onClick={() => generateAudio("fx")}
                      sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#ff9800", "&:hover": { backgroundColor: "#e68a00" } }}
                    >
                      {loading ? "Generating..." : "Generate Sound FX"}
                    </Button>
                  </Stack>
                )}

                {error && <Alert severity="error" sx={{ mt: 2, borderRadius: 2 }}>{error}</Alert>}
              </Box>
            </Paper>
          </Grid>

          {/* Right Panel: Studio Preview */}
          <Grid item xs={12} md={7}>
            <Paper elevation={0} sx={{ 
              p: 4, 
              height: "100%", 
              borderRadius: 3, 
              backgroundColor: "rgba(255, 255, 255, 0.02)", 
              border: "1px dashed rgba(255, 255, 255, 0.1)",
              display: "flex",
              flexDirection: "column"
            }}>
              <Typography variant="h6" fontWeight="bold" gutterBottom color="text.secondary">Studio Monitor</Typography>
              
              {!result && !loading && (
                <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", opacity: 0.3 }}>
                  <PlayIcon sx={{ fontSize: 120, mb: 2 }} />
                  <Typography variant="h5">Ready for Production</Typography>
                  <Typography>Select a tool and enter a prompt to begin.</Typography>
                </Box>
              )}

              {loading && (
                <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                  <CircularProgress size={80} sx={{ mb: 3, color: getTabColor() }} />
                  <Typography variant="h5" fontWeight="bold">Synthesizing Waves...</Typography>
                  <Typography color="text.secondary">Generating high-fidelity audio on local GPU.</Typography>
                </Box>
              )}

              {result && (
                <Stack spacing={4}>
                  <WaveformPlayer
                    src={result.full_url}
                    title={activeTab === 0 ? "Voiceover Master" : activeTab === 1 ? "Music Master" : "SFX Master"}
                    duration={result.duration_s}
                    onDownload={handleDownload}
                    color={getTabColor()}
                  />

                  <Card variant="outlined" sx={{ borderRadius: 3, backgroundColor: "rgba(0,0,0,0.2)" }}>
                    <CardContent>
                      <Typography variant="overline" color="text.secondary" fontWeight="bold">Technical Specs</Typography>
                      <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={6}>
                          <Typography variant="caption" display="block" color="text.secondary">BACKEND</Typography>
                          <Typography variant="body2" fontWeight="bold">{result.meta?.backend}</Typography>
                        </Grid>
                        <Grid item xs={6}>
                          <Typography variant="caption" display="block" color="text.secondary">FIDELITY</Typography>
                          <Typography variant="body2" fontWeight="bold">{result.sample_rate} Hz</Typography>
                        </Grid>
                        <Grid item xs={12}>
                          <Typography variant="caption" display="block" color="text.secondary">OUTPUT PATH</Typography>
                          <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.7rem", opacity: 0.7 }}>{result.path}</Typography>
                        </Grid>
                      </Grid>
                    </CardContent>
                  </Card>

                  <Alert severity="success" icon={<SaveIcon />} sx={{ borderRadius: 2 }}>
                    Asset successfully registered in your <b>Audio</b> library.
                  </Alert>
                </Stack>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Box>
    </PageLayout>
  );
};

export default AudioFoundryPage;
