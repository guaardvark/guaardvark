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
  CircularProgress,
  Typography,
  Box,
  Alert,
  TextField,
  Divider,
} from "@mui/material";

const DATA_COMPONENTS = [
  "clients",
  "documents",
  "projects",
  "tasks",
  "websites",
  "chats",
  "rules",
  "system_settings",
];

const CreateBackupModal = ({ open, onClose, onCreate, isProcessing }) => {
  const [selected, setSelected] = useState([]);
  const [backupName, setBackupName] = useState("");

  const handleChange = (e) => {
    const { name, checked } = e.target;
    setSelected((prev) =>
      checked ? [...prev, name] : prev.filter((c) => c !== name),
    );
  };

  const handleCreate = (type, components) => {
    if (onCreate) onCreate({ type, components, name: backupName });
  };

  const handleSelectAll = () => setSelected(DATA_COMPONENTS);
  const handleSelectNone = () => setSelected([]);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>Create Backup</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ mb: 3 }}>
          <TextField
            fullWidth
            label="Backup Name (Optional)"
            variant="outlined"
            value={backupName}
            onChange={(e) => setBackupName(e.target.value)}
            helperText="Enter a name for the backup file (timestamp will be appended)"
            size="small"
          />
        </Box>

        <Alert severity="info" sx={{ mb: 2 }}>
          <Typography variant="body2">
            <strong>Data Backup</strong> — Database, uploads, settings, and state files.
            Select specific components below or use &quot;All Data&quot; for everything.
          </Typography>
        </Alert>

        <Box sx={{ mb: 1 }}>
          <Box sx={{ display: "flex", gap: 1, mb: 1 }}>
            <Button size="small" onClick={handleSelectAll}>Select All</Button>
            <Button size="small" onClick={handleSelectNone}>Select None</Button>
          </Box>
        </Box>
        <FormGroup>
          {DATA_COMPONENTS.map((c) => (
            <FormControlLabel
              key={c}
              control={
                <Checkbox
                  name={c}
                  checked={selected.includes(c)}
                  onChange={handleChange}
                />
              }
              label={c.charAt(0).toUpperCase() + c.slice(1).replace("_", " ")}
            />
          ))}
        </FormGroup>

        <Divider sx={{ my: 2 }} />

        <Alert severity="success" sx={{ mb: 2 }}>
          <Typography variant="body2">
            <strong>Full Backup</strong> — Everything needed to deploy on a new machine:
            source code, configuration, database, uploads, and all data.
          </Typography>
        </Alert>

        <Alert severity="warning">
          <Typography variant="body2">
            <strong>Code Release</strong> — Source code and configuration only, zero data.
            For distributing to new machines or open-source releases. Recipients run ./start.sh
            for a fresh, clean installation.
          </Typography>
        </Alert>
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} disabled={isProcessing}>
          Cancel
        </Button>
        <Button
          onClick={() => handleCreate("data", selected.length > 0 ? selected : null)}
          disabled={isProcessing}
          variant="outlined"
        >
          {isProcessing ? <CircularProgress size={24} /> : selected.length > 0 ? "Data (Selected)" : "All Data"}
        </Button>
        <Button
          onClick={() => handleCreate("full")}
          disabled={isProcessing}
          variant="contained"
          color="success"
        >
          Full Backup
        </Button>
        <Button
          onClick={() => handleCreate("code_release")}
          disabled={isProcessing}
          variant="contained"
          color="warning"
        >
          Code Release
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateBackupModal;
