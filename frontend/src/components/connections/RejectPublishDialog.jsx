import React, { useEffect, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from "@mui/material";
import { useUiTranslation } from "../../i18n";

/**
 * Confirm rejecting a queued publish, capturing why.
 *
 * The reason is stored on the record and is the only explanation anyone gets
 * later, so it is worth asking for even though the backend defaults it.
 */
const RejectPublishDialog = ({ open, record, busy = false, onCancel, onConfirm }) => {
  const { t } = useUiTranslation();
  const [reason, setReason] = useState("");

  useEffect(() => {
    if (open) setReason("");
  }, [open]);

  const destination = record
    ? `${record.platform}${record.title ? ` — ${record.title}` : ""}`
    : "";

  return (
    <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="sm" fullWidth>
      <DialogTitle>{t("connections.rejectDialog.title")}</DialogTitle>
      <DialogContent>
        <DialogContentText sx={{ mb: 2 }}>
          {destination && <strong>{destination}</strong>}
          {destination && <br />}
          {t("connections.rejectDialog.body")}
        </DialogContentText>
        <TextField
          autoFocus
          fullWidth
          multiline
          minRows={2}
          label={t("connections.rejectDialog.reasonLabel")}
          placeholder={t("connections.rejectDialog.reasonPlaceholder")}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={busy}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>
          {t("connections.rejectDialog.keep")}
        </Button>
        <Button
          onClick={() => onConfirm(reason.trim())}
          color="error"
          variant="contained"
          disabled={busy}
        >
          {busy ? t("connections.rejectDialog.rejecting") : t("connections.rejectDialog.reject")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RejectPublishDialog;
