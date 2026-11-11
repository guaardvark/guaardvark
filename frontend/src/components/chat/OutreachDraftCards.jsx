// Outreach drafts shown in the chat that asked for them ("/outreach reply to
// some posts on Reddit"). While the scout runs this shows what it is doing;
// when it ends, what it found and one card per draft. A card is the same
// approval the Approvals page gives: read or edit the text, Approve, Reject,
// and Post now for an approved draft. Nothing posts without those clicks.
import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Link,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  Visibility as VisibilityIcon,
} from "@mui/icons-material";
import { Link as RouterLink } from "react-router-dom";
import {
  approveDraft,
  fetchDraftsByIds,
  fetchRunResult,
  fetchWatchedPost,
  postNow,
  rejectDraft,
} from "../../api/outreachService";
import { useAppStore } from "../../stores/useAppStore";
import { useUiTranslation } from "../../i18n";

// A scout reads a few pages and drafts with the local model; this is how
// often the chat asks whether it has ended.
const RUN_POLL_MS = 3000;
// While a draft is approved or posting, its card follows the post.
const POST_POLL_MS = 3000;
const IN_FLIGHT = ["approved", "processing", "submitting"];

const DraftCard = ({ draft, watched, onChanged }) => {
  const { t } = useUiTranslation();
  const [text, setText] = useState(draft.text || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const setAgentScreenOpen = useAppStore((s) => s.setAgentScreenOpen);

  // Another draft's text replaces this one only when the row itself changes.
  useEffect(() => {
    setText(draft.text || "");
  }, [draft.id, draft.text]);

  const run = async (action) => {
    setBusy(true);
    setError(null);
    try {
      const out = await action();
      if (out?.message) setNotice(out.message);
      await onChanged();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const status = draft.status;
  const byApi = (watched?.api_platforms || []).includes((draft.platform || "").toLowerCase());
  const watching = Boolean(watched?.watching) || byApi;
  const running = watched?.running;
  const runningThis = running?.event_id === draft.id;
  const last = watched?.last?.event_id === draft.id ? watched.last : null;
  const statusColor = { posted: "success", approved: "info", rejected: "default", aborted: "warning" }[status] || "default";

  return (
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
        <Chip label={`#${draft.id}`} size="small" />
        <Chip label={draft.platform} size="small" variant="outlined" />
        <Chip label={t(`approvals.outreach.status.${status}`, { defaultValue: status })} size="small" color={statusColor} />
        {draft.grade_score != null && (
          <Chip
            label={t("approvals.outreach.grade", { score: Number(draft.grade_score).toFixed(2) })}
            size="small"
            variant="outlined"
          />
        )}
        {draft.target_url && (
          <Link href={draft.target_url} target="_blank" rel="noopener noreferrer" variant="caption" sx={{ wordBreak: "break-all" }}>
            {draft.target_url}
          </Link>
        )}
      </Stack>

      {draft.replying_to?.text && (
        <Box sx={{ borderLeft: 3, borderColor: "divider", pl: 1, mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            {draft.replying_to.author
              ? t("approvals.outreach.chat.replyingToAuthor", { author: draft.replying_to.author })
              : t("approvals.outreach.chat.replyingTo")}
          </Typography>
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
            {draft.replying_to.text}
          </Typography>
        </Box>
      )}

      {status === "drafted" ? (
        <TextField
          label={t("approvals.outreach.textLabel")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          multiline
          minRows={3}
          fullWidth
          size="small"
        />
      ) : (
        <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
          {(status === "posted" && draft.posted_text) || draft.text || t("approvals.noText")}
        </Typography>
      )}

      {status === "approved" && !watching && (
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
      {notice && !last && status !== "rejected" && (
        <Alert severity="info" sx={{ mt: 1 }} onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      {status === "aborted" && draft.abort_reason && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          {draft.abort_reason}
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {(status === "drafted" || IN_FLIGHT.includes(status)) && (
        <Stack direction="row" spacing={1} sx={{ mt: 1 }} alignItems="center">
          {status === "drafted" && (
            <Button
              variant="contained"
              size="small"
              startIcon={<CheckCircleIcon />}
              disabled={busy || !text.trim()}
              onClick={() =>
                run(async () => {
                  const row = await approveDraft(draft.id, text !== (draft.text || "") ? text : null);
                  return { message: t("approvals.outreach.approvedNext", { next: row?.next || "" }).trim() };
                })
              }
            >
              {busy ? t("approvals.working") : t("approvals.outreach.approve")}
            </Button>
          )}
          {status === "approved" && (
            <Button
              variant="contained"
              size="small"
              startIcon={byApi ? undefined : <VisibilityIcon />}
              disabled={busy || !watching || Boolean(running)}
              onClick={() => run(() => postNow(draft.id))}
            >
              {busy
                ? t("approvals.outreach.starting")
                : t(byApi ? "approvals.outreach.postNowApi" : "approvals.outreach.postNow")}
            </Button>
          )}
          {(status === "drafted" || status === "approved") && (
            <Button
              color="error"
              size="small"
              startIcon={<CancelIcon />}
              disabled={busy || runningThis}
              onClick={() => run(() => rejectDraft(draft.id).then(() => null))}
            >
              {t("approvals.outreach.reject")}
            </Button>
          )}
          {running && !runningThis && status === "approved" && (
            <Typography variant="caption" color="text.secondary">
              {t("approvals.outreach.otherPosting", { id: running.event_id })}
            </Typography>
          )}
        </Stack>
      )}
    </Paper>
  );
};

/**
 * `run` is what the /outreach command kept from the intent answer:
 * `{ follow: { task_ids, after_id, platform } }` for a queued scout, or
 * `{ draftIds: [...] }` for drafts that already exist.
 */
const OutreachDraftCards = ({ run }) => {
  const { t } = useUiTranslation();
  const follow = run?.follow;
  const [result, setResult] = useState(null);
  const [drafts, setDrafts] = useState([]);
  const [watched, setWatched] = useState(null);
  const [error, setError] = useState(null);
  const idsRef = useRef(run?.draftIds || []);

  const refreshDrafts = useCallback(async () => {
    if (!idsRef.current.length) return;
    try {
      setDrafts(await fetchDraftsByIds(idsRef.current));
    } catch (err) {
      setError(err.message);
    }
    try {
      setWatched(await fetchWatchedPost());
    } catch {
      // Post now stays off until this answers; the drafts still show.
    }
  }, []);

  // A queued scout: ask until its tasks end, then keep the drafts it wrote.
  useEffect(() => {
    if (!follow?.task_ids?.length) {
      refreshDrafts();
      return undefined;
    }
    let stopped = false;
    let timer = null;
    const ask = async () => {
      try {
        const out = await fetchRunResult(follow);
        if (stopped) return;
        setResult(out);
        setError(null);
        if (out.done) {
          idsRef.current = (out.drafts || []).map((d) => d.id);
          setDrafts(out.drafts || []);
          refreshDrafts();
          return;
        }
      } catch (err) {
        if (stopped) return;
        setError(err.message);
      }
      timer = setTimeout(ask, RUN_POLL_MS);
    };
    ask();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [follow, refreshDrafts]);

  // An approved or posting draft changes without a click here.
  const inFlight = drafts.some((d) => IN_FLIGHT.includes(d.status));
  useEffect(() => {
    if (!inFlight) return undefined;
    const timer = setInterval(refreshDrafts, POST_POLL_MS);
    return () => clearInterval(timer);
  }, [inFlight, refreshDrafts]);

  const waitingOnRun = Boolean(follow?.task_ids?.length) && !result?.done;

  return (
    <Stack spacing={1} sx={{ mt: 1 }}>
      {waitingOnRun && (
        <Stack direction="row" spacing={1} alignItems="center">
          <CircularProgress size={16} />
          <Typography variant="body2" color="text.secondary">
            {t("approvals.outreach.chat.running", { tasks: follow.task_ids.map((id) => `#${id}`).join(", ") })}
          </Typography>
        </Stack>
      )}
      {error && <Alert severity="error">{t("approvals.outreach.chat.readFailed", { reason: error })}</Alert>}
      {result?.done &&
        (result.notes || []).map((note) => (
          <Typography key={note} variant="body2" color="text.secondary">
            {note}
          </Typography>
        ))}
      {result?.done && drafts.length === 0 && (
        <Typography variant="body2">{t("approvals.outreach.chat.noDrafts")}</Typography>
      )}
      {drafts.map((draft) => (
        <DraftCard key={draft.id} draft={draft} watched={watched} onChanged={refreshDrafts} />
      ))}
      {result?.done && result.older_waiting > 0 && (
        <Typography variant="caption" color="text.secondary">
          {t("approvals.outreach.chat.olderWaiting", { count: result.older_waiting })}{" "}
          <Link component={RouterLink} to="/approvals">
            {t("approvals.outreach.chat.approvalsPage")}
          </Link>
        </Typography>
      )}
    </Stack>
  );
};

export default OutreachDraftCards;
