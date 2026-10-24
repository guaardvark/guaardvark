// Asks the person to confirm they may clone a voice before a reference clip
// is imported for cloning (or before an older clip is used). The same step as
// the chat's likeness consent card: nothing is recorded unless they confirm,
// and the backend refuses to clone a clip without the record.
import React from "react";
import PropTypes from "prop-types";
import {
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Typography,
} from "@mui/material";
import { useUiTranslation, translate } from "../../i18n";

// Shown when GET /voice-clips has not supplied the backend's own wording.
export const fallbackVoiceConsentStatement = () => translate("audio.consent.fallbackStatement");

const VoiceConsentDialog = ({ open, clipName, statement, busy, onConfirm, onCancel }) => {
  const { t } = useUiTranslation();
  return (
    <Dialog open={open} onClose={busy ? undefined : onCancel} maxWidth="xs" fullWidth>
      <DialogTitle>{t("audio.consent.title")}</DialogTitle>
      <DialogContent>
        {clipName && (
          <Typography variant="body2" fontWeight="bold" sx={{ mb: 1.5, wordBreak: "break-word" }}>
            {clipName}
          </Typography>
        )}
        <Typography variant="body2" sx={{ mb: 1.5, lineHeight: 1.5 }}>
          {t("audio.consent.youState", { statement: statement || fallbackVoiceConsentStatement() })}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {t("audio.consent.savedWithClip")}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel} disabled={busy}>{t("audio.consent.cancel")}</Button>
        <Button
          variant="contained"
          onClick={onConfirm}
          disabled={busy}
          startIcon={busy ? <CircularProgress size={16} color="inherit" /> : null}
        >
          {t("audio.consent.confirm")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

VoiceConsentDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  clipName: PropTypes.string,
  statement: PropTypes.string,
  busy: PropTypes.bool,
  onConfirm: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default VoiceConsentDialog;
