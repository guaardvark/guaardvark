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
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const typeConfig = () => ({
  full: { label: translate("backups.shared.typeFull"), color: "success" },
  data: { label: translate("backups.shared.typeData"), color: "primary" },
  code: { label: translate("backups.shared.typeCode"), color: "warning" },
  auto: { label: translate("backups.shared.typeAuto"), color: "default" },
});

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function formatDate(timestamp) {
  if (!timestamp) return translate("backups.shared.unknownDate");
  try {
    const d = new Date(timestamp * 1000);
    return translate("backups.shared.dateAtTime", {
      date: d.toLocaleDateString(uiLocale(), { month: "short", day: "numeric", year: "numeric" }),
      time: d.toLocaleTimeString(uiLocale(), { hour: "2-digit", minute: "2-digit" }),
    });
  } catch {
    return translate("backups.shared.unknownDate");
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
  const { t } = useUiTranslation();
  const [confirmDelete, setConfirmDelete] = useState(null);
  const types = typeConfig();

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
        {t("backups.manageBackups.title")}
        <Tooltip title={t("backups.manageBackups.refreshList")}>
          <IconButton onClick={onRefresh} size="small">
            <RefreshIcon />
          </IconButton>
        </Tooltip>
      </DialogTitle>
      <DialogContent dividers>
        {normalizedBackups.length === 0 ? (
          <Box sx={{ textAlign: "center", py: 4 }}>
            <Typography variant="body1" color="text.secondary">
              {t("backups.manageBackups.empty")}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              {t("backups.manageBackups.emptyHint")}
            </Typography>
          </Box>
        ) : (
          <List disablePadding>
            {normalizedBackups.map((backup) => {
              const cfg = types[backup.type] || types.data;
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
                      <Tooltip title={t("dialogs.shared.download")}>
                        <IconButton onClick={() => onDownload?.(backup.name)} size="small">
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={t("backups.manageBackups.restoreFromThis")}>
                        <IconButton onClick={() => onRestore?.(backup.name)} color="primary" size="small">
                          <RestoreIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title={isConfirming ? t("backups.manageBackups.clickAgainToConfirm") : t("dialogs.shared.delete")}>
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
        <Button onClick={onClose}>{t("dialogs.shared.close")}</Button>
      </DialogActions>
    </Dialog>
  );
};

export default ManageBackupsModal;
