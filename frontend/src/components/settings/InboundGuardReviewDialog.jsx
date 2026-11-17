// The inbound guard's findings, for a person to read and decide on. Each row says
// when it was flagged, where it came from, which mode the guard was in, and what
// approving it does. That last part comes from the backend, which acts on the
// same rule, so the words here cannot drift from what the button does.

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Checkbox,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  MenuItem,
  Stack,
  Tab,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { ActionButton, ChoiceChips, ConfirmActionDialog, Hint } from "./ui";
import { inboundGuardService } from "../../api/inboundGuardService";
import { approveHeldChange, rejectHeldChange } from "../../api/heldChanges";
import { FindingList, excerptLabelFor } from "./InboundFindingList";
import { useUiTranslation } from "../../i18n";
import {
  SEVERITIES,
  SORTS,
  TABS,
  VERDICTS,
  approvalSummary,
  categoryOf,
  changeDate,
  decisionOf,
  effectButton,
  effectShort,
  formatExact,
  formatRelative,
  inTab,
  isBlocked,
  isOwnDecision,
  nextSelection,
  sizeText,
  sourceLabel,
  toEntries,
  toggleAll,
  viewEntries,
} from "./inboundGuardReview";

const PAGE = 200;
const SEVERITY_TONE = { critical: "error", high: "error", medium: "warning", low: "info", info: "default" };
const DECISION_TONE = { approved: "success", rejected: "error", clear: "default", allow: "default" };
const EMPTY = { scans: [], git: [], scans_total: 0, git_total: 0 };

const When = ({ iso }) =>
  iso ? (
    <Tooltip title={formatExact(iso)}>
      <span>{formatRelative(iso)}</span>
    </Tooltip>
  ) : (
    "—"
  );

const ChangeDateCell = ({ item, t }) => {
  const date = changeDate(item);
  if (!date) return "—";
  const values = { label: date.label, when: formatExact(date.iso), detail: date.detail };
  return (
    <Tooltip title={t(date.detail ? "inboundGuard.changeDate.tooltipDetail" : "inboundGuard.changeDate.tooltip", values)}>
      <span>{formatRelative(date.iso)}</span>
    </Tooltip>
  );
};

const OwnDecisionChip = ({ t }) => (
  <Tooltip title={t("inboundGuard.ownDecision.chipTooltip")}>
    <Chip size="small" variant="outlined" color="warning" label={t("inboundGuard.ownDecision.chip")} sx={{ height: 18 }} />
  </Tooltip>
);

const Lines = ({ items }) => (
  <Stack component="span">
    {items.map((text) => (
      <span key={text}>{text}</span>
    ))}
  </Stack>
);

/** The item opened below the list: everything a person needs to decide on it. */
const ItemDetail = ({ entry, resolved, t }) => {
  const { item } = entry;
  const date = changeDate(item);
  const when = formatExact(item.flagged_at);
  const dateValues = date ? { when: formatExact(date.iso), detail: date.detail } : null;
  const dateKey = date ? `inboundGuard.detail.${date.kind === "file" ? "fileWritten" : "newestCommit"}${date.detail ? "Detail" : ""}` : null;
  const sizeValues = {
    files: item.files ?? 0,
    lines: item.added_lines ?? 0,
    digest: item.digest,
    id: item.pending_fix_id,
    status: item.fix_status,
  };
  const sizeKey = item.pending_fix_id ? (item.fix_status ? "sizeFixStatus" : "sizeFix") : "size";
  const checks = (item.scanners_run || []).map((s) => t("inboundGuard.detail.check", s)).join(" · ") || t("inboundGuard.unknown");
  const decision = decisionOf(item);
  const decidedValues = {
    decision: t(`inboundGuard.decision.${decision}`),
    when: formatExact(item.decided_at) || t("inboundGuard.unknown"),
    by: item.decided_by || t("inboundGuard.unknown"),
    note: item.decision_note,
  };
  return (
    <Stack spacing={1.25}>
      <Typography variant="subtitle2" sx={{ wordBreak: "break-all" }}>
        {item.subject}
      </Typography>
      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
        {item.verdict !== "allow" && (
          <Chip
            size="small"
            label={t(`inboundGuard.verdict.${isBlocked(item) ? "block" : "hold"}`)}
            color={isBlocked(item) ? "error" : "warning"}
          />
        )}
        {!resolved && <Chip size="small" variant="outlined" label={t(`inboundGuard.category.${categoryOf(item)}`)} />}
        <Chip size="small" variant="outlined" label={sourceLabel(item.source)} />
        <Chip
          size="small"
          variant="outlined"
          label={t("inboundGuard.detail.modeChip", { mode: item.mode_at_record || item.mode || t("inboundGuard.unknown") })}
        />
        {isOwnDecision(item) && <OwnDecisionChip t={t} />}
      </Stack>
      {resolved && (
        <Hint>{t(item.decision_note ? "inboundGuard.detail.decidedNote" : "inboundGuard.detail.decided", decidedValues)}</Hint>
      )}
      <Hint>
        {!when
          ? t("inboundGuard.detail.flaggedUnknown")
          : item.seen > 1
            ? t("inboundGuard.detail.seen", {
                when,
                count: item.seen,
                sources: (item.sources || []).map(sourceLabel).join(", "),
                last: formatExact(item.last_seen_at),
              })
            : t("inboundGuard.detail.flagged", { when })}
      </Hint>
      <Hint>
        {date
          ? t(dateKey, dateValues)
          : t(entry.kind === "git" ? "inboundGuard.detail.noCommitDates" : "inboundGuard.detail.noDate")}
      </Hint>
      <Hint>{t(`inboundGuard.detail.${sizeKey}`, sizeValues)}</Hint>
      {!resolved && isOwnDecision(item) && (
        <Alert severity="warning" sx={{ py: 0.25 }}>
          {t("inboundGuard.ownDecision.alert", { rules: (item.own_decision_rules || []).join(", ") })}
        </Alert>
      )}
      {!resolved && (
        <Alert severity={isBlocked(item) ? "warning" : "info"} sx={{ py: 0.25 }}>
          <strong>{t("inboundGuard.detail.approving")}</strong> {item.what_approve_does}. {item.approve_note}
          <br />
          <strong>{t("inboundGuard.detail.rejecting")}</strong> {item.what_reject_does}.
        </Alert>
      )}
      <Hint>
        {(item.errors || []).length
          ? t("inboundGuard.detail.checksNotes", { checks, notes: item.errors.join("; ") })
          : t("inboundGuard.detail.checks", { checks })}
      </Hint>
      <FindingList findings={item.findings} excerptLabel={excerptLabelFor(item.source)} />
    </Stack>
  );
};

/**
 * Every inbound-guard finding, with selection, batch decisions and the decided history.
 *
 * Plain click opens a row; Ctrl/Cmd-click or its checkbox toggles it; Shift-click
 * selects the range from the last row clicked. Approve all and Reject all cover
 * the rows shown under the current tab and filters. Every batch decides its
 * items one at a time on the server, so each gets the record a single decision
 * writes. A row whose findings touch a way off the machine is never approved by
 * a batch: it is opened and approved on its own, with a typed note.
 */
export default function InboundGuardReviewDialog({ open, onClose, onChanged }) {
  const { t } = useUiTranslation();
  const [view, setView] = useState("open");
  const [data, setData] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [tab, setTab] = useState("all");
  const [verdict, setVerdict] = useState("");
  const [minSeverity, setMinSeverity] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState("newest");
  const [selected, setSelected] = useState(() => new Set());
  const [anchor, setAnchor] = useState(null);
  const [openKey, setOpenKey] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [includeBlocks, setIncludeBlocks] = useState(false);
  const [result, setResult] = useState(null);

  const resolved = view === "resolved";
  const status = resolved ? "resolved" : "open";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await inboundGuardService.listScans(status, PAGE, { git: true, gitLimit: PAGE });
      setData(res?.data || EMPTY);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [status]);

  const loadMore = async () => {
    setLoading(true);
    try {
      const res = await inboundGuardService.listScans(status, PAGE, {
        git: true,
        offset: data.scans.length,
        gitLimit: PAGE,
        gitOffset: data.git.length,
      });
      const more = res?.data || {};
      setData((d) => ({ ...more, scans: [...d.scans, ...(more.scans || [])], git: [...d.git, ...(more.git || [])] }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) {
      setView("open");
      setResult(null);
      setError(null);
    }
  }, [open]);

  useEffect(() => {
    if (open) {
      setData(EMPTY);
      setOpenKey(null);
      setSelected(new Set());
      setAnchor(null);
      load();
    }
  }, [open, load]);

  const entries = useMemo(() => toEntries(data, { resolved }), [data, resolved]);
  const shown = useMemo(
    () =>
      resolved
        ? viewEntries(entries, { source, sort: "decided" })
        : viewEntries(entries, { tab, verdict, minSeverity, source, sort }),
    [entries, resolved, tab, verdict, minSeverity, source, sort],
  );
  const orderedKeys = useMemo(() => shown.map((e) => e.key), [shown]);
  const sources = useMemo(() => [...new Set(entries.map((e) => e.item.source).filter(Boolean))].sort(), [entries]);

  // Selection follows what is shown, so a batch never reaches a row hidden by a tab or filter.
  useEffect(() => {
    setSelected((s) => {
      const visible = new Set(orderedKeys);
      const kept = [...s].filter((k) => visible.has(k));
      return kept.length === s.size ? s : new Set(kept);
    });
  }, [orderedKeys]);

  const selectedEntries = shown.filter((e) => selected.has(e.key));
  const rejectable = selectedEntries.filter((e) => e.item.can_reject);
  const rejectableShown = shown.filter((e) => e.item.can_reject);
  const opened = entries.find((e) => e.key === openKey) || null;
  const allChecked = orderedKeys.length > 0 && orderedKeys.every((k) => selected.has(k));
  const someChecked = !allChecked && orderedKeys.some((k) => selected.has(k));
  const total = (data.scans_total || 0) + (data.git_total || 0);
  const moreToLoad = data.scans.length < (data.scans_total || 0) || data.git.length < (data.git_total || 0);
  const needsNote = Boolean(opened) && isOwnDecision(opened.item) && !note.trim();

  const onRowClick = (event, key) => {
    if (!resolved && event.shiftKey) {
      setSelected((s) => nextSelection(s, orderedKeys, key, anchor, { shift: true }));
    } else if (!resolved && (event.ctrlKey || event.metaKey)) {
      setSelected((s) => nextSelection(s, orderedKeys, key, null));
    } else {
      setOpenKey(key);
    }
    setAnchor(key);
  };

  const onCheck = (event, key) => {
    const shift = Boolean(event.nativeEvent?.shiftKey);
    setSelected((s) => nextSelection(s, orderedKeys, key, anchor, { shift }));
    setAnchor(key);
  };

  const finish = async (message) => {
    setResult(message);
    setSelected(new Set());
    setConfirm(null);
    setIncludeBlocks(false);
    await load();
    onChanged?.();
  };

  const resultMessage = (body, verb) => {
    const leftForYou = body.left_for_you || 0;
    const otherSkipped = (body.skipped || 0) - leftForYou;
    const parts = [t(`inboundGuard.result.${verb}`, { count: body[verb] || 0 })];
    if (otherSkipped > 0) parts.push(t("inboundGuard.result.skipped", { count: otherSkipped }));
    if (body.failed) parts.push(t("inboundGuard.result.failed", { count: body.failed }));
    const reasons = (body.results || [])
      .filter((r) => r.left !== "own_decision" && (r.outcome === "failed" || r.outcome === "skipped"))
      .slice(0, 5)
      .map((r) => t("inboundGuard.result.reason", { item: r.kind === "git" ? r.digest : `#${r.id}`, reason: r.reason }));
    if (leftForYou) reasons.unshift(t("inboundGuard.result.leftForYou", { count: leftForYou }));
    return { text: parts.join(", "), reasons, severity: body.failed || leftForYou ? "warning" : "success" };
  };

  const runConfirmed = async () => {
    setBusy(true);
    setError(null);
    try {
      if (confirm.action === "approve") {
        const summary = approvalSummary(confirm.entries, { includeBlocks });
        const res = await inboundGuardService.approveBatch({
          ids: summary.ids,
          gitDigests: summary.gitDigests,
          note,
          includeBlocks,
        });
        // Rows this page left out are counted with whatever the server left out itself.
        const body = res?.data || {};
        await finish(
          resultMessage(
            { ...body, left_for_you: (body.left_for_you || 0) + summary.ownDecision, skipped: (body.skipped || 0) + summary.ownDecision },
            "approved",
          ),
        );
      } else if (confirm.action === "reject") {
        const res = await inboundGuardService.rejectBatch({
          ids: confirm.entries.filter((e) => e.kind === "scan").map((e) => e.item.id),
          note,
        });
        await finish(resultMessage(res?.data || {}, "rejected"));
      } else {
        const res = await inboundGuardService.clearHistory();
        const done = res?.data || {};
        await finish({
          text: t("inboundGuard.result.cleared", {
            total: done.removed?.total || 0,
            rejected: done.removed?.rejected || 0,
            cleared: done.removed?.cleared || 0,
            approved: done.keeps?.approved || 0,
            open: done.keeps?.open || 0,
          }),
          reasons: [],
          severity: "success",
        });
      }
    } catch (err) {
      setError(err.message);
      setConfirm(null);
    } finally {
      setBusy(false);
    }
  };

  const askClearHistory = async () => {
    setError(null);
    try {
      const res = await inboundGuardService.history();
      setConfirm({ action: "clear", history: res?.data || null });
    } catch (err) {
      setError(err.message);
    }
  };

  const decideOne = async (decision) => {
    setBusy(true);
    setError(null);
    try {
      if (decision === "approve") {
        await approveHeldChange(opened, { note, overrideBlock: isBlocked(opened.item) });
      } else {
        await rejectHeldChange(opened, { note });
      }
      setOpenKey(null);
      setNote("");
      await finish({
        text: t(decision === "approve" ? "inboundGuard.result.approvedOne" : "inboundGuard.result.rejectedOne"),
        reasons: [],
        severity: "success",
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const tabCount = (value) => entries.filter((e) => inTab(e.item, value)).length;
  const describeCounts = (counts) =>
    Object.entries(counts)
      .map(([name, count]) => t("inboundGuard.confirm.count", { count, name }))
      .join(", ") || t("inboundGuard.confirm.none");
  const summary = confirm?.action === "approve" ? approvalSummary(confirm.entries, { includeBlocks }) : null;
  const rejectEffects =
    confirm?.action === "reject"
      ? Object.entries(
          confirm.entries.reduce((acc, e) => {
            acc[e.item.what_reject_does] = (acc[e.item.what_reject_does] || 0) + 1;
            return acc;
          }, {}),
        )
      : [];
  const history = confirm?.action === "clear" ? confirm.history : null;

  const confirmProps = () => {
    if (!confirm) return { title: "", facts: [] };
    if (confirm.action === "clear") {
      return {
        title: t("inboundGuard.history.title"),
        description: history?.removes?.total ? t("inboundGuard.history.text") : t("inboundGuard.history.clearNothing"),
        facts: [
          { label: t("inboundGuard.history.removesRejected"), value: history?.removes?.rejected ?? 0 },
          { label: t("inboundGuard.history.removesCleared"), value: history?.removes?.cleared ?? 0 },
        ],
        keeps: t("inboundGuard.history.keeps", {
          approved: history?.keeps?.approved ?? 0,
          open: history?.keeps?.open ?? 0,
          inUse: history?.keeps?.in_use ?? 0,
        }),
        confirmLabel: t("inboundGuard.history.confirm", { count: history?.removes?.total ?? 0 }),
        disabled: !history?.removes?.total,
      };
    }
    const scopeKeeps = t(confirm.scope === "all" ? "inboundGuard.confirm.keepsShown" : "inboundGuard.confirm.keepsSelected");
    if (confirm.action === "reject") {
      return {
        title: t("inboundGuard.confirm.rejectTitle", { count: confirm.entries.length }),
        description: t("inboundGuard.confirm.rejectText"),
        facts: [
          {
            label: t("inboundGuard.confirm.rejectDoes"),
            value: <Lines items={rejectEffects.map(([text, count]) => t("inboundGuard.confirm.times", { count, text }))} />,
          },
        ],
        keeps: scopeKeeps,
        confirmLabel: t("inboundGuard.confirm.rejectLabel", { count: confirm.entries.length }),
      };
    }
    const facts = [
      {
        label: t("inboundGuard.confirm.items"),
        value: t(confirm.scope === "all" ? "inboundGuard.confirm.itemsShown" : "inboundGuard.confirm.itemsSelected", {
          count: summary.count,
          total: summary.total,
        }),
      },
      { label: t("inboundGuard.confirm.byKind"), value: describeCounts(summary.byKind) },
      { label: t("inboundGuard.confirm.bySource"), value: describeCounts(summary.bySource) },
      { label: t("inboundGuard.confirm.highest"), value: summary.highest || t("inboundGuard.confirm.none") },
      {
        label: t("inboundGuard.confirm.approveDoes"),
        value: <Lines items={summary.effects.map((e) => t("inboundGuard.confirm.times", e))} />,
      },
      {
        label: t("inboundGuard.confirm.blocked"),
        value: summary.blocks
          ? includeBlocks
            ? t("inboundGuard.confirm.blockedIncluded", { count: summary.blocks })
            : t("inboundGuard.confirm.blockedLeftOut", { count: summary.blocksLeftOut })
          : t("inboundGuard.confirm.none"),
      },
    ];
    if (summary.ownDecision) {
      facts.push({
        label: t("inboundGuard.confirm.ownDecision"),
        value: t("inboundGuard.confirm.ownDecisionCount", { count: summary.ownDecision }),
      });
    }
    return {
      title: t("inboundGuard.confirm.approveTitle", { count: summary.count }),
      description:
        summary.count > 0
          ? t("inboundGuard.confirm.approveText")
          : t(summary.blocks ? "inboundGuard.confirm.approveNothing" : "inboundGuard.confirm.approveNothingOwn"),
      facts,
      extra:
        summary.blocks > 0 ? (
          <FormControlLabel
            control={<Checkbox size="small" checked={includeBlocks} onChange={(e) => setIncludeBlocks(e.target.checked)} />}
            label={t("inboundGuard.confirm.includeBlocks", { count: summary.blocks })}
          />
        ) : null,
      keeps: scopeKeeps,
      confirmLabel: t("inboundGuard.confirm.approveLabel", { count: summary.count }),
      disabled: summary.count === 0,
    };
  };
  const { disabled: confirmDisabled, ...confirmView } = confirmProps();

  const select = (label, value, onChange, children, minWidth = 150) => (
    <TextField select size="small" label={label} value={value} onChange={(e) => onChange(e.target.value)} sx={{ minWidth }}>
      {children}
    </TextField>
  );
  const columns = resolved ? 8 : 11;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth PaperProps={{ sx: { height: "90vh" } }}>
      <DialogTitle>{t("inboundGuard.title")}</DialogTitle>
      <DialogContent dividers sx={{ display: "flex", flexDirection: "column", gap: 1, minHeight: 0 }}>
        <Hint>
          {t(data.git_mode ? "inboundGuard.introGit" : "inboundGuard.intro", {
            mode: data.mode || t("inboundGuard.unknown"),
            gitMode: data.git_mode,
          })}
        </Hint>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <ChoiceChips
            options={[
              { value: "open", label: t("inboundGuard.view.open") },
              { value: "resolved", label: t("inboundGuard.view.resolved"), tooltip: t("inboundGuard.view.resolvedTooltip") },
            ]}
            value={view}
            onChange={setView}
            ariaLabel={t("inboundGuard.view.aria")}
          />
          {resolved && (
            <>
              <Box sx={{ flex: 1 }} />
              <ActionButton kind="destructive" disabled={busy} tooltip={t("inboundGuard.history.clearTooltip")} onClick={askClearHistory}>
                {t("inboundGuard.history.clear")}
              </ActionButton>
            </>
          )}
        </Stack>
        {!resolved && (
          <Tabs value={tab} onChange={(_e, v) => setTab(v)} variant="scrollable" sx={{ minHeight: 36 }}>
            {TABS.map((value) => (
              <Tab key={value} value={value} label={t(`inboundGuard.tab.${value}`, { count: tabCount(value) })} sx={{ minHeight: 36, py: 0.5 }} />
            ))}
          </Tabs>
        )}
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          {!resolved &&
            select(
              t("inboundGuard.filter.verdict"),
              verdict,
              setVerdict,
              [
                <MenuItem key="" value="">
                  {t("inboundGuard.filter.anyVerdict")}
                </MenuItem>,
                ...VERDICTS.map((v) => (
                  <MenuItem key={v} value={v}>
                    {t(`inboundGuard.filter.${v}`)}
                  </MenuItem>
                )),
              ],
              160,
            )}
          {!resolved &&
            select(t("inboundGuard.filter.severity"), minSeverity, setMinSeverity, [
              <MenuItem key="" value="">
                {t("inboundGuard.filter.anySeverity")}
              </MenuItem>,
              ...SEVERITIES.map((s) => (
                <MenuItem key={s} value={s}>
                  {t("inboundGuard.filter.orWorse", { severity: s })}
                </MenuItem>
              )),
            ])}
          {select(
            t("inboundGuard.filter.source"),
            source,
            setSource,
            [
              <MenuItem key="" value="">
                {t("inboundGuard.filter.anySource")}
              </MenuItem>,
              ...sources.map((s) => (
                <MenuItem key={s} value={s}>
                  {sourceLabel(s)}
                </MenuItem>
              )),
            ],
            190,
          )}
          {!resolved &&
            select(
              t("inboundGuard.filter.sort"),
              sort,
              setSort,
              SORTS.map((s) => (
                <MenuItem key={s} value={s}>
                  {t(`inboundGuard.sort.${s}`)}
                </MenuItem>
              )),
              190,
            )}
          {!resolved && (
            <>
              <Box sx={{ flex: 1 }} />
              <Typography variant="caption" color="text.secondary">
                {t("inboundGuard.selectedCount", { count: selectedEntries.length })}
              </Typography>
              <ActionButton
                kind="primary"
                disabled={!selectedEntries.length || busy}
                onClick={() => setConfirm({ action: "approve", entries: selectedEntries, scope: "selected" })}
              >
                {t("inboundGuard.approveSelected", { count: selectedEntries.length })}
              </ActionButton>
              <ActionButton
                kind="destructive"
                disabled={!rejectable.length || busy}
                tooltip={t("inboundGuard.rejectSelectedTooltip")}
                onClick={() => setConfirm({ action: "reject", entries: rejectable, scope: "selected" })}
              >
                {t("inboundGuard.rejectSelected", { count: rejectable.length })}
              </ActionButton>
              <Divider orientation="vertical" flexItem />
              <ActionButton
                disabled={!shown.length || busy}
                tooltip={t("inboundGuard.approveAllTooltip")}
                onClick={() => setConfirm({ action: "approve", entries: shown, scope: "all" })}
              >
                {t("inboundGuard.approveAll", { count: shown.length })}
              </ActionButton>
              <ActionButton
                kind="destructive"
                disabled={!rejectableShown.length || busy}
                tooltip={t("inboundGuard.rejectAllTooltip")}
                onClick={() => setConfirm({ action: "reject", entries: rejectableShown, scope: "all" })}
              >
                {t("inboundGuard.rejectAll", { count: rejectableShown.length })}
              </ActionButton>
            </>
          )}
        </Stack>
        {!resolved && <Hint>{t("inboundGuard.howToSelect")}</Hint>}
        {result && (
          <Alert severity={result.severity} onClose={() => setResult(null)} sx={{ py: 0.25 }}>
            {result.text}
            {result.reasons.map((r) => (
              <div key={r}>{r}</div>
            ))}
          </Alert>
        )}
        {error && (
          <Alert severity="error" onClose={() => setError(null)} sx={{ py: 0.25 }}>
            {error}
          </Alert>
        )}
        <TableContainer sx={{ flex: opened ? "1 1 45%" : "1 1 auto", minHeight: 160, border: 1, borderColor: "divider", borderRadius: 1 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                {!resolved && (
                  <TableCell padding="checkbox">
                    <Checkbox
                      size="small"
                      checked={allChecked}
                      indeterminate={someChecked}
                      onChange={() => setSelected((s) => toggleAll(s, orderedKeys))}
                      inputProps={{ "aria-label": t("inboundGuard.col.selectAll") }}
                    />
                  </TableCell>
                )}
                <TableCell>{t("inboundGuard.col.change")}</TableCell>
                {resolved && <TableCell>{t("inboundGuard.col.decision")}</TableCell>}
                {resolved && <TableCell>{t("inboundGuard.col.decided")}</TableCell>}
                {resolved && <TableCell>{t("inboundGuard.col.by")}</TableCell>}
                {resolved && <TableCell>{t("inboundGuard.col.note")}</TableCell>}
                <TableCell>{t("inboundGuard.col.flagged")}</TableCell>
                {!resolved && <TableCell>{t("inboundGuard.col.changeDate")}</TableCell>}
                <TableCell>{t("inboundGuard.col.source")}</TableCell>
                {!resolved && <TableCell>{t("inboundGuard.col.modeThen")}</TableCell>}
                {!resolved && <TableCell>{t("inboundGuard.col.verdict")}</TableCell>}
                {!resolved && <TableCell>{t("inboundGuard.col.severity")}</TableCell>}
                <TableCell align="right">{t("inboundGuard.col.findings")}</TableCell>
                {!resolved && <TableCell>{t("inboundGuard.col.size")}</TableCell>}
                {!resolved && <TableCell>{t("inboundGuard.col.approveDoes")}</TableCell>}
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((entry) => {
                const { item } = entry;
                const size = sizeText(item);
                const decision = decisionOf(item);
                return (
                  <TableRow
                    key={entry.key}
                    hover
                    selected={openKey === entry.key}
                    onClick={(e) => onRowClick(e, entry.key)}
                    data-testid={`row-${entry.key}`}
                    sx={{ cursor: "pointer", userSelect: "none" }}
                  >
                    {!resolved && (
                      <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          size="small"
                          checked={selected.has(entry.key)}
                          onChange={(e) => onCheck(e, entry.key)}
                          inputProps={{ "aria-label": t("inboundGuard.col.selectOne", { subject: item.subject }) }}
                        />
                      </TableCell>
                    )}
                    <TableCell sx={{ maxWidth: 340 }}>
                      <Stack direction="row" spacing={0.75} alignItems="center">
                        <Typography variant="body2" noWrap title={item.subject}>
                          {item.subject}
                        </Typography>
                        {!resolved && isOwnDecision(item) && <OwnDecisionChip t={t} />}
                      </Stack>
                    </TableCell>
                    {resolved && (
                      <TableCell>
                        <Tooltip title={decision === "clear" || decision === "allow" ? t("inboundGuard.decision.clearTooltip") : ""}>
                          <Chip
                            size="small"
                            variant="outlined"
                            color={DECISION_TONE[decision]}
                            label={t(`inboundGuard.decision.${decision}`)}
                            sx={{ height: 18 }}
                          />
                        </Tooltip>
                      </TableCell>
                    )}
                    {resolved && (
                      <TableCell>
                        <When iso={item.decided_at} />
                      </TableCell>
                    )}
                    {resolved && <TableCell>{item.decided_by || "—"}</TableCell>}
                    {resolved && (
                      <TableCell sx={{ maxWidth: 260 }}>
                        <Typography variant="body2" noWrap title={item.decision_note || ""}>
                          {item.decision_note || "—"}
                        </Typography>
                      </TableCell>
                    )}
                    <TableCell>
                      <When iso={item.flagged_at} />
                    </TableCell>
                    {!resolved && (
                      <TableCell>
                        <ChangeDateCell item={item} t={t} />
                      </TableCell>
                    )}
                    <TableCell>{sourceLabel(item.source)}</TableCell>
                    {!resolved && <TableCell>{item.mode_at_record || "—"}</TableCell>}
                    {!resolved && (
                      <TableCell>
                        <Chip
                          size="small"
                          color={isBlocked(item) ? "error" : "warning"}
                          variant={isBlocked(item) ? "filled" : "outlined"}
                          label={t(`inboundGuard.verdict.${isBlocked(item) ? "block" : "hold"}`)}
                          sx={{ height: 18 }}
                        />
                      </TableCell>
                    )}
                    {!resolved && (
                      <TableCell>
                        {item.top_severity ? (
                          <Chip size="small" label={item.top_severity} color={SEVERITY_TONE[item.top_severity] || "default"} sx={{ height: 18 }} />
                        ) : (
                          "—"
                        )}
                      </TableCell>
                    )}
                    <TableCell align="right">{(item.findings || []).length}</TableCell>
                    {!resolved && (
                      <TableCell>
                        <Tooltip title={size.tooltip}>
                          <span>{size.text}</span>
                        </Tooltip>
                      </TableCell>
                    )}
                    {!resolved && (
                      <TableCell>
                        <Tooltip title={item.what_approve_does || ""}>
                          <span>{effectShort(item.approve_effect)}</span>
                        </Tooltip>
                      </TableCell>
                    )}
                  </TableRow>
                );
              })}
              {!loading && shown.length === 0 && (
                <TableRow>
                  <TableCell colSpan={columns}>
                    <Typography variant="body2" color="text.secondary">
                      {entries.length
                        ? t("inboundGuard.emptyFiltered")
                        : t(resolved ? "inboundGuard.emptyResolved" : "inboundGuard.emptyOpen")}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <Stack direction="row" spacing={1} alignItems="center">
          {loading && <CircularProgress size={16} />}
          <Hint>{t(resolved ? "inboundGuard.loadedResolved" : "inboundGuard.loaded", { loaded: entries.length, total })}</Hint>
          {moreToLoad && (
            <ActionButton kind="link" onClick={loadMore} disabled={loading}>
              {t("inboundGuard.loadMore")}
            </ActionButton>
          )}
        </Stack>
        <Divider />
        <Box sx={opened ? { flex: "1 1 45%", overflowY: "auto", minHeight: 120 } : { flex: "0 0 auto" }}>
          {opened ? (
            <ItemDetail entry={opened} resolved={resolved} t={t} />
          ) : (
            <Typography variant="body2" color="text.secondary">
              {t("inboundGuard.clickRow")}
            </Typography>
          )}
        </Box>
        {!resolved && (
          <TextField
            size="small"
            label={t("inboundGuard.note")}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            helperText={needsNote ? t("inboundGuard.ownDecision.noteNeeded") : undefined}
            fullWidth
          />
        )}
      </DialogContent>
      <DialogActions>
        {!resolved && opened && opened.item.can_reject && (
          <ActionButton kind="destructive" onClick={() => decideOne("reject")} loading={busy}>
            {t("inboundGuard.reject")}
          </ActionButton>
        )}
        {!resolved && opened && (
          <ActionButton
            kind="primary"
            onClick={() => decideOne("approve")}
            loading={busy}
            disabled={needsNote}
            tooltip={
              needsNote
                ? t("inboundGuard.ownDecision.noteNeeded")
                : opened.item.what_approve_does
                  ? t("inboundGuard.approveTooltip", { effect: opened.item.what_approve_does })
                  : ""
            }
          >
            {isBlocked(opened.item) ? t("inboundGuard.approveDespiteBlock") : effectButton(opened.item.approve_effect)}
          </ActionButton>
        )}
        <ActionButton onClick={onClose}>{t("inboundGuard.close")}</ActionButton>
      </DialogActions>
      <ConfirmActionDialog
        open={Boolean(confirm)}
        {...confirmView}
        busy={busy}
        confirmDisabled={Boolean(confirmDisabled)}
        onConfirm={runConfirmed}
        onClose={() => {
          setConfirm(null);
          setIncludeBlocks(false);
        }}
      />
    </Dialog>
  );
}
