// The inbound guard in Settings → Agents: one switch for how it acts, and the
// list of what it found. The guard itself lives in scripts/inbound_guard;
// this only shows its verdicts and records a person's answer to them.

import React, { useCallback, useEffect, useState } from "react";
import { Alert, CircularProgress } from "@mui/material";
import { ActionButton, ChoiceChips, Cluster, Hint, Line, StatusPill } from "./ui";
import { inboundGuardService } from "../../api/inboundGuardService";
import InboundGuardReviewDialog from "./InboundGuardReviewDialog";
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

// The source watch judges each file whole, so its count is the file's length, not lines added.
export const scanSize = (scan) =>
  scan.source?.startsWith("watch")
    ? scan.added_lines
      ? translate("settingsAccess.inboundGuard.scanSizeWholeLines", { files: scan.files, lines: scan.added_lines })
      : translate("settingsAccess.inboundGuard.scanSizeWhole", { files: scan.files })
    : translate("settingsAccess.inboundGuard.scanSize", { files: scan.files, lines: scan.added_lines });

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

// "Waiting" is only what has not been applied yet; a change that landed while the
// guard observed is a record to read, not something held.
const openLabel = (state) => {
  const by = state?.open_by;
  if (!by) {
    return state?.open
      ? translate("settingsAccess.inboundGuard.waiting", { count: state.open })
      : translate("settingsAccess.inboundGuard.nothingWaiting");
  }
  if (by.waiting) return translate("settingsAccess.inboundGuard.waiting", { count: by.waiting });
  const toRead = (by.landed || 0) + (by.on_disk || 0);
  return toRead
    ? translate("settingsAccess.inboundGuard.recordedToRead", { count: toRead })
    : translate("settingsAccess.inboundGuard.nothingOpen");
};

const openTooltip = (by) =>
  by
    ? translate("settingsAccess.inboundGuard.openTooltip", {
        waiting: by.waiting, landed: by.landed, onDisk: by.on_disk, blocked: by.blocked,
      })
    : "";

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
        <span title={openTooltip(state?.open_by)}>
          <StatusPill tone={state?.open_by?.waiting > 0 || (!state?.open_by && open > 0) ? "warn" : "neutral"} label={openLabel(state)} />
        </span>
        <ActionButton kind="link" onClick={() => setReviewOpen(true)}>{t("settingsAccess.inboundGuard.reviewFindings")}</ActionButton>
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
      <InboundGuardReviewDialog open={reviewOpen} onClose={() => setReviewOpen(false)} onChanged={load} />
    </Cluster>
  );
}
