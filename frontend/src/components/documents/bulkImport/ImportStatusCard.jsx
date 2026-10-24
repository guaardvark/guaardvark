// The running or finished import, in the work area above the fold: a status
// pill, progress, the worker's message and the results in plain counts.
import React, { useState } from "react";
import { Box, Collapse, LinearProgress, Paper, Stack, Typography } from "@mui/material";
import { ActionButton, StatusPill } from "../../settings/ui";
import { isTerminalStatus, jobPill, jobText, resultLine } from "./importStatus";
import { intoLabel } from "./ImportTargetSelect";
import { useUiTranslation } from "../../../i18n";

const PREVIEW_NAMES_SHOWN = 200;

const ImportStatusCard = ({ job, onOpenFiles, onImportAnother, onStop }) => {
  const { t } = useUiTranslation();
  const [showList, setShowList] = useState(false);
  if (!job) return null;

  const pill = jobPill(job);
  const done = isTerminalStatus(job.status);
  const progress = typeof job.progress === "number" ? job.progress : null;
  const line = resultLine(job.stats, { preview: job.preview });
  const previewNames = job.preview && Array.isArray(job.stats?.files) ? job.stats.files : [];
  // Files too large for one upload are kept as paths and worded here.
  const failures = [
    ...(job.failures || []),
    ...(job.tooLarge || []).map((path) => t("upload.bulk.status.tooLargeFailure", { path })),
  ];
  const message = jobText(job.message);
  const awayNote = jobText(job.awayNote);
  // A record saved before `into` was kept carries its label as text.
  const into = job.into ? intoLabel(job.into) : job.intoLabel;
  const listed = previewNames.length ? previewNames : failures;

  return (
    <Paper variant="outlined" sx={{ p: 2 }} data-testid="import-status">
      <Stack spacing={1.25}>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ xs: "flex-start", sm: "center" }}>
          <StatusPill
            label={pill.label}
            tone={pill.tone}
            tooltip={job.jobId ? t("upload.bulk.status.jobTooltip", { id: job.jobId }) : ""}
          />
          <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1, minWidth: 0, overflowWrap: "anywhere" }}>
            {jobText(job.sourceLabel)}
            {into ? ` → ${into}` : ""}
          </Typography>
          {!done && onStop && (
            <ActionButton onClick={onStop} tooltip={t("upload.bulk.status.stopTooltip")}>
              {t("upload.bulk.status.stop")}
            </ActionButton>
          )}
        </Stack>

        {!done && (
          <LinearProgress
            variant={progress == null ? "indeterminate" : "determinate"}
            value={progress ?? 0}
          />
        )}

        {message && (
          <Typography variant="body2" sx={{ overflowWrap: "anywhere" }}>
            {message}
          </Typography>
        )}
        {line && (
          <Typography variant="body2" color="text.secondary">
            {line}
          </Typography>
        )}
        {awayNote && (
          <Typography variant="caption" color="text.secondary">
            {awayNote}
          </Typography>
        )}

        {listed.length > 0 && (
          <Box>
            <ActionButton kind="link" onClick={() => setShowList((v) => !v)}>
              {showList
                ? t("upload.bulk.status.hideList")
                : previewNames.length
                ? t("upload.bulk.status.showFiles", { count: previewNames.length })
                : t("upload.bulk.status.showFailed", { count: failures.length })}
            </ActionButton>
            <Collapse in={showList} unmountOnExit>
              <Box
                component="ul"
                sx={{ m: 0, mt: 0.5, pl: 3, maxHeight: 220, overflowY: "auto", fontSize: "0.8rem", color: "text.secondary" }}
              >
                {listed.slice(0, PREVIEW_NAMES_SHOWN).map((name, i) => (
                  <li key={`${i}-${name}`}>{name}</li>
                ))}
                {listed.length > PREVIEW_NAMES_SHOWN && (
                  <li>{t("upload.bulk.status.andMore", { count: listed.length - PREVIEW_NAMES_SHOWN })}</li>
                )}
              </Box>
            </Collapse>
          </Box>
        )}

        {done && (onOpenFiles || onImportAnother) && (
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            {onOpenFiles && !job.preview && <ActionButton onClick={onOpenFiles}>{t("upload.bulk.status.openFiles")}</ActionButton>}
            {onImportAnother && <ActionButton onClick={onImportAnother}>{t("upload.bulk.status.importAnother")}</ActionButton>}
          </Stack>
        )}
      </Stack>
    </Paper>
  );
};

export default ImportStatusCard;
