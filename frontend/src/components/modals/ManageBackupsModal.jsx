import React, { useState } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  List,
  ListItem,
  ListItemText,
  IconButton,
  Tooltip,
  Typography,
  Box,
  Chip,
} from "@mui/material";
import RestoreIcon from "@mui/icons-material/Restore";
import DeleteIcon from "@mui/icons-material/Delete";
import DownloadIcon from "@mui/icons-material/Download";
import RefreshIcon from "@mui/icons-material/Refresh";

const TYPE_CONFIG = {
  full: { label: "Full", color: "success" },
  data: { label: "Data", color: "primary" },
  code: { label: "Code", color: "warning" },
  auto: { label: "Auto", color: "default" },
};

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function formatDate(timestamp) {
  if (!timestamp) return "Unknown";
  try {
    const d = new Date(timestamp * 1000);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) +
      " at " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "Unknown";
  }
}

const ManageBackupsModal = ({
  open,
  onClose,
  onRestore,
  onDelete,
  onDownload,
  onRefresh,
  backups = [],
}) => {
  const [confirmDelete, setConfirmDelete] = useState(null);

  // Support both old (string[]) and new (object[]) API formats
  const normalizedBackups = backups.map((b) =>
    typeof b === "string" ? { name: b, size: 0, type: "data", modified: 0 } : b
  );

  const handleDelete = (name) => {
    if (confirmDelete === name) {
      onDelete?.(name);
      setConfirmDelete(null);
    } else {
      setConfirmDelete(name);
      // Auto-reset confirm after 3s
      setTimeout(() => setConfirmDelete((prev) => (prev === name ? null : prev)), 3000);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle sx={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        Manage Backups
        <Tooltip title="Refresh list">
          <IconButton onClick={onRefresh} size="small">
            <RefreshIcon />
          </IconButton>
        </Tooltip>
      </DialogTitle>
      <DialogContent dividers>
        {normalizedBackups.length === 0 ? (
          <Box sx={{ textAlign: "center", py: 4 }}>
            <Typography variant="body1" color="text.secondary">
              No backups found
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Create a backup first to see it here
            </Typography>
          </Box>
        ) : (
          <List disablePadding>
            {normalizedBackups.map((backup) => {
              const cfg = TYPE_CONFIG[backup.type] || TYPE_CONFIG.data;
              const isConfirming = confirmDelete === backup.name;
              return (
                <ListItem
                  key={backup.name}
                  sx={{
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1,
                    mb: 1,
                    pr: 16,
                  }}
                  secondaryAction={
                    <Box sx={{ display: "flex", gap: 0.5 }}>
                      <Tooltip title="Download">
                        <IconButton onClick={() => onDownload?.(backup.name)} size="small">
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Restore from this backup">
                        <IconButton onClick={() => onRestore?.(backup.name)} color="primary" size="small">
                          <RestoreIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={isConfirming ? "Click again to confirm" : "Delete"}>
                        <IconButton
                          onClick={() => handleDelete(backup.name)}
                          color={isConfirming ? "error" : "default"}
                          size="small"
                          sx={isConfirming ? { animation: "pulse 0.5s" } : {}}
                        >
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Box>
                  }
                >
                  <ListItemText
                    primary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
                        <Typography variant="body2" component="span" sx={{ fontWeight: 500, wordBreak: "break-all" }}>
                          {backup.name}
                        </Typography>
                        <Chip label={cfg.label} size="small" color={cfg.color} variant="outlined" />
                        {backup.size > 0 && (
                          <Typography variant="caption" color="text.secondary">
                            {formatBytes(backup.size)}
                          </Typography>
                        )}
                      </Box>
                    }
                    secondary={backup.modified ? formatDate(backup.modified) : undefined}
                  />
                </ListItem>
              );
            })}
          </List>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
};

export default ManageBackupsModal;
