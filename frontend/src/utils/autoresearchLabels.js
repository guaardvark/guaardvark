/**
 * Plain-language labels for the Autoresearch page and its dashboard card.
 *
 * Both read the same backend fields (/api/autoresearch/status and /runs), so
 * the wording lives here once: a run that refused to start says why, scores
 * show the measured baseline against the latest result (which may be lower),
 * and pytest health-check rows are never shown as kept experiments.
 */
import { translate } from "../i18n";

// Getters, so each read follows the interface's language.
const REASON_TEXT = {
  get ollama_unreachable() { return translate("messages.autoresearch.reasons.ollamaUnreachable"); },
  get insufficient_corpus() { return translate("messages.autoresearch.reasons.insufficientCorpus"); },
  get no_eval_pairs() { return translate("messages.autoresearch.reasons.noEvalPairs"); },
  get eval_sources_not_indexed() { return translate("messages.autoresearch.reasons.evalSourcesNotIndexed"); },
  get judge_unset() { return translate("messages.autoresearch.reasons.judgeUnset"); },
  get judge_same_as_answer_model() { return translate("messages.autoresearch.reasons.judgeSameAsAnswerModel"); },
  get celery_unreachable() { return translate("messages.autoresearch.reasons.celeryUnreachable"); },
  get baseline_eval_failed() { return translate("messages.autoresearch.reasons.baselineEvalFailed"); },
  get prerequisite_check_failed() { return translate("messages.autoresearch.reasons.prerequisiteCheckFailed"); },
  get codebase_locked() { return translate("messages.autoresearch.reasons.codebaseLocked"); },
  get self_improvement_disabled() { return translate("messages.autoresearch.reasons.selfImprovementDisabled"); },
};

const HEALTH_STATUS = { keep: "pass", discard: "fail" };

function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** A score to three places, or an em dash when there is none. */
export function formatScore(value) {
  const n = asNumber(value);
  return n === null ? "—" : n.toFixed(3);
}

/** A signed change ("+0.100", "-0.300"), or "" when either side is missing. */
export function formatDelta(value, from) {
  const a = asNumber(value);
  const b = asNumber(from);
  if (a === null || b === null) return "";
  const d = a - b;
  return `${d >= 0 ? "+" : "-"}${Math.abs(d).toFixed(3)}`;
}

/** The machine code at the start of a backend reason ("judge_unset — …"). */
export function reasonCode(reason) {
  const match = /^[a-z_]+/.exec(String(reason || "").trim());
  return match ? match[0] : "";
}

/**
 * A run's halt reason in plain words. Several reasons arrive joined by "; ";
 * an unknown code is shown as the backend wrote it.
 */
export function describeReasons(haltReason) {
  const parts = String(haltReason || "")
    .split("; ")
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length === 0) return translate("messages.autoresearch.noReason");
  return parts.map((p) => REASON_TEXT[reasonCode(p)] || p).join("; ");
}

/**
 * What a research run produced, for a table cell or a one-line summary.
 * kind: "none" | "not_run" | "active" | "done". text is the visible line;
 * detail is the full backend wording for a tooltip.
 */
export function runOutcome(run) {
  if (!run) return { kind: "none", text: "—", detail: "" };
  if (run.status === "failed_precondition") {
    return {
      kind: "not_run",
      text: translate("messages.autoresearch.notRun", { reasons: describeReasons(run.halt_reason) }),
      detail: run.halt_reason || "",
    };
  }
  const base = asNumber(run.baseline_score);
  const latest = asNumber(run.latest_score);
  const best = asNumber(run.best_tried_score);
  const kind = run.status === "running" || run.status === "pending" ? "active" : "done";
  if (base === null && latest === null) {
    const text = run.status === "pending" ? translate("messages.autoresearch.starting") : translate("messages.autoresearch.noBaseline");
    return { kind, text, detail: run.halt_reason || "" };
  }
  const text = best !== null
    ? translate("messages.autoresearch.scoresWithBest", { base: formatScore(base), latest: formatScore(latest), best: formatScore(best) })
    : `${formatScore(base)} → ${formatScore(latest)}`;
  return { kind, text, detail: run.halt_reason ? translate("messages.autoresearch.halt", { reason: run.halt_reason }) : "" };
}

/**
 * How to show one ledger row. Health checks (proposal_source "heal") are
 * pytest snapshots, not experiments; rows a code arm posted for itself are
 * self-reported, not measured by the eval harness.
 */
export function experimentLabel(exp) {
  const metrics = (exp && exp.retrieval_metrics) || {};
  if (exp && exp.proposal_source === "heal") {
    const outcome = HEALTH_STATUS[exp.status] || exp.status || "unknown";
    const shownOutcome = outcome === "pass"
      ? translate("messages.autoresearch.healthPass")
      : outcome === "fail"
        ? translate("messages.autoresearch.healthFail")
        : outcome === "unknown" ? translate("messages.autoresearch.healthUnknown") : outcome;
    return {
      kind: "health",
      status: translate("messages.autoresearch.healthStatus", { outcome: shownOutcome }),
      source: translate("messages.autoresearch.healthCheck"),
      tone: outcome === "fail" ? "warning" : "default",
      selfReported: false,
    };
  }
  const selfReported =
    Boolean(metrics.self_reported) || (exp && exp.proposal_source === "code_arm");
  const status = (exp && exp.status) || "—";
  return {
    kind: selfReported ? "self_reported" : "experiment",
    status,
    source: selfReported ? translate("messages.autoresearch.selfReported") : (exp && exp.proposal_source) || "—",
    tone: status === "keep" ? "success" : status === "crash" ? "error" : "default",
    selfReported,
  };
}

/** "YYYY-MM-DD HH:MM" in local time for a naive-UTC backend timestamp. */
export function formatMeasuredAt(iso) {
  if (!iso) return "";
  const text = String(iso);
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`);
  if (Number.isNaN(date.getTime())) return text;
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

/** "Baseline: 2.394 (measured 2026-10-07 01:09)" or "Baseline: not measured". */
export function baselineText(status) {
  const score = asNumber(status && status.baseline_score);
  if (!score) return translate("messages.autoresearch.baselineNotMeasured");
  const at = formatMeasuredAt(status.baseline_measured_at);
  return at
    ? translate("messages.autoresearch.baselineMeasured", { score: score.toFixed(3), at })
    : translate("messages.autoresearch.baselineNoTime", { score: score.toFixed(3) });
}

/** "11 active · 11 from unindexed docs", from /status.eval_pairs. */
export function evalPairsText(status) {
  const pairs = status && status.eval_pairs;
  if (!pairs) return "—";
  if (!pairs.active) return translate("messages.autoresearch.evalPairsNone");
  return pairs.not_indexed
    ? translate("messages.autoresearch.evalPairsWithUnindexed", { active: pairs.active, unindexed: pairs.not_indexed })
    : translate("messages.autoresearch.evalPairsActive", { active: pairs.active });
}

/** Why the judge setting makes scores self-graded, or null when it does not. */
export function judgeWarning(judge) {
  if (!judge || judge.independent !== false) return null;
  const model = judge.answer_model || translate("messages.autoresearch.activeChatModel");
  if (judge.problem === "judge_same_as_answer_model") {
    return translate("messages.autoresearch.judgeIsAnswerModel", { model });
  }
  return translate("messages.autoresearch.judgeNotSet", { model });
}

/** "19 experiments · 0 promoted", health checks left out of both. */
export function experimentsSummary(status) {
  const experiments = asNumber(status && status.total_experiments) ?? 0;
  const promoted = asNumber(status && status.total_improvements) ?? 0;
  return translate("messages.autoresearch.experimentsSummary", { count: experiments, promoted });
}
