// Reading a bulk import job's status record: the worker's
// (bulk_import_documents_task, polled through /files/bulk-import/<id>/status)
// and the browser upload's, which reports in the same shape.

import { translate } from "../../../i18n";

// The worker ends a job with "complete"; this page once waited for
// "completed" and polled forever. Either spelling ends the job.
const SUCCESS_STATUSES = ["complete", "completed"];
const FAILED_STATUSES = ["error", "failed"];
const TERMINAL_STATUSES = [...SUCCESS_STATUSES, ...FAILED_STATUSES, "cancelled", "stopped"];

export const normalizeStatus = (status) => String(status || "").toLowerCase();
export const isSuccessStatus = (status) => SUCCESS_STATUSES.includes(normalizeStatus(status));
export const isFailedStatus = (status) => FAILED_STATUSES.includes(normalizeStatus(status));
export const isTerminalStatus = (status) => TERMINAL_STATUSES.includes(normalizeStatus(status));

/**
 * What the rules kept out, in the words of the worker's own status message
 * (_skipped_summary): "1 hidden or internal folder and 2 hidden or private files".
 */
export const skippedPhrase = (skipped) => {
  if (!skipped) return "";
  const folders = (skipped.hidden_dirs || 0) + (skipped.internal_dirs || 0);
  const files = (skipped.hidden_files || 0) + (skipped.sensitive_files || 0) + (skipped.symlinks || 0);
  const folderText = folders ? translate("upload.bulk.skipped.folders", { count: folders }) : "";
  const fileText = files ? translate("upload.bulk.skipped.files", { count: files }) : "";
  if (folderText && fileText) {
    return translate("upload.bulk.skipped.foldersAndFiles", { folders: folderText, files: fileText });
  }
  return folderText || fileText;
};

/** The pill for a job: a label and a StatusPill tone. A finished job with failures is never green. */
export const jobPill = (job) => {
  const status = normalizeStatus(job?.status);
  const errors = Number(job?.stats?.errors) || 0;
  if (isSuccessStatus(status)) {
    if (job.preview) return { label: translate("upload.bulk.pill.previewReady"), tone: "info" };
    return errors > 0
      ? { label: translate("upload.bulk.pill.finishedWithErrors", { count: errors }), tone: "warn" }
      : { label: translate("upload.bulk.pill.imported"), tone: "ok" };
  }
  if (isFailedStatus(status)) return { label: translate("upload.bulk.pill.failed"), tone: "error" };
  if (status === "stopped") return { label: translate("upload.bulk.pill.stopped"), tone: "warn" };
  if (status === "cancelled") return { label: translate("upload.bulk.pill.cancelled"), tone: "neutral" };
  if (status === "uploading") return { label: translate("upload.bulk.pill.uploading"), tone: "info" };
  if (status === "queued") return { label: translate("upload.bulk.pill.queued"), tone: "info" };
  return {
    label: job?.preview ? translate("upload.bulk.pill.counting") : translate("upload.bulk.pill.importing"),
    tone: "info",
  };
};

/**
 * One line of results in plain counts, e.g.
 * "298 imported · 4 already there · 0 failed · 298 queued for indexing".
 * A preview says how many files would come in.
 */
export const resultLine = (stats, { preview = false } = {}) => {
  if (!stats) return "";
  const parts = [];
  if (preview) {
    if (stats.total_files != null) {
      parts.push(translate("upload.bulk.result.wouldImport", { count: Number(stats.total_files) || 0 }));
    }
  } else {
    if (stats.imported != null) parts.push(translate("upload.bulk.result.imported", { count: stats.imported }));
    if (stats.skipped) parts.push(translate("upload.bulk.result.alreadyThere", { count: stats.skipped }));
    if (stats.errors != null) parts.push(translate("upload.bulk.result.failed", { count: stats.errors }));
    if (stats.queued_for_indexing) {
      parts.push(translate("upload.bulk.result.queued", { count: stats.queued_for_indexing }));
    }
  }
  const skipped = skippedPhrase(stats.skipped_private);
  if (skipped) parts.push(translate("upload.bulk.result.skipped", { what: skipped }));
  return parts.join(" · ");
};

// Our own words in a job record are kept as { note, values } and worded where
// they are shown, so the record (and the copy the page saves to come back to)
// follows a language change. A plain string is the worker's own message, or a
// record saved before notes existed, and is shown as it is.
const JOB_NOTES = {
  finishedAway: () => translate("upload.bulk.job.finishedAway"),
  unreachable: () => translate("upload.bulk.job.unreachable"),
  expired: () => translate("upload.bulk.job.expired"),
  counting: () => translate("upload.bulk.job.counting"),
  started: () => translate("upload.bulk.job.started"),
  fromServer: ({ path }) => translate("upload.bulk.job.fromServer", { path }),
  nothingToMove: () => translate("upload.bulk.job.nothingToMove"),
  uploadStarting: () => translate("upload.bulk.job.uploadStarting"),
  fromBrowserFolder: ({ name }) => translate("upload.bulk.job.fromBrowserFolder", { name }),
  fromBrowserFiles: ({ count }) => translate("upload.bulk.job.fromBrowserFiles", { count }),
  uploadingFile: ({ number, total, name }) => translate("upload.bulk.job.uploadingFile", { number, total, name }),
  uploadStopped: ({ count, total }) => translate("upload.bulk.job.uploadStopped", { count, total }),
  uploadFinished: () => translate("upload.bulk.job.uploadFinished"),
  uploadFailed: () => translate("upload.bulk.job.uploadFailed"),
};

/** One of our own lines for a job record, worded when shown (see jobText). */
export const jobNote = (note, values = {}) => ({ note, values });

/** A job record's message or label as it reads now: a note worded, a string as it is. */
export const jobText = (text) => {
  if (!text) return "";
  if (typeof text !== "object") return String(text);
  return JOB_NOTES[text.note]?.(text.values || {}) ?? "";
};
