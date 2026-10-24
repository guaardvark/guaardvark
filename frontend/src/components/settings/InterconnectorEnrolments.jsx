// frontend/src/components/settings/InterconnectorEnrolments.jsx
// Master: machines that asked to join the fleet. Each one shows the
// fingerprint of its key; compare it with the one on that machine's own
// Interconnector page before approving. Above them, the transition window
// for machines that joined the old way, with no key of their own
// (backend/services/interconnector_transition.py).

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  IconButton,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { Refresh as RefreshIcon } from "@mui/icons-material";
import * as interconnectorApi from "../../api/interconnectorService";
import { useSnackbar } from "../common/SnackbarProvider";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const statusLabel = () => ({
  pending: translate("settingsInterconnector.enrolments.status.pending"),
  approved: translate("settingsInterconnector.enrolments.status.approved"),
  revoked: translate("settingsInterconnector.enrolments.status.revoked"),
});

const STATUS_COLOR = {
  pending: "warning",
  approved: "success",
  revoked: "error",
};

const errorText = (err) =>
  err?.data?.error?.message || err?.data?.message || err?.message || translate("settingsInterconnector.enrolments.unknownError");

const machineNames = (machines) =>
  machines.map((m) => m.node_name || m.node_id).join(", ");

/** The transition window, while it is open or a machine still needs it. */
const TransitionNotice = ({ transition, busy, onChange }) => {
  const { t } = useUiTranslation();
  const machines = transition?.legacy_machines || [];
  if (!transition || (!transition.open && machines.length === 0)) return null;
  const until = transition.until ? new Date(transition.until).toLocaleString(uiLocale()) : "";
  const hours = transition.window_hours || 48;
  if (transition.open) {
    return (
      <Alert
        severity="warning"
        sx={{ mb: 2 }}
        action={
          <Button color="inherit" size="small" disabled={busy} onClick={() => onChange(false)}>
            {t("settingsInterconnector.enrolments.closeNow")}
          </Button>
        }
      >
        {t("settingsInterconnector.enrolments.transitionOpen", { until })}
        {machines.length > 0
          ? ` ${t("settingsInterconnector.enrolments.stillOnOldRule", { machines: machineNames(machines) })}`
          : ` ${t("settingsInterconnector.enrolments.allEnrolled")}`}
      </Alert>
    );
  }
  return (
    <Alert
      severity="info"
      sx={{ mb: 2 }}
      action={
        <Button color="inherit" size="small" disabled={busy} onClick={() => onChange(true, hours)}>
          {t("settingsInterconnector.enrolments.allowFor", { hours })}
        </Button>
      }
    >
      {t("settingsInterconnector.enrolments.transitionClosed", { machines: machineNames(machines) })}
    </Alert>
  );
};

const InterconnectorEnrolments = () => {
  const { t } = useUiTranslation();
  const { showMessage } = useSnackbar();
  const [enrolments, setEnrolments] = useState([]);
  const [masterFingerprint, setMasterFingerprint] = useState(null);
  const [transition, setTransition] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    const result = await interconnectorApi.getEnrolments();
    if (result.error) {
      setLoadError(result.error);
      return;
    }
    setLoadError(null);
    setEnrolments(result.data?.enrolments || []);
    setMasterFingerprint(result.data?.master_fingerprint || null);
    setTransition(result.data?.transition || null);
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, 30000);
    return () => clearInterval(interval);
  }, [load]);

  const decide = async (row, action) => {
    const name = row.node_name || row.node_id;
    if (action === "revoke" && !window.confirm(t("settingsInterconnector.enrolments.confirmRevoke", { name }))) return;
    if (action === "remove" && !window.confirm(t("settingsInterconnector.enrolments.confirmRemove", { name }))) return;
    setBusy(`${row.node_id}:${action}`);
    try {
      if (action === "approve") await interconnectorApi.approveEnrolment(row.node_id);
      if (action === "revoke") await interconnectorApi.revokeEnrolment(row.node_id);
      if (action === "remove") await interconnectorApi.removeEnrolment(row.node_id);
      const done = {
        approve: t("settingsInterconnector.enrolments.done.approve", { name }),
        revoke: t("settingsInterconnector.enrolments.done.revoke", { name }),
        remove: t("settingsInterconnector.enrolments.done.remove", { name }),
      };
      showMessage(done[action], "success");
      await load();
    } catch (err) {
      const error = errorText(err);
      const failed = {
        approve: t("settingsInterconnector.enrolments.failed.approve", { name, error }),
        revoke: t("settingsInterconnector.enrolments.failed.revoke", { name, error }),
        remove: t("settingsInterconnector.enrolments.failed.remove", { name, error }),
      };
      showMessage(failed[action], "error");
    } finally {
      setBusy(null);
    }
  };

  const changeTransition = async (open, hours) => {
    setBusy("transition");
    try {
      const result = await interconnectorApi.setTransitionWindow(open, hours);
      setTransition(result.data?.transition || null);
      showMessage(open ? t("settingsInterconnector.enrolments.transitionAllowed", { hours }) : t("settingsInterconnector.enrolments.transitionClosedNow"), "success");
    } catch (err) {
      showMessage(t("settingsInterconnector.enrolments.transitionFailed", { error: errorText(err) }), "error");
    } finally {
      setBusy(null);
    }
  };

  const waiting = enrolments.filter((row) => row.status === "pending").length;

  return (
    <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
        <Typography variant="h6">
          {t("settingsInterconnector.enrolments.title")}
          {waiting ? ` ${t("settingsInterconnector.enrolments.waiting", { count: waiting })}` : ""}
        </Typography>
        <IconButton onClick={load} size="small" aria-label={t("settingsInterconnector.enrolments.refresh")}>
          <RefreshIcon />
        </IconButton>
      </Box>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t("settingsInterconnector.enrolments.intro")}
        {masterFingerprint ? ` ${t("settingsInterconnector.enrolments.masterKey", { fingerprint: masterFingerprint })}` : ""}
      </Typography>

      <TransitionNotice transition={transition} busy={Boolean(busy)} onChange={changeTransition} />

      {loadError ? (
        <Alert severity="error">{t("settingsInterconnector.enrolments.loadFailed", { error: loadError })}</Alert>
      ) : enrolments.length === 0 ? (
        <Alert severity="info">{t("settingsInterconnector.enrolments.none")}</Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>{t("settingsInterconnector.enrolments.columns.state")}</TableCell>
                <TableCell>{t("settingsInterconnector.enrolments.columns.machine")}</TableCell>
                <TableCell>{t("settingsInterconnector.enrolments.columns.key")}</TableCell>
                <TableCell>{t("settingsInterconnector.enrolments.columns.asked")}</TableCell>
                <TableCell align="right">{t("settingsInterconnector.enrolments.columns.actions")}</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {enrolments.map((row) => (
                <TableRow key={row.node_id}>
                  <TableCell>
                    <Chip
                      size="small"
                      label={statusLabel()[row.status] || row.status}
                      color={STATUS_COLOR[row.status] || "default"}
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="body2">{row.node_name || row.node_id}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {[row.hostname, row.host].filter(Boolean).join(" · ")}
                    </Typography>
                  </TableCell>
                  <TableCell sx={{ fontFamily: "monospace" }}>{row.fingerprint}</TableCell>
                  <TableCell>
                    {row.requested_at ? new Date(row.requested_at).toLocaleString(uiLocale()) : ""}
                  </TableCell>
                  <TableCell align="right">
                    <Box display="flex" gap={1} justifyContent="flex-end" flexWrap="wrap">
                      {row.status !== "approved" && (
                        <Button
                          size="small"
                          variant="contained"
                          disabled={Boolean(busy)}
                          onClick={() => decide(row, "approve")}
                        >
                          {t("settingsInterconnector.enrolments.approve")}
                        </Button>
                      )}
                      {row.status === "approved" && (
                        <Button
                          size="small"
                          color="error"
                          disabled={Boolean(busy)}
                          onClick={() => decide(row, "revoke")}
                        >
                          {t("settingsInterconnector.enrolments.revoke")}
                        </Button>
                      )}
                      <Button
                        size="small"
                        color="error"
                        disabled={Boolean(busy)}
                        onClick={() => decide(row, "remove")}
                      >
                        {row.status === "pending" ? t("settingsInterconnector.enrolments.decline") : t("settingsInterconnector.enrolments.remove")}
                      </Button>
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
};

export default InterconnectorEnrolments;
