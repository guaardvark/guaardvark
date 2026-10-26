/**
 * SelfEditNotice - under a tool card that changed Guaardvark's own code: what
 * changed, how the checks went, whether a restart is due, and Revert.
 */
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Typography } from "@mui/material";
import { ActionButton } from "../settings/ui";
import { revertSelfEdit } from "../../api/selfEditService";
import { useUiTranslation } from "../../i18n";

const SelfEditNotice = ({ edit }) => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(edit?.status);
  const [busy, setBusy] = useState(false);
  const [conflicts, setConflicts] = useState(null);
  const [error, setError] = useState("");
  if (!edit) return null;
  const files = (edit.files || []).join(", ");

  const revert = async (force = false) => {
    setBusy(true);
    setError("");
    try {
      const res = await revertSelfEdit(edit.group || edit.id, { force });
      setStatus("reverted");
      setConflicts(null);
      if (res?.restart_needed) setError(t("chat.selfEdit.revertedRestart"));
    } catch (e) {
      if (e.status === 409 && e.data?.conflicts) setConflicts(e.data.conflicts);
      else setError(e.message || String(e));
    } finally {
      setBusy(false);
    }
  };

  const line = {
    applied: edit.restart_needed ? t("chat.selfEdit.appliedRestart", { files }) : t("chat.selfEdit.appliedLive", { files }),
    live: t("chat.selfEdit.live", { files }),
    failed_checks: t("chat.selfEdit.undone", { files, error: edit.error || "" }),
    auto_reverted: t("chat.selfEdit.autoReverted", { files }),
    reverted: t("chat.selfEdit.reverted", { files }),
  }[status] || files;

  return (
    <Box data-testid="self-edit-notice" sx={{ mt: 0.75, p: 1, borderRadius: 1, border: 1, borderColor: "divider" }}>
      <Typography variant="caption" sx={{ display: "block" }}>{line}</Typography>
      {(edit.checks || []).length > 0 && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {(edit.checks || []).map((c) => `${c.check}: ${c.ok ? t("chat.selfEdit.checkOk") : c.detail}`).join(" · ")}
        </Typography>
      )}
      {conflicts && (
        <Alert severity="warning" sx={{ mt: 0.5, py: 0 }}>
          {t("chat.selfEdit.conflicts", { files: conflicts.join(", ") })}
        </Alert>
      )}
      {error && <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>{error}</Typography>}
      {["applied", "live"].includes(status) && (
        <Box sx={{ mt: 0.5, display: "flex", gap: 1 }}>
          <ActionButton kind="neutral" loading={busy} onClick={() => revert(Boolean(conflicts))}>
            {conflicts ? t("chat.selfEdit.revertAnyway") : t("chat.selfEdit.revert")}
          </ActionButton>
        </Box>
      )}
    </Box>
  );
};

SelfEditNotice.propTypes = { edit: PropTypes.object };

export default SelfEditNotice;
