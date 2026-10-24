// Self-improvement's switches, the self-check, and the pending fixes: the
// Self-improvement page's controls (they lived in Settings → Agents before).
import React, { useState, useEffect, useCallback } from "react";
import { Box } from "@mui/material";
import { PlayArrow as PlayIcon } from "@mui/icons-material";
import FixesModal from "../settings/FixesModal";
import ScanProgressModal from "../settings/ScanProgressModal";
import { ActionButton, Cluster, Hint, Line, Sep, SettingChip, StatusPill } from "../settings/ui";
import { selfImprovementService } from "../../api/selfImprovementService";
import { uiLocale, useUiTranslation } from "../../i18n";
import { runStatusLabel } from "./SelfImprovementRuns";

export default function SelfImprovementControls() {
  const { t } = useUiTranslation();
  const [siStatus, setSiStatus] = useState(null);
  const [waitingFixes, setWaitingFixes] = useState(null);
  const [scanOpen, setScanOpen] = useState(false);
  const [scanInBackground, setScanInBackground] = useState(false);
  const [scanCancelled, setScanCancelled] = useState(false);
  const [fixesOpen, setFixesOpen] = useState(false);

  const fetchStatus = useCallback(async () => {
    const [statusRes, fixesRes] = await Promise.allSettled([
      selfImprovementService.getStatus(),
      selfImprovementService.countWaitingFixes(),
    ]);
    if (statusRes.status === "fulfilled") setSiStatus(statusRes.value?.data);
    if (fixesRes.status === "fulfilled") setWaitingFixes(Number(fixesRes.value?.data?.waiting) || 0);
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleToggleSelfImprovement = async () => {
    try {
      await selfImprovementService.toggle(!siStatus?.enabled);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle self-improvement:", err);
    }
  };

  const handleToggleCodebaseLock = async () => {
    try {
      await selfImprovementService.lockCodebase(!siStatus?.codebase_locked);
      fetchStatus();
    } catch (err) {
      console.error("Failed to toggle codebase lock:", err);
    }
  };

  const handleOpenScan = () => {
    setScanInBackground(false);
    setScanCancelled(false);
    setScanOpen(true);
  };

  // Stable reference — the modal depends on this in its main effect, and a
  // fresh closure on every render would re-dispatch the scan.
  const handleScanComplete = useCallback((run) => {
    fetchStatus();
    setScanInBackground(false);
    if (run?.status === "cancelled") {
      setScanCancelled(true);
      return;
    }
    const proposedAnything =
      (run?.changes_made && run.changes_made.length > 0) ||
      run?.status === "success";
    if (proposedAnything) {
      setScanOpen(false);
      setFixesOpen(true);
    }
  }, [fetchStatus]);

  const runs = siStatus?.total_fixes || 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.25 }}>
      <Cluster
        label={t("selfImprovement.controls.title")}
        help={t("selfImprovement.controls.help")}
      >
        <Line>
          <SettingChip
            label={t("selfImprovement.controls.enabledChip")}
            on={!!siStatus?.enabled}
            onToggle={handleToggleSelfImprovement}
            disabled={!siStatus}
            tooltip={t("selfImprovement.controls.enabledTooltip")}
          />
          <SettingChip
            label={t("selfImprovement.controls.lockedChip")}
            on={!!siStatus?.codebase_locked}
            onToggle={handleToggleCodebaseLock}
            disabled={!siStatus}
            tooltip={siStatus?.codebase_locked ? t("selfImprovement.controls.lockedOnTooltip") : t("selfImprovement.controls.lockedOffTooltip")}
          />
          <Sep />
          <ActionButton
            onClick={handleOpenScan}
            loading={scanOpen}
            disabled={!siStatus?.enabled || !!siStatus?.codebase_locked}
            startIcon={<PlayIcon />}
            tooltip={
              !siStatus?.enabled
                ? t("selfImprovement.controls.turnOnFirst")
                : siStatus?.codebase_locked
                  ? t("selfImprovement.controls.unlockFirst")
                  : t("selfImprovement.controls.runTooltip")
            }
          >
            {t("selfImprovement.controls.runSelfCheck")}
          </ActionButton>
        </Line>
        <Hint>
          {siStatus?.last_run
            ? t("selfImprovement.controls.lastRun", {
                date: new Date(siStatus.last_run.timestamp).toLocaleString(uiLocale()),
                status: runStatusLabel(siStatus.last_run.status),
                count: runs,
              })
            : siStatus
              ? t("selfImprovement.controls.noRuns")
              : t("selfImprovement.controls.readingStatus")}
          {siStatus?.codebase_locked ? ` ${t("selfImprovement.controls.lockedNote")}` : ""}
        </Hint>
        {scanCancelled && (
          <StatusPill tone="neutral" label={t("selfImprovement.controls.cancelled")} />
        )}
        {scanInBackground && !scanCancelled && (
          <StatusPill tone="info" label={t("selfImprovement.controls.scanInBackground")} />
        )}
      </Cluster>

      <Cluster
        label={t("selfImprovement.controls.pendingFixes")}
        help={t("selfImprovement.controls.pendingFixesHelp")}
      >
        <Line>
          <StatusPill
            tone={waitingFixes ? "warn" : "neutral"}
            label={waitingFixes == null ? "…" : t("selfImprovement.controls.waiting", { count: waitingFixes })}
          />
          <ActionButton onClick={() => setFixesOpen(true)}>
            {t("selfImprovement.controls.reviewFixes")}
          </ActionButton>
        </Line>
      </Cluster>

      <ScanProgressModal
        open={scanOpen}
        onClose={() => setScanOpen(false)}
        onBackground={() => setScanInBackground(true)}
        onComplete={handleScanComplete}
      />
      <FixesModal
        open={fixesOpen}
        onClose={() => {
          setFixesOpen(false);
          fetchStatus();
        }}
      />
    </Box>
  );
}
