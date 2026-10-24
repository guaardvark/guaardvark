// Code the inbound guard holds for a person: edits waiting as pending fixes,
// file operations, and changes the source watch found on disk. Approving lands
// the change first and records the approval after, so an item that can no
// longer land as it was held stays here with the reason.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItemButton,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { Cancel as CancelIcon, CheckCircle as CheckCircleIcon } from "@mui/icons-material";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { inboundGuardService } from "../../api/inboundGuardService";
import { approveHeldChange, isActionable, rejectHeldChange } from "../../api/heldChanges";
import { FindingList, scanSize } from "../settings/InboundGuardSection";
import { ConfirmActionDialog } from "../settings/ui";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const VERDICT_COLOR = { block: "error", hold: "warning" };

const kindLabel = (scan) => {
  if (scan.pending_fix_id) return translate("approvals.held.kindEdit");
  if (scan.landable) return translate("approvals.held.kindFile");
  if (scan.source === "watch") return translate("approvals.held.kindOnDisk");
  return scan.source;
};

// A guard verdict as shown; the value stays the one compared.
const verdictLabel = (verdict) =>
  ({
    block: translate("approvals.held.verdict.block"),
    hold: translate("approvals.held.verdict.hold"),
  })[verdict] ?? verdict;

// What approving does depends on what was kept when the change was stopped. An
// edit refused outright (a block) keeps no text to apply; approving it records
// the digest, so the same change passes when it is made again.
const APPROVAL_EFFECT = (scan) => {
  if (!scan) return "apply";
  if (scan.source === "watch") return "accept";
  if (scan.pending_fix_id || scan.landable) return "apply";
  return "allow-again";
};

const approvedMessage = (effect) =>
  ({
    apply: translate("approvals.held.approvedApply"),
    accept: translate("approvals.held.approvedAccept"),
    "allow-again": translate("approvals.held.approvedAllowAgain"),
  })[effect];

// Scan times are stored as naive UTC (backend/utils/clock.py); read without a
// zone, the browser would take them as local time and put them hours off.
const formatWhen = (iso) => {
  if (!iso) return "";
  const text = String(iso);
  const then = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return translate("approvals.when.justNow");
  if (mins < 60) return translate("approvals.when.minutesAgo", { minutes: mins });
  const hours = Math.round(mins / 60);
  if (hours < 24) return translate("approvals.when.hoursAgo", { hours });
  return new Date(then).toLocaleDateString(uiLocale());
};

const HeldChangesPanel = ({ held, loading, onChanged, showMessage }) => {
  const { t } = useUiTranslation();
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const items = (held || []).filter(isActionable);

  const loadDetail = useCallback(async (id) => {
    setDetailLoading(true);
    setError(null);
    try {
      const res = await inboundGuardService.getScan(id);
      setDetail(res?.data || null);
    } catch (err) {
      setError(err.message);
      setDetail(null);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId != null) loadDetail(selectedId);
    else setDetail(null);
  }, [selectedId, loadDetail]);

  useEffect(() => {
    if (selectedId != null && !items.some((s) => s.id === selectedId)) setSelectedId(null);
  }, [items, selectedId]);

  const act = async (decision) => {
    const entry = { kind: "scan", item: detail };
    setBusy(true);
    setError(null);
    try {
      if (decision === "approve") {
        await approveHeldChange(entry, { note, overrideBlock: detail.verdict === "block" });
        showMessage?.(approvedMessage(APPROVAL_EFFECT(detail)));
      } else {
        await rejectHeldChange(entry, { note });
        showMessage?.(t("approvals.held.rejected"));
      }
      setNote("");
      setSelectedId(null);
      await onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  const renderList = () => {
    if (loading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      );
    }
    if (!items.length) {
      return (
        <Alert severity="info" sx={{ m: 2 }}>
          {t("approvals.held.empty")}
        </Alert>
      );
    }
    return (
      <List dense disablePadding sx={{ maxHeight: 520, overflowY: "auto" }}>
        {items.map((scan) => (
          <ListItemButton key={scan.id} selected={selectedId === scan.id} onClick={() => setSelectedId(scan.id)}>
            <ListItemText
              primary={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" noWrap sx={{ fontWeight: 500, minWidth: 0 }}>
                    {scan.subject}
                  </Typography>
                </Stack>
              }
              secondary={
                <Stack direction="row" spacing={1} alignItems="center" component="span">
                  <Chip
                    label={verdictLabel(scan.verdict)}
                    size="small"
                    color={VERDICT_COLOR[scan.verdict] || "default"}
                    sx={{ height: 18 }}
                  />
                  <Typography variant="caption" color="text.secondary" component="span">
                    {t("approvals.held.listMeta", {
                      kind: kindLabel(scan),
                      count: scan.findings?.length || 0,
                      when: formatWhen(scan.created_at),
                    })}
                  </Typography>
                </Stack>
              }
              secondaryTypographyProps={{ component: "div" }}
            />
          </ListItemButton>
        ))}
      </List>
    );
  };

  const renderDetail = () => {
    if (selectedId == null) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 3 }}>
          {t("approvals.held.selectPrompt")}
        </Typography>
      );
    }
    if (detailLoading || !detail) {
      return (
        <Box sx={{ p: 3 }}>
          {error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={20} />}
        </Box>
      );
    }
    const blocked = detail.verdict === "block";
    const diff = detail.fix?.diff || detail.diff;
    const effect = APPROVAL_EFFECT(detail);
    const onDisk = effect === "accept";
    return (
      <Box sx={{ p: 3 }}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }} flexWrap="wrap">
          <Typography variant="h6" sx={{ wordBreak: "break-all" }}>
            {detail.subject}
          </Typography>
          <Chip label={verdictLabel(detail.verdict)} size="small" color={VERDICT_COLOR[detail.verdict] || "default"} />
          <Chip label={detail.source} size="small" variant="outlined" />
          {detail.fix && <Chip label={t("approvals.held.pendingFix", { id: detail.fix.id })} size="small" variant="outlined" />}
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 2 }}>
          {t("approvals.held.detailMeta", {
            size: scanSize(detail),
            when: formatWhen(detail.created_at),
            digest: detail.digest,
          })}
        </Typography>

        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          {t("approvals.held.findingsHeading")}
        </Typography>
        <FindingList findings={detail.findings} />

        {diff && (
          <Box sx={{ mt: 2 }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              {t("approvals.held.changeHeading")}
            </Typography>
            <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, overflow: "hidden" }}>
              <SyntaxHighlighter
                language="diff"
                style={a11yDark}
                customStyle={{ margin: 0, fontSize: "0.75rem", maxHeight: 360 }}
                wrapLongLines
              >
                {diff}
              </SyntaxHighlighter>
            </Box>
          </Box>
        )}
        {onDisk && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {t("approvals.held.onDiskNote")}
          </Alert>
        )}
        {effect === "allow-again" && (
          <Alert severity="info" sx={{ mt: 2 }}>
            {t("approvals.held.allowAgainNote")}
          </Alert>
        )}

        <Divider sx={{ my: 2 }} />
        <TextField size="small" label={t("approvals.held.note")} value={note} onChange={(e) => setNote(e.target.value)} fullWidth />
        {error && (
          <Alert severity="error" sx={{ mt: 2 }}>
            {error}
          </Alert>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
          <Button
            variant="contained"
            color={blocked ? "error" : "primary"}
            startIcon={<CheckCircleIcon />}
            disabled={busy}
            onClick={() => setConfirming(true)}
          >
            {blocked
              ? t("approvals.held.approveDespiteBlock")
              : onDisk
                ? t("approvals.held.accept")
                : effect === "allow-again"
                  ? t("approvals.held.approve")
                  : t("approvals.held.approveAndApply")}
          </Button>
          <Button color="error" startIcon={<CancelIcon />} disabled={busy} onClick={() => act("reject")}>
            {t("approvals.held.reject")}
          </Button>
        </Stack>
      </Box>
    );
  };

  return (
    <>
      <Box sx={{ display: "flex", minHeight: 360 }}>
        <Box sx={{ width: 340, borderRight: 1, borderColor: "divider" }}>{renderList()}</Box>
        <Box sx={{ flex: 1, overflowY: "auto" }}>{renderDetail()}</Box>
      </Box>
      <ConfirmActionDialog
        open={confirming}
        title={detail?.verdict === "block" ? t("approvals.held.confirmBlockedTitle") : t("approvals.held.confirmApplyTitle")}
        description={
          APPROVAL_EFFECT(detail) === "allow-again"
            ? t("approvals.held.confirmAllowAgain")
            : detail?.verdict === "block"
              ? t("approvals.held.confirmBlocked")
              : t("approvals.held.confirmApply")
        }
        facts={detail ? [{ label: t("approvals.held.findings"), value: detail.findings?.length || 0 }] : []}
        confirmLabel={detail?.verdict === "block" ? t("approvals.held.approveDespiteBlock") : t("approvals.held.approveAndApply")}
        busy={busy}
        onConfirm={() => act("approve")}
        onClose={() => setConfirming(false)}
      />
    </>
  );
};

export default HeldChangesPanel;
