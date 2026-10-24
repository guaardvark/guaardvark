import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  FormGroup,
  FormControlLabel,
  Checkbox,
  TextField,
  Typography,
  CircularProgress,
} from "@mui/material";
import { useUiTranslation } from "../../i18n";

const PurgeIndexModal = ({ open, onClose, onConfirm, isProcessing }) => {
  const { t } = useUiTranslation();
  const [options, setOptions] = useState({
    purgeDocuments: false,
    purgeEmbeddings: false,
    purgeMetadata: false,
    afterDate: "",
    beforeDate: "",
  });

  const handleChange = (e) => {
    const { name, type, checked, value } = e.target;
    setOptions((prev) => ({
      ...prev,
      [name]: type === "checkbox" ? checked : value,
    }));
  };

  const handleConfirm = () => {
    if (onConfirm) onConfirm(options);
  };

  const labelProps = { shrink: true };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{t("backups.purgeIndex.title")}</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ mb: 2 }}>
          {t("backups.purgeIndex.intro")}
        </Typography>
        <FormGroup>
          <FormControlLabel
            control={
              <Checkbox
                name="purgeDocuments"
                checked={options.purgeDocuments}
                onChange={handleChange}
              />
            }
            label={t("backups.purgeIndex.indexedDocuments")}
          />
          <FormControlLabel
            control={
              <Checkbox
                name="purgeEmbeddings"
                checked={options.purgeEmbeddings}
                onChange={handleChange}
              />
            }
            label={t("backups.purgeIndex.embeddings")}
          />
          <FormControlLabel
            control={
              <Checkbox
                name="purgeMetadata"
                checked={options.purgeMetadata}
                onChange={handleChange}
              />
            }
            label={t("backups.purgeIndex.metadata")}
          />
        </FormGroup>
        <TextField
          label={t("backups.purgeIndex.fromDate")}
          type="date"
          name="afterDate"
          value={options.afterDate}
          onChange={handleChange}
          fullWidth
          margin="dense"
          InputLabelProps={labelProps}
        />
        <TextField
          label={t("backups.purgeIndex.toDate")}
          type="date"
          name="beforeDate"
          value={options.beforeDate}
          onChange={handleChange}
          fullWidth
          margin="dense"
          InputLabelProps={labelProps}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isProcessing}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          onClick={handleConfirm}
          disabled={isProcessing}
          color="error"
          variant="contained"
        >
          {isProcessing ? (
            <CircularProgress size={24} color="inherit" />
          ) : (
            t("backups.purgeIndex.purge")
          )}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default PurgeIndexModal;
