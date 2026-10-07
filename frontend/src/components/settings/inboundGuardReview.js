// The inbound guard's review list without the UI: which tab an item belongs to,
// how a click changes the selection, what an "approve all" would cover, and how
// times read. The backend decides what approving each item does
// (approve_effect / what_approve_does); this only groups and words it.

export const SEVERITY_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };
export const SEVERITIES = ["critical", "high", "medium", "low", "info"];

/** Where an item came from, in the words a person reading the list needs. Unknown sources show as-is. */
export const SOURCE_LABELS = {
  "git-merge": "git merge",
  "branch-update": "branch moved",
  fetch: "git fetch",
  cli: "manual scan",
  ci: "CI scan",
  watch: "found on disk by the sweep",
  "watch-audit": "first sweep (existing code)",
  posture: "settings and inventory check",
  code_editor: "code editor",
  code_task: "code task",
  proposal: "proposed fix",
  approved_fix: "approved fix",
  self_code: "Guaardvark's own edit",
  self_knowledge: "agent lessons file",
  backup_restore: "backup restore",
};

export const sourceLabel = (source) => SOURCE_LABELS[source] || source || "unknown";

/** Short column text for each approve_effect; the full sentence comes from the backend. */
export const EFFECT_SHORT = {
  apply: "Applies it",
  accept: "Accepts the file",
  record: "Records only",
  merge_hook: "Lets the merge pass",
  allow_again: "Records only",
};

/** Button text for approving one item, by approve_effect. */
export const EFFECT_BUTTON = {
  apply: "Approve and apply",
  accept: "Accept the file",
  record: "Record approval",
  merge_hook: "Approve for the merge hook",
  allow_again: "Record approval",
};

export const TABS = [
  { value: "all", label: "All" },
  { value: "waiting", label: "Waiting (nothing applied yet)" },
  { value: "landed", label: "Landed — recorded only" },
  { value: "ondisk", label: "On disk (found by sweep)" },
  { value: "blocked", label: "Blocked" },
];

/** waiting | landed | ondisk, from what approving the item does. */
export const categoryOf = (item) => {
  if (item?.approve_effect === "accept") return "ondisk";
  if (item?.approve_effect === "record") return "landed";
  return "waiting";
};

export const isBlocked = (item) => item?.verdict === "block";

export const inTab = (item, tab) => {
  if (tab === "all") return true;
  if (tab === "blocked") return isBlocked(item);
  return categoryOf(item) === tab;
};

/** Rows from the list response, keyed so a database row and a git record can never collide. */
export const toEntries = (data) => [
  ...(data?.scans || []).map((item) => ({ kind: "scan", key: `s${item.id}`, item })),
  ...(data?.git || []).filter((g) => !g.approved).map((item) => ({ kind: "git", key: `g${item.digest}`, item })),
];

const severityOf = (item) => SEVERITY_RANK[item?.top_severity] ?? -1;
const timeOf = (iso) => {
  const t = iso ? new Date(iso).getTime() : NaN;
  return Number.isNaN(t) ? 0 : t;
};

/** Filter by minimum severity and source, then sort. */
export const viewEntries = (entries, { tab = "all", minSeverity = "", source = "", sort = "newest" } = {}) => {
  const floor = minSeverity ? SEVERITY_RANK[minSeverity] : -1;
  const shown = entries.filter(
    (e) => inTab(e.item, tab) && severityOf(e.item) >= floor && (!source || e.item.source === source),
  );
  const by = {
    newest: (a, b) => timeOf(b.item.flagged_at) - timeOf(a.item.flagged_at),
    oldest: (a, b) => timeOf(a.item.flagged_at) - timeOf(b.item.flagged_at),
    severity: (a, b) => severityOf(b.item) - severityOf(a.item) || timeOf(b.item.flagged_at) - timeOf(a.item.flagged_at),
    source: (a, b) =>
      sourceLabel(a.item.source).localeCompare(sourceLabel(b.item.source)) ||
      timeOf(b.item.flagged_at) - timeOf(a.item.flagged_at),
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
 * Blocks are left out unless includeBlocks; everything else is counted by
 * kind, by source and by what approving it does.
 */
export const approvalSummary = (entries, { includeBlocks = false } = {}) => {
  const blocks = entries.filter((e) => isBlocked(e.item));
  const taken = includeBlocks ? entries : entries.filter((e) => !isBlocked(e.item));
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
    byKind: countBy(taken, (e) => (e.kind === "git" ? "git records" : "database items")),
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
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
};

/** The exact moment in the viewer's own time zone, with the zone named. */
export const formatExact = (iso) => {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) return "";
  return t.toLocaleString(undefined, {
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
 * the commits that arrived were made. null when neither is known.
 */
export const changeDate = (item) => {
  if (item?.file_mtime) return { iso: item.file_mtime, label: "file written", detail: item.file_path || "" };
  const c = item?.commits;
  if (c?.newest_at) {
    const n = `${c.count}${c.capped ? "+" : ""} commit${c.count === 1 ? "" : "s"}`;
    return {
      iso: c.newest_at,
      label: "newest commit",
      detail: c.oldest_at && c.oldest_at !== c.newest_at ? `${n}, oldest ${formatExact(c.oldest_at)}` : n,
    };
  }
  return null;
};
