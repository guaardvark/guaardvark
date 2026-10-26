/**
 * PlanCard - a plan written in plan mode, inside the chat.
 *
 * Shows the goal, the steps with their live status, and what the person can
 * do next: approve it (with a card before each action, or allowing the
 * plan's own tools for the run), keep planning, edit it, or reject it. A run
 * that paused at a step offers retry, skip, plan the rest again, or drop it.
 */
import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  MenuItem,
  Paper,
  TextField,
  Typography,
} from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import RadioButtonUncheckedIcon from "@mui/icons-material/RadioButtonUnchecked";
import RedoIcon from "@mui/icons-material/Redo";
import StopCircleIcon from "@mui/icons-material/StopCircle";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ChecklistIcon from "@mui/icons-material/Checklist";
import { ActionButton } from "../settings/ui";
import {
  approvePlan,
  continuePlan,
  editPlan,
  getPlan,
  rejectPlan,
  replanPlan,
} from "../../api/planModeService";
import { useUiTranslation } from "../../i18n";

// Step kinds the backend knows (plan_mode_data.STEP_KINDS); values stay English.
const STEP_KINDS = ["write", "file", "code_change", "image", "video", "audio", "desktop", "lookup", "memory", "other"];
const OPEN_STATUSES = ["awaiting_approval", "executing", "paused"];

const StepIcon = ({ status }) => {
  switch (status) {
    case "done":
      return <CheckCircleIcon color="success" sx={{ fontSize: 18 }} />;
    case "running":
      return <CircularProgress size={16} />;
    case "failed":
      return <ErrorIcon color="error" sx={{ fontSize: 18 }} />;
    case "skipped":
      return <RedoIcon color="disabled" sx={{ fontSize: 18 }} />;
    case "stopped":
      return <StopCircleIcon color="warning" sx={{ fontSize: 18 }} />;
    default:
      return <RadioButtonUncheckedIcon color="disabled" sx={{ fontSize: 18 }} />;
  }
};

StepIcon.propTypes = { status: PropTypes.string };

const ListSection = ({ title, items }) =>
  items && items.length > 0 ? (
    <Box sx={{ mt: 1 }}>
      <Typography variant="caption" sx={{ fontWeight: 600 }}>{title}</Typography>
      <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
        {items.map((item, i) => (
          <Typography component="li" variant="caption" key={i} sx={{ display: "list-item" }}>
            {item}
          </Typography>
        ))}
      </Box>
    </Box>
  ) : null;

ListSection.propTypes = { title: PropTypes.string, items: PropTypes.array };

const PlanCard = ({ plan: initialPlan, onRunStarted, onKeepPlanning, onStop, onPlanChanged }) => {
  const { t } = useUiTranslation();
  const [plan, setPlan] = useState(initialPlan);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState("");
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null);

  // Live updates arrive through the message; a saved card refreshes itself
  // once, since history holds the plan as it was when the message was saved.
  useEffect(() => { setPlan(initialPlan); }, [initialPlan]);
  const planId = initialPlan?.id;
  const openOnLoad = OPEN_STATUSES.includes(initialPlan?.status);
  useEffect(() => {
    if (!planId || !openOnLoad) return undefined;
    let cancelled = false;
    getPlan(planId)
      .then((data) => { if (!cancelled && data?.plan) setPlan(data.plan); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [planId, openOnLoad]);

  const tools = useMemo(
    () => Array.from(new Set((plan?.steps || []).flatMap((s) => s.tools || []))),
    [plan],
  );

  if (!plan) return null;
  const steps = plan.steps || [];
  const doneCount = steps.filter((s) => s.status === "done").length;
  const current = steps.find((s) => s.status === "running");

  const act = async (name, fn) => {
    setBusy(name);
    setError("");
    try {
      const result = await fn();
      return result;
    } catch (e) {
      setError(e.message || String(e));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const update = (next) => {
    setPlan(next);
    onPlanChanged?.(next);
  };

  const approve = (mode) =>
    act(mode, async () => {
      const res = await approvePlan(plan.id, { mode, revision: plan.revision });
      update({ ...plan, status: "executing", approval_mode: mode });
      onRunStarted?.(res);
      return res;
    });

  const reject = () =>
    act("reject", async () => {
      await rejectPlan(plan.id);
      update({ ...plan, status: "rejected" });
    });

  const resume = (action) =>
    act(action, async () => {
      const res = await continuePlan(plan.id, action);
      update({ ...plan, status: "executing", paused_reason: null });
      onRunStarted?.(res);
      return res;
    });

  const replan = () =>
    act("replan", async () => {
      await replanPlan(plan.id);
      const stuck = steps.find((s) => !["done", "skipped"].includes(s.status));
      onKeepPlanning?.(
        stuck
          ? t("chat.plan.replanPrompt", { index: stuck.index, title: stuck.title, error: stuck.error || "" })
          : "",
        { planMode: true },
      );
    });

  const startEdit = () => {
    setDraft({ goal: plan.goal || "", steps: steps.map((s) => ({ ...s })) });
    setEditing(true);
  };

  const saveEdit = () =>
    act("edit", async () => {
      const res = await editPlan(plan.id, {
        revision: plan.revision,
        goal: draft.goal,
        steps: draft.steps.map(({ title, action, kind, tools: stepTools }) => ({ title, action, kind, tools: stepTools })),
      });
      setEditing(false);
      update({ ...plan, status: "superseded" });
      onPlanChanged?.(res.plan, { added: true });
    });

  const setDraftStep = (i, field, value) =>
    setDraft((d) => ({ ...d, steps: d.steps.map((s, j) => (j === i ? { ...s, [field]: value } : s)) }));

  const statusLabel = {
    awaiting_approval: t("chat.plan.status.awaiting"),
    executing: current
      ? t("chat.plan.status.runningStep", { index: current.index, total: steps.length })
      : t("chat.plan.status.running"),
    paused: plan.paused_reason === "step_failed"
      ? t("chat.plan.status.pausedFailed")
      : plan.paused_reason === "interrupted"
        ? t("chat.plan.status.pausedInterrupted")
        : t("chat.plan.status.pausedStopped"),
    done: t("chat.plan.status.done", { done: doneCount, total: steps.length }),
    rejected: t("chat.plan.status.rejected"),
    superseded: t("chat.plan.status.superseded"),
  }[plan.status] || plan.status;

  const faded = ["rejected", "superseded"].includes(plan.status);

  return (
    <Paper
      variant="outlined"
      data-testid="plan-card"
      sx={{ p: 1.5, my: 1, borderColor: plan.status === "awaiting_approval" ? "primary.main" : "divider", opacity: faded ? 0.7 : 1 }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5 }}>
        <ChecklistIcon fontSize="small" color="primary" />
        <Typography variant="subtitle2" sx={{ flex: 1 }}>
          {plan.revision > 1 ? t("chat.plan.titleRevision", { revision: plan.revision }) : t("chat.plan.title")}
        </Typography>
        <Chip size="small" label={statusLabel} color={plan.status === "paused" ? "warning" : plan.status === "done" ? "success" : "default"} />
      </Box>

      {editing ? (
        <TextField
          fullWidth size="small" label={t("chat.plan.goal")} value={draft.goal}
          onChange={(e) => setDraft((d) => ({ ...d, goal: e.target.value }))} sx={{ my: 1 }}
        />
      ) : (
        plan.goal && <Typography variant="body2" sx={{ mb: 1 }}>{plan.goal}</Typography>
      )}

      <Box component="ol" sx={{ m: 0, pl: 0, listStyle: "none" }}>
        {(editing ? draft.steps : steps).map((step, i) => (
          <Box component="li" key={`${step.index}-${i}`} sx={{ display: "flex", gap: 1, py: 0.5, alignItems: "flex-start" }}>
            <Box sx={{ pt: 0.25 }}><StepIcon status={step.status} /></Box>
            {editing ? (
              <Box sx={{ flex: 1, display: "flex", flexDirection: "column", gap: 0.75 }}>
                <Box sx={{ display: "flex", gap: 1 }}>
                  <TextField size="small" sx={{ flex: 1 }} label={t("chat.plan.stepTitle", { index: i + 1 })}
                    value={step.title} onChange={(e) => setDraftStep(i, "title", e.target.value)} />
                  <TextField select size="small" sx={{ width: 140 }} label={t("chat.plan.stepKind")}
                    value={step.kind} onChange={(e) => setDraftStep(i, "kind", e.target.value)}>
                    {STEP_KINDS.map((k) => <MenuItem key={k} value={k}>{t(`chat.plan.kinds.${k}`)}</MenuItem>)}
                  </TextField>
                </Box>
                <TextField size="small" multiline minRows={2} label={t("chat.plan.stepAction")}
                  value={step.action} onChange={(e) => setDraftStep(i, "action", e.target.value)} />
              </Box>
            ) : (
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>
                  {t("chat.plan.stepHeading", { index: step.index, title: step.title })}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", whiteSpace: "pre-wrap" }}>
                  {step.action}
                </Typography>
                <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mt: 0.25 }}>
                  <Chip size="small" variant="outlined" label={t(`chat.plan.kinds.${step.kind}`, { defaultValue: step.kind })} sx={{ height: 18, fontSize: "0.65rem" }} />
                  {(step.tools || []).map((tool) => (
                    <Chip key={tool} size="small" variant="outlined" label={tool} sx={{ height: 18, fontSize: "0.65rem", fontFamily: "monospace" }} />
                  ))}
                </Box>
                {step.error && (
                  <Typography variant="caption" color="error" sx={{ display: "block", mt: 0.25 }}>
                    {t("chat.plan.stepFailed", { error: step.error })}
                  </Typography>
                )}
              </Box>
            )}
          </Box>
        ))}
      </Box>

      {(plan.warnings || []).length > 0 && (
        <Alert severity="warning" sx={{ mt: 1, py: 0 }}>
          {(plan.warnings || []).map((w, i) => (
            <Typography key={i} variant="caption" sx={{ display: "block" }}>{w}</Typography>
          ))}
        </Alert>
      )}

      {((plan.checks || []).length + (plan.risks || []).length + (plan.questions || []).length) > 0 && (
        <Box sx={{ mt: 0.5 }}>
          <Box sx={{ display: "flex", alignItems: "center", cursor: "pointer" }} onClick={() => setDetailsOpen((o) => !o)}>
            <Typography variant="caption" color="text.secondary">{t("chat.plan.details")}</Typography>
            <IconButton size="small" aria-label={t("chat.plan.details")}>
              {detailsOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
            </IconButton>
          </Box>
          <Collapse in={detailsOpen}>
            <ListSection title={t("chat.plan.checks")} items={plan.checks} />
            <ListSection title={t("chat.plan.risks")} items={plan.risks} />
            <ListSection title={t("chat.plan.questions")} items={plan.questions} />
          </Collapse>
        </Box>
      )}

      {error && <Alert severity="error" sx={{ mt: 1, py: 0 }}>{error}</Alert>}

      {plan.status === "awaiting_approval" && !editing && (
        <Box sx={{ display: "flex", gap: 1, mt: 1.25, flexWrap: "wrap", alignItems: "center" }}>
          <ActionButton kind="primary" loading={busy === "ask"} onClick={() => approve("ask")}
            tooltip={t("chat.plan.approveTooltip")}>
            {t("chat.plan.approve")}
          </ActionButton>
          <ActionButton kind="neutral" loading={busy === "allow_plan_tools"} onClick={() => approve("allow_plan_tools")}
            tooltip={tools.length ? t("chat.plan.approveAllowTooltip", { tools: tools.join(", ") }) : t("chat.plan.approveAllowNoTools")}>
            {t("chat.plan.approveAllow")}
          </ActionButton>
          <ActionButton kind="link" onClick={() => onKeepPlanning?.("")}>{t("chat.plan.keepPlanning")}</ActionButton>
          <ActionButton kind="link" onClick={startEdit}>{t("chat.plan.edit")}</ActionButton>
          <ActionButton kind="link" loading={busy === "reject"} onClick={reject}>{t("chat.plan.reject")}</ActionButton>
        </Box>
      )}

      {editing && (
        <Box sx={{ display: "flex", gap: 1, mt: 1.25 }}>
          <ActionButton kind="primary" loading={busy === "edit"} onClick={saveEdit}>{t("chat.plan.saveEdit")}</ActionButton>
          <ActionButton kind="link" onClick={() => setEditing(false)}>{t("chat.plan.cancelEdit")}</ActionButton>
        </Box>
      )}

      {plan.status === "executing" && (
        <Box sx={{ display: "flex", gap: 1, mt: 1.25 }}>
          <ActionButton kind="link" onClick={() => onStop?.()}>{t("chat.plan.stop")}</ActionButton>
        </Box>
      )}

      {plan.status === "paused" && (
        <Box sx={{ display: "flex", gap: 1, mt: 1.25, flexWrap: "wrap" }}>
          <ActionButton kind="primary" loading={busy === "retry"} onClick={() => resume("retry")}>
            {plan.paused_reason === "step_failed" ? t("chat.plan.retry") : t("chat.plan.continue")}
          </ActionButton>
          {plan.paused_reason === "step_failed" && (
            <ActionButton kind="neutral" loading={busy === "skip"} onClick={() => resume("skip")}>{t("chat.plan.skip")}</ActionButton>
          )}
          <ActionButton kind="link" loading={busy === "replan"} onClick={replan}>{t("chat.plan.replan")}</ActionButton>
          <ActionButton kind="link" loading={busy === "reject"} onClick={reject}>{t("chat.plan.drop")}</ActionButton>
        </Box>
      )}
    </Paper>
  );
};

PlanCard.propTypes = {
  plan: PropTypes.object,
  /** Called with the approve/continue response once the run has started. */
  onRunStarted: PropTypes.func,
  /** Focus the input for feedback; (prefill text, {planMode}) */
  onKeepPlanning: PropTypes.func,
  onStop: PropTypes.func,
  /** (plan, {added}) when the card changed the plan or made a new revision. */
  onPlanChanged: PropTypes.func,
};

export default PlanCard;
