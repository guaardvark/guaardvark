import { translate } from "../i18n";

// Legacy video batches predate display_name in their metadata. Their ids are
// Bates stamps (backend/services/output_registration.py): the older
// "VideoBatch_MM-DD-YYYY_NNN" and the current "VideoBatch_MM-DD-YYYY_HHMMSS_NNN".
// Both read as "MM/DD #NNN"; anything else loses only the prefix.
export function fallbackBatchName(batchId) {
  if (!batchId) return translate('messages.batches.fallbackName');
  const m = batchId.match(/^VideoBatch_(\d{2})-(\d{2})-\d{4}(?:_\d{6})?_(\d+)$/);
  if (m) return `${m[1]}/${m[2]} #${m[3]}`;
  return batchId.replace(/^VideoBatch_/, '');
}
