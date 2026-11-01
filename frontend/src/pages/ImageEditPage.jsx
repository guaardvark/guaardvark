// frontend/src/pages/ImageEditPage.jsx
// Image Edit: one place for every photo tool, with the mode chosen here rather
// than guessed from a chat message. Four modes, each one backend tool:
//   New scene, same face -> generate_identity (PuLID-FLUX, up to three faces)
//   Edit the photo       -> edit_image (Qwen-Image-Edit, FLUX Kontext, MOSS)
//   Expand               -> outpaint_image
//   Remove background    -> remove_background
// Photos are uploaded to outputs/edit_inputs; a face photo is checked with the
// detector PuLID crops with, and the person says once that they may use it (the
// same consent record chat reads). An identity result comes back with its
// likeness score, measured after the render (likeness_score.py). A run is a
// tool job polled until it finishes, so leaving the tab does not stop it; the
// pending job and recent results are kept in this browser.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  FormControl,
  FormControlLabel,
  Grid,
  IconButton,
  InputLabel,
  LinearProgress,
  MenuItem,
  Paper,
  Select,
  Slider,
  Stack,
  Switch,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  AddPhotoAlternate as AddPhotoIcon,
  Close as CloseIcon,
  Download as DownloadIcon,
  Edit as EditIcon,
  Face as FaceIcon,
  OpenInFull as OpenIcon,
  Replay as ReplayIcon,
} from "@mui/icons-material";
import ImageLightbox from "../components/images/ImageLightbox";
import lazyWithReload from "../utils/lazyWithReload";
import { guardedMediaSrc } from "../utils/assetGuard";
import {
  checkFaces,
  getCapabilities,
  getJob,
  recordConsent,
  startRun,
  uploadPhoto,
} from "../api/imageEditService";
import { useUiTranslation } from "../i18n";

const ImageModelsModal = lazyWithReload(() => import("../components/modals/ImageModelsModal"));

const MODES = ["identity", "edit", "expand", "background"];
const FRAMING = ["faceToCamera", "mediumCloseUp", "threeQuarter", "lookingAtCamera"];
const SIZES = ["portrait", "square", "landscape", "video", "fullhd", "vertical"];
const MAX_SEED = 2 ** 32 - 1;
const POLL_WAIT_S = 20;
const HISTORY_KEY = "imageEdit.history.v1";
const PENDING_KEY = "imageEdit.pendingJob.v1";
const HISTORY_MAX = 24;
const EXPAND_PRESETS = {
  presetAll: { left: 256, right: 256, top: 256, bottom: 256 },
  presetWider: { left: 384, right: 384, top: 0, bottom: 0 },
  presetTaller: { left: 0, right: 0, top: 384, bottom: 384 },
};

const randomSeed = () => Math.floor(Math.random() * MAX_SEED);

const readStored = (key, fallback) => {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
};

const writeStored = (key, value) => {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Storage blocked: the page still works, it only forgets on reload.
  }
};

/** The face a photo's check found, drawn over its thumbnail. */
const FaceBox = ({ photo }) => {
  const face = photo?.faces?.faces?.[0];
  if (!face || !photo.width || !photo.height) return null;
  const [x0, y0, x1, y1] = face.box;
  return (
    <Box
      sx={{
        position: "absolute",
        left: `${(x0 / photo.width) * 100}%`,
        top: `${(y0 / photo.height) * 100}%`,
        width: `${((x1 - x0) / photo.width) * 100}%`,
        height: `${((y1 - y0) / photo.height) * 100}%`,
        border: "2px solid",
        borderColor: "success.main",
        borderRadius: 1,
        pointerEvents: "none",
      }}
    />
  );
};

const FaceStatus = ({ photo, t }) => {
  if (photo.checking) {
    return <Chip size="small" icon={<CircularProgress size={12} />} label={t("imageEdit.faces.checking")} />;
  }
  if (photo.faces) {
    const count = photo.faces.count || 0;
    if (!count) {
      return (
        <Tooltip title={t("imageEdit.faces.noneHelp")}>
          <Chip size="small" color="error" icon={<FaceIcon />} label={t("imageEdit.faces.none")} />
        </Tooltip>
      );
    }
    return (
      <Chip
        size="small"
        color="success"
        icon={<FaceIcon />}
        label={count > 1 ? t("imageEdit.faces.foundMany", { count }) : t("imageEdit.faces.found")}
      />
    );
  }
  if (photo.face_check_error) {
    return (
      <Tooltip title={photo.face_check_error}>
        <Chip size="small" label={t("imageEdit.faces.unavailable")} />
      </Tooltip>
    );
  }
  return null;
};

const LIKENESS_COLORS = { strong: "success", partial: "warning", not_taken: "error" };

/** How close each person's face in the result is to their photos, as scored after the render. */
const LikenessReport = ({ likeness, t }) => {
  const people = likeness?.people || [];
  if (!people.length) return null;
  const named = people.length > 1;
  const spread = people.length === 1 ? people[0].faces_with_likeness : 0;
  return (
    <Stack spacing={0.5}>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
        {people.map((p) => {
          const label = t(`imageEdit.likeness.${p.band}`, { score: p.score?.toFixed(2) });
          const who = p.person === 1 ? t("imageEdit.scene.you") : t("imageEdit.scene.second");
          return (
            <Tooltip key={p.person} title={t("imageEdit.likeness.help")}>
              <Chip size="small" icon={<FaceIcon />} color={LIKENESS_COLORS[p.band] || "default"}
                label={named ? `${who}: ${label}` : label} />
            </Tooltip>
          );
        })}
      </Stack>
      {spread > 1 && (
        <Typography variant="caption" color="warning.main">
          {t("imageEdit.likeness.onMany", { count: spread })}
        </Typography>
      )}
    </Stack>
  );
};

/** The scene with its faces numbered; clicking a face gives it to the person being chosen. */
const SceneChooser = ({ scene, assign, onPick, t }) => (
  <Box sx={{ position: "relative", width: "100%", aspectRatio: `${scene.width} / ${scene.height}` }}>
    <Box component="img" src={guardedMediaSrc(scene.url)} alt=""
      sx={{ width: "100%", height: "100%", display: "block", borderRadius: 1 }} />
    {scene.faces.map((face, i) => {
      const [x0, y0, x1, y1] = face.box;
      const who = assign.person1 === i ? "you" : assign.person2 === i ? "second" : null;
      const color = who === "you" ? "success.main" : who === "second" ? "info.main" : "warning.main";
      return (
        <Box
          key={i}
          role="button"
          tabIndex={0}
          aria-label={t("imageEdit.scene.faceNumber", { number: i + 1 })}
          onClick={() => onPick(i)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") onPick(i);
          }}
          sx={{
            position: "absolute",
            left: `${(x0 / scene.width) * 100}%`,
            top: `${(y0 / scene.height) * 100}%`,
            width: `${((x1 - x0) / scene.width) * 100}%`,
            height: `${((y1 - y0) / scene.height) * 100}%`,
            border: "2px solid",
            borderColor: color,
            borderRadius: 1,
            cursor: "pointer",
            "&:hover": { bgcolor: "rgba(255,255,255,0.15)" },
          }}
        >
          <Box sx={{ position: "absolute", bottom: "100%", left: -2, mb: 0.25, px: 0.5, bgcolor: color,
            color: "common.black", fontSize: 12, fontWeight: 600, borderRadius: 0.5, whiteSpace: "nowrap" }}>
            {i + 1}{who ? ` · ${t(`imageEdit.scene.${who}`)}` : ""}
          </Box>
        </Box>
      );
    })}
  </Box>
);

/** One photo, or the tile that adds one. */
const PhotoSlot = ({
  photo, label, uploading, onFile, onRemove, faceCheck, needConsent, onConfirm, confirming, t,
}) => {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);

  const takeFiles = (files) => {
    const file = files && files[0];
    if (file) onFile(file);
  };

  if (!photo) {
    return (
      <Paper
        variant="outlined"
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          takeFiles(e.dataTransfer.files);
        }}
        sx={{
          p: 2,
          minHeight: 140,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 1,
          cursor: "pointer",
          borderStyle: "dashed",
          borderColor: dragging ? "primary.main" : "divider",
          textAlign: "center",
        }}
      >
        {uploading ? <CircularProgress size={28} /> : <AddPhotoIcon color="action" />}
        <Typography variant="body2" color="text.secondary">
          {uploading ? t("imageEdit.photos.uploading") : label}
        </Typography>
        {!uploading && (
          <Typography variant="caption" color="text.secondary">{t("imageEdit.photos.drop")}</Typography>
        )}
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => {
            takeFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </Paper>
    );
  }

  return (
    <Paper variant="outlined" sx={{ p: 1, display: "flex", flexDirection: "column", gap: 1 }}>
      <Box sx={{ position: "relative", width: "100%", aspectRatio: `${photo.width} / ${photo.height}`, maxHeight: 260 }}>
        <Box
          component="img"
          src={guardedMediaSrc(photo.url)}
          alt={label}
          sx={{ width: "100%", height: "100%", objectFit: "contain", display: "block", borderRadius: 1 }}
        />
        {faceCheck && <FaceBox photo={photo} />}
        <IconButton
          size="small"
          onClick={onRemove}
          aria-label={t("imageEdit.photos.remove")}
          sx={{ position: "absolute", top: 4, right: 4, bgcolor: "rgba(0,0,0,0.55)", color: "white",
            "&:hover": { bgcolor: "rgba(0,0,0,0.75)" } }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>
      {faceCheck && (
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <FaceStatus photo={photo} t={t} />
          {photo.consented && <Chip size="small" variant="outlined" label={t("imageEdit.consent.confirmed")} />}
        </Stack>
      )}
      {needConsent && !photo.consented && (
        <Alert severity="info" icon={false} sx={{ py: 0.5 }}>
          <Typography variant="caption" component="p" sx={{ mb: 1 }}>{t("imageEdit.consent.question")}</Typography>
          <Button size="small" variant="outlined" onClick={onConfirm} disabled={confirming}>
            {t("imageEdit.consent.confirm")}
          </Button>
        </Alert>
      )}
    </Paper>
  );
};

const ImageEditPage = () => {
  const { t } = useUiTranslation();

  const [caps, setCaps] = useState(null);
  const [capsError, setCapsError] = useState(null);
  const [mode, setMode] = useState("identity");
  // The first photo is shared by every mode; extra photos belong to a mode.
  const [primary, setPrimary] = useState(null);
  const [extras, setExtras] = useState({ identity: [], edit: [] });
  const [uploadingSlot, setUploadingSlot] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const [prompts, setPrompts] = useState({ identity: "", edit: "", expand: "" });
  const [size, setSize] = useState("portrait");
  const [strength, setStrength] = useState(1.0);
  const [seed, setSeed] = useState(randomSeed);
  const [newSeedEachRun, setNewSeedEachRun] = useState(true);
  const [engine, setEngine] = useState("auto");
  const [editSteps, setEditSteps] = useState("");
  const [pad, setPad] = useState(EXPAND_PRESETS.presetAll);
  // "With other people": the scene first, then the faces placed on the ones chosen.
  const [withOthers, setWithOthers] = useState(false);
  const [person2, setPerson2] = useState([]);
  const [scene, setScene] = useState(null);
  const [assign, setAssign] = useState({ person1: null, person2: null });
  const [assignTarget, setAssignTarget] = useState("person1");
  const [job, setJob] = useState(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [history, setHistory] = useState(() => readStored(HISTORY_KEY, []));
  const [lightbox, setLightbox] = useState(null);
  const [installId, setInstallId] = useState(null);
  const pollingRef = useRef(null);

  const loadCaps = useCallback(async () => {
    try {
      const res = await getCapabilities();
      setCaps(res.data);
      setCapsError(null);
      const defaults = res.data?.modes?.identity?.defaults;
      if (defaults?.weight) setStrength(Number(defaults.weight));
    } catch (err) {
      setCapsError(err.message);
    }
  }, []);

  useEffect(() => {
    loadCaps();
  }, [loadCaps]);

  const modeCaps = caps?.modes?.[mode] || {};
  const identityCaps = caps?.modes?.identity || {};
  const editCaps = caps?.modes?.edit || {};
  const faceMode = mode === "identity";
  const maxExtras = mode === "identity"
    ? Math.max(0, (identityCaps.max_references || 3) - 1)
    : mode === "edit" && (engine === "auto" || engine === "qwen-image-edit")
      ? Math.max(0, (editCaps.max_references || 1) - 1)
      : 0;
  const modeExtras = (extras[mode] || []).slice(0, maxExtras);
  const photos = [primary, ...modeExtras].filter(Boolean);
  const secondPhotos = faceMode && withOthers ? person2 : [];
  const facePhotos = faceMode ? [...photos, ...secondPhotos] : [];

  // Face photos need a face check; run it for a photo added under another mode.
  useEffect(() => {
    if (!faceMode) return;
    const unchecked = facePhotos.filter((p) => p && !p.faces && !p.face_check_error && !p.checking);
    unchecked.forEach(async (photo) => {
      const mark = (patch) => {
        setPrimary((cur) => (cur?.url === photo.url ? { ...cur, ...patch } : cur));
        setExtras((cur) => ({
          ...cur,
          identity: cur.identity.map((p) => (p.url === photo.url ? { ...p, ...patch } : p)),
        }));
        setPerson2((cur) => cur.map((p) => (p.url === photo.url ? { ...p, ...patch } : p)));
      };
      mark({ checking: true });
      try {
        const res = await checkFaces(photo.url);
        mark({ ...res.data, checking: false });
      } catch (err) {
        mark({ checking: false, face_check_error: err.message });
      }
    });
    // photos is derived each render; which photos and whether each was checked is what matters.
  }, [faceMode, facePhotos.map((p) => `${p.url}:${Boolean(p.faces)}`).join("|")]);

  const addPhoto = async (slot, file) => {
    if (!file.type.startsWith("image/")) {
      setError(t("imageEdit.photos.notAnImage"));
      return;
    }
    setError(null);
    setUploadingSlot(slot);
    try {
      const res = await uploadPhoto(file, { checkFaces: faceMode });
      const photo = res.data;
      if (slot === "primary") setPrimary(photo);
      else if (slot === "person2") setPerson2((cur) => [...cur, photo]);
      else setExtras((cur) => ({ ...cur, [mode]: [...(cur[mode] || []), photo] }));
    } catch (err) {
      setError(t("imageEdit.photos.uploadFailed", { message: err.message }));
    } finally {
      setUploadingSlot(null);
    }
  };

  const removePhoto = (photo) => {
    if (person2.some((p) => p.url === photo.url)) {
      setPerson2((cur) => cur.filter((p) => p.url !== photo.url));
    } else if (primary?.url === photo.url) {
      const [next, ...rest] = modeExtras;
      setPrimary(next || null);
      if (next) setExtras((cur) => ({ ...cur, [mode]: rest }));
    } else {
      setExtras((cur) => ({ ...cur, [mode]: (cur[mode] || []).filter((p) => p.url !== photo.url) }));
    }
  };

  const confirmLikeness = async (photo) => {
    setConfirming(photo.url);
    try {
      await recordConsent(photo.url);
      const mark = (p) => (p?.url === photo.url ? { ...p, consented: true } : p);
      setPrimary(mark);
      setExtras((cur) => ({ ...cur, identity: cur.identity.map(mark), edit: cur.edit.map(mark) }));
      setPerson2((cur) => cur.map(mark));
    } catch (err) {
      setError(t("imageEdit.consent.failed", { message: err.message }));
    } finally {
      setConfirming(null);
    }
  };

  const addFraming = (key) => {
    const phrase = t(`imageEdit.framing.${key}`);
    setPrompts((cur) => {
      const text = (cur.identity || "").trim();
      if (text.toLowerCase().includes(phrase.toLowerCase())) return cur;
      return { ...cur, identity: text ? `${text.replace(/[.,;\s]+$/, "")}, ${phrase}` : phrase };
    });
  };

  const blocker = useMemo(() => {
    if (!caps) return null;
    if (!modeCaps.available) return modeCaps.reason;
    if (!primary) return t("imageEdit.run.needPhoto");
    if (mode === "identity") {
      if (facePhotos.some((p) => !p.faces?.count)) return t("imageEdit.run.needFace");
      if (facePhotos.some((p) => !p.consented)) return t("imageEdit.run.needConsent");
      if (!prompts.identity.trim()) return t("imageEdit.run.needPrompt");
    }
    if (mode === "edit" && !prompts.edit.trim()) return t("imageEdit.run.needPrompt");
    if (mode === "expand" && !Object.values(pad).some((v) => Number(v) > 0)) return t("imageEdit.run.needSides");
    return null;
  }, [caps, modeCaps, primary, mode, facePhotos, prompts, pad, t]);

  const finish = useCallback((snapshot, request) => {
    const outcome = snapshot.result || {};
    const meta = outcome.metadata || {};
    writeStored(PENDING_KEY, null);
    setRunning(false);
    setJob(null);
    if (snapshot.status === "done" && meta.stage === "scene") {
      const faces = meta.faces || [];
      setScene({ url: meta.image_url, faces, width: meta.width, height: meta.height,
        seed: meta.seed, prompt: request.prompt || "", sources: request.sources || [] });
      setAssign({ person1: faces.length === 1 ? 0 : null, person2: null });
      setAssignTarget(faces.length === 1 ? "person2" : "person1");
      return;
    }
    if (snapshot.status === "done" && meta.image_url) {
      setScene(null);
      const entry = {
        url: meta.image_url,
        mode: request.mode,
        prompt: request.prompt || "",
        seed: meta.seed ?? request.settings?.seed ?? null,
        sources: request.sources || [],
        output: outcome.output || "",
        likeness: meta.likeness || null,
        created: Date.now(),
      };
      setResult(entry);
      setHistory((cur) => {
        const next = [entry, ...cur.filter((h) => h.url !== entry.url)].slice(0, HISTORY_MAX);
        writeStored(HISTORY_KEY, next);
        return next;
      });
    } else {
      setError(outcome.error || t("imageEdit.result.failed"));
    }
  }, [t]);

  const poll = useCallback(async (jobId, request) => {
    pollingRef.current = jobId;
    while (pollingRef.current === jobId) {
      let snapshot;
      try {
        const res = await getJob(jobId, POLL_WAIT_S);
        snapshot = res.data;
      } catch (err) {
        if (pollingRef.current !== jobId) return;
        writeStored(PENDING_KEY, null);
        setRunning(false);
        setJob(null);
        setError(err.status === 404 && err.data?.reason === "restarted"
          ? t("imageEdit.job.lost")
          : t("imageEdit.job.pollFailed", { message: err.message }));
        return;
      }
      if (pollingRef.current !== jobId) return;
      setJob(snapshot);
      if (snapshot.status === "done" || snapshot.status === "failed") {
        finish(snapshot, request);
        return;
      }
    }
  }, [finish, t]);

  // A run started before the tab was left (or the page reloaded) is picked up again.
  useEffect(() => {
    const pending = readStored(PENDING_KEY, null);
    if (pending?.jobId) {
      setRunning(true);
      setJob({ status: "queued", job_id: pending.jobId, elapsed_s: 0 });
      poll(pending.jobId, pending.request || {});
    }
    return () => {
      pollingRef.current = null;
    };
  }, [poll]);

  const startJob = async (request, sources) => {
    setRunning(true);
    setResult(null);
    try {
      const res = await startRun(request);
      const snapshot = res.data;
      const stored = { ...request, sources };
      writeStored(PENDING_KEY, { jobId: snapshot.job_id, request: stored });
      setJob(snapshot);
      poll(snapshot.job_id, stored);
    } catch (err) {
      setRunning(false);
      setError(t("imageEdit.run.startFailed", { message: err.message }));
    }
  };

  const run = async (overrideSeed) => {
    if (blocker || running) return;
    setError(null);
    const runSeed = overrideSeed ?? (newSeedEachRun ? randomSeed() : seed);
    if (mode === "identity") setSeed(runSeed);
    const others = mode === "identity" && withOthers;
    const settings = mode === "identity"
      ? { size, weight: strength, seed: runSeed, ...(others ? { others: true } : {}) }
      : mode === "edit"
        ? { engine, steps: editSteps || undefined }
        : mode === "expand" ? { ...pad } : {};
    const request = {
      mode,
      image: primary.url,
      references: modeExtras.map((p) => p.url),
      ...(others && person2.length ? { person2: person2.map((p) => p.url) } : {}),
      prompt: mode === "background" ? "" : prompts[mode] || "",
      settings,
    };
    if (others) {
      setScene(null);
      setAssign({ person1: null, person2: null });
    }
    await startJob(request, [...photos, ...(others ? person2 : [])].map((p) => p.url));
  };

  // Second step of "With other people": the chosen faces get their identities.
  const placeFaces = async () => {
    if (!scene || assign.person1 === null || running || !primary) return;
    setError(null);
    const settings = { face: assign.person1 + 1, weight: strength };
    if (person2.length && assign.person2 !== null) settings.person2_face = assign.person2 + 1;
    await startJob({ mode: "identity", image: primary.url, scene: scene.url, prompt: scene.prompt, settings },
      scene.sources);
  };

  const pickFace = (index) => {
    if (assignTarget === "person2" && person2.length) {
      setAssign((cur) => ({ person1: cur.person1 === index ? null : cur.person1, person2: index }));
      return;
    }
    setAssign((cur) => ({ person1: index, person2: cur.person2 === index ? null : cur.person2 }));
    if (person2.length) setAssignTarget("person2");
  };
  const facesReady = scene && assign.person1 !== null && (!person2.length || assign.person2 !== null);

  const editResult = (entry) => {
    setPrimary({ url: entry.url, name: entry.url.split("/").pop(), consented: false,
      width: null, height: null });
    setMode("edit");
    setResult(entry);
    // Size is read back so the thumbnail keeps its shape.
    const img = new Image();
    img.onload = () => setPrimary((cur) => (cur?.url === entry.url
      ? { ...cur, width: img.naturalWidth, height: img.naturalHeight } : cur));
    img.src = guardedMediaSrc(entry.url);
  };

  const clearHistory = () => {
    setHistory([]);
    writeStored(HISTORY_KEY, null);
  };

  const photoSlots = () => {
    const label = mode === "identity" ? t("imageEdit.photos.add")
      : mode === "edit" ? t("imageEdit.photos.editSource") : t("imageEdit.photos.source");
    const slots = [
      <PhotoSlot
        key={primary?.url || "primary"}
        photo={primary}
        label={label}
        uploading={uploadingSlot === "primary"}
        onFile={(file) => addPhoto("primary", file)}
        onRemove={() => removePhoto(primary)}
        faceCheck={faceMode}
        needConsent={faceMode}
        onConfirm={() => confirmLikeness(primary)}
        confirming={confirming === primary?.url}
        t={t}
      />,
    ];
    modeExtras.forEach((photo) => {
      slots.push(
        <PhotoSlot
          key={photo.url}
          photo={photo}
          label={label}
          onFile={() => {}}
          onRemove={() => removePhoto(photo)}
          faceCheck={faceMode}
          needConsent={faceMode}
          onConfirm={() => confirmLikeness(photo)}
          confirming={confirming === photo.url}
          t={t}
        />,
      );
    });
    if (primary && modeExtras.length < maxExtras) {
      slots.push(
        <PhotoSlot
          key="extra"
          photo={null}
          label={mode === "edit" ? t("imageEdit.photos.addReference") : t("imageEdit.photos.addAnother")}
          uploading={uploadingSlot === "extra"}
          onFile={(file) => addPhoto("extra", file)}
          t={t}
        />,
      );
    }
    return slots;
  };

  const resultSources = result?.sources?.length ? result.sources : photos.map((p) => p.url);

  return (
    <Box sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
      {capsError && <Alert severity="error">{t("imageEdit.capabilitiesFailed", { message: capsError })}</Alert>}
      {!caps && !capsError && <LinearProgress aria-label={t("imageEdit.loading")} />}

      <ToggleButtonGroup
        exclusive
        value={mode}
        onChange={(_e, value) => value && setMode(value)}
        size="small"
        sx={{ flexWrap: "wrap" }}
      >
        {MODES.map((m) => (
          <ToggleButton key={m} value={m} sx={{ textTransform: "none", px: 2 }}>
            {t(`imageEdit.modes.${m}`)}
            {caps && !caps.modes?.[m]?.available && (
              <Chip size="small" label={t("imageEdit.unavailable")} sx={{ ml: 1, height: 18 }} />
            )}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      <Typography variant="body2" color="text.secondary">{t(`imageEdit.modeHelp.${mode}`)}</Typography>

      {caps && !modeCaps.available && (
        <Alert
          severity="warning"
          action={modeCaps.install && (
            <Button size="small" onClick={() => setInstallId(modeCaps.install)}>{t("imageEdit.install")}</Button>
          )}
        >
          {modeCaps.reason}
        </Alert>
      )}

      <Grid container spacing={2}>
        <Grid item xs={12} md={6}>
          <Stack spacing={2}>
            <Paper variant="outlined" sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1.5 }}>
              <Typography variant="subtitle2">
                {mode === "identity" ? t("imageEdit.photos.faces")
                  : mode === "edit" ? t("imageEdit.photos.editSource") : t("imageEdit.photos.source")}
              </Typography>
              {mode === "identity" && (
                <Typography variant="caption" color="text.secondary">{t("imageEdit.photos.facesHelp")}</Typography>
              )}
              <Grid container spacing={1}>
                {photoSlots().map((slot) => (
                  <Grid item xs={12} sm={photos.length > 0 ? 6 : 12} key={slot.key}>{slot}</Grid>
                ))}
              </Grid>
              {mode === "edit" && maxExtras > 0 && primary && (
                <Typography variant="caption" color="text.secondary">
                  {t("imageEdit.photos.references")}: {t("imageEdit.photos.referencesHelp")}
                </Typography>
              )}
              {mode === "identity" && (
                <Box sx={{ display: "flex", flexDirection: "column", gap: 1, mt: 1 }}>
                  <ToggleButtonGroup
                    exclusive
                    size="small"
                    value={withOthers ? "others" : "only"}
                    onChange={(_e, value) => {
                      if (!value) return;
                      setWithOthers(value === "others");
                      setScene(null);
                    }}
                  >
                    <ToggleButton value="only" sx={{ textTransform: "none" }}>{t("imageEdit.people.only")}</ToggleButton>
                    <ToggleButton value="others" sx={{ textTransform: "none" }}>{t("imageEdit.people.others")}</ToggleButton>
                  </ToggleButtonGroup>
                  <Typography variant="caption" color="text.secondary">
                    {withOthers ? t("imageEdit.people.othersHelp") : t("imageEdit.people.onlyHelp")}
                  </Typography>
                </Box>
              )}
            </Paper>

            {mode === "identity" && withOthers && (
              <Paper variant="outlined" sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1.5 }}>
                <Typography variant="subtitle2">{t("imageEdit.people.second")}</Typography>
                <Typography variant="caption" color="text.secondary">{t("imageEdit.people.secondHelp")}</Typography>
                <Grid container spacing={1}>
                  {person2.map((photo) => (
                    <Grid item xs={12} sm={6} key={photo.url}>
                      <PhotoSlot
                        photo={photo}
                        label={t("imageEdit.people.second")}
                        onFile={() => {}}
                        onRemove={() => removePhoto(photo)}
                        faceCheck
                        needConsent
                        onConfirm={() => confirmLikeness(photo)}
                        confirming={confirming === photo.url}
                        t={t}
                      />
                    </Grid>
                  ))}
                  {person2.length < 2 && (
                    <Grid item xs={12} sm={person2.length ? 6 : 12}>
                      <PhotoSlot
                        photo={null}
                        label={person2.length ? t("imageEdit.photos.addAnother") : t("imageEdit.people.addSecond")}
                        uploading={uploadingSlot === "person2"}
                        onFile={(file) => addPhoto("person2", file)}
                        t={t}
                      />
                    </Grid>
                  )}
                </Grid>
              </Paper>
            )}

            {mode !== "background" && (
              <Paper variant="outlined" sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1.5 }}>
                <TextField
                  label={t(`imageEdit.prompt.${mode}`)}
                  placeholder={t(`imageEdit.prompt.${mode}Placeholder`)}
                  value={prompts[mode] || ""}
                  onChange={(e) => setPrompts((cur) => ({ ...cur, [mode]: e.target.value }))}
                  multiline
                  minRows={3}
                  fullWidth
                  helperText={mode === "identity" ? t("imageEdit.prompt.identityHelp") : undefined}
                />
                {mode === "identity" && (
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                    <Typography variant="caption" color="text.secondary">{t("imageEdit.prompt.framing")}</Typography>
                    {FRAMING.map((key) => (
                      <Chip key={key} size="small" variant="outlined" label={t(`imageEdit.framing.${key}`)}
                        onClick={() => addFraming(key)} />
                    ))}
                  </Stack>
                )}
              </Paper>
            )}

            {mode !== "background" && (
              <Paper variant="outlined" sx={{ p: 2, display: "flex", flexDirection: "column", gap: 2 }}>
                <Typography variant="subtitle2">{t("imageEdit.settings.title")}</Typography>
                {mode === "identity" && (
                  <>
                    <FormControl size="small" fullWidth>
                      <InputLabel>{t("imageEdit.settings.size")}</InputLabel>
                      <Select value={size} label={t("imageEdit.settings.size")} onChange={(e) => setSize(e.target.value)}>
                        {SIZES.map((key) => (
                          <MenuItem key={key} value={key}>{t(`imageEdit.settings.sizes.${key}`)}</MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <Box>
                      <Typography variant="body2" gutterBottom>
                        {t("imageEdit.settings.strength")}: {strength.toFixed(2)}
                      </Typography>
                      <Slider
                        value={strength}
                        min={0.8}
                        max={1.2}
                        step={0.05}
                        marks={[{ value: 0.8, label: "0.8" }, { value: 1.0, label: "1.0" }, { value: 1.2, label: "1.2" }]}
                        onChange={(_e, value) => setStrength(Number(value))}
                      />
                      <Typography variant="caption" color="text.secondary">{t("imageEdit.settings.strengthHelp")}</Typography>
                    </Box>
                    <Stack direction="row" spacing={2} alignItems="center">
                      <TextField
                        size="small"
                        type="number"
                        label={t("imageEdit.settings.seed")}
                        value={seed}
                        disabled={newSeedEachRun}
                        onChange={(e) => setSeed(Math.max(0, Math.min(MAX_SEED, Number(e.target.value) || 0)))}
                        sx={{ width: 180 }}
                      />
                      <FormControlLabel
                        control={<Switch checked={newSeedEachRun} onChange={(e) => setNewSeedEachRun(e.target.checked)} />}
                        label={t("imageEdit.settings.randomSeed")}
                      />
                    </Stack>
                  </>
                )}
                {mode === "edit" && (
                  <>
                    <FormControl size="small" fullWidth>
                      <InputLabel>{t("imageEdit.settings.engine")}</InputLabel>
                      <Select value={engine} label={t("imageEdit.settings.engine")} onChange={(e) => setEngine(e.target.value)}>
                        <MenuItem value="auto">{t("imageEdit.settings.engineAuto")}</MenuItem>
                        {(editCaps.engines || []).map((e) => (
                          <MenuItem key={e.id} value={e.model} disabled={!e.installed}>
                            {e.name}{e.installed ? "" : ` (${t("imageEdit.unavailable")})`}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <TextField
                      size="small"
                      type="number"
                      label={t("imageEdit.settings.steps")}
                      helperText={t("imageEdit.settings.stepsHelp")}
                      value={editSteps}
                      onChange={(e) => setEditSteps(e.target.value)}
                      sx={{ width: 180 }}
                    />
                  </>
                )}
                {mode === "expand" && (
                  <>
                    {!caps?.modes?.expand?.adds_canvas && caps?.modes?.expand?.available && (
                      <Alert severity="info">{t("imageEdit.settings.insideFrame")}</Alert>
                    )}
                    <Typography variant="body2">{t("imageEdit.settings.sides")}</Typography>
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                      {Object.entries(EXPAND_PRESETS).map(([key, value]) => (
                        <Chip key={key} size="small" variant="outlined" label={t(`imageEdit.settings.${key}`)}
                          onClick={() => setPad(value)} />
                      ))}
                    </Stack>
                    <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                      {["left", "right", "top", "bottom"].map((side) => (
                        <TextField
                          key={side}
                          size="small"
                          type="number"
                          label={t(`imageEdit.settings.${side}`)}
                          value={pad[side]}
                          inputProps={{ min: 0, max: caps?.modes?.expand?.max_px || 1024, step: 8 }}
                          onChange={(e) => setPad((cur) => ({ ...cur, [side]: Math.max(0, Number(e.target.value) || 0) }))}
                          sx={{ width: 110 }}
                        />
                      ))}
                    </Stack>
                  </>
                )}
              </Paper>
            )}

            {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
            <Tooltip title={blocker || ""} disableHoverListener={!blocker}>
              <span>
                <Button
                  variant="contained"
                  size="large"
                  fullWidth
                  disabled={Boolean(blocker) || running || !caps}
                  onClick={() => run()}
                >
                  {mode === "identity" && withOthers ? t("imageEdit.run.identityScene") : t(`imageEdit.run.${mode}`)}
                </Button>
              </span>
            </Tooltip>
            {blocker && caps && modeCaps.available && (
              <Typography variant="caption" color="text.secondary">{blocker}</Typography>
            )}
          </Stack>
        </Grid>

        <Grid item xs={12} md={6}>
          <Paper variant="outlined" sx={{ p: 2, display: "flex", flexDirection: "column", gap: 1.5, minHeight: 320 }}>
            <Typography variant="subtitle2">{t("imageEdit.result.title")}</Typography>
            {running && (
              <Box>
                <LinearProgress />
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  {job?.status === "queued"
                    ? job?.note || t("imageEdit.job.queued")
                    : t("imageEdit.job.running", { seconds: Math.round(job?.elapsed_s || 0) })}
                </Typography>
              </Box>
            )}
            {!running && scene && (
              <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
                <Typography variant="subtitle2">{t("imageEdit.scene.title")}</Typography>
                {scene.faces.length === 0 ? (
                  <Alert severity="warning">{t("imageEdit.scene.noFaces")}</Alert>
                ) : (
                  <>
                    <Typography variant="body2" color="text.secondary">
                      {assignTarget === "person2" && person2.length
                        ? t("imageEdit.scene.helpSecond") : t("imageEdit.scene.help")}
                    </Typography>
                    {person2.length > 0 && (
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography variant="caption" color="text.secondary">{t("imageEdit.scene.pickFor")}</Typography>
                        <Chip size="small" label={t("imageEdit.scene.you")}
                          color={assignTarget === "person1" ? "success" : "default"}
                          onClick={() => setAssignTarget("person1")} />
                        <Chip size="small" label={t("imageEdit.scene.second")}
                          color={assignTarget === "person2" ? "info" : "default"}
                          onClick={() => setAssignTarget("person2")} />
                      </Stack>
                    )}
                    <SceneChooser scene={scene} assign={assign} onPick={pickFace} t={t} />
                  </>
                )}
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  <Button variant="contained" size="small" disabled={!facesReady || running} onClick={placeFaces}>
                    {t("imageEdit.scene.apply")}
                  </Button>
                  <Button size="small" startIcon={<ReplayIcon />} disabled={Boolean(blocker) || running}
                    onClick={() => run(randomSeed())}>
                    {t("imageEdit.scene.newScene")}
                  </Button>
                </Stack>
              </Box>
            )}
            {!running && !result && !scene && (
              <Typography variant="body2" color="text.secondary">{t("imageEdit.result.empty")}</Typography>
            )}
            {result && !scene && (
              <>
                <Grid container spacing={1} alignItems="flex-start">
                  <Grid item xs={4}>
                    <Typography variant="caption" color="text.secondary">
                      {resultSources.length > 1 ? t("imageEdit.result.beforeMany") : t("imageEdit.result.before")}
                    </Typography>
                    <Stack spacing={0.5}>
                      {resultSources.map((url) => (
                        <Box key={url} component="img" src={guardedMediaSrc(url)} alt=""
                          sx={{ width: "100%", borderRadius: 1, display: "block" }} />
                      ))}
                    </Stack>
                  </Grid>
                  <Grid item xs={8}>
                    <Typography variant="caption" color="text.secondary">{t("imageEdit.result.after")}</Typography>
                    <Box
                      component="img"
                      src={guardedMediaSrc(result.url)}
                      alt={result.prompt}
                      onClick={() => setLightbox(result)}
                      sx={{ width: "100%", borderRadius: 1, display: "block", cursor: "zoom-in" }}
                    />
                  </Grid>
                </Grid>
                {result.mode === "identity" && <LikenessReport likeness={result.likeness} t={t} />}
                {result.seed !== null && result.seed !== undefined && result.mode === "identity" && (
                  <Typography variant="caption" color="text.secondary">{t("imageEdit.result.seed", { seed: result.seed })}</Typography>
                )}
                <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                  <Button size="small" startIcon={<OpenIcon />} onClick={() => setLightbox(result)}>
                    {t("imageEdit.result.open")}
                  </Button>
                  <Button size="small" startIcon={<DownloadIcon />} component="a" href={result.url}
                    download={result.url.split("/").pop()}>
                    {t("imageEdit.result.download")}
                  </Button>
                  <Button size="small" startIcon={<EditIcon />} onClick={() => editResult(result)}>
                    {t("imageEdit.result.useAsPhoto")}
                  </Button>
                  {result.mode === mode && (
                    <Button size="small" startIcon={<ReplayIcon />} disabled={Boolean(blocker) || running}
                      onClick={() => run()}>
                      {t("imageEdit.result.again")}
                    </Button>
                  )}
                  {result.mode === "identity" && mode === "identity" && result.seed !== null && (
                    <Button size="small" onClick={() => { setNewSeedEachRun(false); setSeed(result.seed); }}>
                      {t("imageEdit.result.reuseSeed", { seed: result.seed })}
                    </Button>
                  )}
                </Stack>
              </>
            )}
          </Paper>

          {history.length > 0 && (
            <Paper variant="outlined" sx={{ p: 2, mt: 2 }}>
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                <Typography variant="subtitle2">{t("imageEdit.result.history")}</Typography>
                <Button size="small" onClick={clearHistory}>{t("imageEdit.result.clearHistory")}</Button>
              </Stack>
              <Grid container spacing={1}>
                {history.map((entry) => (
                  <Grid item xs={4} sm={3} key={entry.url}>
                    <Tooltip title={entry.prompt || t(`imageEdit.modes.${entry.mode}`)}>
                      <Box
                        component="img"
                        src={guardedMediaSrc(entry.url)}
                        alt={entry.prompt}
                        onClick={() => setResult(entry)}
                        sx={{
                          width: "100%", aspectRatio: "1 / 1", objectFit: "cover", borderRadius: 1, cursor: "pointer",
                          outline: result?.url === entry.url ? "2px solid" : "none", outlineColor: "primary.main",
                        }}
                      />
                    </Tooltip>
                  </Grid>
                ))}
              </Grid>
            </Paper>
          )}
        </Grid>
      </Grid>

      {lightbox && (
        <ImageLightbox
          imageUrl={lightbox.url}
          imageName={lightbox.url.split("/").pop()}
          caption={lightbox.prompt}
          seed={lightbox.mode === "identity" ? lightbox.seed : null}
          onClose={() => setLightbox(null)}
        />
      )}

      <React.Suspense fallback={null}>
        {installId && (
          <ImageModelsModal
            open
            installModelId={installId}
            onClose={() => {
              setInstallId(null);
              loadCaps();
            }}
            showMessage={(msg, severity) => {
              if (severity === "error") setError(msg);
            }}
          />
        )}
      </React.Suspense>
    </Box>
  );
};

export default ImageEditPage;
