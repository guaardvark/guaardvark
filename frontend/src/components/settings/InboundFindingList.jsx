// One inbound-guard finding per block, labelled by who wrote what: the rule and
// its explanation come from Guaardvark's rules (or an extension's, or the
// posture checks); only the quoted line comes from the change itself.

import React from "react";
import { Box, Chip, Stack, Typography } from "@mui/material";
import { translate, useUiTranslation } from "../../i18n";

const SEVERITY_TONE = { critical: "error", high: "error", medium: "warning", low: "info", info: "default" };

const originOf = (f) => {
  if (f.origin) return f.origin;
  if (f.provider) return f.provider === "core" ? "core" : "extension";
  return f.pack === "posture" ? "posture" : null;
};

const originLabel = (f) => {
  const origin = originOf(f);
  if (origin === "extension") {
    return translate("inboundGuard.finding.byExtension", {
      extension: String(f.provider || "").split(".")[0],
      provider: f.provider,
    });
  }
  if (origin === "posture") return translate("inboundGuard.finding.byPosture");
  if (origin === "core") return translate("inboundGuard.finding.byCore");
  if (origin === "unknown") return translate("inboundGuard.finding.byUnknown");
  return translate("inboundGuard.finding.rule");
};

/**
 * The findings of one verdict.
 *
 * @param {object[]} findings
 * @param {number}   [max]           how many to show; the rest are counted, not hidden
 * @param {string}   [excerptLabel]  what the quoted text is ("Line in the change (quoted)" by default)
 */
export const FindingList = ({ findings, max = 50, excerptLabel }) => {
  const { t } = useUiTranslation();
  const all = findings || [];
  const quoted = excerptLabel || t("inboundGuard.finding.quotedChange");
  return (
    <Stack spacing={1}>
      {all.slice(0, max).map((f, i) => (
        <Box key={`${f.rule}-${f.path}-${f.line}-${i}`}>
          <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
            <Chip size="small" label={f.severity} color={SEVERITY_TONE[f.severity] || "default"} sx={{ height: 18 }} />
            <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
              {f.rule} · {f.line ? `${f.path}:${f.line}` : f.path}
            </Typography>
          </Stack>
          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", ml: 0.5 }}>
            {originLabel(f)}
          </Typography>
          <Typography variant="body2" sx={{ ml: 0.5 }}>
            {f.why}
          </Typography>
          {f.excerpt && (
            <Box sx={{ ml: 0.5 }}>
              <Typography variant="caption" sx={{ color: "text.secondary" }}>
                {t("inboundGuard.finding.quoted", { label: quoted })}
              </Typography>
              <Typography
                variant="caption"
                component="pre"
                sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all", color: "text.secondary" }}
              >
                {f.excerpt}
              </Typography>
            </Box>
          )}
        </Box>
      ))}
      {all.length > max && (
        <Typography variant="caption" sx={{ color: "text.secondary" }}>
          {t("inboundGuard.finding.more", { count: all.length - max })}
        </Typography>
      )}
    </Stack>
  );
};

/** What the quoted text in a finding is, by where the item came from. */
export const excerptLabelFor = (source) => {
  if (source === "posture") return translate("inboundGuard.finding.quotedPosture");
  if (source === "watch" || source === "watch-audit") return translate("inboundGuard.finding.quotedFile");
  return translate("inboundGuard.finding.quotedChange");
};

export default FindingList;
