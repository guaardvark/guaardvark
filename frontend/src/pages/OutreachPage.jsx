import React, { useEffect, useMemo, useState, useCallback } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Divider,
  FormControl,
  IconButton,
  InputLabel,
  LinearProgress,
  Link,
  List,
  ListItemButton,
  ListItemText,
  MenuItem,
  Paper,
  Select,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import RefreshIcon from "@mui/icons-material/Refresh";
import SendIcon from "@mui/icons-material/Send";
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PowerSettingsNewIcon from "@mui/icons-material/PowerSettingsNew";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import VisibilityIcon from "@mui/icons-material/Visibility";
import AddCommentIcon from "@mui/icons-material/AddComment";
import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import EditIcon from "@mui/icons-material/Edit";
import SaveIcon from "@mui/icons-material/Save";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import * as outreachApi from "../api/outreachService";
import { translate, uiLocale, useUiTranslation, Trans } from "../i18n";

// Per-platform character soft-caps used by the pre-flight panel.
// Sources: reddit (10000 comment), youtube (~10000), discord (2000),
// facebook (~5000), internal note (no cap — display only).
const platformLimits = () => ({
  reddit: { max: 10000, label: translate("outreach.limits.reddit") },
  reddit_share: { max: 300, label: translate("outreach.limits.redditShare") },
  youtube: { max: 10000, label: translate("outreach.limits.youtube") },
  discord: { max: 2000, label: translate("outreach.limits.discord") },
  facebook: { max: 5000, label: translate("outreach.limits.facebook") },
  internal: { max: 0, label: translate("outreach.limits.internal") },
});

const toneOptions = () => [
  { value: "default", label: translate("outreach.tones.default") },
  { value: "engaging", label: translate("outreach.tones.engaging") },
  { value: "technical", label: translate("outreach.tones.technical") },
  { value: "casual", label: translate("outreach.tones.casual") },
  { value: "formal", label: translate("outreach.tones.formal") },
  { value: "humorous", label: translate("outreach.tones.humorous") },
];

// How an approved draft goes out (backend platforms.py, also in status.posting_modes):
// "watched" = one post at a time, started with Post now on the Approvals page while
// the person watches the Agent Screen; "bot" = the Discord bot sends it; null = the
// queue is the only output, flagged in the modal so that is no surprise. X and
// LinkedIn are not offered: outreach never posts to them (a person's own
// LinkedIn posts go through Social Accounts).
const platformOptions = () => [
  { value: "reddit", label: translate("outreach.platforms.reddit"), posts: "watched" },
  { value: "youtube", label: translate("outreach.platforms.youtube"), posts: "watched" },
  { value: "discord", label: translate("outreach.platforms.discord"), posts: "bot" },
  { value: "facebook", label: translate("outreach.platforms.facebook"), posts: "watched" },
  { value: "internal", label: translate("outreach.platforms.internal"), posts: null },
];

// Terminal statuses the History panel lists ("all" shows these together).
const HISTORY_STATUSES = ["posted", "aborted", "rejected", "unsupported"];

// Labels for the audit statuses shown as chips; any other status shows as sent.
const statusLabels = () => ({
  posted: translate("outreach.statuses.posted"),
  aborted: translate("outreach.statuses.aborted"),
  rejected: translate("outreach.statuses.rejected"),
  unsupported: translate("outreach.statuses.unsupported"),
  all: translate("outreach.statuses.all"),
  drafted: translate("outreach.statuses.drafted"),
  approved: translate("outreach.statuses.approved"),
});

const CHAT_EXAMPLES = [
  "comment on youtube videos about local LLMs",
  "find reddit threads about image generation and draft replies",
  "/outreach youtube <topic>",
  "/outreach status",
];

// The "What you promote" form, as the page edits it. Keywords are typed as
// one comma-separated line per feature and split on save.
const EMPTY_PROFILE_FORM = {
  name: "",
  pitch: "",
  site_url: "",
  repo_url: "",
  account_handle: "",
  videos: [],
  features: [],
};

function profileToForm(p) {
  return {
    ...EMPTY_PROFILE_FORM,
    ...(p || {}),
    videos: (p?.videos || []).map((v) => ({ label: v.label || "", url: v.url || "" })),
    features: (p?.features || []).map((f) => ({
      key: f.key || "",
      blurb: f.blurb || "",
      keywords: (f.keywords || []).join(", "),
    })),
  };
}

function formToProfile(form) {
  return {
    ...form,
    features: form.features.map((f) => ({
      ...f,
      keywords: f.keywords.split(",").map((k) => k.trim()).filter(Boolean),
    })),
  };
}

function countWords(text) {
  if (!text) return 0;
  return text.trim().split(/\s+/).filter(Boolean).length;
}

function extractUrls(text) {
  if (!text) return [];
  const re = /https?:\/\/[^\s)]+/g;
  return text.match(re) || [];
}

function formatGate(passed) {
  if (passed === null || passed === undefined) return <RadioButtonUncheckedIcon fontSize="small" sx={{ opacity: 0.5 }} />;
  return passed
    ? <CheckCircleOutlineIcon fontSize="small" color="success" />
    : <ErrorOutlineIcon fontSize="small" color="warning" />;
}

const OutreachPage = () => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const [queue, setQueue] = useState([]);
  const [approved, setApproved] = useState([]);
  const [selected, setSelected] = useState(null);
  const [modalError, setModalError] = useState(null);
  const [draft, setDraft] = useState("");
  const [tone, setTone] = useState("default");
  const [platform, setPlatform] = useState("reddit");
  const [snippets, setSnippets] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileForm, setProfileForm] = useState(EMPTY_PROFILE_FORM);
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState(null);
  const [loadingQueue, setLoadingQueue] = useState(false);
  const [loadingSnippets, setLoadingSnippets] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);
  const [passTopics, setPassTopics] = useState("");
  const [lastTaskId, setLastTaskId] = useState(null);
  const [killConfirmOpen, setKillConfirmOpen] = useState(false);
  const [postingStopConfirmOpen, setPostingStopConfirmOpen] = useState(false);

  // Citation tool
  const [citationUrl, setCitationUrl] = useState("");
  const [citationMeta, setCitationMeta] = useState(null);
  const [citationLoading, setCitationLoading] = useState(false);

  // New-draft modal — manual entry path. Cron jobs feed the queue automatically;
  // this lets a human kick off a one-off draft for a specific URL/topic.
  const [newDraftOpen, setNewDraftOpen] = useState(false);
  const [ndPlatform, setNdPlatform] = useState("reddit");
  const [ndAction, setNdAction] = useState("comment");
  const [ndUrl, setNdUrl] = useState("");
  const [ndThreadId, setNdThreadId] = useState("");
  const [ndContext, setNdContext] = useState("");
  const [ndBody, setNdBody] = useState("");
  const [ndGrade, setNdGrade] = useState(null);
  const [ndBusy, setNdBusy] = useState(false);
  // /draft-comment already inserts a queue row when seeding. Stash its id so
  // Save updates that row instead of creating a duplicate.
  const [ndSeededId, setNdSeededId] = useState(null);
  const [ndScouting, setNdScouting] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      setStatus(await outreachApi.fetchStatus());
    } catch (e) {
      setError(t("outreach.errors.statusFetch", { error: e.message }));
    }
  }, []);

  const fetchQueue = useCallback(async () => {
    setLoadingQueue(true);
    try {
      const rows = await outreachApi.fetchQueue();
      const list = Array.isArray(rows) ? rows : [];
      setQueue(list);
      setSelected((prev) => {
        if (!prev) return prev;
        const still = list.find((r) => r.id === prev.id);
        if (still) return still;
        return null;
      });
    } catch (e) {
      setError(t("outreach.errors.queueFetch", { error: e.message }));
    } finally {
      setLoadingQueue(false);
    }
  }, []);

  const fetchApproved = useCallback(async () => {
    try {
      const rows = await outreachApi.fetchApproved();
      setApproved(Array.isArray(rows) ? rows : []);
    } catch (e) {
      console.warn(`approved fetch failed: ${e.message}`);
      // The approved list would read as empty; say it did not load.
      setError(t("outreach.errors.approvedFetch", { error: e.message }));
    }
  }, []);

  // History: terminal-state audit rows (HISTORY_STATUSES). Read-only —
  // gives the user a click-through receipt for every comment that actually
  // landed (or didn't) so they can verify or audit afterward. The queue panel
  // above only shows status='drafted'; this fills the gap on the other end.
  const [history, setHistory] = useState([]);
  const [historyFilter, setHistoryFilter] = useState("posted");  // one of HISTORY_STATUSES, or 'all'
  const [loadingHistory, setLoadingHistory] = useState(false);

  const fetchHistory = useCallback(async () => {
    setLoadingHistory(true);
    try {
      const rows = await outreachApi.fetchAudit(200);
      setHistory(Array.isArray(rows) ? rows : []);
    } catch (e) {
      console.warn(`history fetch failed: ${e.message}`);
      setError(t("outreach.errors.historyFetch", { error: e.message }));
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  const fetchSnippets = useCallback(async () => {
    setLoadingSnippets(true);
    try {
      setSnippets(await outreachApi.fetchSnippets());
    } catch (e) {
      setError(t("outreach.errors.snippetsFetch", { error: e.message }));
    } finally {
      setLoadingSnippets(false);
    }
  }, []);

  const fetchProfile = useCallback(async () => {
    try {
      const out = await outreachApi.fetchProfile();
      setProfile(out?.profile || null);
    } catch (e) {
      setError(t("outreach.errors.profileFetch", { error: e.message }));
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchQueue();
    fetchApproved();
    fetchSnippets();
    fetchProfile();
    fetchHistory();
    const t = setInterval(() => {
      fetchStatus();
      fetchQueue();
      fetchApproved();
      fetchHistory();
    }, 15000);
    return () => clearInterval(t);
  }, [fetchStatus, fetchQueue, fetchApproved, fetchSnippets, fetchProfile, fetchHistory]);

  const profileConfigured = Boolean(profile?.name && profile?.pitch);

  const openProfileDialog = () => {
    setProfileForm(profileToForm(profile));
    setProfileError(null);
    setProfileOpen(true);
  };

  const setProfileField = (field, value) => {
    setProfileForm((prev) => ({ ...prev, [field]: value }));
  };

  const setProfileRow = (list, index, field, value) => {
    setProfileForm((prev) => ({
      ...prev,
      [list]: prev[list].map((row, i) => (i === index ? { ...row, [field]: value } : row)),
    }));
  };

  const addProfileRow = (list, row) => {
    setProfileForm((prev) => ({ ...prev, [list]: [...prev[list], row] }));
  };

  const removeProfileRow = (list, index) => {
    setProfileForm((prev) => ({ ...prev, [list]: prev[list].filter((_, i) => i !== index) }));
  };

  const handleSaveProfile = async () => {
    setProfileBusy(true);
    setProfileError(null);
    try {
      const out = await outreachApi.saveProfile(formToProfile(profileForm));
      setProfile(out?.profile || null);
      setProfileOpen(false);
      setInfo(out?.configured
        ? t("outreach.info.profileSaved")
        : t("outreach.info.profileSavedIncomplete"));
      await fetchSnippets();
    } catch (e) {
      setProfileError(e.message);
    } finally {
      setProfileBusy(false);
    }
  };

  const selectQueueItem = (row) => {
    setSelected(row);
    setDraft(row.draft_text || "");
    if (row.platform) setPlatform(row.platform);
    setError(null);
    setInfo(null);
  };

  const limit = platformLimits()[platform] || { max: 0, label: platform };
  const charCount = draft.length;
  const wordCount = countWords(draft);
  const urls = useMemo(() => extractUrls(draft), [draft]);

  // Pre-flight gates
  const lengthOk = limit.max === 0 ? null : charCount > 0 && charCount <= limit.max;
  const linkOk = urls.length === 0 ? null : urls.every(u => /^https?:\/\/[^\s]+$/.test(u));
  const draftPresent = draft.trim().length > 0;
  const gradeOk = selected && selected.grade_score != null ? selected.grade_score >= 0.7 : null;

  const preflightBlocksApprove = lengthOk === false || gradeOk === false || !draftPresent;

  const handleApprove = async () => {
    if (!selected || preflightBlocksApprove) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const row = await outreachApi.approveDraft(selected.id, draft);
      setInfo(`${t("outreach.info.approved", { id: selected.id })} ${row?.next || ""}`.trim());
      setSelected(null);
      setDraft("");
      await fetchQueue();
      await fetchApproved();
    } catch (e) {
      setError(t("outreach.errors.approve", { error: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      await outreachApi.rejectDraft(selected.id);
      setInfo(t("outreach.info.rejected", { id: selected.id }));
      setSelected(null);
      setDraft("");
      await fetchQueue();
    } catch (e) {
      setError(t("outreach.errors.reject", { error: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const handleRedraft = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    const priorId = selected.id;
    try {
      const out = await outreachApi.draftComment({
        platform,
        thread_context: selected.draft_text
          ? `(prior draft was: "${selected.draft_text}") — redraft with a different angle.`
          : "",
        target_url: selected.target_url,
        target_thread_id: selected.target_thread_id,
        tone,
        mode: "comment",
      });
      setDraft(out.draft || "");
      setInfo(t("outreach.info.redrafted", { grade: (out.grade ?? 0).toFixed(2), reason: out.reason || "" }));
      try {
        await outreachApi.rejectDraft(priorId);
      } catch (_) {
        /* prior may already be gone */
      }
      await fetchQueue();
      if (out.audit_id) {
        const rows = await outreachApi.fetchQueue();
        const next = (Array.isArray(rows) ? rows : []).find((r) => r.id === out.audit_id);
        if (next) selectQueueItem(next);
      }
    } catch (e) {
      setError(t("outreach.errors.redraft", { error: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const handleSaveEdit = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const updated = await outreachApi.patchDraft(selected.id, { draft_text: draft, platform });
      setSelected(updated);
      setInfo(t("outreach.info.saved", { id: selected.id }));
      await fetchQueue();
    } catch (e) {
      setError(t("outreach.errors.save", { error: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const openNewDraft = () => {
    setNdPlatform(platform || "reddit");
    setNdAction("comment");
    setNdUrl("");
    setNdThreadId("");
    setNdContext("");
    setNdBody("");
    setNdGrade(null);
    setNdSeededId(null);
    setNewDraftOpen(true);
    setError(null); setInfo(null);
  };

  const handleSeedNewDraftLLM = async () => {
    if (!ndContext.trim()) {
      setError(t("outreach.errors.needContext"));
      return;
    }
    setNdBusy(true);
    setError(null);
    try {
      const out = await outreachApi.draftComment({
        platform: ndPlatform,
        mode: ndAction === "share" ? "share" : "comment",
        thread_context: ndContext,
        target_url: ndUrl || null,
        target_thread_id: ndThreadId || null,
        tone,
        share_target: ndThreadId || ndUrl || "(unspecified)",
        share_link: ndUrl || undefined,
      });
      setNdBody(out.draft || "");
      setNdGrade(out.grade != null ? Number(out.grade) : null);
      // /draft-comment also persisted a row. Remember its id so Save PATCHes
      // it instead of creating a sibling row.
      if (out.audit_id) setNdSeededId(out.audit_id);
      await fetchQueue();
    } catch (e) {
      setError(t("outreach.errors.seed", { error: e.message }));
    } finally {
      setNdBusy(false);
    }
  };

  const handleSaveNewDraft = async () => {
    if (!ndBody.trim()) {
      setError(t("outreach.errors.emptyBody"));
      return;
    }
    setNdBusy(true);
    setError(null); setInfo(null);
    try {
      // If the LLM seeded this draft, /draft-comment already wrote the row —
      // PATCH it. Otherwise POST a fresh row.
      const payload = {
        platform: ndPlatform,
        action: ndAction,
        target_url: ndUrl || null,
        target_thread_id: ndThreadId || null,
        draft_text: ndBody,
        ...(ndSeededId ? {} : { grade_score: ndGrade }),
      };
      const row = ndSeededId
        ? await outreachApi.patchDraft(ndSeededId, payload)
        : await outreachApi.createDraft(payload);
      setNewDraftOpen(false);
      setInfo(
        ndSeededId
          ? t("outreach.info.updatedDraft", { id: row.id })
          : t("outreach.info.createdDraft", { id: row.id }),
      );
      await fetchQueue();
      // Auto-select the freshly saved row so the user can immediately keep
      // editing or hit Approve without hunting for it in the list.
      selectQueueItem(row);
    } catch (e) {
      setError(t("outreach.errors.save", { error: e.message }));
    } finally {
      setNdBusy(false);
    }
  };

  const handleRunPass = async (passPlatform) => {
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const body = { platform: passPlatform };
      // YouTube: scout keyword_profiles then immediately draft (GitHub CTA).
      if (passPlatform === "youtube") {
        body.chain_draft = true;
      }
      const topicList = passTopics
        .split(/[,;\n]+/)
        .map((t) => t.trim())
        .filter(Boolean);
      if (topicList.length && (passPlatform === "youtube" || passPlatform === "reddit")) {
        body.topics = topicList;
      }
      const out = await outreachApi.runPass(body);
      if (out.task_id != null) setLastTaskId(out.task_id);
      setInfo(out.message || t("outreach.info.passQueued", { platform: passPlatform, id: out.task_id }));
      // Pass takes seconds-to-minutes on the worker. Refresh once now and
      // again shortly after so the new draft lands in the visible queue
      // without making the user hit the refresh icon.
      await fetchQueue();
      await fetchApproved();
      setTimeout(() => { fetchQueue(); fetchApproved(); fetchStatus(); }, 8000);
    } catch (e) {
      setError(t("outreach.errors.runPass", { error: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const handleScoutUrl = async ({ silent = false } = {}) => {
    const url = (ndUrl || "").trim();
    if (!url || !/^https?:\/\//i.test(url)) {
      if (!silent) setError(t("outreach.errors.needUrl"));
      return;
    }
    setNdScouting(true);
    if (!silent) setError(null);
    try {
      const out = await outreachApi.scoutUrl(url);
      // The scout returns thread_context already concatenated. If the user
      // hasn't typed anything in Topic, fill it. Otherwise leave their input
      // alone — surface a hint instead.
      if (out.thread_context) {
        setNdContext((prev) => prev && prev.trim() ? prev : out.thread_context);
      }
      if (out.target_thread_id && !ndThreadId) setNdThreadId(out.target_thread_id);
      if (out.suggested_platform) setNdPlatform(out.suggested_platform);
      if (!silent) {
        setInfo(
          t("outreach.info.scouted", {
            host: out.hostname || t("outreach.info.scoutedUrl"),
            source: out.source,
          }),
        );
      }
    } catch (e) {
      if (!silent) setError(t("outreach.errors.scout", { error: e.message }));
    } finally {
      setNdScouting(false);
    }
  };

  const handleEnableToggle = () => {
    if (!status) return;
    setKillConfirmOpen(true);
  };

  const confirmEnableToggle = async () => {
    if (!status) return;
    setKillConfirmOpen(false);
    setBusy(true);
    try {
      if (status.enabled) {
        await outreachApi.killOutreach();
      } else {
        await outreachApi.enableOutreach();
      }
      await fetchStatus();
    } catch (e) {
      setError(t("outreach.errors.toggle", { error: e.message }));
    } finally {
      setBusy(false);
    }
  };

  const confirmPostingStopToggle = async () => {
    if (!status) return;
    const stopping = !status.posting_stopped;
    setPostingStopConfirmOpen(false);
    setBusy(true);
    try {
      if (stopping) {
        const result = await outreachApi.stopAllPosting();
        const held = result.held_publishes || 0;
        const inFlight = result.in_flight_publishes || 0;
        setInfo(
          t("outreach.info.postingStopped", { count: held }) +
            (inFlight ? ` ${t("outreach.info.inFlight", { count: inFlight })}` : ""),
        );
      } else {
        await outreachApi.resumePosting();
        setInfo(t("outreach.info.postingResumed"));
      }
      await fetchStatus();
    } catch (e) {
      setError(
        stopping
          ? t("outreach.errors.stop", { error: e.message })
          : t("outreach.errors.resume", { error: e.message }),
      );
    } finally {
      setBusy(false);
    }
  };

  const closeNewDraftModal = async () => {
    if (ndBusy) return;
    if (ndSeededId) {
      try {
        await outreachApi.rejectDraft(ndSeededId);
      } catch (_) {
        /* ignore */
      }
      setNdSeededId(null);
      await fetchQueue();
    }
    setModalError(null);
    setNewDraftOpen(false);
  };

  const handleFetchMeta = async () => {
    if (!citationUrl.trim()) return;
    setCitationLoading(true);
    setCitationMeta(null);
    try {
      setCitationMeta(await outreachApi.fetchMeta(citationUrl.trim()));
    } catch (e) {
      setError(t("outreach.errors.citation", { error: e.message }));
    } finally {
      setCitationLoading(false);
    }
  };

  const insertSnippet = (text) => {
    setDraft((prev) => (prev ? `${prev.trimEnd()}\n\n${text}` : text));
  };

  const insertCitation = () => {
    if (!citationMeta) return;
    const cite = `${citationMeta.title} (${citationMeta.hostname}) — ${citationMeta.url}`;
    insertSnippet(cite);
  };

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────
  return (
    // Bounded scroll container. The previous `minHeight: calc(100vh - 64px)`
    // expanded the page taller than its parent (App's main content is
    // `overflow: hidden`), so anything past the fold was clipped — user had
    // to zoom out to see the History panel. The 15s polling tick also caused
    // visible "jumps" because state updates that resized child paper heights
    // pushed the whole page up; with overflow:auto here, scroll position is
    // preserved across renders.
    <Box sx={{ p: 2, height: "100%", overflowY: "auto" }}>
      {/* Top status bar */}
      <Paper sx={{ p: 1.5, mb: 2 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
          <Typography variant="h6" sx={{ mr: 2 }}>{t("outreach.header.title")}</Typography>
          {status ? (
            <>
              <Chip
                size="small"
                icon={<PowerSettingsNewIcon />}
                color={status.enabled ? "success" : "default"}
                label={status.enabled ? t("outreach.header.enabled") : t("outreach.header.disabled")}
                onClick={handleEnableToggle}
                variant="filled"
              />
              <Chip
                size="small"
                icon={<VisibilityIcon />}
                color="warning"
                label={t("outreach.header.approvalChip")}
                variant="outlined"
              />
              {status.cadence && Object.entries(status.cadence).map(([p, c]) => (
                <Chip
                  key={p}
                  size="small"
                  variant="outlined"
                  color={c.redis === "unavailable" ? "warning" : "default"}
                  label={
                    c.redis === "unavailable"
                      ? t("outreach.header.redisOffline", { platform: p })
                      : t("outreach.header.cadence", {
                          platform: p,
                          posts: c.posts_in_24h ?? 0,
                          cap: c.daily_cap ?? 0,
                        }) +
                        (c.last_post_seconds_ago != null
                          ? ` · ${t("outreach.header.lastPost", {
                              minutes: Math.floor(c.last_post_seconds_ago / 60),
                            })}`
                          : "")
                  }
                />
              ))}
            </>
          ) : (
            <CircularProgress size={16} />
          )}
          <Box sx={{ flex: 1 }} />
          {status && (
            <Button
              size="small"
              color={status.posting_stopped ? "success" : "error"}
              variant="outlined"
              startIcon={status.posting_stopped ? <PlayArrowIcon /> : <StopCircleIcon />}
              onClick={() => setPostingStopConfirmOpen(true)}
              disabled={busy}
            >
              {status.posting_stopped ? t("outreach.header.resumePosting") : t("outreach.header.stopPosting")}
            </Button>
          )}
          {lastTaskId != null && (
            <Typography variant="caption" color="text.secondary">
              <Trans
                i18nKey="outreach.header.lastPass"
                values={{ id: lastTaskId }}
                components={{ jobLink: <Link component={RouterLink} to="/tasks" underline="hover" /> }}
              />
            </Typography>
          )}
          <Tooltip title={t("outreach.header.refresh")}>
            <IconButton
              size="small"
              onClick={() => { fetchStatus(); fetchQueue(); fetchApproved(); fetchHistory(); fetchSnippets(); fetchProfile(); }}
            >
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Box>
        {status?.posting_stopped && (
          <Alert severity="error" icon={<StopCircleIcon />} sx={{ mt: 1 }}>
            {t("outreach.header.postingStoppedAlert")}
          </Alert>
        )}
        {status && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1, fontSize: "0.7rem" }}>
            {t("outreach.header.howItWorks")}
          </Typography>
        )}
      </Paper>

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>{error}</Alert>
      )}
      {info && (
        <Alert severity="info" onClose={() => setInfo(null)} sx={{ mb: 2 }}>{info}</Alert>
      )}

      {busy && <LinearProgress sx={{ mb: 1 }} />}

      <Box sx={{
        display: "grid",
        gridTemplateColumns: { xs: "minmax(0, 1fr)", lg: "320px minmax(0, 1fr) 320px" },
        gap: 2,
        alignItems: "start",
      }}>
        {/* ── Source / Context Zone ─────────────────────────────────── */}
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle2" sx={{ flex: 1 }}>
                {t("outreach.profile.title")}
              </Typography>
              {profile && (
                <Button
                  size="small"
                  variant={profileConfigured ? "text" : "outlined"}
                  startIcon={profileConfigured ? <EditIcon /> : <AddIcon />}
                  onClick={openProfileDialog}
                  sx={{ textTransform: "none", py: 0.25 }}
                >
                  {profileConfigured ? t("outreach.profile.edit") : t("outreach.profile.setUp")}
                </Button>
              )}
            </Stack>
            {!profile ? (
              <CircularProgress size={20} />
            ) : profileConfigured ? (
              <Stack spacing={0.5}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{profile.name}</Typography>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}
                >
                  {profile.pitch}
                </Typography>
                <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5 }}>
                  {profile.site_url && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={t("outreach.profile.site")}
                      sx={{ height: 18, fontSize: "0.65rem" }}
                    />
                  )}
                  {profile.repo_url && (
                    <Chip
                      size="small"
                      variant="outlined"
                      label={t("outreach.profile.repo")}
                      sx={{ height: 18, fontSize: "0.65rem" }}
                    />
                  )}
                  {profile.account_handle && <Chip size="small" variant="outlined" label={`@${profile.account_handle}`} sx={{ height: 18, fontSize: "0.65rem" }} />}
                  <Chip
                    size="small"
                    variant="outlined"
                    label={t("outreach.profile.videoCount", { count: profile.videos?.length || 0 })}
                    sx={{ height: 18, fontSize: "0.65rem" }}
                  />
                  <Chip
                    size="small"
                    variant="outlined"
                    label={t("outreach.profile.featureCount", { count: profile.features?.length || 0 })}
                    sx={{ height: 18, fontSize: "0.65rem" }}
                  />
                </Stack>
                {!profile.site_url && (
                  <Typography variant="caption" color="warning.main">
                    {t("outreach.profile.noSiteUrl")}
                  </Typography>
                )}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">
                {t("outreach.profile.empty")}
              </Typography>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle2" sx={{ flex: 1 }}>
                {t("outreach.queue.title", { count: queue.length })}
              </Typography>
              <Tooltip title={t("outreach.queue.newTooltip")}>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<AddIcon />}
                  onClick={openNewDraft}
                  sx={{ textTransform: "none", py: 0.25 }}
                >
                  {t("outreach.queue.new")}
                </Button>
              </Tooltip>
            </Stack>
            <Stack direction="row" spacing={0.5} sx={{ mb: 1, flexWrap: "wrap" }}>
              <Tooltip title={t("outreach.queue.redditPassTooltip")}>
                <span style={{ flex: 1, minWidth: "30%" }}>
                  <Button
                    fullWidth
                    size="small"
                    variant="text"
                    startIcon={<PlayArrowIcon />}
                    disabled={busy || !status?.enabled || status?.posting_stopped}
                    onClick={() => handleRunPass("reddit")}
                    sx={{ textTransform: "none", justifyContent: "flex-start", fontSize: "0.75rem" }}
                  >
                    {t("outreach.queue.redditPass")}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title={t("outreach.queue.youtubePassTooltip")}>
                <span style={{ flex: 1, minWidth: "30%" }}>
                  <Button
                    fullWidth
                    size="small"
                    variant="text"
                    startIcon={<PlayArrowIcon />}
                    disabled={busy || !status?.enabled || status?.posting_stopped}
                    onClick={() => handleRunPass("youtube")}
                    sx={{ textTransform: "none", justifyContent: "flex-start", fontSize: "0.75rem" }}
                  >
                    {t("outreach.queue.youtubePass")}
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title={t("outreach.queue.selfShareTooltip")}>
                <span style={{ flex: 1, minWidth: "30%" }}>
                  <Button
                    fullWidth
                    size="small"
                    variant="text"
                    startIcon={<PlayArrowIcon />}
                    disabled={busy || !status?.enabled || status?.posting_stopped}
                    onClick={() => handleRunPass("self_share")}
                    sx={{ textTransform: "none", justifyContent: "flex-start", fontSize: "0.75rem" }}
                  >
                    {t("outreach.queue.selfShare")}
                  </Button>
                </span>
              </Tooltip>
            </Stack>
            <TextField
              size="small"
              fullWidth
              label={t("outreach.queue.passTopics")}
              placeholder={t("outreach.queue.passTopicsPlaceholder")}
              value={passTopics}
              onChange={(e) => setPassTopics(e.target.value)}
              helperText={t("outreach.queue.passTopicsHelp")}
              sx={{ mb: 1, "& .MuiFormHelperText-root": { fontSize: "0.65rem", mx: 0 } }}
              inputProps={{ "aria-label": t("outreach.queue.passTopicsLabel") }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1, fontSize: "0.65rem" }}>
              {t("outreach.queue.youtubePassNote")}
            </Typography>
            {approved.length > 0 && (
              <Paper variant="outlined" sx={{ mb: 1, p: 1 }}>
                <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
                  {t("outreach.queue.approvedWaiting", { count: approved.length })}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5, fontSize: "0.65rem" }}>
                  <Trans
                    i18nKey="outreach.queue.approvedNote"
                    components={{ approvalsLink: <Link component={RouterLink} to="/approvals" underline="hover" /> }}
                  />
                </Typography>
                <List dense disablePadding sx={{ maxHeight: 140, overflowY: "auto" }}>
                  {approved.map((r) => (
                    <ListItemButton key={r.id} sx={{ py: 0.25, borderRadius: 1 }} disabled>
                      <ListItemText
                        primary={
                          <Stack direction="row" spacing={0.75} alignItems="center">
                            <Chip size="small" label={`#${r.id}`} sx={{ height: 16, fontSize: "0.6rem" }} />
                            <Chip size="small" label={r.platform} sx={{ height: 16, fontSize: "0.6rem" }} />
                            <Typography variant="caption" color="text.secondary" noWrap sx={{ maxWidth: 140 }}>
                              {r.target_url || r.target_thread_id || "—"}
                            </Typography>
                          </Stack>
                        }
                        secondary={
                          r.created_at
                            ? new Date(r.created_at).toLocaleString(uiLocale())
                            : undefined
                        }
                        secondaryTypographyProps={{ variant: "caption", fontSize: "0.6rem" }}
                      />
                    </ListItemButton>
                  ))}
                </List>
              </Paper>
            )}
            <Paper variant="outlined" sx={{ mb: 1, p: 1 }}>
              <Typography variant="caption" sx={{ fontWeight: 600, display: "block", mb: 0.5 }}>
                {t("outreach.queue.askInChat")}
              </Typography>
              <Stack spacing={0.25}>
                {CHAT_EXAMPLES.map((ex) => (
                  <Typography
                    key={ex}
                    variant="caption"
                    color="text.secondary"
                    sx={{ fontFamily: "monospace", fontSize: "0.65rem" }}
                  >
                    {ex}
                  </Typography>
                ))}
              </Stack>
            </Paper>
            {loadingQueue ? (
              <CircularProgress size={20} />
            ) : queue.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                {t("outreach.queue.empty")}
              </Typography>
            ) : (
              <List dense disablePadding sx={{ maxHeight: 360, overflowY: "auto" }}>
                {queue.map((row) => (
                  <ListItemButton
                    key={row.id}
                    selected={selected?.id === row.id}
                    onClick={() => selectQueueItem(row)}
                    sx={{ borderRadius: 1, mb: 0.5 }}
                  >
                    <ListItemText
                      primary={
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Chip size="small" label={row.platform} sx={{ height: 18, fontSize: "0.65rem" }} />
                          {row.grade_score != null && (
                            <Chip
                              size="small"
                              label={t("outreach.queue.grade", { grade: row.grade_score.toFixed(2) })}
                              color={row.grade_score >= 0.7 ? "success" : "warning"}
                              sx={{ height: 18, fontSize: "0.65rem" }}
                            />
                          )}
                        </Stack>
                      }
                      secondary={
                        <Typography variant="caption" sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                          {row.draft_text || t("outreach.queue.emptyDraft")}
                        </Typography>
                      }
                    />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{t("outreach.source.title")}</Typography>
            {selected ? (
              <Stack spacing={1}>
                <Typography variant="caption" color="text.secondary">
                  {t("outreach.source.drafted", { date: new Date(selected.created_at).toLocaleString(uiLocale()) })}
                </Typography>
                {selected.target_url && (
                  <Link href={selected.target_url} target="_blank" rel="noopener" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, fontSize: "0.85rem" }}>
                    {selected.target_url} <OpenInNewIcon fontSize="inherit" />
                  </Link>
                )}
                {selected.target_thread_id && (
                  <Typography variant="caption" color="text.secondary">
                    {t("outreach.source.threadId", { id: selected.target_thread_id })}
                  </Typography>
                )}
                {selected.abort_reason && (
                  <Alert severity="warning" sx={{ py: 0.5 }}>{selected.abort_reason}</Alert>
                )}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">
                {t("outreach.source.empty")}
              </Typography>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{t("outreach.snippets.title")}</Typography>
            {loadingSnippets ? (
              <CircularProgress size={20} />
            ) : snippets && (snippets.pitch || snippets.site_url || snippets.repo_url || snippets.videos?.length || Object.keys(snippets.feature_blurbs || {}).length) ? (
              <Stack spacing={0.5}>
                {snippets.pitch && (
                  <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.pitch)}>
                    {t("outreach.snippets.insertPitch")}
                  </Button>
                )}
                {snippets.site_url && (
                  <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.site_url)}>
                    {t("outreach.snippets.insertSiteUrl")}
                  </Button>
                )}
                {snippets.repo_url && (
                  <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.repo_url)}>
                    {t("outreach.snippets.insertRepoUrl")}
                  </Button>
                )}
                {(snippets.videos || []).map((v, i) => (
                  <Button key={`${v.url}-${i}`} size="small" variant="outlined" onClick={() => insertSnippet(v.url)}>
                    {t("outreach.snippets.insertVideo", {
                      label: v.label || t("outreach.snippets.videoNumber", { number: i + 1 }),
                    })}
                  </Button>
                ))}
                {Object.keys(snippets.feature_blurbs || {}).length > 0 && (
                  <>
                    <Divider sx={{ my: 1 }} />
                    <Typography variant="caption" color="text.secondary">{t("outreach.snippets.featureBlurbs")}</Typography>
                    {Object.entries(snippets.feature_blurbs).map(([k, v]) => (
                      <Tooltip key={k} title={v} placement="left">
                        <Button size="small" sx={{ justifyContent: "flex-start", textTransform: "none" }} onClick={() => insertSnippet(v)}>
                          {k}
                        </Button>
                      </Tooltip>
                    ))}
                  </>
                )}
              </Stack>
            ) : snippets ? (
              <Typography variant="body2" color="text.secondary">
                {t("outreach.snippets.fillProfile")}
              </Typography>
            ) : (
              <Typography variant="body2" color="text.secondary">{t("outreach.snippets.unavailable")}</Typography>
            )}
          </Paper>
        </Stack>

        {/* ── Drafting Zone ─────────────────────────────────────────── */}
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" spacing={2} sx={{ mb: 1.5 }}>
              <FormControl size="small" sx={{ minWidth: 220 }}>
                <InputLabel>{t("outreach.drafting.platform")}</InputLabel>
                <Select value={platform} label={t("outreach.drafting.platform")} onChange={(e) => setPlatform(e.target.value)}>
                  {platformOptions().map(p => (<MenuItem key={p.value} value={p.value}>{p.label}</MenuItem>))}
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 240 }}>
                <InputLabel>{t("outreach.drafting.tone")}</InputLabel>
                <Select value={tone} label={t("outreach.drafting.tone")} onChange={(e) => setTone(e.target.value)}>
                  {toneOptions().map((option) => (
                    <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Box sx={{ flex: 1 }} />
              <Tooltip title={t("outreach.drafting.redraftTooltip")}>
                <span>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<AddCommentIcon />}
                    onClick={handleRedraft}
                    disabled={!selected || busy}
                  >
                    {t("outreach.drafting.redraft")}
                  </Button>
                </span>
              </Tooltip>
            </Stack>
            <TextField
              fullWidth
              multiline
              minRows={14}
              maxRows={30}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={t("outreach.drafting.placeholder")}
              sx={{ "& .MuiInputBase-input": { fontFamily: '"Inter","Roboto",sans-serif', fontSize: "0.95rem" } }}
            />
            <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
              <Typography variant="caption" color="text.secondary">
                {t("outreach.drafting.words", { count: wordCount })}
              </Typography>
              <Typography variant="caption" color={lengthOk === false ? "warning.main" : "text.secondary"}>
                {t("outreach.drafting.chars", { count: charCount })}
                {limit.max > 0 ? ` / ${limit.max}` : ""}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("outreach.drafting.linkCount", { count: urls.length })}
              </Typography>
            </Stack>
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{t("outreach.citation.title")}</Typography>
            <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
              <TextField
                size="small"
                fullWidth
                placeholder={t("outreach.citation.placeholder")}
                value={citationUrl}
                onChange={(e) => setCitationUrl(e.target.value)}
              />
              <Button
                size="small"
                variant="contained"
                onClick={handleFetchMeta}
                disabled={!citationUrl.trim() || citationLoading}
              >
                {citationLoading ? <CircularProgress size={16} /> : t("outreach.citation.fetch")}
              </Button>
            </Stack>
            {citationMeta && (
              <Stack spacing={0.5}>
                <Typography variant="body2"><strong>{citationMeta.title || t("outreach.citation.noTitle")}</strong></Typography>
                <Typography variant="caption" color="text.secondary">{citationMeta.hostname}</Typography>
                {citationMeta.description && (
                  <Typography variant="caption" sx={{ fontStyle: "italic" }}>{citationMeta.description}</Typography>
                )}
                <Box>
                  <Button size="small" onClick={insertCitation}>{t("outreach.citation.insert")}</Button>
                </Box>
              </Stack>
            )}
          </Paper>
        </Stack>

        {/* ── Task / Output Zone ─────────────────────────────────────── */}
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{t("outreach.preflight.title")}</Typography>
            <Stack spacing={0.75}>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(draftPresent)}
                <Typography variant="body2">{t("outreach.preflight.draftPresent")}</Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(lengthOk)}
                <Typography variant="body2">
                  {t("outreach.preflight.lengthOk")}{" "}
                  {limit.max > 0
                    ? t("outreach.preflight.lengthDetail", { count: charCount, max: limit.max, label: limit.label })
                    : t("outreach.preflight.noCap")}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(linkOk)}
                <Typography variant="body2">
                  {t("outreach.preflight.linksOk")}{" "}
                  {urls.length === 0 ? t("outreach.preflight.none") : `(${urls.length})`}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(gradeOk)}
                <Typography variant="body2">
                  {t("outreach.preflight.gradeOk")}{" "}
                  {selected?.grade_score != null
                    ? `(${selected.grade_score.toFixed(2)})`
                    : t("outreach.preflight.noDraftSelected")}
                </Typography>
              </Stack>
            </Stack>
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>{t("outreach.action.title")}</Typography>
            <Stack spacing={1}>
              <Button
                fullWidth
                variant="contained"
                color="primary"
                startIcon={<SendIcon />}
                disabled={!selected || preflightBlocksApprove || busy}
                onClick={handleApprove}
              >
                {t("outreach.action.approve")}
              </Button>
              {preflightBlocksApprove && selected && (
                <Typography variant="caption" color="warning.main">
                  {t("outreach.action.fixWarnings")}
                </Typography>
              )}
              <Button
                fullWidth
                variant="outlined"
                startIcon={<SaveIcon />}
                disabled={!selected || selected.status !== "drafted" || !draftPresent || busy}
                onClick={handleSaveEdit}
              >
                {t("outreach.action.saveEdits")}
              </Button>
              <Button
                fullWidth
                variant="outlined"
                startIcon={<CloseIcon />}
                disabled={!selected || busy}
                onClick={handleReject}
              >
                {t("outreach.action.reject")}
              </Button>
              <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
                {t("outreach.action.note")}
              </Typography>
            </Stack>
          </Paper>

          {selected && (
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>{t("outreach.selected.title")}</Typography>
              <Stack spacing={0.5}>
                <Typography variant="caption" color="text.secondary">
                  <Trans
                    i18nKey="outreach.selected.idStatus"
                    values={{ id: selected.id }}
                    components={{
                      statusChip: (
                        <Chip
                          size="small"
                          label={statusLabels()[selected.status] || selected.status}
                          sx={{ height: 18, fontSize: "0.65rem" }}
                        />
                      ),
                    }}
                  />
                </Typography>
                {selected.task_id && (
                  <Typography variant="caption" color="text.secondary">
                    {t("outreach.selected.taskId", { id: selected.task_id })}
                  </Typography>
                )}
              </Stack>
            </Paper>
          )}
        </Stack>
      </Box>

      {/* ── History (read-only audit trail) ─────────────────────────── */}
      <Paper sx={{ p: 2, mt: 2 }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
          <Typography variant="subtitle2" sx={{ flex: 1 }}>
            {t("outreach.history.title")}
          </Typography>
          {[...HISTORY_STATUSES, "all"].map((f) => (
            <Chip
              key={f}
              size="small"
              label={statusLabels()[f] || f}
              variant={historyFilter === f ? "filled" : "outlined"}
              color={historyFilter === f ? "primary" : "default"}
              onClick={() => setHistoryFilter(f)}
              sx={{ textTransform: "capitalize" }}
            />
          ))}
          <Tooltip title={t("outreach.history.refresh")}>
            <span>
              <IconButton size="small" onClick={fetchHistory} disabled={loadingHistory}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
        {loadingHistory && history.length === 0 ? (
          <CircularProgress size={20} />
        ) : (() => {
          const filtered = historyFilter === "all"
            ? history.filter((r) => HISTORY_STATUSES.includes(r.status))
            : history.filter((r) => r.status === historyFilter);
          if (filtered.length === 0) {
            return (
              <Typography variant="body2" color="text.secondary">
                {t("outreach.history.empty")}
              </Typography>
            );
          }
          return (
            <Box sx={{ maxHeight: 480, overflowY: "auto" }}>
              <List dense disablePadding>
                {filtered.map((row) => {
                  const ts = row.created_at ? new Date(row.created_at) : null;
                  const text = (row.posted_text || row.draft_text || "").trim();
                  const statusColor = row.status === "posted" ? "success"
                                    : row.status === "aborted" ? "error"
                                    : row.status === "unsupported" ? "warning"
                                    : "default";
                  return (
                    <Box
                      key={row.id}
                      sx={{
                        py: 0.75, px: 1, mb: 0.5,
                        borderRadius: 1,
                        bgcolor: "background.default",
                        borderLeft: 3,
                        borderColor: row.status === "posted" ? "success.main"
                                   : row.status === "aborted" ? "error.main"
                                   : row.status === "unsupported" ? "warning.main"
                                   : "divider",
                      }}
                    >
                      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
                        <Tooltip title={ts ? ts.toString() : ""}>
                          <Typography variant="caption" color="text.secondary" sx={{ minWidth: 130 }}>
                            {ts ? ts.toLocaleString(uiLocale()) : "—"}
                          </Typography>
                        </Tooltip>
                        <Chip size="small" label={row.platform} sx={{ height: 18, fontSize: "0.65rem" }} />
                        <Chip
                          size="small"
                          label={statusLabels()[row.status] || row.status}
                          color={statusColor}
                          sx={{ height: 18, fontSize: "0.65rem" }}
                        />
                        {row.target_url ? (
                          <Link
                            href={row.target_url}
                            target="_blank"
                            rel="noopener"
                            sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, fontSize: "0.8rem", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                          >
                            {row.target_url} <OpenInNewIcon fontSize="inherit" />
                          </Link>
                        ) : (
                          <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
                            {t("outreach.history.noTarget")}
                          </Typography>
                        )}
                        <Typography variant="caption" color="text.secondary">
                          #{row.id}
                        </Typography>
                      </Stack>
                      {text && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{
                            mt: 0.5,
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            overflow: "hidden",
                          }}
                        >
                          {text}
                        </Typography>
                      )}
                      {row.abort_reason && (
                        <Typography variant="caption" color="error.main" sx={{ display: "block", mt: 0.25 }}>
                          {row.abort_reason}
                        </Typography>
                      )}
                    </Box>
                  );
                })}
              </List>
            </Box>
          );
        })()}
      </Paper>

      {/* ── New Draft modal ─────────────────────────────────────────── */}
      <Dialog
        open={newDraftOpen}
        onClose={() => { closeNewDraftModal(); }}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1 }}>
          {t("outreach.newDraft.title")}
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {t("outreach.newDraft.subtitle")}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {modalError && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setModalError(null)}>
              {modalError}
            </Alert>
          )}
          <Box sx={{
            display: "grid",
            gridTemplateColumns: { xs: "minmax(0, 1fr)", md: "260px minmax(0, 1fr)" },
            gap: 2,
          }}>
            {/* Metadata column */}
            <Stack spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>{t("outreach.newDraft.platform")}</InputLabel>
                <Select value={ndPlatform} label={t("outreach.newDraft.platform")} onChange={(e) => setNdPlatform(e.target.value)}>
                  {platformOptions().map(p => (
                    <MenuItem key={p.value} value={p.value}>
                      {p.label}{p.posts ? "" : ` ${t("outreach.newDraft.notPosted")}`}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>{t("outreach.newDraft.action")}</InputLabel>
                <Select value={ndAction} label={t("outreach.newDraft.action")} onChange={(e) => setNdAction(e.target.value)}>
                  <MenuItem value="comment">{t("outreach.newDraft.actionComment")}</MenuItem>
                  <MenuItem value="share">{t("outreach.newDraft.actionShare")}</MenuItem>
                </Select>
              </FormControl>
              <Stack direction="row" spacing={0.5} alignItems="flex-start">
                <TextField
                  size="small"
                  label={t("outreach.newDraft.targetUrl")}
                  placeholder="https://reddit.com/r/SideProject/..."
                  value={ndUrl}
                  onChange={(e) => setNdUrl(e.target.value)}
                  // Auto-scout the moment the user tabs/clicks away — only if
                  // they haven't typed a topic yet. Silent so it doesn't fight
                  // the "Save" workflow if the URL is already known-good.
                  onBlur={() => {
                    if (ndUrl.trim() && !ndContext.trim()) {
                      handleScoutUrl({ silent: true });
                    }
                  }}
                  fullWidth
                />
                <Tooltip title={t("outreach.newDraft.scoutTooltip")}>
                  <span>
                    <IconButton
                      size="small"
                      onClick={() => handleScoutUrl()}
                      disabled={ndScouting || !ndUrl.trim()}
                      sx={{ mt: 0.25 }}
                    >
                      {ndScouting ? <CircularProgress size={16} /> : <TravelExploreIcon fontSize="small" />}
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
              <TextField
                size="small"
                label={t("outreach.newDraft.threadId")}
                placeholder={t("outreach.newDraft.threadIdPlaceholder")}
                value={ndThreadId}
                onChange={(e) => setNdThreadId(e.target.value)}
                fullWidth
              />
              {!platformOptions().find(p => p.value === ndPlatform)?.posts && (
                <Alert severity="info" sx={{ py: 0.5, fontSize: "0.7rem" }}>
                  {t("outreach.newDraft.notShipped", { platform: ndPlatform })}
                </Alert>
              )}
              <Divider sx={{ my: 0.5 }} />
              <Typography variant="caption" color="text.secondary">
                {t("outreach.newDraft.toneNote", { tone })}
              </Typography>
              {ndGrade != null && (
                <Chip
                  size="small"
                  label={t("outreach.newDraft.grade", { grade: ndGrade.toFixed(2) })}
                  color={ndGrade >= 0.7 ? "success" : "warning"}
                  sx={{ alignSelf: "flex-start" }}
                />
              )}
              {ndSeededId && (
                <Typography variant="caption" color="text.secondary">
                  {t("outreach.newDraft.seeded", { id: ndSeededId })}
                </Typography>
              )}
            </Stack>

            {/* Body column */}
            <Stack spacing={1.5}>
              <TextField
                label={t("outreach.newDraft.context")}
                placeholder={t("outreach.newDraft.contextPlaceholder")}
                value={ndContext}
                onChange={(e) => setNdContext(e.target.value)}
                multiline
                minRows={3}
                maxRows={6}
                fullWidth
              />
              <Stack direction="row" spacing={1}>
                <Tooltip title={t("outreach.newDraft.draftTooltip")}>
                  <span>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={ndBusy ? <CircularProgress size={14} /> : <AutoAwesomeIcon />}
                      disabled={ndBusy || !ndContext.trim()}
                      onClick={handleSeedNewDraftLLM}
                    >
                      {t("outreach.newDraft.draftWithLlm")}
                    </Button>
                  </span>
                </Tooltip>
                <Box sx={{ flex: 1 }} />
                <Typography variant="caption" color="text.secondary" sx={{ alignSelf: "center" }}>
                  {t("outreach.newDraft.chars", { count: ndBody.length })}
                </Typography>
              </Stack>
              <TextField
                label={t("outreach.newDraft.body")}
                placeholder={t("outreach.newDraft.bodyPlaceholder")}
                value={ndBody}
                onChange={(e) => setNdBody(e.target.value)}
                multiline
                minRows={10}
                maxRows={20}
                fullWidth
              />
            </Stack>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={closeNewDraftModal} disabled={ndBusy}>
            {t("outreach.newDraft.cancel")}
          </Button>
          <Button
            variant="contained"
            startIcon={<SaveIcon />}
            onClick={handleSaveNewDraft}
            disabled={ndBusy || !ndBody.trim()}
          >
            {ndSeededId ? t("outreach.newDraft.saveSeeded") : t("outreach.newDraft.save")}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── What you promote ────────────────────────────────────────── */}
      <Dialog
        open={profileOpen}
        onClose={() => { if (!profileBusy) setProfileOpen(false); }}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1 }}>
          {t("outreach.profileDialog.title")}
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            {t("outreach.profileDialog.subtitle")}
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          {profileError && (
            <Alert severity="error" sx={{ mb: 2 }} onClose={() => setProfileError(null)}>
              {profileError}
            </Alert>
          )}
          <Stack spacing={1.5}>
            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
              <TextField
                size="small"
                label={t("outreach.profileDialog.name")}
                placeholder={t("outreach.profileDialog.namePlaceholder")}
                value={profileForm.name}
                onChange={(e) => setProfileField("name", e.target.value)}
                fullWidth
                inputProps={{ maxLength: 80 }}
              />
              <TextField
                size="small"
                label={t("outreach.profileDialog.handle")}
                placeholder={t("outreach.profileDialog.handlePlaceholder")}
                value={profileForm.account_handle}
                onChange={(e) => setProfileField("account_handle", e.target.value)}
                fullWidth
                inputProps={{ maxLength: 80 }}
              />
            </Stack>
            <TextField
              size="small"
              label={t("outreach.profileDialog.pitch")}
              placeholder={t("outreach.profileDialog.pitchPlaceholder")}
              value={profileForm.pitch}
              onChange={(e) => setProfileField("pitch", e.target.value)}
              multiline
              minRows={2}
              maxRows={6}
              fullWidth
              inputProps={{ maxLength: 2000 }}
            />
            <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
              <TextField
                size="small"
                label={t("outreach.profileDialog.siteUrl")}
                placeholder="https://"
                value={profileForm.site_url}
                onChange={(e) => setProfileField("site_url", e.target.value)}
                fullWidth
                helperText={t("outreach.profileDialog.siteUrlHelp")}
              />
              <TextField
                size="small"
                label={t("outreach.profileDialog.repoUrl")}
                placeholder="https://"
                value={profileForm.repo_url}
                onChange={(e) => setProfileField("repo_url", e.target.value)}
                fullWidth
                helperText={t("outreach.profileDialog.repoUrlHelp")}
              />
            </Stack>

            <Divider />
            <Stack direction="row" alignItems="center">
              <Typography variant="subtitle2" sx={{ flex: 1 }}>{t("outreach.profileDialog.videos")}</Typography>
              <Button
                size="small"
                startIcon={<AddIcon />}
                disabled={profileForm.videos.length >= 20}
                onClick={() => addProfileRow("videos", { label: "", url: "" })}
                sx={{ textTransform: "none" }}
              >
                {t("outreach.profileDialog.addVideo")}
              </Button>
            </Stack>
            {profileForm.videos.length === 0 && (
              <Typography variant="caption" color="text.secondary">
                {t("outreach.profileDialog.videosEmpty")}
              </Typography>
            )}
            {profileForm.videos.map((v, i) => (
              <Stack key={`video-${i}`} direction="row" spacing={1} alignItems="flex-start">
                <TextField
                  size="small"
                  label={t("outreach.profileDialog.label")}
                  value={v.label}
                  onChange={(e) => setProfileRow("videos", i, "label", e.target.value)}
                  sx={{ width: { xs: "40%", md: 220 } }}
                  inputProps={{ maxLength: 80 }}
                />
                <TextField
                  size="small"
                  label={t("outreach.profileDialog.url")}
                  placeholder="https://"
                  value={v.url}
                  onChange={(e) => setProfileRow("videos", i, "url", e.target.value)}
                  fullWidth
                />
                <Tooltip title={t("outreach.profileDialog.remove")}>
                  <IconButton size="small" onClick={() => removeProfileRow("videos", i)} sx={{ mt: 0.25 }}>
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            ))}

            <Divider />
            <Stack direction="row" alignItems="center">
              <Typography variant="subtitle2" sx={{ flex: 1 }}>{t("outreach.profileDialog.features")}</Typography>
              <Button
                size="small"
                startIcon={<AddIcon />}
                disabled={profileForm.features.length >= 40}
                onClick={() => addProfileRow("features", { key: "", blurb: "", keywords: "" })}
                sx={{ textTransform: "none" }}
              >
                {t("outreach.profileDialog.addFeature")}
              </Button>
            </Stack>
            <Typography variant="caption" color="text.secondary">
              {t("outreach.profileDialog.featuresNote")}
            </Typography>
            {profileForm.features.map((f, i) => (
              <Stack key={`feature-${i}`} direction="row" spacing={1} alignItems="flex-start">
                <TextField
                  size="small"
                  label={t("outreach.profileDialog.key")}
                  placeholder="local_ai"
                  value={f.key}
                  onChange={(e) => setProfileRow("features", i, "key", e.target.value)}
                  sx={{ width: { xs: "30%", md: 160 } }}
                  inputProps={{ maxLength: 40 }}
                />
                <TextField
                  size="small"
                  label={t("outreach.profileDialog.blurb")}
                  value={f.blurb}
                  onChange={(e) => setProfileRow("features", i, "blurb", e.target.value)}
                  fullWidth
                  inputProps={{ maxLength: 300 }}
                />
                <TextField
                  size="small"
                  label={t("outreach.profileDialog.keywords")}
                  placeholder={t("outreach.profileDialog.keywordsPlaceholder")}
                  value={f.keywords}
                  onChange={(e) => setProfileRow("features", i, "keywords", e.target.value)}
                  sx={{ width: { xs: "40%", md: 260 } }}
                />
                <Tooltip title={t("outreach.profileDialog.remove")}>
                  <IconButton size="small" onClick={() => removeProfileRow("features", i)} sx={{ mt: 0.25 }}>
                    <DeleteOutlineIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </Stack>
            ))}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setProfileOpen(false)} disabled={profileBusy}>
            {t("outreach.profileDialog.cancel")}
          </Button>
          <Button
            variant="contained"
            startIcon={profileBusy ? <CircularProgress size={14} /> : <SaveIcon />}
            onClick={handleSaveProfile}
            disabled={profileBusy}
          >
            {t("outreach.profileDialog.save")}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={postingStopConfirmOpen} onClose={() => setPostingStopConfirmOpen(false)}>
        <DialogTitle>
          {status?.posting_stopped ? t("outreach.postingDialog.resumeTitle") : t("outreach.postingDialog.stopTitle")}
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            {status?.posting_stopped
              ? t("outreach.postingDialog.resumeBody")
              : t("outreach.postingDialog.stopBody")}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPostingStopConfirmOpen(false)}>{t("outreach.postingDialog.cancel")}</Button>
          <Button
            variant="contained"
            color={status?.posting_stopped ? "success" : "error"}
            onClick={confirmPostingStopToggle}
            autoFocus
          >
            {status?.posting_stopped ? t("outreach.postingDialog.resume") : t("outreach.postingDialog.stop")}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={killConfirmOpen} onClose={() => setKillConfirmOpen(false)}>
        <DialogTitle>
          {status?.enabled ? t("outreach.enableDialog.disableTitle") : t("outreach.enableDialog.enableTitle")}
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            {status?.enabled
              ? t("outreach.enableDialog.disableBody")
              : t("outreach.enableDialog.enableBody")}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setKillConfirmOpen(false)}>{t("outreach.enableDialog.cancel")}</Button>
          <Button
            variant="contained"
            color={status?.enabled ? "warning" : "success"}
            onClick={confirmEnableToggle}
            autoFocus
          >
            {status?.enabled ? t("outreach.enableDialog.disable") : t("outreach.enableDialog.enable")}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default OutreachPage;
