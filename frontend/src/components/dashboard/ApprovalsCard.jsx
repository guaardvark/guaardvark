// Waiting for You Dashboard Card - what the Approvals page holds, at a glance.
// Outreach drafts are approved or rejected here, as on that page. Publishes,
// held code changes and approved drafts waiting for "Post now" need what that
// page shows (the revision, the diff, the Agent Screen), so they open it.

import React, { useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, IconButton, Link, Stack, Tooltip, Typography } from "@mui/material";
import { Cancel, CheckCircle, ChevronRight, Refresh } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import { usePendingApprovals } from "../../hooks/usePendingApprovals";
import { approveDraft, rejectDraft } from "../../api/outreachService";
import { useSnackbar } from "../common/SnackbarProvider";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

export const APPROVALS_PATH = "/approvals";
const tabPath = (tab) => `${APPROVALS_PATH}?tab=${tab}`;

/** Rows a section shows before it points at the Approvals page for the rest. */
export const SECTION_LIMIT = 4;

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

// The words that would post; `text` is the API's reading of a reply's envelope.
const draftWords = (draft) => draft?.text ?? draft?.draft_text ?? "";

// A guard verdict as shown; the value stays the one compared.
const verdictLabel = (verdict) =>
  ({
    block: translate("approvals.held.verdict.block"),
    hold: translate("approvals.held.verdict.hold"),
  })[verdict] ?? verdict;

const clamp = (lines) => ({
  display: "-webkit-box",
  WebkitLineClamp: lines,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
});

const SectionHeading = ({ label, count }) => (
  <Typography
    variant="overline"
    sx={{ display: "block", lineHeight: 1.6, mt: 0.5, color: "text.secondary", fontSize: "0.62rem" }}
  >
    {label} ({count})
  </Typography>
);

// A row that opens the Approvals page on the tab that can act on it.
const ReviewRow = ({ chip, chipColor = "default", text, when, onOpen, label }) => (
  <Box
    component="button"
    type="button"
    onClick={onOpen}
    aria-label={label}
    className="non-draggable"
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 0.75,
      width: "100%",
      p: 0.5,
      border: 0,
      borderRadius: 1,
      bgcolor: "transparent",
      color: "inherit",
      font: "inherit",
      textAlign: "left",
      cursor: "pointer",
      "&:hover, &:focus-visible": { bgcolor: "action.hover" },
    }}
  >
    {chip && <Chip label={chip} color={chipColor} size="small" variant="outlined" sx={{ fontSize: "0.6rem", height: 18 }} />}
    <Box sx={{ minWidth: 0, flex: 1 }}>
      <Typography variant="body2" sx={{ fontSize: "0.78rem", ...clamp(2) }}>
        {text}
      </Typography>
      {when && (
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
          {when}
        </Typography>
      )}
    </Box>
    <ChevronRight fontSize="small" sx={{ opacity: 0.6 }} />
  </Box>
);

const ApprovalsCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const { t } = useUiTranslation();
    const navigate = useNavigate();
    const { showMessage } = useSnackbar();
    const { count, pending, held, outreach, outreachWaiting, loading, heldLoading, outreachLoading, error, refresh } =
      usePendingApprovals();
    const [busyId, setBusyId] = useState(null);
    const [expandedId, setExpandedId] = useState(null);
    const [actionError, setActionError] = useState(null);

    const decide = async (draft, approve) => {
      setBusyId(draft.id);
      setActionError(null);
      try {
        if (approve) {
          const row = await approveDraft(draft.id, null);
          showMessage(t("approvals.outreach.approvedNext", { next: row?.next || "" }).trim(), "success");
        } else {
          await rejectDraft(draft.id);
          showMessage(t("approvals.outreach.rejected"), "info");
        }
        await refresh();
      } catch (err) {
        setActionError(err?.message || String(err));
      } finally {
        setBusyId(null);
      }
    };

    const more = (rows, tab) =>
      rows.length > SECTION_LIMIT && (
        <Button
          variant="text"
          size="small"
          onClick={() => navigate(tabPath(tab))}
          className="non-draggable"
          sx={{ fontSize: "0.7rem", textTransform: "none" }}
        >
          {t("dashboard.approvals.more", { count: rows.length - SECTION_LIMIT })}
        </Button>
      );

    const stillLoading = loading || heldLoading || outreachLoading;

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title={t("dashboard.approvals.title")}
        titleBarActions={
          count > 0 ? (
            <Chip label={count} color="warning" size="small" sx={{ height: 16, fontSize: "0.6rem" }} />
          ) : null
        }
        {...props}
      >
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
          <Typography variant="body2" sx={{ flex: 1, fontSize: "0.78rem", color: "text.secondary" }}>
            {count > 0 ? t("dashboard.approvals.summary", { count }) : ""}
          </Typography>
          <Tooltip title={t("dashboard.refreshData")}>
            <IconButton size="small" onClick={() => refresh()} className="non-draggable">
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        {error && (
          <Alert severity="warning" sx={{ my: 0.5 }}>
            {error}
          </Alert>
        )}
        {actionError && (
          <Alert severity="error" sx={{ my: 0.5 }} onClose={() => setActionError(null)}>
            {actionError}
          </Alert>
        )}

        {count === 0 && stillLoading && <CircularProgress size={22} sx={{ display: "block", mx: "auto", my: 2 }} />}
        {count === 0 && !stillLoading && !error && (
          <Typography variant="body2" sx={{ color: "text.secondary", mt: 2, textAlign: "center" }}>
            {t("dashboard.approvals.empty")}
          </Typography>
        )}

        {outreach.length > 0 && (
          <>
            <SectionHeading label={t("dashboard.approvals.sections.outreach")} count={outreach.length} />
            {outreach.slice(0, SECTION_LIMIT).map((draft) => {
              const words = draftWords(draft);
              const expanded = expandedId === draft.id;
              const busy = busyId === draft.id;
              return (
                <Box
                  key={draft.id}
                  className="non-draggable"
                  sx={{ p: 0.75, mb: 0.75, border: 1, borderColor: "divider", borderRadius: 1 }}
                >
                  <Stack direction="row" spacing={0.75} alignItems="center" sx={{ mb: 0.5 }}>
                    <Chip label={draft.platform} size="small" variant="outlined" sx={{ fontSize: "0.6rem", height: 18 }} />
                    <Typography variant="caption" noWrap sx={{ minWidth: 0, flex: 1 }}>
                      {draft.action}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem", flexShrink: 0 }}>
                      {formatWhen(draft.created_at)}
                    </Typography>
                  </Stack>
                  <Typography
                    variant="body2"
                    onClick={() => setExpandedId(expanded ? null : draft.id)}
                    title={expanded ? "" : t("dashboard.approvals.showAll")}
                    sx={{
                      fontSize: "0.78rem",
                      whiteSpace: "pre-wrap",
                      overflowWrap: "anywhere",
                      cursor: "pointer",
                      ...(expanded ? {} : clamp(4)),
                    }}
                  >
                    {words || t("approvals.noText")}
                  </Typography>
                  {draft.target_url && (
                    <Link
                      href={draft.target_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      variant="caption"
                      sx={{ display: "block", mt: 0.25, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    >
                      {draft.target_url}
                    </Link>
                  )}
                  <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} alignItems="center">
                    <Button
                      size="small"
                      variant="contained"
                      color="success"
                      startIcon={<CheckCircle />}
                      disabled={busy}
                      onClick={() => decide(draft, true)}
                      sx={{ textTransform: "none", fontSize: "0.72rem", py: 0.25 }}
                    >
                      {t("approvals.outreach.approve")}
                    </Button>
                    <Button
                      size="small"
                      color="error"
                      startIcon={<Cancel />}
                      disabled={busy}
                      onClick={() => decide(draft, false)}
                      sx={{ textTransform: "none", fontSize: "0.72rem", py: 0.25 }}
                    >
                      {t("approvals.outreach.reject")}
                    </Button>
                    <Box sx={{ flex: 1 }} />
                    <Button
                      size="small"
                      onClick={() => navigate(tabPath("outreach"))}
                      sx={{ textTransform: "none", fontSize: "0.72rem", py: 0.25 }}
                    >
                      {t("dashboard.edit")}
                    </Button>
                  </Stack>
                </Box>
              );
            })}
            {more(outreach, "outreach")}
          </>
        )}

        {outreachWaiting.length > 0 && (
          <>
            <SectionHeading label={t("dashboard.approvals.sections.ready")} count={outreachWaiting.length} />
            {outreachWaiting.slice(0, SECTION_LIMIT).map((draft) => (
              <ReviewRow
                key={draft.id}
                chip={draft.platform}
                chipColor="info"
                text={draftWords(draft) || t("approvals.noText")}
                when={formatWhen(draft.created_at)}
                label={t("dashboard.approvals.openOn", { tab: t("approvals.tabs.outreach") })}
                onOpen={() => navigate(tabPath("outreach"))}
              />
            ))}
            {more(outreachWaiting, "outreach")}
          </>
        )}

        {pending.length > 0 && (
          <>
            <SectionHeading label={t("dashboard.approvals.sections.publishes")} count={pending.length} />
            {pending.slice(0, SECTION_LIMIT).map((record) => (
              <ReviewRow
                key={record.id}
                chip={record.platform}
                chipColor="warning"
                text={record.title || record.body || t("approvals.noText")}
                when={formatWhen(record.created_at)}
                label={t("dashboard.approvals.openOn", { tab: t("approvals.tabs.publishes") })}
                onOpen={() => navigate(tabPath("pending"))}
              />
            ))}
            {more(pending, "pending")}
          </>
        )}

        {held.length > 0 && (
          <>
            <SectionHeading label={t("dashboard.approvals.sections.code")} count={held.length} />
            {held.slice(0, SECTION_LIMIT).map((scan) => (
              <ReviewRow
                key={scan.id}
                chip={verdictLabel(scan.verdict)}
                chipColor={scan.verdict === "block" ? "error" : "warning"}
                text={scan.subject || t("approvals.noText")}
                when={formatWhen(scan.created_at)}
                label={t("dashboard.approvals.openOn", { tab: t("approvals.tabs.code") })}
                onOpen={() => navigate(tabPath("code"))}
              />
            ))}
            {more(held, "code")}
          </>
        )}
      </DashboardCardWrapper>
    );
  },
);

ApprovalsCard.displayName = "ApprovalsCard";
export default ApprovalsCard;
