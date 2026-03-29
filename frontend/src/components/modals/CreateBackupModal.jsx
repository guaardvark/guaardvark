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
  Card,
  CardActionArea,
  CardContent,
} from "@mui/material";
import StorageIcon from "@mui/icons-material/Storage";
import CloudDoneIcon from "@mui/icons-material/CloudDone";
import CodeIcon from "@mui/icons-material/Code";

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

const COMPONENT_LABELS = {
  clients: "Clients",
  documents: "Documents & Files",
  projects: "Projects",
  tasks: "Tasks",
  websites: "Websites",
  chats: "Chat History",
  rules: "Rules & Prompts",
  system_settings: "System Settings",
};

const CreateBackupModal = ({ open, onClose, onCreate, isProcessing }) => {
  const [selected, setSelected] = useState([]);
  const [backupName, setBackupName] = useState("");
  const [selectedType, setSelectedType] = useState(null);
  const [includePlugins, setIncludePlugins] = useState(false);

  const handleChange = (e) => {
    const { name, checked } = e.target;
    setSelected((prev) =>
      checked ? [...prev, name] : prev.filter((c) => c !== name),
    );
  };

  const handleCreate = () => {
    if (!selectedType || !onCreate) return;
    if (selectedType === "data") {
      onCreate({ type: "data", components: selected.length > 0 ? selected : null, name: backupName, include_plugins: includePlugins });
    } else {
      onCreate({ type: selectedType, name: backupName });
    }
  };

  const handleSelectAll = () => setSelected([...DATA_COMPONENTS]);
  const handleSelectNone = () => setSelected([]);

  const handleClose = () => {
    setSelectedType(null);
    setSelected([]);
    setBackupName("");
    setIncludePlugins(false);
    onClose();
  };

  const typeCards = [
    {
      key: "data",
      icon: <StorageIcon sx={{ fontSize: 36 }} />,
      title: "Data Backup",
      subtitle: "Database, uploads, settings",
      description: "Back up your application data with optional component selection. Typically 20-200 MB.",
      color: "primary",
    },
    {
      key: "full",
      icon: <CloudDoneIcon sx={{ fontSize: 36 }} />,
      title: "Full Backup",
      subtitle: "Everything for deployment",
      description: "Complete system: code, config, database, uploads, and all data. Deployable to a new machine.",
      color: "success",
    },
    {
      key: "code_release",
      icon: <CodeIcon sx={{ fontSize: 36 }} />,
      title: "Code Release",
      subtitle: "Source code only, zero data",
      description: "For distribution or fresh installs. Recipients run ./start.sh for a clean setup.",
      color: "warning",
    },
  ];

  return (
    <Dialog open={open} onClose={handleClose} fullWidth maxWidth="md">
      <DialogTitle>Create Backup</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ mb: 2 }}>
          <TextField
            fullWidth
            label="Backup Name (Optional)"
            variant="outlined"
            value={backupName}
            onChange={(e) => setBackupName(e.target.value)}
            helperText="Custom name for the backup file (timestamp appended automatically)"
            size="small"
          />
        </Box>

        {/* Type selector cards */}
        <Typography variant="subtitle2" color="text.secondary" gutterBottom>
          Select backup type:
        </Typography>
        <Box sx={{ display: "flex", gap: 1.5, mb: 2, flexWrap: "wrap" }}>
          {typeCards.map((card) => (
            <Card
              key={card.key}
              variant="outlined"
              sx={{
                flex: "1 1 180px",
                minWidth: 180,
                border: 2,
                borderColor: selectedType === card.key ? `${card.color}.main` : "divider",
                bgcolor: selectedType === card.key ? `${card.color}.main` + "0A" : "transparent",
                transition: "all 0.15s",
              }}
            >
              <CardActionArea onClick={() => setSelectedType(card.key)} sx={{ height: "100%" }}>
                <CardContent sx={{ textAlign: "center", py: 2, px: 1.5 }}>
                  <Box sx={{ color: `${card.color}.main`, mb: 0.5 }}>{card.icon}</Box>
                  <Typography variant="subtitle2">{card.title}</Typography>
                  <Typography variant="caption" color="text.secondary" display="block">
                    {card.subtitle}
                  </Typography>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Box>

        {selectedType && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <Typography variant="body2">
              {typeCards.find((c) => c.key === selectedType)?.description}
            </Typography>
          </Alert>
        )}

        {/* Component selection (only for data backups) */}
        {selectedType === "data" && (
          <>
            <Divider sx={{ my: 2 }} />
            <Typography variant="subtitle2" color="text.secondary" gutterBottom>
              Select components (leave empty for all):
            </Typography>
            <Box sx={{ display: "flex", gap: 1, mb: 1 }}>
              <Button size="small" onClick={handleSelectAll}>Select All</Button>
              <Button size="small" onClick={handleSelectNone}>Select None</Button>
            </Box>
            <FormGroup row>
              {DATA_COMPONENTS.map((c) => (
                <FormControlLabel
                  key={c}
                  sx={{ width: "48%", minWidth: 170 }}
                  control={
                    <Checkbox
                      name={c}
                      size="small"
                      checked={selected.includes(c)}
                      onChange={handleChange}
                    />
                  }
                  label={<Typography variant="body2">{COMPONENT_LABELS[c] || c}</Typography>}
                />
              ))}
            </FormGroup>
            <Divider sx={{ my: 2 }} />
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={includePlugins}
                  onChange={(e) => setIncludePlugins(e.target.checked)}
                />
              }
              label={
                <Typography variant="body2">
                  Include Plugins (scripts, configs — excludes models and datasets)
                </Typography>
              }
            />
          </>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose} disabled={isProcessing}>
          Cancel
        </Button>
        <Button
          onClick={handleCreate}
          disabled={!selectedType || isProcessing}
          variant="contained"
          color={selectedType ? typeCards.find((c) => c.key === selectedType)?.color || "primary" : "primary"}
        >
          {isProcessing ? <CircularProgress size={20} sx={{ mr: 1 }} /> : null}
          {isProcessing ? "Creating..." : selectedType ? `Create ${typeCards.find((c) => c.key === selectedType)?.title}` : "Select a Type"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateBackupModal;
