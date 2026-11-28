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
import OutreachDraftImage from "../outreach/OutreachDraftImage";
import { useAppStore } from "../../stores/useAppStore";
import { useUiTranslation } from "../../i18n";
import { draftWords, shareDraftText, shareParts } from "../../utils/outreachDraftText";

// A scout reads a few pages and drafts with the local model; this is how
// often the chat asks whether it has ended.
const RUN_POLL_MS = 3000;
// While a draft is approved or posting, its card follows the post.
const POST_POLL_MS = 3000;
const IN_FLIGHT = ["approved", "processing", "submitting"];

const DraftCard = ({ draft, watched, onChanged, review }) => {
  const { t } = useUiTranslation();
  // A share is edited as its title and its text, and stored back as one document.
  const share = shareParts(draft);
  const words = share ? share.body : draft.text || "";
  const [text, setText] = useState(words);
  const [title, setTitle] = useState(share ? share.title : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const setAgentScreenOpen = useAppStore((s) => s.setAgentScreenOpen);

  // Another draft's text replaces this one only when the row itself changes.
  useEffect(() => {
    setText(words);
    setTitle(share ? share.title : "");
  }, [draft.id, draft.text]);

  const editedDraftText = () => {
    if (share) {
      return title !== share.title || text !== share.body ? shareDraftText(share, { title, body: text }) : null;
    }
    return text !== words ? text : null;
  };

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
  // What the draft answers: the post or comment, when the row kept it.
  const source = draft.source?.text || draft.source?.title ? draft.source : null;
  const byApi = (watched?.api_platforms || []).includes((draft.platform || "").toLowerCase());
  const watching = Boolean(watched?.watching) || byApi;
  const running = watched?.running;
  const runningThis = running?.event_id === draft.id;
  const last = watched?.last?.event_id === draft.id ? watched.last : null;
  const statusColor = { posted: "success", approved: "info", rejected: "default", aborted: "warning" }[status] || "default";

  return (
    <Paper variant="outlined" sx={{ p: 1.5 }}>
      <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: "center", flexWrap: "wrap", mb: 1 }}>
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

      {source && (
        <Box sx={{ borderLeft: 3, borderColor: "divider", pl: 1, mb: 1 }}>
          <Typography variant="caption" color="text.secondary">
            {source.author
              ? t("approvals.outreach.chat.answeringAuthor", { author: source.author })
              : t("approvals.outreach.chat.answering")}
            {source.community ? ` ${t("approvals.outreach.chat.answeringIn", { community: source.community })}` : ""}
          </Typography>
          {source.title && (
            <Typography variant="subtitle2" sx={{ whiteSpace: "pre-wrap" }}>
              {source.title}
            </Typography>
          )}
          {source.text && (
            <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", maxHeight: 240, overflowY: "auto" }}>
              {source.text}
            </Typography>
          )}
          {source.why && (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
              {t("approvals.outreach.chat.pickedBecause", { why: source.why })}
            </Typography>
          )}
        </Box>
      )}

      {status === "drafted" ? (
        <>
          {share && (
            <TextField
              label={t("social.postFields.title")}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              fullWidth
              size="small"
              sx={{ mb: 1 }}
            />
          )}
          <TextField
            label={share ? t("social.postFields.text") : t("approvals.outreach.textLabel")}
            value={text}
            onChange={(e) => setText(e.target.value)}
            multiline
            minRows={3}
            fullWidth
            size="small"
          />
        </>
      ) : (
        <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
          {(status === "posted" && draft.posted_text) || draftWords(draft) || t("approvals.noText")}
        </Typography>
      )}
      {share?.link && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5, wordBreak: "break-all" }}>
          {t("social.postFields.link")}: {share.link}
        </Typography>
      )}
      <OutreachDraftImage draft={draft} sx={{ mt: 1 }} />

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
        <Stack direction="row" spacing={1} sx={{ alignItems: "center", mt: 1 }}>
          {status === "drafted" && (
            <Button
              variant="contained"
              size="small"
              startIcon={<CheckCircleIcon />}
              disabled={busy || !(share ? title : text).trim()}
              onClick={() =>
                run(async () => {
                  const row = await approveDraft(draft.id, editedDraftText());
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
      {review && (status === "drafted" || status === "approved") && !runningThis && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>
          {t("approvals.outreach.chat.reviewHint")}
        </Typography>
      )}
    </Paper>
  );
};

/**
 * `run` is what the /outreach command kept from the intent answer:
 * `{ follow: { task_ids, after_id, platform } }` for a queued scout, or
 * `{ draftIds: [...] }` for drafts that already exist. With `review: true`
 * the drafts are answered in words, one at a time (outreachReview.js): a
 * followed scout then shows only its progress and what it found, and each
 * draft arrives as its own message.
 */
const OutreachDraftCards = ({ run }) => {
  const { t } = useUiTranslation();
  const follow = run?.follow;
  const review = Boolean(run?.review);
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
          if (review) return;
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
  }, [follow, review, refreshDrafts]);

  // An approved or posting draft changes without a click here; in a review
  // a draft also changes by what is said in the chat.
  const inFlight = drafts.some((d) => IN_FLIGHT.includes(d.status) || (review && d.status === "drafted"));
  useEffect(() => {
    if (!inFlight) return undefined;
    const timer = setInterval(refreshDrafts, POST_POLL_MS);
    return () => clearInterval(timer);
  }, [inFlight, refreshDrafts]);

  const waitingOnRun = Boolean(follow?.task_ids?.length) && !result?.done;

  return (
    <Stack spacing={1} sx={{ mt: 1 }}>
      {waitingOnRun && (
        <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
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
      {result?.done && drafts.length === 0 && !(review && (result.drafts || []).length) && (
        <Typography variant="body2">{t("approvals.outreach.chat.noDrafts")}</Typography>
      )}
      {drafts.map((draft) => (
        <DraftCard key={draft.id} draft={draft} watched={watched} onChanged={refreshDrafts} review={review} />
      ))}
      {result?.done && !review && result.older_waiting > 0 && (
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
