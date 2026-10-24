// frontend/src/pages/ApprovalsPage.jsx
//
// Everything waiting on a human. Publishes: every publish is held here until it
// is approved, except one a person starts from the app to a Discord webhook set
// to post without approval; anything an agent queues (chat, MCP, a schedule)
// waits whatever that switch says. A waiting publish can be edited here first;
// Approve sends the revision on screen. Outreach: drafts to approve, then approved
// Reddit/YouTube/Facebook drafts to start with "Post now, I'm watching".
// Code changes: what the inbound guard holds before it lands in this checkout.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  FormControlLabel,
  IconButton,
  Link,
  List,
  ListItemButton,
  ListItemText,
  Paper,
  Stack,
  Switch,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import {
  Cancel as CancelIcon,
  CheckCircle as CheckCircleIcon,
  DoNotDisturb as DoNotDisturbIcon,
  Edit as EditIcon,
  Refresh as RefreshIcon,
} from "@mui/icons-material";
import PageLayout from "../components/layout/PageLayout";
import RejectPublishDialog from "../components/connections/RejectPublishDialog";
import EditPublishDialog from "../components/connections/EditPublishDialog";
import { draftedBy, editedFieldWords } from "../components/connections/publishEdit";
import HeldChangesPanel from "../components/approvals/HeldChangesPanel";
import OutreachDraftsPanel from "../components/approvals/OutreachDraftsPanel";
import AlwaysApprovedToolsPanel from "../components/approvals/AlwaysApprovedToolsPanel";
import { useSnackbar } from "../components/common/SnackbarProvider";
import {
  approvePublish,
  cancelPublish,
  fetchConnections,
  fetchPublishes,
  rejectPublish,
} from "../api/connectionsService";
import {
  desktopNotificationsAvailable,
  desktopNotificationsGranted,
  requestDesktopNotifications,
  usePendingApprovals,
} from "../hooks/usePendingApprovals";
import { translate, uiLocale, useUiTranslation } from "../i18n";

// status → chip colour, so a publish reads the same wherever it appears.
const STATUS_COLOR = {
  awaiting_approval: "warning",
  scheduled: "info",
  queued: "info",
  processing: "info",
  posted: "success",
  rejected: "error",
  cancelled: "default",
  failed: "error",
  deleted: "default",
};

const HISTORY_STATUSES = ["scheduled", "posted", "rejected", "cancelled", "failed", "deleted"];

const formatAt = (iso) => (iso ? new Date(iso).toLocaleString(uiLocale()) : "");

// A publish's status as shown; the value from the API stays the one compared.
const publishStatusLabel = (status) =>
  ({
    awaiting_approval: translate("approvals.publishes.status.awaiting_approval"),
    scheduled: translate("approvals.publishes.status.scheduled"),
    queued: translate("approvals.publishes.status.queued"),
    processing: translate("approvals.publishes.status.processing"),
    posted: translate("approvals.publishes.status.posted"),
    rejected: translate("approvals.publishes.status.rejected"),
    cancelled: translate("approvals.publishes.status.cancelled"),
    failed: translate("approvals.publishes.status.failed"),
    deleted: translate("approvals.publishes.status.deleted"),
  })[status] ?? status;

// What a publish action reports when it lands, and when it fails without a message.
const publishDone = (verb) =>
  ({
    approved: translate("approvals.publishes.approvedDone"),
    cancelled: translate("approvals.publishes.cancelledDone"),
    rejected: translate("approvals.publishes.rejectedDone"),
  })[verb];

const publishFailed = (verb) =>
  ({
    approved: translate("approvals.publishes.approveFailed"),
    cancelled: translate("approvals.publishes.cancelFailed"),
    rejected: translate("approvals.publishes.rejectFailed"),
  })[verb];

// Sources the backend always holds for approval, whatever a connection says.
const AGENT_SOURCES = new Set(["chat", "mcp", "schedule"]);

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

// "Edited 5m ago by bot: text", with the editor and the changed fields only when known.
const editedLine = (record) => {
  const when = formatWhen(record.edited_at);
  const editor = record.edited_by && record.edited_by !== "ui" ? record.edited_by : "";
  const fields = editedFieldWords(record);
  if (editor && fields) return translate("approvals.publishes.editedByWithFields", { when, editor, fields });
  if (editor) return translate("approvals.publishes.editedBy", { when, editor });
  if (fields) return translate("approvals.publishes.editedWithFields", { when, fields });
  return translate("approvals.publishes.edited", { when });
};

const ApprovalsPage = () => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const [tab, setTab] = useState("pending");
  const [connections, setConnections] = useState([]);
  const [queued, setQueued] = useState([]);
  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [rejecting, setRejecting] = useState(null);
  const [editing, setEditing] = useState(null);
  const [notifyEnabled, setNotifyEnabled] = useState(desktopNotificationsGranted);

  const {
    pending, held, outreach, outreachWaiting, loading, heldLoading, outreachLoading, error, refresh,
  } = usePendingApprovals({
    notify: notifyEnabled,
  });

  // to_dict() carries connection_id but no label, so the names come from the
  // connections list and fall back to the platform slug.
  const connectionName = useCallback(
    (record) => {
      const match = connections.find((c) => c.id === record.connection_id);
      return match?.display_name || match?.handle || record.platform;
    },
    [connections],
  );

  const loadSupporting = useCallback(async () => {
    try {
      const [conns, queuedRows] = await Promise.all([
        fetchConnections(),
        fetchPublishes({ status: "queued,scheduled", limit: 50 }),
      ]);
      setConnections(conns);
      setQueued(queuedRows);
    } catch {
      // Non-fatal: the queue still renders with platform slugs instead of names.
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const rows = await Promise.all(
        HISTORY_STATUSES.map((status) => fetchPublishes({ status, limit: 50 })),
      );
      setHistory(
        rows
          .flat()
          .sort((a, b) => (b.created_at || "").localeCompare(a.created_at || "")),
      );
    } catch (err) {
      showMessage(err?.message || t("approvals.publishes.historyFailed"), "error");
    } finally {
      setHistoryLoading(false);
    }
  }, [showMessage]);

  useEffect(() => {
    loadSupporting();
  }, [loadSupporting]);

  useEffect(() => {
    if (tab === "history") loadHistory();
  }, [tab, loadHistory]);

  const rows = tab === "pending" ? pending : history;
  const selected = useMemo(
    () => rows.find((r) => r.id === selectedId) || null,
    [rows, selectedId],
  );

  const runAction = async (record, action, verb) => {
    setBusyId(record.id);
    try {
      await action();
      showMessage(publishDone(verb));
      await Promise.all([refresh(), loadSupporting()]);
      if (tab === "history") await loadHistory();
      setSelectedId(null);
    } catch (err) {
      // Edited or decided elsewhere since this page loaded it: show what it is now.
      if (err?.status === 409 && err?.data?.code) {
        showMessage(err.message, "warning");
        await Promise.all([refresh(), loadSupporting()]);
      } else {
        showMessage(err?.message || publishFailed(verb), "error");
      }
    } finally {
      setBusyId(null);
      setRejecting(null);
    }
  };

  const handleToggleNotify = async (checked) => {
    if (!checked) {
      setNotifyEnabled(false);
      return;
    }
    const granted = await requestDesktopNotifications();
    setNotifyEnabled(granted);
    if (!granted) {
      showMessage(
        t("approvals.notifyBlocked"),
        "warning",
      );
    }
  };

  const renderList = () => {
    if (tab === "pending" && loading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (tab === "history" && historyLoading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (!rows.length) {
      return (
        <Alert severity="info" sx={{ m: 2 }}>
          {tab === "pending"
            ? t("approvals.publishes.emptyPending")
            : t("approvals.publishes.emptyHistory")}
        </Alert>
      );
    }
    return (
      <List dense disablePadding sx={{ maxHeight: 520, overflowY: "auto" }}>
        {rows.map((record) => (
          <ListItemButton
            key={record.id}
            selected={selectedId === record.id}
            onClick={() => setSelectedId(record.id)}
          >
            <ListItemText
              primary={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" noWrap sx={{ fontWeight: 500 }}>
                    {connectionName(record)}
                  </Typography>
                  {AGENT_SOURCES.has(record.requested_by) && (
                    <Chip label={record.requested_by} size="small" variant="outlined" />
                  )}
                  {record.edited_at && <Chip label={t("approvals.publishes.editedChip")} size="small" variant="outlined" />}
                </Stack>
              }
              secondary={
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{
                    display: "-webkit-box",
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: "vertical",
                    overflow: "hidden",
                  }}
                >
                  {record.title || record.body || t("approvals.noText")} · {formatWhen(record.created_at)}
                </Typography>
              }
            />
            {tab === "history" && (
              <Chip
                label={publishStatusLabel(record.status)}
                size="small"
                color={STATUS_COLOR[record.status] || "default"}
              />
            )}
          </ListItemButton>
        ))}
      </List>
    );
  };

  const renderDetail = () => {
    if (!selected) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3 }}>
          {t("approvals.publishes.selectPrompt")}
        </Typography>
      );
    }
    const isPending = selected.status === "awaiting_approval";
    const busy = busyId === selected.id;
    const drafted = draftedBy(selected);
    return (
      <Box sx={{ p: 3 }}>
        {drafted && isPending && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {t("approvals.publishes.draftedReview", { drafted })}
          </Alert>
        )}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="h6">{connectionName(selected)}</Typography>
          <Chip
            label={publishStatusLabel(selected.status)}
            size="small"
            color={STATUS_COLOR[selected.status] || "default"}
          />
          <Chip label={selected.visibility} size="small" variant="outlined" />
          {selected.scheduled_at && (
            <Chip label={t("approvals.publishes.scheduledAt", { when: formatAt(selected.scheduled_at) })} size="small" variant="outlined" />
          )}
          {selected.ai_disclosure && <Chip label={t("approvals.publishes.aiLabel")} size="small" variant="outlined" />}
          {Object.entries(selected.post_options || {}).map(([name, value]) => (
            <Chip key={name} label={`${name}: ${value}`} size="small" variant="outlined" />
          ))}
        </Stack>

        {selected.title && (
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            {selected.title}
          </Typography>
        )}
        {selected.body && (
          <Paper variant="outlined" sx={{ p: 2, mb: 2, whiteSpace: "pre-wrap" }}>
            <Typography variant="body2">{selected.body}</Typography>
          </Paper>
        )}
        {selected.link_url && (
          <Typography variant="body2" sx={{ mb: 2 }}>
            <Link href={selected.link_url} target="_blank" rel="noopener noreferrer">
              {selected.link_url}
            </Link>
          </Typography>
        )}

        <Stack direction="row" spacing={2} sx={{ mb: 2 }} flexWrap="wrap">
          <Typography variant="caption" color="text.secondary">
            {t("approvals.publishes.requestedBy", {
              source: selected.requested_by,
              when: formatWhen(selected.created_at),
            })}
          </Typography>
          {selected.edited_at && (
            <Typography variant="caption" color="text.secondary">
              {editedLine(selected)}
            </Typography>
          )}
          {!!selected.media_refs?.length && (
            <Typography variant="caption" color="text.secondary">
              {t("approvals.publishes.mediaItems", { count: selected.media_refs.length })}
            </Typography>
          )}
        </Stack>

        {selected.error_message && (
          <Alert
            severity={selected.status === "rejected" ? "info" : "error"}
            sx={{ mb: 2 }}
          >
            {selected.status === "rejected"
              ? t("approvals.publishes.reason", { reason: selected.error_message })
              : selected.error_message}
          </Alert>
        )}
        {selected.remote_url && (
          <Alert severity="success" sx={{ mb: 2 }}>
            <Link href={selected.remote_url} target="_blank" rel="noopener noreferrer">
              {t("approvals.publishes.viewPost")}
            </Link>
          </Alert>
        )}

        {isPending && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
              {selected.scheduled_at && new Date(selected.scheduled_at).getTime() > Date.now()
                ? t("approvals.publishes.approveSchedules", {
                    when: formatAt(selected.scheduled_at),
                    account: connectionName(selected),
                  })
                : t("approvals.publishes.approveSends", { account: connectionName(selected) })}
            </Typography>
            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                startIcon={<CheckCircleIcon />}
                disabled={busy}
                onClick={() =>
                  runAction(
                    selected,
                    () => approvePublish(selected.id, selected.revision ?? 0),
                    "approved",
                  )
                }
              >
                {busy ? t("approvals.working") : t("approvals.publishes.approve")}
              </Button>
              <Button startIcon={<EditIcon />} disabled={busy} onClick={() => setEditing(selected)}>
                {t("approvals.publishes.edit")}
              </Button>
              <Button
                color="error"
                startIcon={<CancelIcon />}
                disabled={busy}
                onClick={() => setRejecting(selected)}
              >
                {t("approvals.publishes.reject")}
              </Button>
              <Button
                startIcon={<DoNotDisturbIcon />}
                disabled={busy}
                onClick={() =>
                  runAction(selected, () => cancelPublish(selected.id), "cancelled")
                }
              >
                {t("approvals.publishes.cancel")}
              </Button>
            </Stack>
          </>
        )}
      </Box>
    );
  };

  return (
    <PageLayout
      title={t("approvals.title")}
      subtitle={t("approvals.subtitle")}
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          {desktopNotificationsAvailable() && (
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={notifyEnabled}
                  onChange={(e) => handleToggleNotify(e.target.checked)}
                />
              }
              label={<Typography variant="body2">{t("approvals.notifyMe")}</Typography>}
            />
          )}
          <Tooltip title={t("common.refresh")}>
            <IconButton onClick={() => { refresh(); loadSupporting(); }}>
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      }
    >
      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {!!queued.length && (
        <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            {t("approvals.publishes.goingOutNext", { count: queued.length })}
          </Typography>
          <List dense disablePadding sx={{ maxHeight: 140, overflowY: "auto" }}>
            {queued.map((record) => (
              <ListItemButton key={record.id} disabled>
                <ListItemText
                  primary={
                    record.status === "scheduled"
                      ? `${connectionName(record)} · ${formatAt(record.scheduled_at)}`
                      : connectionName(record)
                  }
                  secondary={record.title || record.body || t("approvals.noText")}
                  primaryTypographyProps={{ variant: "body2" }}
                  secondaryTypographyProps={{ variant: "caption", noWrap: true }}
                />
              </ListItemButton>
            ))}
          </List>
        </Paper>
      )}

      <Paper variant="outlined">
        <Tabs
          value={tab}
          onChange={(_, value) => {
            setTab(value);
            setSelectedId(null);
          }}
          sx={{ borderBottom: 1, borderColor: "divider" }}
        >
          <Tab
            value="pending"
            label={pending.length ? t("approvals.tabs.publishesCount", { count: pending.length }) : t("approvals.tabs.publishes")}
          />
          <Tab
            value="outreach"
            label={
              outreach.length + outreachWaiting.length
                ? t("approvals.tabs.outreachCount", { count: outreach.length + outreachWaiting.length })
                : t("approvals.tabs.outreach")
            }
          />
          <Tab
            value="code"
            label={held.length ? t("approvals.tabs.codeCount", { count: held.length }) : t("approvals.tabs.code")}
          />
          <Tab value="chat_tools" label={t("approvals.tabs.chatTools")} />
          <Tab value="history" label={t("approvals.tabs.history")} />
        </Tabs>
        {tab === "chat_tools" ? (
          <AlwaysApprovedToolsPanel showMessage={showMessage} />
        ) : tab === "code" ? (
          <HeldChangesPanel
            held={held}
            loading={heldLoading}
            onChanged={refresh}
            showMessage={showMessage}
          />
        ) : tab === "outreach" ? (
          <OutreachDraftsPanel
            drafts={outreach}
            waiting={outreachWaiting}
            loading={outreachLoading}
            onChanged={refresh}
            showMessage={showMessage}
          />
        ) : (
          <Box sx={{ display: "flex", minHeight: 360 }}>
            <Box sx={{ width: 340, borderRight: 1, borderColor: "divider" }}>
              {renderList()}
            </Box>
            <Box sx={{ flex: 1, overflowY: "auto" }}>{renderDetail()}</Box>
          </Box>
        )}
      </Paper>

      <EditPublishDialog
        open={!!editing}
        record={editing}
        connection={editing ? connections.find((c) => c.id === editing.connection_id) || null : null}
        accountLabel={editing ? connectionName(editing) : ""}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          showMessage(t("approvals.publishes.savedWaiting"));
          await Promise.all([refresh(), loadSupporting()]);
        }}
      />

      <RejectPublishDialog
        open={!!rejecting}
        record={rejecting}
        busy={busyId === rejecting?.id}
        onCancel={() => setRejecting(null)}
        onConfirm={(reason) =>
          runAction(rejecting, () => rejectPublish(rejecting.id, reason), "rejected")
        }
      />
    </PageLayout>
  );
};

export default ApprovalsPage;
