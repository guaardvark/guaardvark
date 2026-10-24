// frontend/src/components/settings/ui/ConfirmActionDialog.jsx
// The one confirmation every destructive action goes through. Says what will be
// removed, shows real counts when the caller has them, and names what is kept.
// `reversible` is for an action that switches back on its own or can be put
// back (an update with rollback): neutral styling and no "cannot be undone".

import React from "react";
import { Box, Dialog, DialogActions, DialogContent, DialogTitle, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import ActionButton from "./ActionButton";
import { translate, useUiTranslation } from "../../../i18n";

/**
 * @param {boolean}  open
 * @param {string}   title
 * @param {ReactNode} [description]
 * @param {Array<{label:string,value:ReactNode}>} [facts]  counts or sizes
 * @param {ReactNode} [keeps]      what is NOT touched
 * @param {ReactNode} [extra]      optional extra control (e.g. keep-vs-delete chip)
 * @param {string}   [confirmLabel]
 * @param {boolean}  [busy]
 * @param {boolean}  [reversible]  neutral styling, no "cannot be undone"
 * @param {function} onConfirm
 * @param {function} onClose
 */
const ConfirmActionDialog = ({
  open,
  title,
  description,
  facts = [],
  keeps,
  extra,
  confirmLabel = translate("settings.ui.confirmDialog.confirm"),
  busy = false,
  reversible = false,
  onConfirm,
  onClose,
}) => {
  const { t } = useUiTranslation();
  return (
  <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
    <DialogTitle
      sx={{ display: "flex", alignItems: "center", gap: 1, color: reversible ? "text.primary" : "error.main", fontSize: "1rem" }}
    >
      {!reversible && <WarningAmberIcon fontSize="small" />}
      {title}
    </DialogTitle>
    <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      {description && (
        <Typography variant="body2" color="text.secondary">
          {description}
        </Typography>
      )}
      {facts.length > 0 && (
        <Box
          sx={(theme) => ({
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            columnGap: 2,
            rowGap: 0.5,
            p: 1.25,
            borderRadius: "6px",
            bgcolor: alpha(reversible ? theme.palette.text.primary : theme.palette.error.main, 0.06),
            border: `1px solid ${alpha(reversible ? theme.palette.text.primary : theme.palette.error.main, 0.2)}`,
          })}
        >
          {facts.map((f) => (
            <React.Fragment key={f.label}>
              <Typography variant="caption" color="text.secondary">
                {f.label}
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 500 }}>
                {f.value}
              </Typography>
            </React.Fragment>
          ))}
        </Box>
      )}
      {extra}
      {keeps && (
        <Typography variant="caption" color="text.secondary">
          {t("settings.ui.confirmDialog.notTouched", { keeps })}
        </Typography>
      )}
      {!reversible && (
        <Typography variant="caption" sx={{ color: "error.main" }}>
          {t("settings.ui.confirmDialog.cannotUndo")}
        </Typography>
      )}
    </DialogContent>
    <DialogActions>
      <ActionButton onClick={onClose} disabled={busy}>
        {t("settings.ui.confirmDialog.cancel")}
      </ActionButton>
      <ActionButton kind={reversible ? "neutral" : "destructive"} onClick={onConfirm} loading={busy}>
        {confirmLabel}
      </ActionButton>
    </DialogActions>
  </Dialog>
  );
};

export default ConfirmActionDialog;
