import React, { useEffect, useMemo, useState, useCallback } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
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
import VisibilityIcon from "@mui/icons-material/Visibility";
import AddCommentIcon from "@mui/icons-material/AddComment";
import AddIcon from "@mui/icons-material/Add";
import SaveIcon from "@mui/icons-material/Save";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api";

// Per-platform character soft-caps used by the pre-flight panel.
// Sources: reddit (10000 comment), twitter (280), discord (2000), facebook (~5000),
// internal note (no cap — display only).
const PLATFORM_LIMITS = {
  reddit: { max: 10000, label: "Reddit comment" },
  reddit_share: { max: 300, label: "Reddit title" },
  discord: { max: 2000, label: "Discord message" },
  facebook: { max: 5000, label: "Facebook post" },
  twitter: { max: 280, label: "Tweet" },
  internal: { max: 0, label: "Internal note" },
};

const TONE_OPTIONS = [
  { value: "default", label: "Default voice (direct, competent, dry)" },
  { value: "engaging", label: "Engaging / curious" },
  { value: "technical", label: "Technical / precise" },
  { value: "casual", label: "Casual / phone-typed" },
  { value: "formal", label: "Formal" },
  { value: "humorous", label: "Humorous (dry, used sparingly)" },
];

// `auto: true` = the backend has a real posting path for this platform
// (Reddit servo loop, Discord cog). For the others the queue is the only
// output today — flagged in the modal so users aren't surprised when an
// approved Twitter draft never goes anywhere.
const PLATFORM_OPTIONS = [
  { value: "reddit", label: "Reddit comment", auto: true },
  { value: "discord", label: "Discord message", auto: true },
  { value: "twitter", label: "Twitter / X", auto: false },
  { value: "facebook", label: "Facebook", auto: false },
  { value: "internal", label: "Internal note", auto: false },
];

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
  const [status, setStatus] = useState(null);
  const [queue, setQueue] = useState([]);
  const [selected, setSelected] = useState(null);
  const [draft, setDraft] = useState("");
  const [tone, setTone] = useState("default");
  const [platform, setPlatform] = useState("reddit");
  const [snippets, setSnippets] = useState(null);
  const [loadingQueue, setLoadingQueue] = useState(false);
  const [loadingSnippets, setLoadingSnippets] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);

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
      const r = await fetch(`${BASE_URL}/social-outreach/status`);
      if (r.ok) setStatus(await r.json());
    } catch (e) {
      setError(`status fetch failed: ${e.message}`);
    }
  }, []);

  const fetchQueue = useCallback(async () => {
    setLoadingQueue(true);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/queue`);
      if (r.ok) {
        const rows = await r.json();
        setQueue(Array.isArray(rows) ? rows : []);
      }
    } catch (e) {
      setError(`queue fetch failed: ${e.message}`);
    } finally {
      setLoadingQueue(false);
    }
  }, []);

  const fetchSnippets = useCallback(async () => {
    setLoadingSnippets(true);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/snippets`);
      if (r.ok) setSnippets(await r.json());
    } catch (e) {
      setError(`snippets fetch failed: ${e.message}`);
    } finally {
      setLoadingSnippets(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchQueue();
    fetchSnippets();
    // Refresh status + queue periodically — keeps the dashboard honest
    // about cadence and incoming drafts. 15s feels like the right pace.
    const t = setInterval(() => { fetchStatus(); fetchQueue(); }, 15000);
    return () => clearInterval(t);
  }, [fetchStatus, fetchQueue, fetchSnippets]);

  const selectQueueItem = (row) => {
    setSelected(row);
    setDraft(row.draft_text || "");
    if (row.platform) setPlatform(row.platform);
    setError(null);
    setInfo(null);
  };

  const limit = PLATFORM_LIMITS[platform] || { max: 0, label: platform };
  const charCount = draft.length;
  const wordCount = countWords(draft);
  const urls = useMemo(() => extractUrls(draft), [draft]);

  // Pre-flight gates
  const lengthOk = limit.max === 0 ? null : charCount > 0 && charCount <= limit.max;
  const linkOk = urls.length === 0 ? null : urls.every(u => /^https?:\/\/[^\s]+$/.test(u));
  const draftPresent = draft.trim().length > 0;
  const gradeOk = selected && selected.grade_score != null ? selected.grade_score >= 0.7 : null;

  const handleApprove = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/approve/${selected.id}`, { 
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_text: draft })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setInfo(`Approved draft #${selected.id}. Will go out on the next outreach pass.`);
      // Clear selection so the editor doesn't keep showing the just-approved
      // draft as a "ghost" after fetchQueue removes it from the list.
      setSelected(null);
      setDraft("");
      await fetchQueue();
    } catch (e) {
      setError(`approve failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/reject/${selected.id}`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setInfo(`Rejected draft #${selected.id}.`);
      setSelected(null);
      setDraft("");
      await fetchQueue();
    } catch (e) {
      setError(`reject failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleRedraft = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/draft-comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform,
          thread_context: selected.draft_text
            ? `(prior draft was: "${selected.draft_text}") — redraft with a different angle.`
            : "",
          target_url: selected.target_url,
          target_thread_id: selected.target_thread_id,
          tone,
          mode: "comment",
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const out = await r.json();
      setDraft(out.draft || "");
      setInfo(`Redrafted (grade ${(out.grade ?? 0).toFixed(2)}): ${out.reason || ""}`);
      await fetchQueue();
    } catch (e) {
      setError(`redraft failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleSaveEdit = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/drafts/${selected.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft_text: draft, platform }),
      });
      if (!r.ok) {
        const errBody = await r.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${r.status}`);
      }
      const updated = await r.json();
      setSelected(updated);
      setInfo(`Saved draft #${selected.id}. Still in the queue, not posted.`);
      await fetchQueue();
    } catch (e) {
      setError(`save failed: ${e.message}`);
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
      setError("Add some thread context — give the LLM something to riff on.");
      return;
    }
    setNdBusy(true);
    setError(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/draft-comment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform: ndPlatform,
          mode: ndAction === "share" ? "share" : "comment",
          thread_context: ndContext,
          target_url: ndUrl || null,
          target_thread_id: ndThreadId || null,
          tone,
          // share-mode needs these — harmless to send for comment mode
          share_target: ndThreadId || ndUrl || "(unspecified)",
          share_link: ndUrl || undefined,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const out = await r.json();
      setNdBody(out.draft || "");
      setNdGrade(out.grade != null ? Number(out.grade) : null);
      // /draft-comment also persisted a row. Remember its id so Save PATCHes
      // it instead of creating a sibling row.
      if (out.audit_id) setNdSeededId(out.audit_id);
      await fetchQueue();
    } catch (e) {
      setError(`LLM seed failed: ${e.message}`);
    } finally {
      setNdBusy(false);
    }
  };

  const handleSaveNewDraft = async () => {
    if (!ndBody.trim()) {
      setError("Draft body is empty — type something or seed it with the LLM.");
      return;
    }
    setNdBusy(true);
    setError(null); setInfo(null);
    try {
      // If the LLM seeded this draft, /draft-comment already wrote the row —
      // PATCH it. Otherwise POST a fresh row.
      const url = ndSeededId
        ? `${BASE_URL}/social-outreach/drafts/${ndSeededId}`
        : `${BASE_URL}/social-outreach/drafts`;
      const method = ndSeededId ? "PATCH" : "POST";
      const r = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform: ndPlatform,
          action: ndAction,
          target_url: ndUrl || null,
          target_thread_id: ndThreadId || null,
          draft_text: ndBody,
          ...(ndSeededId ? {} : { grade_score: ndGrade }),
        }),
      });
      if (!r.ok) {
        const errBody = await r.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${r.status}`);
      }
      const row = await r.json();
      setNewDraftOpen(false);
      setInfo(`${ndSeededId ? "Updated" : "Created"} draft #${row.id}. It's in the queue waiting for approval.`);
      await fetchQueue();
      // Auto-select the freshly saved row so the user can immediately keep
      // editing or hit Approve without hunting for it in the list.
      selectQueueItem(row);
    } catch (e) {
      setError(`save failed: ${e.message}`);
    } finally {
      setNdBusy(false);
    }
  };

  const handleRunPass = async (passPlatform) => {
    setBusy(true);
    setError(null); setInfo(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/run-pass`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: passPlatform }),
      });
      if (!r.ok) {
        const errBody = await r.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${r.status}`);
      }
      const out = await r.json();
      setInfo(out.message || `${passPlatform} pass queued (task ${out.task_id}).`);
      // Pass takes seconds-to-minutes on the worker. Refresh once now and
      // again shortly after so the new draft lands in the visible queue
      // without making the user hit the refresh icon.
      await fetchQueue();
      setTimeout(() => { fetchQueue(); fetchStatus(); }, 8000);
    } catch (e) {
      setError(`run-pass failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleScoutUrl = async ({ silent = false } = {}) => {
    const url = (ndUrl || "").trim();
    if (!url || !/^https?:\/\//i.test(url)) {
      if (!silent) setError("Paste an http(s) URL first.");
      return;
    }
    setNdScouting(true);
    if (!silent) setError(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/scout-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!r.ok) {
        const errBody = await r.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${r.status}`);
      }
      const out = await r.json();
      // The scout returns thread_context already concatenated. If the user
      // hasn't typed anything in Topic, fill it. Otherwise leave their input
      // alone — surface a hint instead.
      if (out.thread_context) {
        setNdContext((prev) => prev && prev.trim() ? prev : out.thread_context);
      }
      if (out.target_thread_id && !ndThreadId) setNdThreadId(out.target_thread_id);
      if (out.suggested_platform) setNdPlatform(out.suggested_platform);
      if (!silent) {
        setInfo(`Scouted ${out.hostname || "URL"} (${out.source}). Topic prefilled — edit before drafting.`);
      }
    } catch (e) {
      if (!silent) setError(`scout failed: ${e.message}`);
    } finally {
      setNdScouting(false);
    }
  };

  const handleEnableToggle = async () => {
    if (!status) return;
    setBusy(true);
    try {
      const path = status.enabled ? "kill" : "enable";
      await fetch(`${BASE_URL}/social-outreach/${path}`, { method: "POST" });
      await fetchStatus();
    } catch (e) {
      setError(`toggle failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleSupervisedToggle = async () => {
    if (!status) return;
    setBusy(true);
    try {
      await fetch(`${BASE_URL}/social-outreach/supervised`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: !status.supervised }),
      });
      await fetchStatus();
    } catch (e) {
      setError(`supervised toggle failed: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const handleFetchMeta = async () => {
    if (!citationUrl.trim()) return;
    setCitationLoading(true);
    setCitationMeta(null);
    try {
      const r = await fetch(`${BASE_URL}/social-outreach/fetch-meta`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: citationUrl.trim() }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setCitationMeta(await r.json());
    } catch (e) {
      setError(`citation fetch failed: ${e.message}`);
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
    <Box sx={{ p: 2, minHeight: "calc(100vh - 64px)" }}>
      {/* Top status bar */}
      <Paper sx={{ p: 1.5, mb: 2, display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6" sx={{ mr: 2 }}>Outreach</Typography>
        {status ? (
          <>
            <Chip
              size="small"
              icon={<PowerSettingsNewIcon />}
              color={status.enabled ? "success" : "default"}
              label={status.enabled ? "Enabled" : "Disabled"}
              onClick={handleEnableToggle}
              variant="filled"
            />
            <Chip
              size="small"
              icon={<VisibilityIcon />}
              color={status.supervised ? "warning" : "default"}
              label={status.supervised ? "Supervised (queue only)" : "Unsupervised"}
              onClick={handleSupervisedToggle}
              variant="outlined"
            />
            {status.cadence && Object.entries(status.cadence).map(([p, c]) => (
              <Chip
                key={p}
                size="small"
                variant="outlined"
                label={`${p}: ${c.posts_in_24h ?? 0}/${c.daily_cap ?? 0} today` +
                  (c.last_post_seconds_ago != null ? ` · last ${Math.floor(c.last_post_seconds_ago / 60)}m ago` : "")}
              />
            ))}
          </>
        ) : (
          <CircularProgress size={16} />
        )}
        <Box sx={{ flex: 1 }} />
        <Tooltip title="Refresh">
          <IconButton size="small" onClick={() => { fetchStatus(); fetchQueue(); }}>
            <RefreshIcon />
          </IconButton>
        </Tooltip>
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
        gridTemplateColumns: { xs: "1fr", lg: "320px 1fr 320px" },
        gap: 2,
        alignItems: "start",
      }}>
        {/* ── Source / Context Zone ─────────────────────────────────── */}
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
              <Typography variant="subtitle2" sx={{ flex: 1 }}>
                Queue ({queue.length})
              </Typography>
              <Tooltip title="Start a new draft from scratch">
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<AddIcon />}
                  onClick={openNewDraft}
                  sx={{ textTransform: "none", py: 0.25 }}
                >
                  New
                </Button>
              </Tooltip>
            </Stack>
            <Stack direction="row" spacing={0.5} sx={{ mb: 1.25 }}>
              <Tooltip title="Run the Reddit outreach pass now (next sub from targets.json) — drafts land here when it's done">
                <span style={{ flex: 1 }}>
                  <Button
                    fullWidth
                    size="small"
                    variant="text"
                    startIcon={<PlayArrowIcon />}
                    disabled={busy || !status?.enabled}
                    onClick={() => handleRunPass("reddit")}
                    sx={{ textTransform: "none", justifyContent: "flex-start", fontSize: "0.75rem" }}
                  >
                    Reddit pass
                  </Button>
                </span>
              </Tooltip>
              <Tooltip title="Run the self-share pass — submits a link post to the next round-robin sub">
                <span style={{ flex: 1 }}>
                  <Button
                    fullWidth
                    size="small"
                    variant="text"
                    startIcon={<PlayArrowIcon />}
                    disabled={busy || !status?.enabled}
                    onClick={() => handleRunPass("self_share")}
                    sx={{ textTransform: "none", justifyContent: "flex-start", fontSize: "0.75rem" }}
                  >
                    Self-share
                  </Button>
                </span>
              </Tooltip>
            </Stack>
            <Typography variant="caption" color="text.disabled" sx={{ display: "block", mb: 1, fontSize: "0.65rem" }}>
              Discord cog auto-polls every 10 min — no manual trigger needed.
            </Typography>
            {loadingQueue ? (
              <CircularProgress size={20} />
            ) : queue.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                No drafts pending. The Reddit tick fires every 45 min, the Discord cog every 10 min. Hit refresh after that to see new entries.
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
                              label={`grade ${row.grade_score.toFixed(2)}`}
                              color={row.grade_score >= 0.7 ? "success" : "warning"}
                              sx={{ height: 18, fontSize: "0.65rem" }}
                            />
                          )}
                        </Stack>
                      }
                      secondary={
                        <Typography variant="caption" sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                          {row.draft_text || "(empty draft)"}
                        </Typography>
                      }
                    />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Source</Typography>
            {selected ? (
              <Stack spacing={1}>
                <Typography variant="caption" color="text.secondary">
                  Drafted {new Date(selected.created_at).toLocaleString()}
                </Typography>
                {selected.target_url && (
                  <Link href={selected.target_url} target="_blank" rel="noopener" sx={{ display: "inline-flex", alignItems: "center", gap: 0.5, fontSize: "0.85rem" }}>
                    {selected.target_url} <OpenInNewIcon fontSize="inherit" />
                  </Link>
                )}
                {selected.target_thread_id && (
                  <Typography variant="caption" color="text.secondary">thread id: {selected.target_thread_id}</Typography>
                )}
                {selected.abort_reason && (
                  <Alert severity="warning" sx={{ py: 0.5 }}>{selected.abort_reason}</Alert>
                )}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">
                Select a queue item to load its source context.
              </Typography>
            )}
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Snippet bank</Typography>
            {loadingSnippets ? (
              <CircularProgress size={20} />
            ) : snippets ? (
              <Stack spacing={0.5}>
                <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.pitch)}>Insert pitch</Button>
                <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.site_url)}>Insert site URL</Button>
                <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.github_url)}>Insert GitHub</Button>
                <Button size="small" variant="outlined" onClick={() => insertSnippet(snippets.gotham_rising_url)}>Insert Gotham Rising demo</Button>
                <Divider sx={{ my: 1 }} />
                <Typography variant="caption" color="text.secondary">Feature blurbs</Typography>
                {snippets.feature_blurbs && Object.entries(snippets.feature_blurbs).map(([k, v]) => (
                  <Tooltip key={k} title={v} placement="left">
                    <Button size="small" sx={{ justifyContent: "flex-start", textTransform: "none" }} onClick={() => insertSnippet(v)}>
                      {k}
                    </Button>
                  </Tooltip>
                ))}
              </Stack>
            ) : (
              <Typography variant="body2" color="text.secondary">Snippets unavailable.</Typography>
            )}
          </Paper>
        </Stack>

        {/* ── Drafting Zone ─────────────────────────────────────────── */}
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Stack direction="row" spacing={2} sx={{ mb: 1.5 }}>
              <FormControl size="small" sx={{ minWidth: 220 }}>
                <InputLabel>Platform</InputLabel>
                <Select value={platform} label="Platform" onChange={(e) => setPlatform(e.target.value)}>
                  {PLATFORM_OPTIONS.map(p => (<MenuItem key={p.value} value={p.value}>{p.label}</MenuItem>))}
                </Select>
              </FormControl>
              <FormControl size="small" sx={{ minWidth: 240 }}>
                <InputLabel>Tone</InputLabel>
                <Select value={tone} label="Tone" onChange={(e) => setTone(e.target.value)}>
                  {TONE_OPTIONS.map(t => (<MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>))}
                </Select>
              </FormControl>
              <Box sx={{ flex: 1 }} />
              <Tooltip title="Have the LLM redraft this with current tone + platform">
                <span>
                  <Button
                    size="small"
                    variant="outlined"
                    startIcon={<AddCommentIcon />}
                    onClick={handleRedraft}
                    disabled={!selected || busy}
                  >
                    Redraft
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
              placeholder="Pick a queue item to load its draft, or type one here. Markdown is fine for Reddit / Discord."
              sx={{ "& .MuiInputBase-input": { fontFamily: '"Inter","Roboto",sans-serif', fontSize: "0.95rem" } }}
            />
            <Stack direction="row" spacing={2} sx={{ mt: 1 }}>
              <Typography variant="caption" color="text.secondary">{wordCount} words</Typography>
              <Typography variant="caption" color={lengthOk === false ? "warning.main" : "text.secondary"}>
                {charCount} chars{limit.max > 0 ? ` / ${limit.max}` : ""}
              </Typography>
              <Typography variant="caption" color="text.secondary">{urls.length} link{urls.length === 1 ? "" : "s"}</Typography>
            </Stack>
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Citation tool</Typography>
            <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
              <TextField
                size="small"
                fullWidth
                placeholder="Paste a URL — title + domain pulled for clean attribution"
                value={citationUrl}
                onChange={(e) => setCitationUrl(e.target.value)}
              />
              <Button
                size="small"
                variant="contained"
                onClick={handleFetchMeta}
                disabled={!citationUrl.trim() || citationLoading}
              >
                {citationLoading ? <CircularProgress size={16} /> : "Fetch"}
              </Button>
            </Stack>
            {citationMeta && (
              <Stack spacing={0.5}>
                <Typography variant="body2"><strong>{citationMeta.title || "(no title)"}</strong></Typography>
                <Typography variant="caption" color="text.secondary">{citationMeta.hostname}</Typography>
                {citationMeta.description && (
                  <Typography variant="caption" sx={{ fontStyle: "italic" }}>{citationMeta.description}</Typography>
                )}
                <Box>
                  <Button size="small" onClick={insertCitation}>Insert citation</Button>
                </Box>
              </Stack>
            )}
          </Paper>
        </Stack>

        {/* ── Task / Output Zone ─────────────────────────────────────── */}
        <Stack spacing={2}>
          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Pre-flight</Typography>
            <Stack spacing={0.75}>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(draftPresent)}
                <Typography variant="body2">Draft present</Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(lengthOk)}
                <Typography variant="body2">
                  Length OK {limit.max > 0 ? `(${charCount}/${limit.max} for ${limit.label})` : "(no platform cap)"}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(linkOk)}
                <Typography variant="body2">
                  Links well-formed {urls.length === 0 ? "(none)" : `(${urls.length})`}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                {formatGate(gradeOk)}
                <Typography variant="body2">
                  Self-grade ≥ 0.7 {selected?.grade_score != null ? `(${selected.grade_score.toFixed(2)})` : "(no draft selected)"}
                </Typography>
              </Stack>
            </Stack>
          </Paper>

          <Paper sx={{ p: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Action</Typography>
            <Stack spacing={1}>
              <Button
                fullWidth
                variant="contained"
                color="primary"
                startIcon={<SendIcon />}
                disabled={!selected || !draftPresent || busy}
                onClick={handleApprove}
              >
                Approve for posting
              </Button>
              <Button
                fullWidth
                variant="outlined"
                startIcon={<SaveIcon />}
                disabled={!selected || selected.status !== "drafted" || !draftPresent || busy}
                onClick={handleSaveEdit}
              >
                Save edits
              </Button>
              <Button
                fullWidth
                variant="outlined"
                startIcon={<CloseIcon />}
                disabled={!selected || busy}
                onClick={handleReject}
              >
                Reject
              </Button>
              <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
                Approve marks the draft so the next outreach pass posts it. Reject removes it from the queue.
              </Typography>
            </Stack>
          </Paper>

          {selected && (
            <Paper sx={{ p: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Selected draft</Typography>
              <Stack spacing={0.5}>
                <Typography variant="caption" color="text.secondary">
                  id #{selected.id} · status: <Chip size="small" label={selected.status} sx={{ height: 18, fontSize: "0.65rem" }} />
                </Typography>
                {selected.task_id && (
                  <Typography variant="caption" color="text.secondary">task id: {selected.task_id}</Typography>
                )}
              </Stack>
            </Paper>
          )}
        </Stack>
      </Box>

      {/* ── New Draft modal ─────────────────────────────────────────── */}
      <Dialog
        open={newDraftOpen}
        onClose={() => !ndBusy && setNewDraftOpen(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ pb: 1 }}>
          New outreach draft
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
            Lands in the queue as a drafted row. Approve later from the main panel — or seed the body with the LLM, edit, and save.
          </Typography>
        </DialogTitle>
        <DialogContent dividers>
          <Box sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "260px 1fr" },
            gap: 2,
          }}>
            {/* Metadata column */}
            <Stack spacing={1.5}>
              <FormControl size="small" fullWidth>
                <InputLabel>Platform</InputLabel>
                <Select value={ndPlatform} label="Platform" onChange={(e) => setNdPlatform(e.target.value)}>
                  {PLATFORM_OPTIONS.map(p => (
                    <MenuItem key={p.value} value={p.value}>
                      {p.label}{p.auto ? "" : " (no auto-post yet)"}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>Action</InputLabel>
                <Select value={ndAction} label="Action" onChange={(e) => setNdAction(e.target.value)}>
                  <MenuItem value="comment">Comment / reply</MenuItem>
                  <MenuItem value="share">Share / new post</MenuItem>
                </Select>
              </FormControl>
              <Stack direction="row" spacing={0.5} alignItems="flex-start">
                <TextField
                  size="small"
                  label="Target URL"
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
                <Tooltip title="Have the agent fetch OP + top comments for this URL">
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
                label="Thread ID (optional)"
                placeholder="auto-filled by Scout for Reddit URLs"
                value={ndThreadId}
                onChange={(e) => setNdThreadId(e.target.value)}
                fullWidth
              />
              {!PLATFORM_OPTIONS.find(p => p.value === ndPlatform)?.auto && (
                <Alert severity="info" sx={{ py: 0.5, fontSize: "0.7rem" }}>
                  No auto-post backend for {ndPlatform} yet — approving will queue the row but not ship it.
                </Alert>
              )}
              <Divider sx={{ my: 0.5 }} />
              <Typography variant="caption" color="text.secondary">
                Tone for LLM seeding picks up the main page's tone selector ({tone}).
              </Typography>
              {ndGrade != null && (
                <Chip
                  size="small"
                  label={`LLM grade: ${ndGrade.toFixed(2)}`}
                  color={ndGrade >= 0.7 ? "success" : "warning"}
                  sx={{ alignSelf: "flex-start" }}
                />
              )}
              {ndSeededId && (
                <Typography variant="caption" color="text.secondary">
                  Seeded queue row #{ndSeededId} — Save will update it in place.
                </Typography>
              )}
            </Stack>

            {/* Body column */}
            <Stack spacing={1.5}>
              <TextField
                label="Topic / thread context"
                placeholder="Paste the OP + a few top comments, or describe what the thread is about. The LLM uses this to draft a relevant reply."
                value={ndContext}
                onChange={(e) => setNdContext(e.target.value)}
                multiline
                minRows={3}
                maxRows={6}
                fullWidth
              />
              <Stack direction="row" spacing={1}>
                <Tooltip title="Have the LLM draft the body from the topic + tone above">
                  <span>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={ndBusy ? <CircularProgress size={14} /> : <AutoAwesomeIcon />}
                      disabled={ndBusy || !ndContext.trim()}
                      onClick={handleSeedNewDraftLLM}
                    >
                      Draft with LLM
                    </Button>
                  </span>
                </Tooltip>
                <Box sx={{ flex: 1 }} />
                <Typography variant="caption" color="text.secondary" sx={{ alignSelf: "center" }}>
                  {ndBody.length} chars
                </Typography>
              </Stack>
              <TextField
                label="Draft body"
                placeholder="Type the post here, or click 'Draft with LLM' to seed it from the topic above."
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
          <Button onClick={() => setNewDraftOpen(false)} disabled={ndBusy}>
            Cancel
          </Button>
          <Button
            variant="contained"
            startIcon={<SaveIcon />}
            onClick={handleSaveNewDraft}
            disabled={ndBusy || !ndBody.trim()}
          >
            {ndSeededId ? "Save (update seeded row)" : "Save draft"}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default OutreachPage;
