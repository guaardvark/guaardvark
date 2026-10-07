// The inbound guard in Settings → Agents: one switch for how it acts, and the
// list of what it found. The guard itself lives in scripts/inbound_guard; this
// only shows its verdicts and records a person's answer to them.

import React, { useCallback, useEffect, useState } from "react";
import { Alert, CircularProgress } from "@mui/material";
import { ActionButton, ChoiceChips, Cluster, Hint, Line, StatusPill } from "./ui";
import { inboundGuardService } from "../../api/inboundGuardService";
import InboundGuardReviewDialog from "./InboundGuardReviewDialog";
import { formatExact } from "./inboundGuardReview";

const MODES = [
  { value: "off", label: "Off", tooltip: "Nothing is read. Code lands exactly as before." },
  {
    value: "observe",
    label: "Observe",
    tooltip:
      "Changes are read and recorded: Guaardvark's own edits, git merges, fetches and branch moves, and files the " +
      "sweep finds changed. Nothing is held or refused.",
  },
  {
    value: "enforce",
    label: "Enforce",
    tooltip:
      "Guaardvark's own risky edits wait for your approval and clearly malicious ones are refused; a git merge " +
      "commit the guard holds is refused until approved. Fast-forward pulls, rebase, reset, cherry-pick, am, " +
      "--no-verify and Interconnector deliveries are never refused: they are recorded after they land.",
  },
];

const openLabel = (state) => {
  const by = state?.open_by;
  if (!by) return state?.open ? `${state.open} open` : "nothing open";
  if (by.waiting) return `${by.waiting} waiting`;
  const toRead = (by.landed || 0) + (by.on_disk || 0);
  return toRead ? `${toRead} recorded to read` : "nothing open";
};

const openTooltip = (by) =>
  by
    ? `Waiting, nothing applied yet: ${by.waiting}. Landed, recorded only: ${by.landed}. On disk, found by the ` +
      `sweep: ${by.on_disk}. Blocked (any of these): ${by.blocked}. Git records are listed in the review.`
    : "";

const describeSweep = (sweep) => {
  if (!sweep?.at) return "No sweep yet.";
  if (sweep.skipped) return `Sweep skipped ${formatExact(sweep.at)}: ${sweep.skipped}.`;
  const parts = [`Last sweep ${formatExact(sweep.at)}: ${sweep.files} files`];
  if (sweep.seeded) parts.push(`first read of the code (${sweep.audit_findings || 0} existing findings to read once)`);
  else parts.push(`${sweep.changed} changed`);
  if (sweep.held) parts.push(`${sweep.held} raised for review (already on disk)`);
  return parts.join(", ") + ".";
};

export default function InboundGuardSection() {
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
  const scanners = state?.providers?.scanners || [];

  return (
    <Cluster
      label="Inbound guard"
      help={
        "Reads code changes as they come into this install: Guaardvark's own edits before they land, git merge " +
        "commits before they are made, fetches and branch moves after, and changed files at each sweep. In Enforce, " +
        "risky own edits and held merge commits wait for approval; everything else is recorded after it lands."
      }
    >
      <Line>
        <ChoiceChips options={MODES} value={state?.mode || "off"} onChange={changeMode} ariaLabel="Inbound guard mode" />
        <span title={openTooltip(state?.open_by)}>
          <StatusPill tone={state?.open_by?.waiting > 0 ? "warn" : "neutral"} label={openLabel(state)} />
        </span>
        <ActionButton kind="link" onClick={() => setReviewOpen(true)}>
          Review findings
        </ActionButton>
        <ActionButton
          onClick={sweepNow}
          loading={sweeping}
          disabled={(state?.mode || "off") === "off"}
          tooltip="Read every watched file that changed since the last sweep: this checkout's code, ComfyUI custom nodes, extensions, the agent's notes."
        >
          Sweep now
        </ActionButton>
      </Line>
      <Hint>{describeSweep(state?.sweep)}</Hint>
      <Hint>
        Git hooks {state?.git?.installed ? `installed (${state.git.mode})` : "not installed — run scripts/install_hooks.sh"}
        {scanners.length ? ` · extra checks from ${scanners.join(", ")}` : ""}
      </Hint>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
      <InboundGuardReviewDialog open={reviewOpen} onClose={() => setReviewOpen(false)} onChanged={load} />
    </Cluster>
  );
}
