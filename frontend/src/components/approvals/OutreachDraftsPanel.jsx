// Outreach drafts waiting for a person. Every draft waits here until someone
// approves it; approving posts nothing. An approved Reddit, YouTube or Facebook
// draft then waits for "Post now, I'm watching": that click starts that one
// post, which the agent drives in its browser on the Agent Screen while the
// person watches. Approved Discord drafts are sent by the Discord bot.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  List,
  ListItemButton,
  ListItemText,
  ListSubheader,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  DesktopWindows as DesktopWindowsIcon,
  Visibility as VisibilityIcon,
} from "@mui/icons-material";
import { Link as RouterLink } from "react-router-dom";
import { approveDraft, fetchWatchedPost, postNow, rejectDraft } from "../../api/outreachService";
import { useAppStore } from "../../stores/useAppStore";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

// While this panel is open it follows the Agent Screen and a running post
// closely enough for the button to follow the viewer being opened or closed.
const WATCH_POLL_MS = 3000;

const formatWhen = (iso) => {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return translate("approvals.when.justNow");
  if (mins < 60) return translate("approvals.when.minutesAgo", { minutes: mins });
  const hours = Math.round(mins / 60);
  if (hours < 24) return translate("approvals.when.hoursAgo", { hours });
  return new Date(iso).toLocaleDateString(uiLocale());
};

// A draft's status as shown; the value from the API stays the one compared.
const draftStatusLabel = (status) =>
  ({
    candidate: translate("approvals.outreach.status.candidate"),
    drafted: translate("approvals.outreach.status.drafted"),
    approved: translate("approvals.outreach.status.approved"),
    rejected: translate("approvals.outreach.status.rejected"),
    processing: translate("approvals.outreach.status.processing"),
    submitting: translate("approvals.outreach.status.submitting"),
    posted: translate("approvals.outreach.status.posted"),
    aborted: translate("approvals.outreach.status.aborted"),
    unsupported: translate("approvals.outreach.status.unsupported"),
  })[status] ?? status;

const OutreachDraftsPanel = ({ drafts, waiting: waitingFromHook, loading, onChanged, showMessage }) => {
  const { t } = useUiTranslation();
  const [selectedId, setSelectedId] = useState(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [watched, setWatched] = useState(null);
  const setAgentScreenOpen = useAppStore((s) => s.setAgentScreenOpen);

  const refreshWatched = useCallback(async () => {
    try {
      setWatched(await fetchWatchedPost());
    } catch {
      // The panel still lists drafts; Post now stays off until this answers.
    }
  }, []);

  useEffect(() => {
    refreshWatched();
    const timer = setInterval(refreshWatched, WATCH_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshWatched]);

  const waiting = watched?.waiting || waitingFromHook || [];
  const selected =
    (drafts || []).find((d) => d.id === selectedId) ||
    waiting.find((d) => d.id === selectedId) ||
    null;
  const isWaiting = selected?.status === "approved";

  // Reset the editable text only when another draft is picked, not on every poll.
  useEffect(() => {
    setText(selected?.draft_text || "");
    setError(null);
  }, [selectedId]);

  const act = async (approve) => {
    setBusy(true);
    setError(null);
    try {
      if (approve) {
        const row = await approveDraft(selected.id, text !== selected.draft_text ? text : null);
        showMessage?.(t("approvals.outreach.approvedNext", { next: row?.next || "" }).trim());
      } else {
        await rejectDraft(selected.id);
        showMessage?.(t("approvals.outreach.rejected"));
      }
      setSelectedId(null);
      await Promise.all([onChanged?.(), refreshWatched()]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const startPost = async () => {
    setBusy(true);
    setError(null);
    try {
      const out = await postNow(selected.id);
      showMessage?.(out?.message || t("approvals.outreach.postingStarted"));
      await refreshWatched();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const renderItem = (d) => (
    <ListItemButton key={d.id} selected={selectedId === d.id} onClick={() => setSelectedId(d.id)}>
      <ListItemText
        primary={
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip label={d.platform} size="small" variant="outlined" />
            <Typography variant="body2" noWrap sx={{ minWidth: 0 }}>
              {d.action}
            </Typography>
            {watched?.running?.event_id === d.id && <Chip label={t("approvals.outreach.postingChip")} size="small" color="info" />}
          </Stack>
        }
        secondary={
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
          >
            {d.draft_text || t("approvals.noText")} · {formatWhen(d.created_at)}
          </Typography>
        }
      />
    </ListItemButton>
  );

  const renderList = () => {
    if (loading && !waiting.length) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (!drafts?.length && !waiting.length) {
      return (
        <Alert severity="info" sx={{ m: 2 }}>
          {t("approvals.outreach.empty")}
        </Alert>
      );
    }
    return (
      <List dense disablePadding sx={{ maxHeight: 520, overflowY: "auto" }}>
        {waiting.length > 0 && (
          <>
            <ListSubheader disableSticky>{t("approvals.outreach.readyHeading", { count: waiting.length })}</ListSubheader>
            {waiting.map(renderItem)}
          </>
        )}
        {drafts?.length > 0 && (
          <>
            <ListSubheader disableSticky>{t("approvals.outreach.waitingHeading", { count: drafts.length })}</ListSubheader>
            {drafts.map(renderItem)}
          </>
        )}
      </List>
    );
  };

  const renderPostNow = () => {
    const running = watched?.running;
    const runningThis = running?.event_id === selected.id;
    const last = watched?.last?.event_id === selected.id ? watched.last : null;
    const watching = Boolean(watched?.watching);
    return (
      <>
        <Paper variant="outlined" sx={{ p: 2, mt: 2, whiteSpace: "pre-wrap" }}>
          <Typography variant="body2">{selected.draft_text || t("approvals.noText")}</Typography>
        </Paper>
        <Alert severity="info" icon={<DesktopWindowsIcon />} sx={{ mt: 2 }}>
          {t("approvals.outreach.postNowNote")}
        </Alert>
        {!watching && (
          <Alert
            severity="warning"
            sx={{ mt: 1 }}
            action={
              <Button color="inherit" size="small" onClick={() => setAgentScreenOpen(true)}>
                {t("approvals.outreach.openAgentScreen")}
              </Button>
            }
          >
            {watched?.watching_reason || t("approvals.outreach.screenClosed")}
          </Alert>
        )}
        {runningThis && (
          <Alert severity="info" sx={{ mt: 1 }}>
            {t("approvals.outreach.postingNow")}
          </Alert>
        )}
        {last && !runningThis && (
          <Alert severity={last.posted ? "success" : "warning"} sx={{ mt: 1 }}>
            {last.message}
          </Alert>
        )}
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: 2 }} alignItems="center">
          <Button
            variant="contained"
            startIcon={<VisibilityIcon />}
            disabled={busy || !watching || Boolean(running)}
            onClick={startPost}
          >
            {busy ? t("approvals.outreach.starting") : t("approvals.outreach.postNow")}
          </Button>
          <Button color="error" startIcon={<CancelIcon />} disabled={busy || runningThis} onClick={() => act(false)}>
            {t("approvals.outreach.reject")}
          </Button>
          <Box sx={{ flex: 1 }} />
          {running && !runningThis && (
            <Typography variant="caption" color="text.secondary">
              {t("approvals.outreach.otherPosting", { id: running.event_id })}
            </Typography>
          )}
        </Stack>
      </>
    );
  };

  const renderDetail = () => {
    if (!selected) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3 }}>
          {t("approvals.outreach.selectPrompt")}
        </Typography>
      );
    }
    return (
      <Box sx={{ p: 3 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }} flexWrap="wrap">
          <Typography variant="h6">{selected.platform}</Typography>
          <Chip label={selected.action} size="small" variant="outlined" />
          <Chip label={draftStatusLabel(selected.status)} size="small" color={isWaiting ? "success" : "default"} />
          {selected.grade_score != null && (
            <Chip label={t("approvals.outreach.grade", { score: Number(selected.grade_score).toFixed(2) })} size="small" variant="outlined" />
          )}
        </Stack>
        {selected.target_url && (
          <Typography variant="body2" sx={{ mb: 2, wordBreak: "break-all" }}>
            <Link href={selected.target_url} target="_blank" rel="noopener noreferrer">
              {selected.target_url}
            </Link>
          </Typography>
        )}
        {isWaiting ? (
          renderPostNow()
        ) : (
          <>
            <TextField
              label={t("approvals.outreach.textLabel")}
              value={text}
              onChange={(e) => setText(e.target.value)}
              multiline
              minRows={5}
              fullWidth
            />
            {error && (
              <Alert severity="error" sx={{ mt: 2 }}>
                {error}
              </Alert>
            )}
            <Stack direction="row" spacing={1} sx={{ mt: 2 }} alignItems="center">
              <Button variant="contained" startIcon={<CheckCircleIcon />} disabled={busy || !text.trim()} onClick={() => act(true)}>
                {busy ? t("approvals.working") : t("approvals.outreach.approve")}
              </Button>
              <Button color="error" startIcon={<CancelIcon />} disabled={busy} onClick={() => act(false)}>
                {t("approvals.outreach.reject")}
              </Button>
              <Box sx={{ flex: 1 }} />
              <Link component={RouterLink} to="/outreach" variant="body2">
                {t("approvals.outreach.outreachPage")}
              </Link>
            </Stack>
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
              {t("approvals.outreach.approveNote")}
            </Typography>
          </>
        )}
      </Box>
    );
  };

  return (
    <Box sx={{ display: "flex", minHeight: 360 }}>
      <Box sx={{ width: 340, borderRight: 1, borderColor: "divider" }}>{renderList()}</Box>
      <Box sx={{ flex: 1, overflowY: "auto" }}>{renderDetail()}</Box>
    </Box>
  );
};

export default OutreachDraftsPanel;
