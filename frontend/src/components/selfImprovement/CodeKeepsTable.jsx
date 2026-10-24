// Code keeps: swarm arms the autoresearch director staged as pending fixes,
// approved or rejected here. Shown on the Autoresearch and Self-improvement
// pages.
import React from "react";
import {
  Button,
  Chip,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

/** A server time as a local date; a time without a zone is UTC. */
export const formatServerTime = (iso) => {
  if (!iso) return "—";
  try {
    const text = String(iso);
    return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : `${text}Z`).toLocaleString(uiLocale());
  } catch {
    return iso;
  }
};

const formatDate = formatServerTime;

// A pending fix's status as shown; the stored value stays the one compared.
const fixStatusLabel = (status) =>
  ({
    proposed: translate("selfImprovement.fixStatus.proposed"),
    triaged: translate("selfImprovement.fixStatus.triaged"),
    approved: translate("selfImprovement.fixStatus.approved"),
    applied: translate("selfImprovement.fixStatus.applied"),
    rejected: translate("selfImprovement.fixStatus.rejected"),
  })[status] ?? status;

export default function CodeKeepsTable({ keeps, onDecide }) {
  const { t } = useUiTranslation();
  if (!keeps || keeps.length === 0) return null;
  return (
    <Paper
      elevation={0}
      sx={{ p: 2, mb: 3, border: 1, borderColor: "divider" }}
    >
      <Typography variant="h6" sx={{ mb: 1 }}>
        {t("selfImprovement.codeKeeps.title")}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        {t("selfImprovement.codeKeeps.intro")}
      </Typography>
      <TableContainer>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t("selfImprovement.codeKeeps.description")}</TableCell>
              <TableCell>{t("selfImprovement.codeKeeps.status")}</TableCell>
              <TableCell>{t("selfImprovement.codeKeeps.created")}</TableCell>
              <TableCell align="right">{t("selfImprovement.codeKeeps.actions")}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {keeps.map((fix) => (
              <TableRow key={fix.id}>
                <TableCell>{fix.fix_description}</TableCell>
                <TableCell>
                  <Chip label={fixStatusLabel(fix.status)} size="small" sx={{ height: 20, fontSize: "0.7rem" }} />
                </TableCell>
                <TableCell>{formatDate(fix.created_at)}</TableCell>
                <TableCell align="right">
                  {fix.status === "proposed" && (
                    <>
                      <Button size="small" onClick={() => onDecide(fix.id, "approve")}>
                        {t("selfImprovement.codeKeeps.approve")}
                      </Button>
                      <Button size="small" color="warning" onClick={() => onDecide(fix.id, "reject")}>
                        {t("selfImprovement.codeKeeps.reject")}
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Paper>
  );
}
