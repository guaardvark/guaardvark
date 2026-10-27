// frontend/src/pages/AudioFoundryPage.jsx
import React, { useCallback, useEffect, useState, useRef, Suspense } from "react";
import { formatUiError } from "../utils/uiError";
import { fileNameOf } from "../utils/displayPath";
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
import { useSearchParams } from "react-router-dom";
import PageLayout from "../components/layout/PageLayout";
import WaveformPlayer from "../components/audio/WaveformPlayer";
import axios from "axios";
import { ActionButton, DashboardStrip, DashboardTile } from "../components/settings/ui";
import AlertSnackbar from "../components/common/AlertSnackbar";
import VoiceConsentDialog from "../components/audio/VoiceConsentDialog";
import VoiceClipManager from "../components/audio/VoiceClipManager";
import { confirmVoiceClipConsent, voiceClipAudioUrl } from "../api/audioFoundryService";
import SettingsIcon from "@mui/icons-material/Settings";
import lazyWithReload from "../utils/lazyWithReload";
import { useUiTranslation, translate, Trans } from "../i18n";

const AudioFoundryModelsModal = lazyWithReload(() => import("../components/modals/AudioFoundryModelsModal"));

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

// Hardcoded fallback if /api/audio-foundry/voices is unreachable (the backend
// answers it from the checkout's catalog while the plugin is stopped, so this
// is for a backend that does not answer). The live source of truth is the GET
// /voices endpoint, which the frontend fetches on mount. The two should stay
// roughly aligned.
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

// The tags above go to the rewriter in English; this is how a chip shows them.
const musicTagLabel = (tag) => ({
  Cinematic: translate("audio.musicTags.cinematic"),
  Synthwave: translate("audio.musicTags.synthwave"),
  "Lo-fi": translate("audio.musicTags.lofi"),
  Ambient: translate("audio.musicTags.ambient"),
  Classical: translate("audio.musicTags.classical"),
  Orchestral: translate("audio.musicTags.orchestral"),
  Jazz: translate("audio.musicTags.jazz"),
  Electronic: translate("audio.musicTags.electronic"),
  "Hip-hop": translate("audio.musicTags.hipHop"),
  Rock: translate("audio.musicTags.rock"),
  Folk: translate("audio.musicTags.folk"),
  Cyberpunk: translate("audio.musicTags.cyberpunk"),
  Dark: translate("audio.musicTags.dark"),
  Uplifting: translate("audio.musicTags.uplifting"),
  Melancholy: translate("audio.musicTags.melancholy"),
  Energetic: translate("audio.musicTags.energetic"),
  Calm: translate("audio.musicTags.calm"),
  Epic: translate("audio.musicTags.epic"),
  Romantic: translate("audio.musicTags.romantic"),
  Tense: translate("audio.musicTags.tense"),
  Dreamy: translate("audio.musicTags.dreamy"),
  Piano: translate("audio.musicTags.piano"),
  Cello: translate("audio.musicTags.cello"),
  Violin: translate("audio.musicTags.violin"),
  Synth: translate("audio.musicTags.synth"),
  "Electric guitar": translate("audio.musicTags.electricGuitar"),
  "Acoustic guitar": translate("audio.musicTags.acousticGuitar"),
  Drums: translate("audio.musicTags.drums"),
  Strings: translate("audio.musicTags.strings"),
  Brass: translate("audio.musicTags.brass"),
  Choir: translate("audio.musicTags.choir"),
})[tag] || tag;

// "Default" in the voice picker: send no voice_id and let Audio Foundry pick
// (Chatterbox's stock voice in auto, Kokoro's default voice in kokoro).
const DEFAULT_VOICE = "default";

// The engine a voice request will use (plugins/audio_foundry/backends/voice_gen.py):
// in auto a reference clip means Chatterbox, a picked built-in voice means Kokoro.
const voiceEngine = (voiceBackend, voiceId, referenceClip) => {
  if (voiceBackend !== "auto") return voiceBackend;
  if (referenceClip) return "chatterbox";
  if (voiceId && voiceId !== DEFAULT_VOICE) return "kokoro";
  return "auto";
};

// Compose chip selections + free text into the LLM rewriter's input. The
// rewriter expects natural-ish input (it was trained on prose-to-tags), so
// we just join with commas and let it sort the vocabulary out.
const idsForTab = (tab, voiceBackend, musicModel, voiceId, referenceClip) => {
  if (tab === 0) {
    const engine = voiceEngine(voiceBackend, voiceId, referenceClip);
    if (engine === "kokoro") return { ids: ["kokoro"], any: false };
    if (engine === "chatterbox") return { ids: ["chatterbox"], any: false };
    return { ids: ["chatterbox", "kokoro"], any: true };
  }
  // Music model values are the catalog ids.
  if (tab === 1) return { ids: [musicModel], any: false };
  return { ids: ["stable-audio-open"], any: false };
};

const missingCatalogRows = (catalog, ids, any) => {
  const rows = ids.map((id) => catalog.find((m) => m.id === id)).filter(Boolean);
  if (!rows.length) return [];
  if (any) return rows.some((m) => m.installed) ? [] : rows;
  return rows.filter((m) => !m.installed);
};

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

// ACE-Step 1.5 reads a sentence. Chips are hints appended to it, not the caption.
const composeAce15Query = (genres, moods, instruments, extraText) => {
  const hints = [...genres, ...moods, ...instruments].map((s) => s.toLowerCase());
  const text = (extraText || "").trim();
  if (text && hints.length) return `${text}\n\nAlso: ${hints.join(", ")}`;
  if (text) return text;
  return hints.join(", ");
};

const METERS = [
  { value: "4", label: "4/4" },
  { value: "3", label: "3/4" },
  { value: "6", label: "6/8" },
  { value: "2", label: "2/4" },
];

// [code sent to ACE-Step, name as shown].
const vocalLanguages = () => [
  ["", translate("audio.languages.auto")],
  ["en", translate("audio.languages.en")],
  ["es", translate("audio.languages.es")],
  ["fr", translate("audio.languages.fr")],
  ["de", translate("audio.languages.de")],
  ["it", translate("audio.languages.it")],
  ["pt", translate("audio.languages.pt")],
  ["ja", translate("audio.languages.ja")],
  ["ko", translate("audio.languages.ko")],
  ["zh", translate("audio.languages.zh")],
  ["yue", translate("audio.languages.yue")],
  ["hi", translate("audio.languages.hi")],
  ["bn", translate("audio.languages.bn")],
  ["ar", translate("audio.languages.ar")],
  ["ru", translate("audio.languages.ru")],
];

const meterLabel = (value) => METERS.find((m) => m.value === String(value))?.label || value || "";

const languageLabel = (code) => {
  if (!code || code === "unknown") return "";
  const row = vocalLanguages().find(([value]) => value === code);
  return row ? row[1] : code;
};

const lockSummary = (locks) => {
  if (!locks) return "";
  const parts = [];
  if (locks.bpm) parts.push(`${locks.bpm} bpm`);
  if (locks.keyscale) parts.push(locks.keyscale);
  if (locks.timesignature) parts.push(meterLabel(locks.timesignature));
  if (locks.vocal_language) parts.push(locks.vocal_language);
  return parts.join(", ");
};

const AudioFoundryPage = () => {
  const { t } = useUiTranslation();
  const [activeTab, setActiveTab] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  // Async-job state: a long transcript returns 202 + job_id and we poll.
  const [progress, setProgress] = useState(null);  // {current,total,status} while a job runs
  const [jobId, setJobId] = useState(null);
  const pollRef = useRef(null);

  // Form States
  const [voiceText, setVoiceText] = useState("");
  const [fxPrompt, setFxPrompt] = useState("");
  const [duration, setDuration] = useState(60);
  // FX Lab has its own length: Music runs 10-240 s, Stable Audio Open stops at 47 s.
  const [fxDuration, setFxDuration] = useState(10);

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
  // Music backend: the sidecar's ACE-Step v1 (default), the sidecar's optional
  // ACE-Step 1.5, or MiniMax Music 3 through ComfyUI, which sings tagged lyrics.
  // The optional ones are offered only when installed (the registry says),
  // otherwise the option names the modal.
  const [musicModel, setMusicModel] = useState("ace-step");
  const [musicLyrics, setMusicLyrics] = useState("");
  const [catalog, setCatalog] = useState([]);
  const [pluginInfo, setPluginInfo] = useState({ running: false, enabled: false, status: "unknown" });
  const [modelsModalOpen, setModelsModalOpen] = useState(false);
  const [highlightModelId, setHighlightModelId] = useState(null);
  const [toast, setToast] = useState({ open: false, message: "", severity: "info" });
  const showMessage = useCallback((message, severity = "info") => {
    setToast({ open: true, message, severity });
  }, []);
  const refreshCatalog = useCallback(() => {
    axios.get(`${API_BASE}/audio-foundry/models`)
      .then(({ data }) => {
        if (!data?.success) return;
        setCatalog(data.models || []);
        setPluginInfo(data.plugin || { running: false, enabled: false, status: "unknown" });
      })
      .catch(() => {});
  }, []);
  useEffect(() => { refreshCatalog(); }, [refreshCatalog]);
  // /audio?models=<id> opens Manage models on that row; the Cast page's voice
  // picker links here for a voice that is not installed.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const wanted = searchParams.get("models");
    if (wanted === null) return;
    setHighlightModelId(wanted || null);
    setModelsModalOpen(true);
    const next = new URLSearchParams(searchParams);
    next.delete("models");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);
  const music3Ready = catalog.find((m) => m.id === "minimax-music3-int8")?.installed ?? null;
  const ace15Ready = catalog.find((m) => m.id === "ace-step-1.5")?.installed ?? null;
  const [musicPolish, setMusicPolish] = useState(true);
  const [musicPreview, setMusicPreview] = useState(null);
  const [musicPolishing, setMusicPolishing] = useState(false);
  // ACE-Step 1.5 arrangement from the loaded planner. Editable, then composed.
  const [arrangement, setArrangement] = useState(null);
  const [arranging, setArranging] = useState(false);
  const [voiceBackend, setVoiceBackend] = useState("auto");
  const [voiceId, setVoiceId] = useState(DEFAULT_VOICE);
  const [voiceGroups, setVoiceGroups] = useState(FALLBACK_VOICES);

  // Chatterbox reference clips for zero-shot voice cloning. `referenceClip`
  // holds the currently-selected clip object {id, filename, path, consented};
  // null means "use Chatterbox's default voice". `voiceClipLibrary` is the
  // list of previously-uploaded clips fetched from /voice-clips.
  const [referenceClip, setReferenceClip] = useState(null);
  const [voiceClipLibrary, setVoiceClipLibrary] = useState([]);
  const [uploadingClip, setUploadingClip] = useState(false);
  // A clip is cloned only after the person confirms the consent statement:
  // before a new clip is imported, or before an older clip without a record
  // is used. {file} or {clip} while the dialog is open.
  const [consentStatement, setConsentStatement] = useState("");
  const [consentRequest, setConsentRequest] = useState(null);

  // Pull library of existing reference clips. Refreshes after every successful
  // upload/delete so the picker stays current.
  const refreshVoiceClips = useCallback(() => {
    axios.get(`${API_BASE}/audio-foundry/voice-clips`)
      .then((res) => {
        setVoiceClipLibrary(res.data?.clips || []);
        if (res.data?.consent_statement) setConsentStatement(res.data.consent_statement);
      })
      .catch(() => { /* plugin offline; leave library empty */ });
  }, []);
  useEffect(() => { refreshVoiceClips(); }, [refreshVoiceClips]);

  const handleClipUpload = (file) => {
    if (!file) return;
    setConsentRequest({ file });
  };

  const handlePickClip = (clip) => {
    if (!clip) return;
    if (clip.consented) {
      setReferenceClip(clip);
    } else {
      setConsentRequest({ clip });
    }
  };

  const handleConsentConfirmed = async () => {
    const request = consentRequest;
    if (!request) return;
    setUploadingClip(true);
    setError(null);
    try {
      if (request.file) {
        const fd = new FormData();
        fd.append("file", request.file);
        fd.append("name", request.file.name);
        fd.append("consent_confirmed", "true");
        const res = await axios.post(
          `${API_BASE}/audio-foundry/voice-clips/upload`,
          fd,
          { headers: { "Content-Type": "multipart/form-data" } },
        );
        if (!res.data?.consented) {
          throw new Error(t("audio.page.consentNotSaved"));
        }
        setReferenceClip(res.data);
      } else {
        await confirmVoiceClipConsent(request.clip);
        setReferenceClip({ ...request.clip, consented: true });
      }
      setConsentRequest(null);
      refreshVoiceClips();
    } catch (err) {
      console.error("Voice clip consent failed:", err);
      setError(formatUiError(err.response?.data?.error) || err.message || t("audio.page.importFailed"));
      setConsentRequest(null);
    } finally {
      setUploadingClip(false);
    }
  };

  // A clip whose consent was withdrawn, or that was deleted, stops being the
  // selected reference; picking a withdrawn clip again asks for consent.
  const handleClipRemoved = (clip) => {
    setReferenceClip((current) =>
      current && (current.filename || current.id) === (clip.filename || clip.id) ? null : current,
    );
  };

  // Pull the live voice catalog from the backend on mount. Falls back to the
  // hardcoded FALLBACK_VOICES if the backend does not answer. This way new
  // Kokoro voices appear without a frontend redeploy.
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
        // Backend unreachable: quietly use FALLBACK_VOICES.
      });
    return () => { cancelled = true; };
  }, []);

  // Per-backend availability from /status — e.g. FX (Stable Audio Open)
  // requires a CUDA GPU and reports unavailable on machines without one.
  // Unknown (plugin offline / older plugin) leaves everything enabled.
  const [fxUnavailableReason, setFxUnavailableReason] = useState(null);
  useEffect(() => {
    let cancelled = false;
    axios.get(`${API_BASE}/audio-foundry/status`)
      .then((res) => {
        if (cancelled) return;
        const fx = res.data?.backends?.fx;
        if (fx && fx.available === false) {
          setFxUnavailableReason(
            fx.unavailable_reason || t("audio.page.fxUnavailable")
          );
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const handleTabChange = (event, newValue) => {
    setActiveTab(newValue);
    setResult(null);
    setError(null);
  };

  // ----- Async job polling -------------------------------------------------
  // Large transcripts/songs run as a background job in the plugin (no more 504
  // on a held-open HTTP request). Submit returns 202 + job_id; we poll status.

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  // Stop polling if the user navigates away mid-job.
  useEffect(() => () => stopPolling(), []);

  // The plugin registers the file as a Document and returns `document_id`. The
  // browser plays it back via the Documents download route (which serves
  // WAV/MP3 inline). The raw filesystem `path` is for backend logs only — it's
  // never reachable from the browser.
  const finishWithResult = (data) => {
    const docId = data.document_id;
    if (!docId) {
      setError(
        data.registration_error ||
        t("audio.page.notRegistered")
      );
    } else {
      setResult({ ...data, full_url: `${API_BASE}/files/document/${docId}/download` });
    }
  };

  const beginPolling = (id) => {
    stopPolling();
    setProgress({ current: 0, total: 0, status: "queued" });
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await axios.get(`${API_BASE}/audio-foundry/jobs/${id}`);
        setProgress({
          current: data.progress?.current ?? 0,
          total: data.progress?.total ?? 0,
          status: data.status,
        });
        if (data.status === "done") {
          stopPolling();
          finishWithResult(data.result || {});
          setProgress(null); setJobId(null); setLoading(false);
        } else if (data.status === "error") {
          stopPolling();
          setError(data.error || t("audio.page.jobFailed"));
          setProgress(null); setJobId(null); setLoading(false);
        } else if (data.status === "cancelled") {
          stopPolling();
          setProgress(null); setJobId(null); setLoading(false);
        }
      } catch (err) {
        // Job lost (plugin restarted) → stop and tell the user. Transient
        // network blips just retry on the next tick.
        if (err.response?.status === 404) {
          stopPolling();
          setError(t("audio.page.jobLost"));
          setProgress(null); setJobId(null); setLoading(false);
        }
      }
    }, 3000);
  };

  const handleCancelJob = async () => {
    if (!jobId) return;
    try { await axios.post(`${API_BASE}/audio-foundry/jobs/${jobId}/cancel`); } catch (_) { /* best-effort cancel */ }
    // The poll loop will observe status === "cancelled" and reset.
  };

  const tabNeed = idsForTab(activeTab, voiceBackend, musicModel, voiceId, referenceClip);
  const missingRows = missingCatalogRows(catalog, tabNeed.ids, tabNeed.any);
  const pluginRunning = !!pluginInfo.running;
  const openModels = (modelId) => {
    setHighlightModelId(modelId || missingRows[0]?.id || null);
    setModelsModalOpen(true);
  };

  const generateAudio = async (type) => {
    if (!pluginRunning) {
      setError(t("audio.page.notRunning"));
      openModels();
      return;
    }
    if (missingRows.length) {
      setError(t("audio.page.notInstalled", { name: missingRows[0].name }));
      openModels(missingRows[0].id);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      let endpoint = "";
      let payload = {};

      if (type === "voice") {
        endpoint = "/generate/voice";
        payload = { text: voiceText, backend: voiceBackend };
        // A reference clip (Chatterbox) and a built-in voice (Kokoro) each
        // decide the voice, so at most one is sent. In auto either one picks
        // its engine and Audio Foundry reports an error rather than speak
        // with a different voice; with neither, auto uses Chatterbox's stock
        // voice (Kokoro's default when Chatterbox cannot run), as before.
        if (referenceClip && voiceBackend !== "kokoro") {
          payload.reference_clip_path = referenceClip.path;
        } else if (voiceBackend !== "chatterbox" && voiceId !== DEFAULT_VOICE) {
          payload.voice_id = voiceId;
        }
      } else if (type === "fx") {
        endpoint = "/generate/fx";
        payload = { prompt: fxPrompt, duration_s: fxDuration };
      }

      const res = await axios.post(
        `${API_BASE}/audio-foundry${endpoint}`,
        { ...payload, async: true },
      );
      if (res.status === 202 && res.data?.job_id) {
        setJobId(res.data.job_id);
        beginPolling(res.data.job_id);
        return;  // keep `loading` true; polling clears it on terminal status
      }
      finishWithResult(res.data);  // inline (short text) — unchanged behavior
    } catch (err) {
      console.error("Audio generation failed:", err);
      setError(
        formatUiError(err.response?.data?.detail || err.response?.data?.error) ||
        t("audio.page.generationFailed"),
      );
      setLoading(false);
    } finally {
      // Inline + error paths set loading=false above / in catch; the async path
      // intentionally leaves it true until the poll loop resolves.
      if (!pollRef.current) setLoading(false);
    }
  };

  // The route serves audio inline, so opening it in a tab only played it.
  const handleDownload = (url) => {
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  // ----- Music tab helpers -------------------------------------------------

  // Toggle a chip in/out of one of the three category arrays. Auto-clears any
  // existing preview because the preview is keyed to the inputs that produced
  // it — letting it linger past an input change would let the user generate
  // music that doesn't match the chips visible on screen.
  const toggleMusicChip = (category, value) => {
    setMusicPreview(null);
    setArrangement(null);
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
    setArrangement(null);
    setMusicExtra(value);
  };

  const handleMusicInstrumentalToggle = (value) => {
    setMusicPreview(null);
    setArrangement(null);
    setMusicInstrumental(value);
  };

  // Ask the backend to translate the chip+text composition into a clean
  // ACE-Step tag prompt via the local LLM (negative_prompt comes back empty:
  // ACE-Step v1 has no negative conditioning). On any failure
  // (Ollama down, model refused, JSON gibberish), the endpoint returns a
  // `fallback: true` payload with the user's raw text — we surface a one-line
  // warning and let them continue with the un-polished prompt.
  const polishMusicPrompt = async () => {
    const intent = composeMusicIntent(musicGenres, musicMoods, musicInstruments, musicExtra);
    if (!intent) {
      setError(t("audio.page.needMusicInput"));
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
        reason: formatUiError(err.response?.data?.error) || t("audio.page.polishUnreachable"),
        style_prompt: intent,
        negative_prompt: "",
        tags_used: [],
      });
    } finally {
      setMusicPolishing(false);
    }
  };

  const isAce15 = musicModel === "ace-step-1.5";

  // Ask the 1.5 planner, already loaded with the turbo model, for a caption
  // and the tempo, key and meter. Nothing is rendered.
  const arrangeMusic = async () => {
    const query = composeAce15Query(musicGenres, musicMoods, musicInstruments, musicExtra);
    if (!query) {
      setError(t("audio.page.needSongDescription"));
      return;
    }
    setArranging(true);
    setError(null);
    try {
      const res = await axios.post(`${API_BASE}/audio-foundry/plan-music`, {
        style_prompt: query,
        lyrics: musicInstrumental ? "" : musicLyrics.trim(),
        instrumental_only: musicInstrumental,
        duration_s: duration,
        model: "ace-step-1.5",
      });
      const next = res.data?.arrangement;
      if (!next?.caption) {
        setError(t("audio.page.noCaption"));
        return;
      }
      setArrangement(next);
    } catch (err) {
      console.error("Music arrange failed:", err);
      const detail = err.response?.data?.detail;
      setError(
        (typeof detail === "string" && detail) ||
        formatUiError(err.response?.data?.error) ||
        t("audio.page.arrangeFailed")
      );
    } finally {
      setArranging(false);
    }
  };

  const updateArrangement = (patch) => {
    setArrangement((prev) => (prev ? { ...prev, ...patch } : prev));
  };

  // Final generation: send {style_prompt, negative_prompt, duration_s,
  // instrumental_only} to /generate/music. Resolves the prompt from the
  // preview if one exists, otherwise from the raw chip+text composition.
  // ACE-Step 1.5 sends the edited arrangement and does not plan a second time.
  const generateMusic = async () => {
    const intent = isAce15
      ? composeAce15Query(musicGenres, musicMoods, musicInstruments, musicExtra)
      : composeMusicIntent(musicGenres, musicMoods, musicInstruments, musicExtra);
    if (!intent && !musicPreview && !arrangement) {
      setError(t("audio.page.needMusicInput"));
      return;
    }
    if (isAce15 && !arrangement?.caption?.trim()) {
      setError(t("audio.page.arrangeFirst"));
      return;
    }

    let stylePrompt = intent;
    let negativePrompt = "";
    if (isAce15) {
      stylePrompt = arrangement.caption.trim();
    } else if (musicPreview) {
      stylePrompt = musicPreview.style_prompt || intent;
      negativePrompt = musicPreview.negative_prompt || "";
    }

    if (musicModel !== "minimax-music3-int8" && !pluginRunning) {
      setError(t("audio.page.notRunning"));
      openModels();
      return;
    }
    if (missingRows.length) {
      setError(t("audio.page.notInstalled", { name: missingRows[0].name }));
      openModels(missingRows[0].id);
      return;
    }
    if (isAce15 && arrangement?.bpm) {
      const bpm = Number(arrangement.bpm);
      if (!Number.isInteger(bpm) || bpm < 30 || bpm > 300) {
        setError(t("audio.page.badTempo"));
        return;
      }
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
      if (isAce15) {
        payload.model = "ace-step-1.5";
        payload.arrangement_accepted = true;
        if (!musicInstrumental && (arrangement.lyrics || "").trim()) {
          payload.lyrics = arrangement.lyrics.trim();
        }
        const bpm = Number(arrangement.bpm);
        if (bpm) payload.bpm = bpm;
        if (arrangement.keyscale) payload.keyscale = arrangement.keyscale;
        if (arrangement.timesignature) payload.timesignature = arrangement.timesignature;
        if (arrangement.vocal_language && arrangement.vocal_language !== "unknown") {
          payload.vocal_language = arrangement.vocal_language;
        }
      } else {
        if (!musicInstrumental && musicLyrics.trim()) payload.lyrics = musicLyrics.trim();
        if (musicModel !== "ace-step") payload.model = musicModel;
      }

      const res = await axios.post(
        `${API_BASE}/audio-foundry/generate/music`,
        { ...payload, async: true },
      );
      if (res.status === 202 && res.data?.job_id) {
        setJobId(res.data.job_id);
        beginPolling(res.data.job_id);
        return;  // keep `loading` true; polling clears it on terminal status
      }
      finishWithResult(res.data);  // inline (short song) — unchanged behavior
    } catch (err) {
      console.error("Music generation failed:", err);
      setError(err.response?.data?.detail || formatUiError(err.response?.data?.error) || t("audio.page.generationFailed"));
      setLoading(false);
    } finally {
      if (!pollRef.current) setLoading(false);
    }
  };

  const getTabColor = () => {
    if (activeTab === 0) return "#9c27b0"; // Voice: Purple
    if (activeTab === 1) return "#2196f3"; // Music: Blue
    return "#ff9800"; // FX: Orange
  };

  const installedCount = catalog.filter((m) => m.installed).length;
  const missingCount = catalog.filter((m) => !m.installed).length;

  return (
    <PageLayout
      title={t("audio.page.title")}
      subtitle={t("audio.page.subtitle")}
      actions={
        <ActionButton onClick={() => openModels()}>{t("audio.page.manageModels")}</ActionButton>
      }
    >
      <Box sx={{ maxWidth: 1200, mx: "auto", mt: 2, px: 2 }}>
        <DashboardStrip>
          <DashboardTile
            label={t("audio.page.plugin")}
            value={pluginRunning ? t("audio.page.running") : (pluginInfo.status || t("audio.page.stopped"))}
            sub={pluginRunning ? "Audio Foundry" : t("audio.page.startFromModels")}
            tone={pluginRunning ? "ok" : "error"}
            onClick={() => openModels()}
          />
          <DashboardTile
            label={t("audio.page.weights")}
            value={catalog.length ? t("audio.page.installedCount", { count: installedCount }) : t("audio.page.checking")}
            sub={missingCount ? t("audio.page.missingCount", { count: missingCount }) : (catalog.length ? t("audio.page.ready") : "")}
            tone={missingCount ? "warn" : catalog.length ? "ok" : "off"}
            onClick={() => openModels()}
          />
        </DashboardStrip>

        {!pluginRunning && (
          <Alert
            severity="warning"
            sx={{ mt: 2, mb: 1 }}
            action={
              <Button color="inherit" size="small" startIcon={<SettingsIcon />} onClick={() => openModels()}>
                {t("audio.page.manageModels")}
              </Button>
            }
          >
            {t("audio.page.notRunningAlert")}
          </Alert>
        )}

        {pluginRunning && missingRows.length > 0 && (
          <Alert
            severity="warning"
            sx={{ mt: 2, mb: 1 }}
            action={
              <Button
                color="inherit"
                size="small"
                startIcon={<SettingsIcon />}
                onClick={() => openModels(missingRows[0].id)}
              >
                {t("audio.page.downloadModel", { name: missingRows[0].name })}
              </Button>
            }
          >
            <Trans
              i18nKey="audio.page.notInstalledAlert"
              values={{ name: missingRows[0].name }}
              components={{ bold: <strong /> }}
            />
          </Alert>
        )}

        <Grid container spacing={3} sx={{ mt: 0.5 }}>
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
                <Tab icon={<VoiceIcon />} label={t("audio.page.voiceTab")} sx={{ "&.Mui-selected": { color: "#9c27b0" } }} />
                <Tab icon={<MusicIcon />} label={t("audio.page.musicTab")} sx={{ "&.Mui-selected": { color: "#2196f3" } }} />
                <Tab icon={<FxIcon />} label={t("audio.page.fxTab")} sx={{ "&.Mui-selected": { color: "#ff9800" } }} />
              </Tabs>

              <Box sx={{ p: 4, minHeight: 450 }}>
                {activeTab === 0 && (
                  <Stack spacing={3}>
                    <Typography variant="h6" fontWeight="bold">{t("audio.voice.title")}</Typography>
                    <TextField
                      multiline
                      rows={8}
                      fullWidth
                      variant="filled"
                      placeholder={t("audio.voice.scriptPlaceholder")}
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
                    {voiceBackend !== "chatterbox" && !(voiceBackend === "auto" && referenceClip) && (
                      <FormControl variant="filled" fullWidth size="small">
                        <InputLabel>{t("audio.voice.voice")}</InputLabel>
                        <Select
                          value={voiceId}
                          onChange={(e) => setVoiceId(e.target.value)}
                          MenuProps={{ PaperProps: { sx: { maxHeight: 360 } } }}
                          sx={{ borderRadius: 2 }}
                        >
                          <MenuItem value={DEFAULT_VOICE}>
                            {voiceBackend === "auto"
                              ? t("audio.voice.automatic")
                              : t("audio.voice.defaultHeart")}
                          </MenuItem>
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
                            ? t("audio.voice.referenceHintChatterbox")
                            : t("audio.voice.referenceHintAuto")}
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
                                  src={voiceClipAudioUrl(referenceClip)}
                                  style={{ width: "100%", height: 32, marginTop: 4 }}
                                />
                              </Box>
                              <Button
                                size="small"
                                onClick={() => setReferenceClip(null)}
                                startIcon={<CloseIcon />}
                                sx={{ flexShrink: 0 }}
                              >
                                {t("audio.voice.clear")}
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
                            {uploadingClip ? t("audio.voice.importing") : t("audio.voice.importClip")}
                            <input
                              hidden
                              type="file"
                              accept="audio/*,.wav,.mp3,.ogg,.flac,.m4a"
                              onChange={(e) => {
                                handleClipUpload(e.target.files?.[0]);
                                // Picking the same file again after Cancel must reopen the dialog.
                                e.target.value = "";
                              }}
                            />
                          </Button>
                        )}

                        {voiceClipLibrary.length > 0 && !referenceClip && (
                          <FormControl variant="filled" size="small" fullWidth>
                            <InputLabel>{t("audio.voice.pickPrevious")}</InputLabel>
                            <Select
                              value=""
                              onChange={(e) => {
                                handlePickClip(voiceClipLibrary.find((x) => x.filename === e.target.value));
                              }}
                              MenuProps={{ PaperProps: { sx: { maxHeight: 300 } } }}
                              sx={{ borderRadius: 2 }}
                            >
                              {voiceClipLibrary.map((c) => (
                                <MenuItem key={c.filename} value={c.filename}>
                                  <Box sx={{ display: "flex", justifyContent: "space-between", width: "100%" }}>
                                    <span>{c.filename}</span>
                                    <Typography component="span" variant="caption" sx={{ opacity: 0.5, ml: 2 }}>
                                      {c.consented ? "" : `${t("audio.voice.needsConsent")} · `}{(c.size_bytes / 1024).toFixed(0)} KB
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        )}

                        <VoiceClipManager
                          clips={voiceClipLibrary}
                          onChanged={refreshVoiceClips}
                          onRemoved={handleClipRemoved}
                        />
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
                      {loading ? t("audio.voice.synthesizing") : t("audio.voice.generate")}
                    </Button>
                  </Stack>
                )}

                {activeTab === 1 && (
                  <Stack spacing={2.5}>
                    <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <Typography variant="h6" fontWeight="bold">{t("audio.music.title")}</Typography>
                      {!isAce15 && (
                        <FormControlLabel
                          control={
                            <Switch
                              checked={musicPolish}
                              onChange={(e) => { setMusicPolish(e.target.checked); setMusicPreview(null); }}
                              size="small"
                            />
                          }
                          label={<Typography variant="caption">{t("audio.music.polishWithAi")}</Typography>}
                          sx={{ m: 0 }}
                        />
                      )}
                    </Box>

                    {/* Genre row — pick the anchor that keeps ACE-Step v1 from drifting to country.
                        On 1.5 these are hints; the description is what the planner reads. */}
                    <Box>
                      <Typography variant="caption" fontWeight="bold" color="text.secondary">{t("audio.music.genre")}</Typography>
                      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
                        {MUSIC_GENRES.map((g) => (
                          <Chip
                            key={g}
                            label={musicTagLabel(g)}
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
                      <Typography variant="caption" fontWeight="bold" color="text.secondary">{t("audio.music.mood")}</Typography>
                      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
                        {MUSIC_MOODS.map((m) => (
                          <Chip
                            key={m}
                            label={musicTagLabel(m)}
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
                      <Typography variant="caption" fontWeight="bold" color="text.secondary">{t("audio.music.instruments")}</Typography>
                      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75, mt: 0.5 }}>
                        {MUSIC_INSTRUMENTS.map((i) => (
                          <Chip
                            key={i}
                            label={musicTagLabel(i)}
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
                    {isAce15 && (
                      <Typography variant="caption" color="text.secondary">
                        {t("audio.music.describeHint")}
                      </Typography>
                    )}
                    <TextField
                      fullWidth
                      variant="filled"
                      multiline
                      minRows={isAce15 ? 3 : 2}
                      placeholder={isAce15
                        ? t("audio.music.describePlaceholder")
                        : t("audio.music.detailsPlaceholder")}
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
                        label={<Typography variant="caption">{t("audio.music.instrumentalOnly")}</Typography>}
                      />
                      <TextField
                        select
                        size="small"
                        label={t("audio.music.model")}
                        value={musicModel}
                        onChange={(e) => {
                          setMusicModel(e.target.value);
                          setMusicPreview(null);
                          setArrangement(null);
                        }}
                        sx={{ minWidth: 220 }}
                        SelectProps={{ native: true }}
                      >
                        <option value="ace-step">ACE-Step v1 (Audio Foundry)</option>
                        <option value="ace-step-1.5" disabled={ace15Ready === false}>
                          {ace15Ready === false ? t("audio.music.installInModels", { model: "ACE-Step 1.5" }) : "ACE-Step 1.5 (Audio Foundry)"}
                        </option>
                        <option value="minimax-music3-int8" disabled={music3Ready === false}>
                          {music3Ready === false ? t("audio.music.installInModels", { model: "MiniMax Music 3" }) : t("audio.music.music3SingsLyrics")}
                        </option>
                      </TextField>
                    </Box>
                    {!musicInstrumental && (
                      <TextField
                        label={t("audio.music.lyricsOptional")}
                        multiline
                        minRows={3}
                        maxRows={8}
                        size="small"
                        value={musicLyrics}
                        onChange={(e) => { setMusicLyrics(e.target.value); setArrangement(null); }}
                        placeholder={t("audio.music.lyricsPlaceholder")}
                        helperText={t("audio.music.lyricsHelp")}
                        fullWidth
                      />
                    )}

                    <Box>
                      <Typography variant="caption" fontWeight="bold">{t("audio.music.duration", { seconds: duration })}</Typography>
                      <Slider
                        value={duration}
                        min={10}
                        max={240}
                        step={10}
                        onChange={(e, v) => { setDuration(v); setArrangement(null); }}
                        sx={{ color: "#2196f3" }}
                      />
                    </Box>

                    {/* v1 polish preview. 1.5 shows an editable arrangement from its planner. */}
                    {!isAce15 && musicPolish && musicPreview && (
                      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "action.hover" }}>
                        <Stack spacing={1.25}>
                          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <Typography variant="caption" fontWeight="bold" color="primary">
                              {musicPreview.fallback ? t("audio.music.polishedFallback") : t("audio.music.polished")}
                            </Typography>
                            <Button
                              size="small"
                              onClick={() => setMusicPreview(null)}
                              sx={{ minWidth: 0, textTransform: "none" }}
                            >
                              {t("audio.music.discard")}
                            </Button>
                          </Box>
                          {musicPreview.fallback && musicPreview.reason && (
                            <Alert severity="warning" variant="outlined" sx={{ py: 0 }}>
                              {musicPreview.reason}
                            </Alert>
                          )}
                          <Box>
                            <Typography variant="caption" color="text.secondary">{t("audio.music.style")}</Typography>
                            <Typography variant="body2">{musicPreview.style_prompt}</Typography>
                          </Box>
                          {musicPreview.negative_prompt && (
                            <Box>
                              <Typography variant="caption" color="text.secondary">{t("audio.music.avoid")}</Typography>
                              <Typography variant="body2" sx={{ opacity: 0.75 }}>
                                {musicPreview.negative_prompt}
                              </Typography>
                            </Box>
                          )}
                        </Stack>
                      </Paper>
                    )}

                    {isAce15 && arrangement && (
                      <Paper variant="outlined" sx={{ p: 2, borderRadius: 2, bgcolor: "action.hover" }}>
                        <Stack spacing={1.25}>
                          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <Typography variant="caption" fontWeight="bold" color="primary">
                              {t("audio.music.arrangement")}
                            </Typography>
                            <Button
                              size="small"
                              onClick={() => setArrangement(null)}
                              sx={{ minWidth: 0, textTransform: "none" }}
                            >
                              {t("audio.music.discard")}
                            </Button>
                          </Box>
                          <Typography variant="caption" color="text.secondary">
                            {t("audio.music.arrangementHint")}
                          </Typography>
                          {lockSummary(arrangement.locks) && (
                            <Typography variant="caption" color="text.secondary">
                              {t("audio.music.keptFromDescription", { summary: lockSummary(arrangement.locks) })}
                            </Typography>
                          )}
                          <TextField
                            label={t("audio.music.caption")}
                            multiline
                            minRows={3}
                            size="small"
                            value={arrangement.caption || ""}
                            onChange={(e) => updateArrangement({ caption: e.target.value })}
                            fullWidth
                          />
                          {!musicInstrumental && (
                            <TextField
                              label={t("audio.music.lyrics")}
                              multiline
                              minRows={4}
                              maxRows={10}
                              size="small"
                              value={arrangement.lyrics || ""}
                              onChange={(e) => updateArrangement({ lyrics: e.target.value })}
                              fullWidth
                            />
                          )}
                          <Stack direction="row" spacing={1}>
                            <TextField
                              label={t("audio.music.bpm")}
                              size="small"
                              type="number"
                              value={arrangement.bpm ?? ""}
                              onChange={(e) => updateArrangement({ bpm: e.target.value === "" ? null : Number(e.target.value) })}
                              inputProps={{ min: 30, max: 300 }}
                              sx={{ width: 100 }}
                            />
                            <TextField
                              label={t("audio.music.key")}
                              size="small"
                              value={arrangement.keyscale || ""}
                              onChange={(e) => updateArrangement({ keyscale: e.target.value })}
                              placeholder="A minor"
                              sx={{ flex: 1 }}
                            />
                          </Stack>
                          <Stack direction="row" spacing={1}>
                            <TextField
                              select
                              label={t("audio.music.meter")}
                              size="small"
                              value={arrangement.timesignature || ""}
                              onChange={(e) => updateArrangement({ timesignature: e.target.value })}
                              sx={{ flex: 1 }}
                              SelectProps={{ native: true }}
                            >
                              <option value="">{t("audio.languages.auto")}</option>
                              {METERS.map((m) => (
                                <option key={m.value} value={m.value}>{m.label}</option>
                              ))}
                            </TextField>
                            <TextField
                              select
                              label={t("audio.music.language")}
                              size="small"
                              InputLabelProps={{ shrink: true }}
                              value={arrangement.vocal_language && arrangement.vocal_language !== "unknown" ? arrangement.vocal_language : ""}
                              onChange={(e) => updateArrangement({ vocal_language: e.target.value })}
                              sx={{ flex: 1 }}
                              SelectProps={{ native: true }}
                            >
                              {vocalLanguages().map(([code, label]) => (
                                <option key={code || "auto"} value={code}>{label}</option>
                              ))}
                              {arrangement.vocal_language
                                && arrangement.vocal_language !== "unknown"
                                && !vocalLanguages().some(([code]) => code === arrangement.vocal_language) && (
                                <option value={arrangement.vocal_language}>{arrangement.vocal_language}</option>
                              )}
                            </TextField>
                          </Stack>
                          {arrangement.genres && (
                            <Typography variant="caption" color="text.secondary">
                              {t("audio.music.genres", { genres: arrangement.genres })}
                            </Typography>
                          )}
                        </Stack>
                      </Paper>
                    )}

                    {/* 1.5 arranges before compose. v1 polishes into tags, or composes directly. */}
                    {isAce15 && !arrangement ? (
                      <Button
                        variant="contained"
                        size="large"
                        startIcon={arranging ? <CircularProgress size={20} color="inherit" /> : <MagicIcon />}
                        disabled={arranging || loading}
                        onClick={arrangeMusic}
                        sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#2196f3", "&:hover": { backgroundColor: "#1976d2" } }}
                      >
                        {arranging ? t("audio.music.arranging") : t("audio.music.arrange")}
                      </Button>
                    ) : !isAce15 && musicPolish && !musicPreview ? (
                      <Button
                        variant="contained"
                        size="large"
                        startIcon={musicPolishing ? <CircularProgress size={20} color="inherit" /> : <MagicIcon />}
                        disabled={musicPolishing || loading}
                        onClick={polishMusicPrompt}
                        sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#2196f3", "&:hover": { backgroundColor: "#1976d2" } }}
                      >
                        {musicPolishing ? t("audio.music.polishing") : t("audio.music.polishPreview")}
                      </Button>
                    ) : (
                      <Button
                        variant="contained"
                        size="large"
                        startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <MusicIcon />}
                        disabled={
                          loading ||
                          (isAce15
                            ? !arrangement?.caption?.trim()
                            : (!composeMusicIntent(musicGenres, musicMoods, musicInstruments, musicExtra) && !musicPreview))
                        }
                        onClick={generateMusic}
                        sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#2196f3", "&:hover": { backgroundColor: "#1976d2" } }}
                      >
                        {loading ? t("audio.music.composing") : t("audio.music.compose")}
                      </Button>
                    )}
                  </Stack>
                )}

                {activeTab === 2 && (
                  <Stack spacing={3}>
                    <Typography variant="h6" fontWeight="bold">{t("audio.fx.title")}</Typography>
                    {fxUnavailableReason && (
                      <Alert severity="info" sx={{ borderRadius: 2 }}>
                        {fxUnavailableReason}
                      </Alert>
                    )}
                    <TextField
                      fullWidth
                      variant="filled"
                      placeholder={t("audio.fx.placeholder")}
                      value={fxPrompt}
                      onChange={(e) => setFxPrompt(e.target.value)}
                      sx={{ "& .MuiFilledInput-root": { borderRadius: 2 } }}
                    />
                    <Box>
                      <Typography variant="caption" fontWeight="bold">{t("audio.fx.duration", { seconds: fxDuration })}</Typography>
                      <Slider
                        value={fxDuration}
                        min={1}
                        max={47}
                        onChange={(e, v) => setFxDuration(v)}
                        sx={{ color: "#ff9800" }}
                      />
                    </Box>
                    <Button
                      variant="contained"
                      size="large"
                      startIcon={loading ? <CircularProgress size={20} color="inherit" /> : <FxIcon />}
                      disabled={loading || !fxPrompt || !!fxUnavailableReason}
                      onClick={() => generateAudio("fx")}
                      sx={{ py: 1.5, borderRadius: 2, backgroundColor: "#ff9800", "&:hover": { backgroundColor: "#e68a00" } }}
                    >
                      {loading ? t("audio.fx.generating") : t("audio.fx.generate")}
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
              <Typography variant="h6" fontWeight="bold" gutterBottom color="text.secondary">{t("audio.monitor.title")}</Typography>
              
              {!result && !loading && (
                <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", opacity: 0.3 }}>
                  <PlayIcon sx={{ fontSize: 120, mb: 2 }} />
                  <Typography variant="h5">{t("audio.monitor.ready")}</Typography>
                  <Typography>{t("audio.monitor.readyHint")}</Typography>
                </Box>
              )}

              {loading && (
                <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                  <CircularProgress
                    size={80}
                    sx={{ mb: 3, color: getTabColor() }}
                    variant={progress && progress.total > 0 ? "determinate" : "indeterminate"}
                    value={progress && progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : undefined}
                  />
                  <Typography variant="h5" fontWeight="bold">{t("audio.monitor.synthesizing")}</Typography>
                  <Typography color="text.secondary">
                    {progress && progress.total > 0
                      ? t("audio.monitor.chunk", {
                        current: progress.current,
                        total: progress.total,
                        percent: Math.round((progress.current / progress.total) * 100),
                      })
                      : progress && progress.status === "queued"
                        ? t("audio.monitor.queued")
                        : t("audio.monitor.generating")}
                  </Typography>
                  {jobId && (
                    <Button
                      size="small"
                      color="warning"
                      variant="outlined"
                      onClick={handleCancelJob}
                      sx={{ mt: 2 }}
                    >
                      {t("audio.monitor.cancel")}
                    </Button>
                  )}
                </Box>
              )}

              {result && (
                <Stack spacing={4}>
                  <WaveformPlayer
                    src={result.full_url}
                    title={activeTab === 0 ? t("audio.monitor.voiceMaster") : activeTab === 1 ? t("audio.monitor.musicMaster") : t("audio.monitor.sfxMaster")}
                    duration={result.duration_s}
                    onDownload={handleDownload}
                    color={getTabColor()}
                  />

                  <Card variant="outlined" sx={{ borderRadius: 3, backgroundColor: "rgba(0,0,0,0.2)" }}>
                    <CardContent>
                      <Typography variant="overline" color="text.secondary" fontWeight="bold">{t("audio.monitor.specs")}</Typography>
                      <Grid container spacing={2} sx={{ mt: 1 }}>
                        <Grid item xs={6}>
                          <Typography variant="caption" display="block" color="text.secondary">{t("audio.monitor.backend")}</Typography>
                          <Typography variant="body2" fontWeight="bold">{result.meta?.backend}</Typography>
                        </Grid>
                        <Grid item xs={6}>
                          <Typography variant="caption" display="block" color="text.secondary">{t("audio.monitor.fidelity")}</Typography>
                          <Typography variant="body2" fontWeight="bold">{result.sample_rate} Hz</Typography>
                        </Grid>
                        {result.meta?.seed != null && result.meta.seed !== "" && (
                          <Grid item xs={6}>
                            <Typography variant="caption" display="block" color="text.secondary">{t("audio.monitor.seed")}</Typography>
                            <Typography variant="body2" fontWeight="bold">{String(result.meta.seed)}</Typography>
                          </Grid>
                        )}
                        <Grid item xs={12}>
                          {/* The file's name only: the server path carries the account name, and
                              the file is reached through the Audio library, not the path. */}
                          <Typography variant="caption" display="block" color="text.secondary">{t("audio.monitor.file")}</Typography>
                          <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.7rem", opacity: 0.7 }}>
                            {fileNameOf(result.path) || "—"}
                          </Typography>
                        </Grid>
                        {result.meta?.arrangement && (
                          <Grid item xs={12}>
                            <Typography variant="caption" display="block" color="text.secondary">{t("audio.monitor.captionSent")}</Typography>
                            <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                              {result.meta.arrangement.caption || "—"}
                            </Typography>
                            <Stack direction="row" spacing={2} sx={{ mt: 1, flexWrap: "wrap" }}>
                              {result.meta.arrangement.bpm != null && result.meta.arrangement.bpm !== "" && (
                                <Typography variant="body2"><b>{result.meta.arrangement.bpm}</b> bpm</Typography>
                              )}
                              {result.meta.arrangement.keyscale && (
                                <Typography variant="body2">{result.meta.arrangement.keyscale}</Typography>
                              )}
                              {result.meta.arrangement.timesignature && (
                                <Typography variant="body2">{meterLabel(result.meta.arrangement.timesignature)}</Typography>
                              )}
                              {languageLabel(result.meta.arrangement.vocal_language) && (
                                <Typography variant="body2">{languageLabel(result.meta.arrangement.vocal_language)}</Typography>
                              )}
                            </Stack>
                            {result.meta.arrangement.lyrics && (
                              <Box sx={{ mt: 1, maxHeight: 160, overflow: "auto" }}>
                                <Typography variant="caption" display="block" color="text.secondary">{t("audio.monitor.lyricsSent")}</Typography>
                                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", opacity: 0.9 }}>
                                  {result.meta.arrangement.lyrics}
                                </Typography>
                              </Box>
                            )}
                          </Grid>
                        )}
                      </Grid>
                    </CardContent>
                  </Card>

                  <Alert severity="success" icon={<SaveIcon />} sx={{ borderRadius: 2 }}>
                    <Trans i18nKey="audio.monitor.registered" components={{ bold: <b /> }} />
                  </Alert>
                </Stack>
              )}
            </Paper>
          </Grid>
        </Grid>
      </Box>
      <Suspense fallback={null}>
        <AudioFoundryModelsModal
          open={modelsModalOpen}
          onClose={() => setModelsModalOpen(false)}
          showMessage={showMessage}
          highlightModelId={highlightModelId}
          onChanged={refreshCatalog}
        />
      </Suspense>
      <VoiceConsentDialog
        open={!!consentRequest}
        clipName={consentRequest?.file?.name || consentRequest?.clip?.filename}
        statement={consentStatement}
        busy={uploadingClip}
        onConfirm={handleConsentConfirmed}
        onCancel={() => setConsentRequest(null)}
      />
      <AlertSnackbar
        open={toast.open}
        onClose={() => setToast((t) => ({ ...t, open: false }))}
        severity={toast.severity}
        message={toast.message}
      />
    </PageLayout>
  );
};

export default AudioFoundryPage;
