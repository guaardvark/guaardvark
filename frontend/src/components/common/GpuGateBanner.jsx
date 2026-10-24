import React from "react";
import { Alert, Tooltip } from "@mui/material";
import { useUiTranslation } from "../../i18n";

/**
 * @param {boolean} [gpuBusy] - GPU held or cooling down (preferred)
 * @param {boolean} [gpuBlocked] - Legacy alias for gpuBusy when queueMode is false
 * @param {string}  [blockReason] - plain sentence from useJobsGate ("A video is rendering")
 * @param {string}  [blockDetail] - the holder's kind and id; shown only in a tooltip
 * @param {boolean} [queueMode] - Queue-backed surfaces: informational, not a hard block
 */
export default function GpuGateBanner({
  gpuBusy,
  gpuBlocked,
  blockReason,
  blockDetail,
  queueMode = false,
}) {
  const { t } = useUiTranslation();
  const busy = gpuBusy ?? gpuBlocked;
  if (!busy || !blockReason) return null;

  const text = queueMode
    ? t("layoutCommon.gpuGate.queued", { reason: blockReason })
    : t("layoutCommon.gpuGate.blocked", { reason: blockReason });

  const alert = (
    <Alert severity="info" sx={{ mb: 1 }}>
      {text}
    </Alert>
  );
  if (!blockDetail) return alert;
  return (
    <Tooltip title={t("layoutCommon.gpuGate.job", { detail: blockDetail })} arrow>
      {alert}
    </Tooltip>
  );
}