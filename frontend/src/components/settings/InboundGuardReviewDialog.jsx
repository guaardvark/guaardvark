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
import { ActionButton, ConfirmActionDialog, Hint } from "./ui";
import { inboundGuardService } from "../../api/inboundGuardService";
import { approveHeldChange, rejectHeldChange } from "../../api/heldChanges";
import { FindingList, excerptLabelFor } from "./InboundFindingList";
import {
  EFFECT_BUTTON,
  EFFECT_SHORT,
  SEVERITIES,
  TABS,
  approvalSummary,
  categoryOf,
  changeDate,
  formatExact,
  formatRelative,
  inTab,
  isBlocked,
  nextSelection,
  sourceLabel,
  toEntries,
  toggleAll,
  viewEntries,
} from "./inboundGuardReview";

const PAGE = 200;
const SEVERITY_TONE = { critical: "error", high: "error", medium: "warning", low: "info", info: "default" };
const CATEGORY_LABEL = {
  waiting: "Waiting — nothing applied yet",
  landed: "Landed — recorded only",
  ondisk: "On disk — found by sweep",
};
const SORTS = [
  { value: "newest", label: "Newest flagged" },
  { value: "oldest", label: "Oldest flagged" },
  { value: "severity", label: "Severity" },
  { value: "source", label: "Source" },
];

const When = ({ iso }) =>
  iso ? (
    <Tooltip title={formatExact(iso)}>
      <span>{formatRelative(iso)}</span>
    </Tooltip>
  ) : (
    "—"
  );

const ChangeDateCell = ({ item }) => {
  const date = changeDate(item);
  if (!date) return "—";
  return (
    <Tooltip title={`${date.label}: ${formatExact(date.iso)}${date.detail ? ` · ${date.detail}` : ""}`}>
      <span>{formatRelative(date.iso)}</span>
    </Tooltip>
  );
};

const describeCounts = (counts) =>
  Object.entries(counts)
    .map(([k, n]) => `${n} ${k}`)
    .join(", ") || "none";

const resultMessage = (body, verb) => {
  const parts = [`${body[verb] || 0} ${verb}`];
  if (body.skipped) parts.push(`${body.skipped} skipped`);
  if (body.failed) parts.push(`${body.failed} failed`);
  const reasons = (body.results || [])
    .filter((r) => r.outcome === "failed" || r.outcome === "skipped")
    .slice(0, 5)
    .map((r) => `${r.kind === "git" ? r.digest : `#${r.id}`}: ${r.reason}`);
  return { text: parts.join(", "), reasons, severity: body.failed ? "warning" : "success" };
};

/** The item opened below the list: everything a person needs to decide on it. */
const ItemDetail = ({ entry }) => {
  const { item } = entry;
  const date = changeDate(item);
  return (
    <Stack spacing={1.25}>
      <Typography variant="subtitle2" sx={{ wordBreak: "break-all" }}>
        {item.subject}
      </Typography>
      <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
        <Chip size="small" label={isBlocked(item) ? "Blocked" : "Hold"} color={isBlocked(item) ? "error" : "warning"} />
        <Chip size="small" variant="outlined" label={CATEGORY_LABEL[categoryOf(item)]} />
        <Chip size="small" variant="outlined" label={sourceLabel(item.source)} />
        <Chip size="small" variant="outlined" label={`guard was in ${item.mode_at_record || "unknown"} mode`} />
      </Stack>
      <Hint>
        Flagged {formatExact(item.flagged_at) || "at an unknown time"}
        {item.seen > 1 ? ` · recorded ${item.seen} times (${(item.sources || []).map(sourceLabel).join(", ")}), last ${formatExact(item.last_seen_at)}` : ""}
      </Hint>
      <Hint>
        {date
          ? `${date.label === "file written" ? "File last written" : "Newest commit"} ${formatExact(date.iso)}${date.detail ? ` · ${date.detail}` : ""}`
          : entry.kind === "git"
            ? "Commit dates unknown: this record names no commits this clone has."
            : "No file or commit date for this kind of item."}
      </Hint>
      <Hint>
        {item.files ?? 0} file(s), +{item.added_lines ?? 0} line(s) · digest {item.digest}
        {item.pending_fix_id ? ` · pending fix #${item.pending_fix_id}${item.fix_status ? ` (${item.fix_status})` : ""}` : ""}
      </Hint>
      <Alert severity={isBlocked(item) ? "warning" : "info"} sx={{ py: 0.25 }}>
        <strong>Approving:</strong> {item.what_approve_does}. {item.approve_note}
        <br />
        <strong>Rejecting:</strong> {item.what_reject_does}.
      </Alert>
      <Hint>
        Checks that ran: {(item.scanners_run || []).map((s) => `${s.name} (${s.by})`).join(" · ") || "unknown"}
        {(item.errors || []).length ? ` · notes: ${item.errors.join("; ")}` : ""}
      </Hint>
      <FindingList findings={item.findings} excerptLabel={excerptLabelFor(item.source)} />
    </Stack>
  );
};

/**
 * Every open inbound-guard finding, with selection and batch decisions.
 *
 * Plain click opens a row; Ctrl/Cmd-click or its checkbox toggles it; Shift-click
 * selects the range from the last row clicked. Approve all covers the rows shown
 * under the current tab and filters, and leaves blocks out unless they are ticked.
 */
export default function InboundGuardReviewDialog({ open, onClose, onChanged }) {
  const [data, setData] = useState({ scans: [], git: [], scans_total: 0, git_total: 0 });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [tab, setTab] = useState("all");
  const [minSeverity, setMinSeverity] = useState("");
  const [source, setSource] = useState("");
  const [sort, setSort] = useState("newest");
  const [selected, setSelected] = useState(() => new Set());
  const [anchor, setAnchor] = useState(null);
  const [openKey, setOpenKey] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [includeBlocks, setIncludeBlocks] = useState(false);
  const [result, setResult] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await inboundGuardService.listScans("open", PAGE, { git: true, gitLimit: PAGE });
      setData(res?.data || { scans: [], git: [] });
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMore = async () => {
    setLoading(true);
    try {
      const res = await inboundGuardService.listScans("open", PAGE, {
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
      setOpenKey(null);
      setSelected(new Set());
      setAnchor(null);
      setResult(null);
      setError(null);
      load();
    }
  }, [open, load]);

  const entries = useMemo(() => toEntries(data), [data]);
  const shown = useMemo(
    () => viewEntries(entries, { tab, minSeverity, source, sort }),
    [entries, tab, minSeverity, source, sort],
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
  const opened = entries.find((e) => e.key === openKey) || null;
  const allChecked = orderedKeys.length > 0 && orderedKeys.every((k) => selected.has(k));
  const someChecked = !allChecked && orderedKeys.some((k) => selected.has(k));
  const moreToLoad = data.scans.length < (data.scans_total || 0) || data.git.length < (data.git_total || 0);

  const onRowClick = (event, key) => {
    if (event.shiftKey) {
      setSelected((s) => nextSelection(s, orderedKeys, key, anchor, { shift: true }));
    } else if (event.ctrlKey || event.metaKey) {
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

  const runBatch = async () => {
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
        await finish(resultMessage(res?.data || {}, "approved"));
      } else {
        const res = await inboundGuardService.rejectBatch({
          ids: confirm.entries.filter((e) => e.kind === "scan").map((e) => e.item.id),
          note,
        });
        await finish(resultMessage(res?.data || {}, "rejected"));
      }
    } catch (err) {
      setError(err.message);
      setConfirm(null);
    } finally {
      setBusy(false);
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
      await finish({ text: decision === "approve" ? "Approved 1 item" : "Rejected 1 item", reasons: [], severity: "success" });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const tabCount = (value) => entries.filter((e) => inTab(e.item, value)).length;
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

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xl" fullWidth PaperProps={{ sx: { height: "90vh" } }}>
      <DialogTitle>Inbound guard findings</DialogTitle>
      <DialogContent dividers sx={{ display: "flex", flexDirection: "column", gap: 1, minHeight: 0 }}>
        <Hint>
          Findings are written by fixed rules running on this machine: Guaardvark&apos;s own, and any an extension
          adds (each finding says which). Quoted lines come from the change itself. The guard is in{" "}
          {data.mode || "unknown"} mode{data.git_mode ? `; the git hooks are in ${data.git_mode} mode` : ""}.
        </Hint>
        <Tabs value={tab} onChange={(_e, v) => setTab(v)} variant="scrollable" sx={{ minHeight: 36 }}>
          {TABS.map((t) => (
            <Tab key={t.value} value={t.value} label={`${t.label} (${tabCount(t.value)})`} sx={{ minHeight: 36, py: 0.5 }} />
          ))}
        </Tabs>
        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
          <TextField select size="small" label="Severity" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value)} sx={{ minWidth: 150 }}>
            <MenuItem value="">Any severity</MenuItem>
            {SEVERITIES.map((s) => (
              <MenuItem key={s} value={s}>
                {s} or worse
              </MenuItem>
            ))}
          </TextField>
          <TextField select size="small" label="Source" value={source} onChange={(e) => setSource(e.target.value)} sx={{ minWidth: 190 }}>
            <MenuItem value="">Any source</MenuItem>
            {sources.map((s) => (
              <MenuItem key={s} value={s}>
                {sourceLabel(s)}
              </MenuItem>
            ))}
          </TextField>
          <TextField select size="small" label="Sort" value={sort} onChange={(e) => setSort(e.target.value)} sx={{ minWidth: 160 }}>
            {SORTS.map((s) => (
              <MenuItem key={s.value} value={s.value}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ flex: 1 }} />
          <Typography variant="caption" color="text.secondary">
            {selectedEntries.length} selected
          </Typography>
          <ActionButton
            kind="primary"
            disabled={!selectedEntries.length || busy}
            onClick={() => setConfirm({ action: "approve", entries: selectedEntries, scope: "selected" })}
          >
            Approve selected ({selectedEntries.length})
          </ActionButton>
          <ActionButton
            kind="destructive"
            disabled={!rejectable.length || busy}
            tooltip="Git records cannot be rejected here; they are left out."
            onClick={() => setConfirm({ action: "reject", entries: rejectable, scope: "selected" })}
          >
            Reject selected ({rejectable.length})
          </ActionButton>
          <ActionButton
            disabled={!shown.length || busy}
            tooltip="Every row shown under this tab and these filters. Blocks are left out unless you tick them in the next step."
            onClick={() => setConfirm({ action: "approve", entries: shown, scope: "all" })}
          >
            Approve all ({shown.length})
          </ActionButton>
        </Stack>
        <Hint>Click a row to read it. Tick its box or Ctrl/Cmd-click to select it; Shift-click selects a range.</Hint>
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
        <TableContainer sx={{ flex: "1 1 45%", minHeight: 160, border: 1, borderColor: "divider", borderRadius: 1 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox">
                  <Checkbox
                    size="small"
                    checked={allChecked}
                    indeterminate={someChecked}
                    onChange={() => setSelected((s) => toggleAll(s, orderedKeys))}
                    inputProps={{ "aria-label": "Select every row shown" }}
                  />
                </TableCell>
                <TableCell>Change</TableCell>
                <TableCell>Flagged</TableCell>
                <TableCell>File / commit date</TableCell>
                <TableCell>Source</TableCell>
                <TableCell>Mode then</TableCell>
                <TableCell>Top severity</TableCell>
                <TableCell align="right">Findings</TableCell>
                <TableCell>What approve does</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {shown.map((entry) => {
                const { item } = entry;
                return (
                  <TableRow
                    key={entry.key}
                    hover
                    selected={openKey === entry.key}
                    onClick={(e) => onRowClick(e, entry.key)}
                    data-testid={`row-${entry.key}`}
                    sx={{ cursor: "pointer", userSelect: "none" }}
                  >
                    <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        size="small"
                        checked={selected.has(entry.key)}
                        onChange={(e) => onCheck(e, entry.key)}
                        inputProps={{ "aria-label": `Select ${item.subject}` }}
                      />
                    </TableCell>
                    <TableCell sx={{ maxWidth: 360 }}>
                      <Stack direction="row" spacing={0.75} alignItems="center">
                        {isBlocked(item) && <Chip size="small" color="error" label="block" sx={{ height: 18 }} />}
                        <Typography variant="body2" noWrap title={item.subject}>
                          {item.subject}
                        </Typography>
                      </Stack>
                    </TableCell>
                    <TableCell>
                      <When iso={item.flagged_at} />
                    </TableCell>
                    <TableCell>
                      <ChangeDateCell item={item} />
                    </TableCell>
                    <TableCell>{sourceLabel(item.source)}</TableCell>
                    <TableCell>{item.mode_at_record || "—"}</TableCell>
                    <TableCell>
                      {item.top_severity ? (
                        <Chip size="small" label={item.top_severity} color={SEVERITY_TONE[item.top_severity] || "default"} sx={{ height: 18 }} />
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell align="right">{(item.findings || []).length}</TableCell>
                    <TableCell>
                      <Tooltip title={item.what_approve_does || ""}>
                        <span>{EFFECT_SHORT[item.approve_effect] || "—"}</span>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                );
              })}
              {!loading && shown.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9}>
                    <Typography variant="body2" color="text.secondary">
                      {entries.length ? "Nothing under this tab and these filters." : "Nothing is open."}
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
        <Stack direction="row" spacing={1} alignItems="center">
          {loading && <CircularProgress size={16} />}
          <Hint>
            {entries.length} loaded of {(data.scans_total || 0) + (data.git_total || 0)} open
          </Hint>
          {moreToLoad && (
            <ActionButton kind="link" onClick={loadMore} disabled={loading}>
              Load more
            </ActionButton>
          )}
        </Stack>
        <Divider />
        <Box sx={{ flex: "1 1 45%", overflowY: "auto", minHeight: 120 }}>
          {opened ? (
            <ItemDetail entry={opened} />
          ) : (
            <Typography variant="body2" color="text.secondary">
              Click a row to read what the guard found.
            </Typography>
          )}
        </Box>
        <TextField size="small" label="Note (kept with your decision)" value={note} onChange={(e) => setNote(e.target.value)} fullWidth />
      </DialogContent>
      <DialogActions>
        {opened && opened.item.can_reject && (
          <ActionButton kind="destructive" onClick={() => decideOne("reject")} loading={busy}>
            Reject
          </ActionButton>
        )}
        {opened && (
          <ActionButton
            kind="primary"
            onClick={() => decideOne("approve")}
            loading={busy}
            tooltip={opened.item.what_approve_does ? `Approving will ${opened.item.what_approve_does}.` : ""}
          >
            {isBlocked(opened.item) ? "Approve despite the block" : EFFECT_BUTTON[opened.item.approve_effect] || "Approve"}
          </ActionButton>
        )}
        <ActionButton onClick={onClose}>Close</ActionButton>
      </DialogActions>
      <ConfirmActionDialog
        open={Boolean(confirm)}
        title={
          confirm?.action === "reject"
            ? `Reject ${confirm.entries.length} item(s)?`
            : `Approve ${summary?.count || 0} item(s)?`
        }
        description={
          confirm?.action === "reject"
            ? "Each item is rejected on its own. Git records are not in this list: they cannot be rejected here."
            : summary?.count === 0
              ? "Every item here is blocked, and blocks are left out. Tick below to include them, or cancel."
              : "Each item is approved on its own, in order. One that cannot be approved stays open, and the reason is shown."
        }
        facts={
          summary
            ? [
                { label: "Items", value: `${summary.count} of ${summary.total} ${confirm.scope === "all" ? "shown" : "selected"}` },
                { label: "By kind", value: describeCounts(summary.byKind) },
                { label: "By source", value: describeCounts(summary.bySource) },
                { label: "Highest severity", value: summary.highest || "none" },
                {
                  label: "What approving does",
                  value: (
                    <Stack component="span">
                      {summary.effects.map((e) => (
                        <span key={e.text}>
                          {e.count} × {e.text}
                        </span>
                      ))}
                    </Stack>
                  ),
                },
                {
                  label: "Blocked",
                  value: summary.blocks
                    ? includeBlocks
                      ? `${summary.blocks} included`
                      : `${summary.blocksLeftOut} left out`
                    : "none",
                },
              ]
            : [
                {
                  label: "What rejecting does",
                  value: (
                    <Stack component="span">
                      {rejectEffects.map(([text, count]) => (
                        <span key={text}>
                          {count} × {text}
                        </span>
                      ))}
                    </Stack>
                  ),
                },
              ]
        }
        extra={
          summary && summary.blocks > 0 ? (
            <FormControlLabel
              control={<Checkbox size="small" checked={includeBlocks} onChange={(e) => setIncludeBlocks(e.target.checked)} />}
              label={`Also approve the ${summary.blocks} blocked item(s). Read every finding first.`}
            />
          ) : null
        }
        keeps={confirm?.scope === "all" ? "rows not shown under this tab or filter" : "rows you did not select"}
        confirmLabel={confirm?.action === "reject" ? `Reject ${confirm?.entries.length || 0}` : `Approve ${summary?.count || 0}`}
        busy={busy}
        onConfirm={runBatch}
        onClose={() => {
          setConfirm(null);
          setIncludeBlocks(false);
        }}
      />
    </Dialog>
  );
}
