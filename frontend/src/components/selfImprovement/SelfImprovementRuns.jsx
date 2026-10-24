// Self-improvement runs, newest first, with a filter by trigger or result and
// each run's detail on click. Shown on the Self-improvement page and in the
// System Dashboard's log.
import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Refresh } from "@mui/icons-material";
import { selfImprovementService } from "../../api/selfImprovementService";
import { UNCLE_GOLD } from "../../utils/familyColors";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

// Run statuses and triggers as shown; the values stay the ones filtered on.
export const runStatusLabel = (status) =>
  ({
    running: translate("selfImprovement.runStatus.running"),
    success: translate("selfImprovement.runStatus.success"),
    failed: translate("selfImprovement.runStatus.failed"),
    cancelled: translate("selfImprovement.runStatus.cancelled"),
    blocked_by_guardian: translate("selfImprovement.runStatus.blocked_by_guardian"),
  })[status] ?? status;

const triggerLabel = (trigger) =>
  ({
    scheduled: translate("selfImprovement.trigger.scheduled"),
    reactive: translate("selfImprovement.trigger.reactive"),
    directed: translate("selfImprovement.trigger.directed"),
    family_learning: translate("selfImprovement.trigger.family_learning"),
  })[trigger] ?? trigger;

const filterLabel = (filter) =>
  ({
    all: translate("selfImprovement.runs.filter.all"),
    scheduled: translate("selfImprovement.runs.filter.scheduled"),
    reactive: translate("selfImprovement.runs.filter.reactive"),
    directed: translate("selfImprovement.runs.filter.directed"),
    success: translate("selfImprovement.runs.filter.success"),
    failed: translate("selfImprovement.runs.filter.failed"),
  })[filter];

export default function SelfImprovementRuns({ onTotal }) {
  const { t } = useUiTranslation();
  const [siRuns, setSiRuns] = useState([]);
  const [siRunsLoading, setSiRunsLoading] = useState(false);
  const [siRunsFilter, setSiRunsFilter] = useState("all");
  const [siExpandedRow, setSiExpandedRow] = useState(null);

  const fetchSiRuns = useCallback(async () => {
    setSiRunsLoading(true);
    try {
      const res = await selfImprovementService.getRuns(50, 0);
      const allRuns = res?.data?.runs || [];
      onTotal?.(allRuns.length);
      setSiRuns(
        siRunsFilter === "all"
          ? allRuns
          : allRuns.filter((r) =>
              siRunsFilter === "success" || siRunsFilter === "failed"
                ? r.status === siRunsFilter
                : r.trigger === siRunsFilter
            )
      );
    } catch (err) {
      console.error("Failed to fetch SI runs:", err);
    } finally {
      setSiRunsLoading(false);
    }
  }, [siRunsFilter, onTotal]);

  useEffect(() => {
    fetchSiRuns();
  }, [fetchSiRuns]);

  return (
    <>
      <Stack direction="row" spacing={0.5} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
        {["all", "scheduled", "reactive", "directed", "success", "failed"].map((f) => (
          <Chip
            key={f}
            label={filterLabel(f)}
            size="small"
            variant={siRunsFilter === f ? "filled" : "outlined"}
            color={siRunsFilter === f ? "primary" : "default"}
            onClick={() => { setSiRunsFilter(f); }}
          />
        ))}
        <Button size="small" onClick={fetchSiRuns} startIcon={<Refresh />}>{t("common.refresh")}</Button>
      </Stack>

      {siRunsLoading ? (
        <CircularProgress size={24} />
      ) : siRuns.length === 0 ? (
        <Typography variant="body2" color="text.secondary">{t("selfImprovement.runs.empty")}</Typography>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("selfImprovement.runs.status")}</TableCell>
                <TableCell>{t("selfImprovement.runs.trigger")}</TableCell>
                <TableCell>{t("selfImprovement.runs.timestamp")}</TableCell>
                <TableCell>{t("selfImprovement.runs.duration")}</TableCell>
                <TableCell>{t("selfImprovement.runs.uncleReviewed")}</TableCell>
                <TableCell>{t("selfImprovement.runs.details")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {siRuns.map((run) => (
                <React.Fragment key={run.id}>
                  <TableRow
                    hover
                    sx={{ cursor: "pointer" }}
                    onClick={() => setSiExpandedRow(siExpandedRow === run.id ? null : run.id)}
                  >
                    <TableCell>
                      <Chip
                        label={runStatusLabel(run.status)}
                        size="small"
                        color={run.status === "success" ? "success" : run.status === "failed" ? "error" : "warning"}
                      />
                    </TableCell>
                    <TableCell>{triggerLabel(run.trigger)}</TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {run.timestamp ? new Date(run.timestamp).toLocaleString(uiLocale()) : "—"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {run.duration_seconds
                        ? t("selfImprovement.runs.durationSeconds", { seconds: run.duration_seconds.toFixed(1) })
                        : "—"}
                    </TableCell>
                    <TableCell>
                      {run.uncle_reviewed ? (
                        <Chip label={t("selfImprovement.runs.reviewed")} size="small" sx={{ bgcolor: UNCLE_GOLD, color: "#000", fontSize: "0.65rem" }} />
                      ) : (
                        <Typography variant="caption" color="text.secondary">{t("selfImprovement.runs.notReviewed")}</Typography>
                      )}
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="primary" sx={{ cursor: "pointer" }}>
                        {siExpandedRow === run.id ? t("common.collapse") : t("selfImprovement.runs.expand")}
                      </Typography>
                    </TableCell>
                  </TableRow>
                  {siExpandedRow === run.id && (
                    <TableRow>
                      <TableCell colSpan={6} sx={{ bgcolor: "action.hover" }}>
                        <Box sx={{ p: 1 }}>
                          {run.error_message && (
                            <Alert severity="error" sx={{ mb: 1 }}>{run.error_message}</Alert>
                          )}
                          {run.uncle_feedback && (
                            <Box sx={{ mb: 1 }}>
                              <Typography variant="caption" fontWeight="bold" sx={{ color: UNCLE_GOLD }}>
                                {t("selfImprovement.runs.uncleFeedback")}:
                              </Typography>
                              <Typography variant="body2">{run.uncle_feedback}</Typography>
                            </Box>
                          )}
                          {run.changes_made && (
                            <Box sx={{ mb: 1 }}>
                              <Typography variant="caption" fontWeight="bold">{t("selfImprovement.runs.changesMade")}:</Typography>
                              <Box component="pre" sx={{ fontSize: "0.7rem", bgcolor: "background.default", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 200 }}>
                                {typeof run.changes_made === "string" ? run.changes_made : JSON.stringify(run.changes_made, null, 2)}
                              </Box>
                            </Box>
                          )}
                          {run.test_results_before && (
                            <Box>
                              <Typography variant="caption" fontWeight="bold">{t("selfImprovement.runs.testResults")}:</Typography>
                              <Box component="pre" sx={{ fontSize: "0.7rem", bgcolor: "background.default", p: 1, borderRadius: 1, overflow: "auto", maxHeight: 150 }}>
                                {typeof run.test_results_before === "string" ? run.test_results_before : JSON.stringify(run.test_results_before, null, 2)}
                              </Box>
                            </Box>
                          )}
                        </Box>
                      </TableCell>
                    </TableRow>
                  )}
                </React.Fragment>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </>
  );
}
