// Client-side Hugging Face paste checks. Copy matches parse_hf_url on the server
// so a bad host fails before the round-trip.
import { translate } from "../i18n";

export function hfUrlClientError(raw) {
  const url = (raw || "").trim();
  if (!url) return translate("messages.hfUrl.empty");
  const lower = url.toLowerCase();
  const hasScheme = lower.includes("://");
  const isHf = lower.includes("huggingface.co") || lower.includes("hf.co");
  if (hasScheme && !isHf) {
    return translate("messages.hfUrl.notHuggingFace");
  }
  if (
    /huggingface\.co\/(datasets|spaces)\b/i.test(url) ||
    /hf\.co\/(datasets|spaces)\b/i.test(url) ||
    /^(datasets|spaces)\//i.test(url)
  ) {
    return translate("messages.hfUrl.notModelRepo");
  }
  return "";
}

export function formatWeightSize(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "";
  return ` (${(n / 1024 ** 3).toFixed(2)} GB)`;
}

export function selectedBytes(files, selected) {
  return (files || [])
    .filter((f) => selected.includes(f.src))
    .reduce((sum, f) => sum + (Number(f.size) || 0), 0);
}
