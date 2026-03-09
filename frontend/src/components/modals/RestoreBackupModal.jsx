import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  CircularProgress,
  Typography,
  Box,
} from "@mui/material";

const RestoreBackupModal = ({ open, onClose, onRestore, isProcessing }) => {
  const [file, setFile] = useState(null);
  const [fileInfo, setFileInfo] = useState(null);

  const handleFile = (e) => {
    const selectedFile = e.target.files?.[0] || null;
    setFile(selectedFile);
    setFileInfo(null);
    
    if (selectedFile) {
      setFileInfo({
        name: selectedFile.name,
        size: (selectedFile.size / 1024).toFixed(1) + ' KB',
        type: selectedFile.type || 'Unknown'
      });
    }
  };

  const handleRestore = () => {
    if (onRestore && file) onRestore(file);
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>Restore Backup</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ mb: 2 }}>
          <Typography variant="body2" color="text.secondary" gutterBottom>
            Select a backup file to restore from:
          </Typography>
        </Box>
        <TextField
          type="file"
          fullWidth
          inputProps={{ accept: ".zip,.json" }}
          onChange={handleFile}
          helperText="Supported formats: .zip (server backup) or .json (exported data)"
        />
        {fileInfo && (
          <Box sx={{ mt: 2, p: 1, bgcolor: 'info.light', borderRadius: 1 }}>
            <Typography variant="body2" color="info.contrastText">
              <strong>File:</strong> {fileInfo.name}<br/>
              <strong>Size:</strong> {fileInfo.size}<br/>
              <strong>Type:</strong> {fileInfo.type}
            </Typography>
          </Box>
        )}
        <Box sx={{ mt: 2, p: 1, bgcolor: 'warning.light', borderRadius: 1 }}>
          <Typography variant="body2" color="warning.contrastText">
            <strong>Warning:</strong> Restoring a backup will overwrite existing data. 
            Make sure to backup current data first.
          </Typography>
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isProcessing}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleRestore}
          disabled={!file || isProcessing}
        >
          {isProcessing ? <CircularProgress size={24} /> : "Restore"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RestoreBackupModal;
