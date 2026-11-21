// The inbound guard's review list without the UI: which tab an item belongs to,
// how a click changes the selection, what an "approve all" would cover, and how
// times read. The backend decides what approving each item does
// (approve_effect / what_approve_does) and which items no batch may approve
// (own_decision); this only groups and words it.

import { translate, uiLocale } from "../../i18n";

const T = (key, options) => translate(`inboundGuard.${key}`, options);

export const SEVERITY_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };
export const SEVERITIES = ["critical", "high", "medium", "low", "info"];

const KNOWN_SOURCES = new Set([
  "git-merge",
  "branch-update",
  "fetch",
  "cli",
  "ci",
  "watch",
  "watch-audit",
  "posture",
  "code_editor",
  "code_task",
  "proposal",
  "approved_fix",
  "self_code",
  "self_knowledge",
  "backup_restore",
]);

/** Where an item came from, in the words a person reading the list needs. Unknown sources show as-is. */
export const sourceLabel = (source) => {
  if (!source) return T("unknown");
  return KNOWN_SOURCES.has(source) ? T(`source.${source}`) : source;
};

const EFFECTS = new Set(["apply", "accept", "record", "merge_hook", "allow_again"]);

/** Short column text for an approve_effect; the full sentence comes from the backend. */
export const effectShort = (effect) => (EFFECTS.has(effect) ? T(`effectShort.${effect}`) : "—");

/** Button text for approving one item, by approve_effect. */
export const effectButton = (effect) => T(`effectButton.${EFFECTS.has(effect) ? effect : "fallback"}`);

export const TABS = ["all", "waiting", "landed", "ondisk"];
export const VERDICTS = ["hold", "block"];
export const SORTS = ["newest", "oldest", "verdict", "severity", "source", "size"];

/** waiting | landed | ondisk, from what approving the item does. */
export const categoryOf = (item) => {
  if (item?.approve_effect === "accept") return "ondisk";
  if (item?.approve_effect === "record") return "landed";
  return "waiting";
};

export const isBlocked = (item) => item?.verdict === "block";

/** A change no batch approves: one of its findings is a way off the machine. */
export const isOwnDecision = (item) => item?.own_decision === true;

export const inTab = (item, tab) => tab === "all" || categoryOf(item) === tab;

/**
 * Rows from the list response, keyed so a database row and a git record can never collide.
 * The open list leaves approved git records out; the decided list holds only those.
 */
export const toEntries = (data, { resolved = false } = {}) => [
  ...(data?.scans || []).map((item) => ({ kind: "scan", key: `s${item.id}`, item })),
  ...(data?.git || [])
    .filter((g) => Boolean(g.approved) === resolved)
    .map((item) => ({ kind: "git", key: `g${item.digest}`, item })),
];

const severityOf = (item) => SEVERITY_RANK[item?.top_severity] ?? -1;
const timeOf = (iso) => {
  const t = iso ? new Date(iso).getTime() : NaN;
  return Number.isNaN(t) ? 0 : t;
};
const flagged = (e) => timeOf(e.item.flagged_at);
const linesOf = (item) => Number(item?.added_lines) || 0;
const filesOf = (item) => Number(item?.files) || 0;

/** Filter by tab, verdict, minimum severity and source, then sort. */
export const viewEntries = (
  entries,
  { tab = "all", verdict = "", minSeverity = "", source = "", sort = "newest" } = {},
) => {
  const floor = minSeverity ? SEVERITY_RANK[minSeverity] : -1;
  const shown = entries.filter(
    (e) =>
      inTab(e.item, tab) &&
      (!verdict || e.item.verdict === verdict) &&
      severityOf(e.item) >= floor &&
      (!source || e.item.source === source),
  );
  const newestFirst = (a, b) => flagged(b) - flagged(a);
  const by = {
    newest: newestFirst,
    oldest: (a, b) => flagged(a) - flagged(b),
    verdict: (a, b) =>
      Number(isBlocked(b.item)) - Number(isBlocked(a.item)) ||
      severityOf(b.item) - severityOf(a.item) ||
      newestFirst(a, b),
    severity: (a, b) => severityOf(b.item) - severityOf(a.item) || newestFirst(a, b),
    source: (a, b) => sourceLabel(a.item.source).localeCompare(sourceLabel(b.item.source)) || newestFirst(a, b),
    size: (a, b) => linesOf(b.item) - linesOf(a.item) || filesOf(b.item) - filesOf(a.item) || newestFirst(a, b),
    decided: (a, b) => timeOf(b.item.decided_at) - timeOf(a.item.decided_at) || newestFirst(a, b),
  }[sort];
  return by ? [...shown].sort(by) : shown;
};

/**
 * The selection after one click on a row or its checkbox.
 *
 * @param {Set<string>} selected   keys selected now
 * @param {string[]}    ordered    keys in the order shown
 * @param {string}      key        the row clicked
 * @param {string|null} anchor     the row a shift-click extends from
 * @param {{shift?: boolean}} mods shift selects the range from anchor to key; otherwise key is toggled
 * @returns {Set<string>}
 */
export const nextSelection = (selected, ordered, key, anchor, { shift = false } = {}) => {
  const next = new Set(selected);
  const from = anchor ? ordered.indexOf(anchor) : -1;
  const to = ordered.indexOf(key);
  if (shift && from >= 0 && to >= 0) {
    const [lo, hi] = from <= to ? [from, to] : [to, from];
    ordered.slice(lo, hi + 1).forEach((k) => next.add(k));
    return next;
  }
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
};

/** Select every shown row, or clear them all when every one is already selected. */
export const toggleAll = (selected, ordered) => {
  const next = new Set(selected);
  const all = ordered.length > 0 && ordered.every((k) => next.has(k));
  ordered.forEach((k) => (all ? next.delete(k) : next.add(k)));
  return next;
};

const countBy = (list, fn) =>
  list.reduce((acc, x) => {
    const k = fn(x);
    acc[k] = (acc[k] || 0) + 1;
    return acc;
  }, {});

/**
 * What approving these entries would do, for the confirmation.
 *
 * Own-decision items are never taken (the server skips them too). Blocks are
 * left out unless includeBlocks. Everything taken is counted by kind, by
 * source and by what approving it does.
 */
export const approvalSummary = (entries, { includeBlocks = false } = {}) => {
  const own = entries.filter((e) => isOwnDecision(e.item));
  const eligible = entries.filter((e) => !isOwnDecision(e.item));
  const blocks = eligible.filter((e) => isBlocked(e.item));
  const taken = includeBlocks ? eligible : eligible.filter((e) => !isBlocked(e.item));
  const highest = taken.reduce(
    (best, e) => (severityOf(e.item) > (SEVERITY_RANK[best] ?? -1) ? e.item.top_severity : best),
    null,
  );
  const effects = Object.entries(countBy(taken, (e) => e.item.what_approve_does || e.item.approve_effect || "?")).map(
    ([text, count]) => ({ text, count }),
  );
  return {
    total: entries.length,
    count: taken.length,
    blocks: blocks.length,
    blocksLeftOut: includeBlocks ? 0 : blocks.length,
    ownDecision: own.length,
    byKind: countBy(taken, (e) => T(`kind.${e.kind === "git" ? "git" : "scan"}`)),
    bySource: countBy(taken, (e) => sourceLabel(e.item.source)),
    highest,
    effects,
    ids: taken.filter((e) => e.kind === "scan").map((e) => e.item.id),
    gitDigests: taken.filter((e) => e.kind === "git").map((e) => e.item.digest),
  };
};

/** "3 min ago", "5 h ago", "4 days ago"; "—" when the time is missing or unreadable. */
export const formatRelative = (iso, now = Date.now()) => {
  const then = iso ? new Date(iso).getTime() : NaN;
  if (Number.isNaN(then)) return "—";
  const mins = Math.round((now - then) / 60000);
  if (mins < 1) return T("when.justNow");
  if (mins < 60) return T("when.minutes", { count: mins });
  const hours = Math.round(mins / 60);
  if (hours < 24) return T("when.hours", { count: hours });
  return T("when.days", { count: Math.round(hours / 24) });
};

/** The exact moment in the viewer's own time zone, with the zone named. */
export const formatExact = (iso) => {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) return "";
  return t.toLocaleString(uiLocale(), {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
};

/**
 * The File / commit date cell: when the file on disk was last written, or when
 * the commits that arrived were made. null when neither is known. `kind` is
 * "file" or "commit"; `label` is the same in words.
 */
export const changeDate = (item) => {
  if (item?.file_mtime) {
    return { kind: "file", iso: item.file_mtime, label: T("changeDate.fileWritten"), detail: item.file_path || "" };
  }
  const c = item?.commits;
  if (c?.newest_at) {
    const commits = T("changeDate.commits", { count: c.count, shown: `${c.count}${c.capped ? "+" : ""}` });
    return {
      kind: "commit",
      iso: c.newest_at,
      label: T("changeDate.newestCommit"),
      detail:
        c.oldest_at && c.oldest_at !== c.newest_at
          ? T("changeDate.commitsOldest", { commits, oldest: formatExact(c.oldest_at) })
          : commits,
    };
  }
  return null;
};

/** How much the guard read for an item; the sweep reads files whole, so its count is not "added". */
export const sizeText = (item) => {
  const values = { files: filesOf(item), lines: linesOf(item) };
  const whole = String(item?.source || "").startsWith("watch");
  return {
    text: T(whole ? "sizeWhole" : "size", values),
    tooltip: T(whole ? "sizeWholeTooltip" : "sizeTooltip", values),
  };
};

/** What happened to a decided record, in one word, from its status and verdict. */
export const decisionOf = (item) => {
  if (item?.status === "approved" || item?.approved) return "approved";
  if (item?.status === "rejected") return "rejected";
  return item?.verdict === "allow" ? "allow" : "clear";
};
