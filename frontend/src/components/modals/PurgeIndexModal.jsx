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

const PurgeIndexModal = ({ open, onClose, onConfirm, isProcessing }) => {
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
      <DialogTitle>Purge Index</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" sx={{ mb: 2 }}>
          Select which parts of the index to purge and optionally specify a date
          range.
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
            label="Indexed Documents"
          />
          <FormControlLabel
            control={
              <Checkbox
                name="purgeEmbeddings"
                checked={options.purgeEmbeddings}
                onChange={handleChange}
              />
            }
            label="Embeddings"
          />
          <FormControlLabel
            control={
              <Checkbox
                name="purgeMetadata"
                checked={options.purgeMetadata}
                onChange={handleChange}
              />
            }
            label="Metadata"
          />
        </FormGroup>
        <TextField
          label="From Date"
          type="date"
          name="afterDate"
          value={options.afterDate}
          onChange={handleChange}
          fullWidth
          margin="dense"
          InputLabelProps={labelProps}
        />
        <TextField
          label="To Date"
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
          Cancel
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
            "Purge"
          )}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default PurgeIndexModal;
