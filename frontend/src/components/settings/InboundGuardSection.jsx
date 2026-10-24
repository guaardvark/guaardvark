// The inbound guard in Settings → Agents: one switch for how it acts, and the
// list of changes it is holding. The guard itself lives in scripts/inbound_guard;
// this only shows its verdicts and records a person's answer to them.

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { ActionButton, ChoiceChips, Cluster, Hint, Line, StatusPill } from "./ui";
import { inboundGuardService } from "../../api/inboundGuardService";
import { approveHeldChange, rejectHeldChange } from "../../api/heldChanges";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const modes = () => [
  { value: "off", label: translate("settingsAccess.inboundGuard.modes.off"), tooltip: translate("settingsAccess.inboundGuard.modes.offTooltip") },
  {
    value: "observe",
    label: translate("settingsAccess.inboundGuard.modes.observe"),
    tooltip: translate("settingsAccess.inboundGuard.modes.observeTooltip"),
  },
  {
    value: "enforce",
    label: translate("settingsAccess.inboundGuard.modes.enforce"),
    tooltip: translate("settingsAccess.inboundGuard.modes.enforceTooltip"),
  },
];

const SEVERITY_TONE = { critical: "error", high: "error", medium: "warning", low: "info", info: "default" };

// The source watch judges each file whole, so its count is the file's length, not lines added.
export const scanSize = (scan) =>
  scan.source?.startsWith("watch")
    ? scan.added_lines
      ? translate("settingsAccess.inboundGuard.scanSizeWholeLines", { files: scan.files, lines: scan.added_lines })
      : translate("settingsAccess.inboundGuard.scanSizeWhole", { files: scan.files })
    : translate("settingsAccess.inboundGuard.scanSize", { files: scan.files, lines: scan.added_lines });

export const FindingList = ({ findings, max = 50 }) => (
  <Stack spacing={0.75}>
    {(findings || []).slice(0, max).map((f, i) => (
      <Box key={`${f.rule}-${f.path}-${f.line}-${i}`}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip size="small" label={f.severity} color={SEVERITY_TONE[f.severity] || "default"} sx={{ height: 18 }} />
          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
            {f.rule} · {f.line ? `${f.path}:${f.line}` : f.path}
          </Typography>
        </Stack>
        <Typography variant="body2" sx={{ ml: 0.5 }}>{f.why}</Typography>
        {f.excerpt && (
          <Typography
            variant="caption"
            component="pre"
            sx={{ ml: 0.5, m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all", color: "text.secondary" }}
          >
            {f.excerpt}
          </Typography>
        )}
      </Box>
    ))}
  </Stack>
);

function ReviewDialog({ open, onClose, onChanged }) {
  const { t } = useUiTranslation();
  const [data, setData] = useState({ scans: [], git: [] });
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await inboundGuardService.listScans("open");
      setData(res?.data || { scans: [], git: [] });
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      setSelected(null);
      setError(null);
      load();
    }
  }, [open, load]);

  const decide = async (decision, overrideBlock = false) => {
    setBusy(true);
    setError(null);
    try {
      if (decision === "approve") {
        await approveHeldChange(selected, { note, overrideBlock });
      } else {
        await rejectHeldChange(selected, { note });
      }
      setSelected(null);
      setNote("");
      await load();
      onChanged?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const items = [
    ...(data.scans || []).map((item) => ({ kind: "scan", key: `s${item.id}`, item })),
    ...(data.git || []).filter((g) => !g.approved).map((item) => ({ kind: "git", key: `g${item.digest}${item.ts}`, item })),
  ];
  const item = selected?.item;
  const blocked = item?.verdict === "block";

  return (
    <Dialog open={open} onClose={onClose} maxWidth="lg" fullWidth>
      <DialogTitle>{t("settingsAccess.inboundGuard.review.title")}</DialogTitle>
      <DialogContent dividers sx={{ display: "flex", gap: 2, minHeight: 360 }}>
        <Box sx={{ width: 320, flexShrink: 0, overflowY: "auto" }}>
          {loading && <CircularProgress size={18} />}
          {!loading && items.length === 0 && (
            <Typography variant="body2" color="text.secondary">{t("settingsAccess.inboundGuard.review.nothingWaiting")}</Typography>
          )}
          {items.map((entry) => (
            <Box
              key={entry.key}
              onClick={() => setSelected(entry)}
              sx={{
                p: 1,
                mb: 0.5,
                borderRadius: 1,
                cursor: "pointer",
                bgcolor: selected?.key === entry.key ? "action.selected" : "transparent",
                "&:hover": { bgcolor: "action.hover" },
              }}
            >
              <Typography variant="body2" sx={{ wordBreak: "break-all" }}>{entry.item.subject}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("settingsAccess.inboundGuard.review.entrySummary", {
                  verdict: entry.item.verdict,
                  source: entry.item.source,
                  count: (entry.item.findings || []).length,
                })}
                {entry.kind === "git" ? " · git" : ""}
              </Typography>
            </Box>
          ))}
        </Box>
        <Divider orientation="vertical" flexItem />
        <Box sx={{ flex: 1, overflowY: "auto" }}>
          {!item ? (
            <Typography variant="body2" color="text.secondary">{t("settingsAccess.inboundGuard.review.pickChange")}</Typography>
          ) : (
            <Stack spacing={1.5}>
              <Typography variant="subtitle2" sx={{ wordBreak: "break-all" }}>{item.subject}</Typography>
              <Hint>
                {item.verdict === "block" ? t("settingsAccess.inboundGuard.review.blocked") : t("settingsAccess.inboundGuard.review.held")} · {item.source} · {scanSize(item)} ·{" "}
                {t("settingsAccess.inboundGuard.review.digest", { digest: item.digest })}
                {item.pending_fix_id ? ` · ${t("settingsAccess.inboundGuard.review.pendingFix", { id: item.pending_fix_id })}` : ""}
                {selected.kind === "git" ? ` · ${t("settingsAccess.inboundGuard.review.gitApproval")}` : ""}
              </Hint>
              <FindingList findings={item.findings} />
              <TextField size="small" label={t("settingsAccess.inboundGuard.review.note")} value={note} onChange={(e) => setNote(e.target.value)} fullWidth />
              {error && <Alert severity="error">{error}</Alert>}
            </Stack>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        {item && selected.kind === "scan" && (
          <ActionButton kind="destructive" onClick={() => decide("reject")} loading={busy}>{t("settingsAccess.inboundGuard.review.reject")}</ActionButton>
        )}
        {item && (
          <ActionButton
            kind="primary"
            onClick={() => decide("approve", blocked)}
            loading={busy}
            tooltip={blocked ? t("settingsAccess.inboundGuard.review.blockedTooltip") : ""}
          >
            {blocked ? t("settingsAccess.inboundGuard.review.approveDespiteBlock") : item.landable || item.pending_fix_id ? t("settingsAccess.inboundGuard.review.approveAndApply") : t("settingsAccess.inboundGuard.review.approve")}
          </ActionButton>
        )}
        <ActionButton onClick={onClose}>{t("settingsAccess.inboundGuard.review.close")}</ActionButton>
      </DialogActions>
    </Dialog>
  );
}

const describeSweep = (sweep) => {
  if (!sweep?.at) return translate("settingsAccess.inboundGuard.sweep.none");
  const time = new Date(sweep.at).toLocaleTimeString(uiLocale());
  if (sweep.skipped) return translate("settingsAccess.inboundGuard.sweep.skipped", { time, reason: sweep.skipped });
  if (sweep.seeded) {
    const findings = sweep.audit_findings || 0;
    return sweep.held
      ? translate("settingsAccess.inboundGuard.sweep.seededHeld", { time, files: sweep.files, findings, held: sweep.held })
      : translate("settingsAccess.inboundGuard.sweep.seeded", { time, files: sweep.files, findings });
  }
  return sweep.held
    ? translate("settingsAccess.inboundGuard.sweep.changedHeld", { time, files: sweep.files, changed: sweep.changed, held: sweep.held })
    : translate("settingsAccess.inboundGuard.sweep.changed", { time, files: sweep.files, changed: sweep.changed });
};

export default function InboundGuardSection() {
  const { t } = useUiTranslation();
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [sweeping, setSweeping] = useState(false);

  const sweepNow = async () => {
    setSweeping(true);
    try {
      await inboundGuardService.sweep();
    } catch (err) {
      setError(err.message);
    } finally {
      setSweeping(false);
      load();
    }
  };

  const load = useCallback(async () => {
    try {
      const res = await inboundGuardService.getState();
      setState(res?.data || null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const changeMode = async (mode) => {
    const previous = state;
    setState((s) => ({ ...s, mode }));
    try {
      await inboundGuardService.setMode(mode);
      load();
    } catch (err) {
      setState(previous);
      setError(err.message);
    }
  };

  if (!state && !error) return <CircularProgress size={16} />;
  const open = state?.open || 0;
  const scanners = state?.providers?.scanners || [];

  return (
    <Cluster
      label={t("settingsAccess.inboundGuard.label")}
      help={t("settingsAccess.inboundGuard.help")}
    >
      <Line>
        <ChoiceChips options={modes()} value={state?.mode || "off"} onChange={changeMode} ariaLabel={t("settingsAccess.inboundGuard.modeAria")} />
        <StatusPill
          tone={open > 0 ? "warn" : "neutral"}
          label={open > 0 ? t("settingsAccess.inboundGuard.waiting", { count: open }) : t("settingsAccess.inboundGuard.nothingWaiting")}
        />
        <ActionButton kind="link" onClick={() => setReviewOpen(true)}>{t("settingsAccess.inboundGuard.reviewHeld")}</ActionButton>
        <ActionButton
          onClick={sweepNow}
          loading={sweeping}
          disabled={(state?.mode || "off") === "off"}
          tooltip={t("settingsAccess.inboundGuard.sweepTooltip")}
        >
          {t("settingsAccess.inboundGuard.sweepNow")}
        </ActionButton>
      </Line>
      <Hint>{describeSweep(state?.sweep)}</Hint>
      <Hint>
        {state?.git?.installed ? t("settingsAccess.inboundGuard.gitHooksInstalled", { mode: state.git.mode }) : t("settingsAccess.inboundGuard.gitHooksMissing")}
        {scanners.length ? ` · ${t("settingsAccess.inboundGuard.extraChecks", { scanners: scanners.join(", ") })}` : ""}
      </Hint>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <ReviewDialog open={reviewOpen} onClose={() => setReviewOpen(false)} onChanged={load} />
    </Cluster>
  );
}
