// frontend/src/pages/SocialPostsPage.jsx
//
// Every publish to a social account, like WordPress Pages lists a site's
// pages: status, scheduled time, AI disclosure, the link on the platform, and
// Delete on platform where the provider supports it. A post waiting for
// approval can be edited here; approving stays on the Approvals page, which
// this page links to. New post opens the composer without an asset, for text
// and link posts.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  FormControl,
  IconButton,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useNavigate, useSearchParams } from "react-router-dom";

import PageLayout from "../components/layout/PageLayout";
import PublishModal from "../components/modals/PublishModal";
import EditPublishDialog from "../components/connections/EditPublishDialog";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { ActionButton, ConfirmActionDialog, StatusPill } from "../components/settings/ui";
import {
  cancelPublish,
  deletePublishRemote,
  fetchConnections,
  fetchProviders,
  fetchPublishes,
} from "../api/connectionsService";
import { STATUS_TONE, deleteOnPlatform, statusLabel } from "../components/social/platformRules";
import { translate, uiLocale, useUiTranslation } from "../i18n";

// Filter choices; a value may name several statuses, comma separated.
const statusFilters = () => [
  { value: "", label: translate("social.posts.filters.all") },
  { value: "awaiting_approval", label: translate("social.platformRules.status.awaitingApproval") },
  { value: "scheduled", label: translate("social.platformRules.status.scheduled") },
  { value: "queued,processing", label: translate("social.platformRules.status.processing") },
  { value: "posted", label: translate("social.platformRules.status.posted") },
  { value: "failed", label: translate("social.platformRules.status.failed") },
  { value: "deleted", label: translate("social.platformRules.status.deleted") },
  { value: "rejected,cancelled", label: translate("social.posts.filters.rejectedOrCancelled") },
];

const CANCELLABLE = new Set(["awaiting_approval", "queued", "scheduled"]);

const formatWhen = (iso) => (iso ? new Date(iso).toLocaleString(uiLocale()) : "—");

const SocialPostsPage = () => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const { showMessage } = useSnackbar();
  const [searchParams, setSearchParams] = useSearchParams();
  const connectionFilter = searchParams.get("connection_id") || "";
  const statusFilter = searchParams.get("status") || "";

  const [connections, setConnections] = useState([]);
  const [specs, setSpecs] = useState({});
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [composing, setComposing] = useState(false);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, providerSpecs, rows] = await Promise.all([
        fetchConnections("social"),
        fetchProviders("social"),
        fetchPublishes({ connection_id: connectionFilter, status: statusFilter, limit: 200 }),
      ]);
      setConnections(conns);
      setSpecs(Object.fromEntries(providerSpecs.map((s) => [s.provider, s])));
      setRecords(rows);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [connectionFilter, statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const setFilter = (key, value) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    setSearchParams(next, { replace: true });
  };

  const accountName = useCallback(
    (record) => {
      const match = connections.find((c) => c.id === record.connection_id);
      return match ? match.display_name || match.handle || match.account_slug : t("social.posts.removedAccount");
    },
    [connections],
  );

  // Scheduled posts first, soonest first; then everything else, newest first.
  const rows = useMemo(() => {
    const upcoming = records
      .filter((r) => r.status === "scheduled")
      .sort((a, b) => (a.scheduled_at || "").localeCompare(b.scheduled_at || ""));
    const rest = records.filter((r) => r.status !== "scheduled");
    return [...upcoming, ...rest];
  }, [records]);

  const handleCancel = async (record) => {
    setBusyId(record.id);
    try {
      await cancelPublish(record.id);
      showMessage(t("social.posts.cancelled"), "success");
      await load();
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async () => {
    if (!deleting) return;
    setBusyId(deleting.id);
    try {
      const result = await deletePublishRemote(deleting.id);
      showMessage(result.message || t("social.posts.deletedOnPlatform"), "success");
      setDeleting(null);
      await load();
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setBusyId(null);
    }
  };

  const deletingSpec = deleting ? specs[deleting.platform] : null;

  return (
    <PageLayout
      title={t("social.posts.title")}
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          <ActionButton kind="link" onClick={() => navigate("/social/accounts")}>
            {t("social.posts.socialAccounts")}
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/approvals")}>
            {t("social.posts.approvals")}
          </ActionButton>
          <ActionButton startIcon={<AddIcon />} onClick={() => setComposing(true)}>
            {t("social.posts.newPost")}
          </ActionButton>
          <Tooltip title={t("social.posts.refresh")}>
            <IconButton onClick={load} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      }
    >
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} sx={{ mb: 2 }}>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>{t("social.posts.account")}</InputLabel>
          <Select
            label={t("social.posts.account")}
            value={connectionFilter}
            onChange={(e) => setFilter("connection_id", e.target.value)}
          >
            <MenuItem value="">{t("social.posts.allAccounts")}</MenuItem>
            {connections.map((conn) => (
              <MenuItem key={conn.id} value={String(conn.id)}>
                {(specs[conn.provider]?.label || conn.provider) + " · " + (conn.display_name || conn.handle || conn.account_slug)}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
        <FormControl size="small" sx={{ minWidth: 220 }}>
          <InputLabel>{t("social.posts.status")}</InputLabel>
          <Select
            label={t("social.posts.status")}
            value={statusFilter}
            onChange={(e) => setFilter("status", e.target.value)}
          >
            {statusFilters().map((option) => (
              <MenuItem key={option.value || "all"} value={option.value}>
                {option.label}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {!loading && !rows.length ? (
        <Box sx={{ textAlign: "center", py: 6 }}>
          <Typography variant="body1" color="text.secondary" gutterBottom>
            {t("social.posts.emptyTitle")}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {t("social.posts.emptyBody")}
          </Typography>
        </Box>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("social.posts.columns.account")}</TableCell>
                <TableCell>{t("social.posts.columns.post")}</TableCell>
                <TableCell>{t("social.posts.columns.status")}</TableCell>
                <TableCell>{t("social.posts.columns.scheduled")}</TableCell>
                <TableCell>{t("social.posts.columns.posted")}</TableCell>
                <TableCell>{t("social.posts.columns.aiLabel")}</TableCell>
                <TableCell align="right">{t("social.posts.columns.actions")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((record) => {
                const spec = specs[record.platform];
                const platformLabel = spec?.label || record.platform;
                const removal = deleteOnPlatform(record, spec?.capabilities);
                const disclosure = spec?.capabilities?.ai_disclosure;
                return (
                  <TableRow key={record.id} hover>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>
                        {accountName(record)}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {platformLabel}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 360 }}>
                      {record.title && (
                        <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                          {record.title}
                        </Typography>
                      )}
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
                      >
                        {record.body ||
                          (record.media_refs?.length
                            ? t("social.posts.mediaItems", { count: record.media_refs.length })
                            : t("social.posts.noText"))}
                      </Typography>
                      {record.error_message && (
                        <Typography
                          variant="caption"
                          sx={{ display: "block", color: record.status === "failed" ? "error.main" : "text.secondary" }}
                        >
                          {record.error_message}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <StatusPill label={statusLabel(record, platformLabel)} tone={STATUS_TONE[record.status] || "neutral"} />
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Typography variant="body2">{formatWhen(record.scheduled_at)}</Typography>
                      {record.scheduled_at && (
                        <Typography variant="caption" color="text.secondary">
                          {record.scheduled_natively
                            ? t("social.posts.byPlatform", { platform: platformLabel })
                            : t("social.posts.byGuaardvark")}
                        </Typography>
                      )}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>{formatWhen(record.posted_at)}</TableCell>
                    <TableCell>
                      {record.ai_disclosure ? (
                        <Tooltip
                          title={
                            disclosure?.mechanism === "platform_flag"
                              ? t("social.posts.setAsFlag", { flag: disclosure.flag })
                              : t("social.posts.lineInPost", { text: disclosure?.text || "" })
                          }
                        >
                          <span>{t("social.posts.yes")}</span>
                        </Tooltip>
                      ) : (
                        t("social.posts.no")
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} justifyContent="flex-end" alignItems="center">
                        {record.remote_url && (
                          <Tooltip title={t("social.posts.openOn", { platform: platformLabel })}>
                            <IconButton
                              size="small"
                              component="a"
                              href={record.remote_url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              <OpenInNewIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {record.status === "awaiting_approval" && (
                          <>
                            <ActionButton onClick={() => setEditing(record)}>{t("social.posts.edit")}</ActionButton>
                            <ActionButton kind="link" onClick={() => navigate("/approvals")}>
                              {t("social.posts.review")}
                            </ActionButton>
                          </>
                        )}
                        {CANCELLABLE.has(record.status) && !record.scheduled_natively && (
                          <ActionButton loading={busyId === record.id} onClick={() => handleCancel(record)}>
                            {t("social.posts.cancel")}
                          </ActionButton>
                        )}
                        {removal.show && (
                          <ActionButton
                            kind="destructive"
                            disabled={!removal.ok}
                            tooltip={removal.reason}
                            onClick={() => setDeleting(record)}
                          >
                            {t("social.posts.deleteOnPlatform")}
                          </ActionButton>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      <PublishModal
        open={composing}
        onClose={() => {
          setComposing(false);
          load();
        }}
        onFeedback={(feedback) => showMessage(feedback.message, feedback.severity)}
      />

      <EditPublishDialog
        open={Boolean(editing)}
        record={editing}
        spec={editing ? specs[editing.platform] || null : null}
        connection={editing ? connections.find((c) => c.id === editing.connection_id) || null : null}
        accountLabel={editing ? accountName(editing) : ""}
        onClose={() => setEditing(null)}
        onSaved={async () => {
          setEditing(null);
          showMessage(t("social.posts.saved"), "success");
          await load();
        }}
      />

      <ConfirmActionDialog
        open={Boolean(deleting)}
        title={
          deletingSpec?.label || deleting?.platform
            ? t("social.posts.deleteTitle", { platform: deletingSpec?.label || deleting?.platform })
            : t("social.posts.deleteTitleUnknown")
        }
        description={
          deleting?.status === "scheduled"
            ? t("social.posts.deleteScheduled")
            : t("social.posts.deletePosted")
        }
        facts={
          deleting
            ? [
                { label: t("social.posts.factAccount"), value: accountName(deleting) },
                {
                  label: deleting.status === "scheduled" ? t("social.posts.factScheduled") : t("social.posts.factPosted"),
                  value: formatWhen(deleting.scheduled_at && deleting.status === "scheduled" ? deleting.scheduled_at : deleting.posted_at),
                },
                {
                  label: t("social.posts.factText"),
                  value: (deleting.title || deleting.body || "").slice(0, 120) || t("social.posts.noText"),
                },
              ]
            : []
        }
        confirmLabel={t("social.posts.deleteOnPlatform")}
        busy={busyId === deleting?.id}
        onConfirm={handleDelete}
        onClose={() => setDeleting(null)}
      />
    </PageLayout>
  );
};

export default SocialPostsPage;
