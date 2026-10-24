import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  CircularProgress,
  Typography,
  Box,
  Alert,
  Tabs,
  Tab,
  List,
  ListItem,
  ListItemText,
  ListItemButton,
  Chip,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import StorageIcon from "@mui/icons-material/Storage";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function formatDate(timestamp) {
  if (!timestamp) return "";
  try {
    const d = new Date(timestamp * 1000);
    return translate("backups.shared.dateAtTime", {
      date: d.toLocaleDateString(uiLocale(), { month: "short", day: "numeric", year: "numeric" }),
      time: d.toLocaleTimeString(uiLocale(), { hour: "2-digit", minute: "2-digit" }),
    });
  } catch {
    return "";
  }
}

const typeConfig = () => ({
  full: { label: translate("backups.shared.typeFull"), color: "success" },
  data: { label: translate("backups.shared.typeData"), color: "primary" },
  code: { label: translate("backups.shared.typeCode"), color: "warning" },
  auto: { label: translate("backups.shared.typeAuto"), color: "default" },
});

const RestoreBackupModal = ({ open, onClose, onRestore, isProcessing, backups = [] }) => {
  const { t } = useUiTranslation();
  const [tab, setTab] = useState(0);
  const [file, setFile] = useState(null);
  const [fileInfo, setFileInfo] = useState(null);
  const [selectedBackup, setSelectedBackup] = useState(null);
  const [confirmed, setConfirmed] = useState(false);

  // Reset state on open
  useEffect(() => {
    if (open) {
      setFile(null);
      setFileInfo(null);
      setSelectedBackup(null);
      setConfirmed(false);
      // Default to server tab if backups exist
      setTab(backups.length > 0 ? 0 : 1);
    }
  }, [open, backups.length]);

  // Normalize backups (support string[] and object[])
  const normalizedBackups = backups.map((b) =>
    typeof b === "string" ? { name: b, size: 0, type: "data", modified: 0 } : b
  );

  const handleFile = (e) => {
    const selectedFile = e.target.files?.[0] || null;
    setFile(selectedFile);
    setSelectedBackup(null);
    setConfirmed(false);
    if (selectedFile) {
      setFileInfo({
        name: selectedFile.name,
        size: formatBytes(selectedFile.size),
      });
    } else {
      setFileInfo(null);
    }
  };

  const handleRestore = () => {
    if (!confirmed) {
      setConfirmed(true);
      return;
    }
    if (tab === 0 && selectedBackup) {
      onRestore?.(selectedBackup);
    } else if (tab === 1 && file) {
      onRestore?.(file);
    }
  };

  const hasSelection = (tab === 0 && selectedBackup) || (tab === 1 && file);
  const types = typeConfig();

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{t("backups.restoreBackup.title")}</DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        <Tabs
          value={tab}
          onChange={(_, v) => { setTab(v); setConfirmed(false); }}
          variant="fullWidth"
          sx={{ borderBottom: 1, borderColor: "divider" }}
        >
          <Tab icon={<StorageIcon fontSize="small" />} iconPosition="start" label={t("backups.restoreBackup.serverBackups")} />
          <Tab icon={<UploadFileIcon fontSize="small" />} iconPosition="start" label={t("backups.restoreBackup.importFile")} />
        </Tabs>

        <Box sx={{ p: 2 }}>
          {/* Tab 0: Pick from server backups */}
          {tab === 0 && (
            normalizedBackups.length === 0 ? (
              <Typography variant="body2" color="text.secondary" textAlign="center" py={3}>
                {t("backups.restoreBackup.noServerBackups")}
              </Typography>
            ) : (
              <List disablePadding sx={{ maxHeight: 300, overflow: "auto" }}>
                {normalizedBackups.map((b) => {
                  const cfg = types[b.type] || types.data;
                  const isSelected = selectedBackup === b.name;
                  return (
                    <ListItem key={b.name} disablePadding sx={{ mb: 0.5 }}>
                      <ListItemButton
                        selected={isSelected}
                        onClick={() => { setSelectedBackup(b.name); setConfirmed(false); }}
                        sx={{ borderRadius: 1, border: 1, borderColor: isSelected ? "primary.main" : "divider" }}
                      >
                        <ListItemText
                          primary={
                            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                              <Typography variant="body2" sx={{ fontWeight: 500, wordBreak: "break-all", flex: 1 }}>
                                {b.name}
                              </Typography>
                              <Chip label={cfg.label} size="small" color={cfg.color} variant="outlined" />
                              {b.size > 0 && (
                                <Typography variant="caption" color="text.secondary" sx={{ flexShrink: 0 }}>
                                  {formatBytes(b.size)}
                                </Typography>
                              )}
                            </Box>
                          }
                          secondary={b.modified ? formatDate(b.modified) : undefined}
                        />
                      </ListItemButton>
                    </ListItem>
                  );
                })}
              </List>
            )
          )}

          {/* Tab 1: Upload a file */}
          {tab === 1 && (
            <Box>
              <Button
                variant="outlined"
                component="label"
                fullWidth
                sx={{ py: 3, borderStyle: "dashed" }}
              >
                {fileInfo ? fileInfo.name : t("backups.restoreBackup.chooseFile")}
                <input type="file" hidden accept=".zip" onChange={handleFile} />
              </Button>
              {fileInfo && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
                  {t("backups.restoreBackup.size", { size: fileInfo.size })}
                </Typography>
              )}
            </Box>
          )}

          {/* Warning */}
          {hasSelection && (
            <Alert severity="warning" sx={{ mt: 2 }}>
              <Typography variant="body2">
                {t("backups.restoreBackup.overwriteWarning")}
              </Typography>
            </Alert>
          )}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isProcessing}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          variant="contained"
          color={confirmed ? "error" : "primary"}
          onClick={handleRestore}
          disabled={!hasSelection || isProcessing}
        >
          {isProcessing ? (
            <CircularProgress size={20} sx={{ mr: 1 }} />
          ) : null}
          {isProcessing ? t("backups.restoreBackup.restoring") : confirmed ? t("backups.restoreBackup.confirmRestore") : t("backups.restoreBackup.restore")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default RestoreBackupModal;
