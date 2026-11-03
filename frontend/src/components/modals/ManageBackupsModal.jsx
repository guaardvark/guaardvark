import React, { useEffect, useState } from "react";
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
  Switch,
  FormControlLabel,
  TextField,
  Alert,
} from "@mui/material";
import RestoreIcon from "@mui/icons-material/Restore";
import DeleteIcon from "@mui/icons-material/Delete";
import DownloadIcon from "@mui/icons-material/Download";
import RefreshIcon from "@mui/icons-material/Refresh";
import PushPinIcon from "@mui/icons-material/PushPin";
import PushPinOutlinedIcon from "@mui/icons-material/PushPinOutlined";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const typeConfig = () => ({
  full: { label: translate("backups.shared.typeFull"), color: "success" },
  data: { label: translate("backups.shared.typeData"), color: "primary" },
  code: { label: translate("backups.shared.typeCode"), color: "warning" },
  auto: { label: translate("backups.shared.typeAuto"), color: "default" },
});

const NUMBER_FIELDS = [
  ["backup_keep_last", "keepLast", "keepLastUnit"],
  ["backup_max_age_days", "maxAgeDays", "maxAgeDaysUnit"],
  ["backup_max_total_gb", "maxTotalGb", "maxTotalGbUnit"],
  ["backup_min_free_gb", "minFreeGb", "minFreeGbUnit"],
];

export function formatBytes(bytes) {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(sizes.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
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

const isOn = (v) => v === true || v === "true" || v === "1" || v === 1;

function AutoBackupSettings({ settings, onSave }) {
  const { t } = useUiTranslation();
  const [draft, setDraft] = useState(null);
  const [state, setState] = useState({ saving: false, saved: false, error: null });

  useEffect(() => {
    if (!settings) return;
    const next = { backup_daily_enabled: isOn(settings.backup_daily_enabled) };
    NUMBER_FIELDS.forEach(([key]) => {
      next[key] = settings[key] === undefined || settings[key] === null ? "" : String(settings[key]);
    });
    setDraft(next);
    setState({ saving: false, saved: false, error: null });
  }, [settings]);

  if (!settings || !draft) return null;

  const dirty =
    isOn(settings.backup_daily_enabled) !== draft.backup_daily_enabled ||
    NUMBER_FIELDS.some(([key]) => String(settings[key] ?? "") !== String(draft[key] ?? ""));

  const save = async () => {
    setState({ saving: true, saved: false, error: null });
    try {
      await onSave?.(draft);
      setState({ saving: false, saved: true, error: null });
    } catch (e) {
      setState({ saving: false, saved: false, error: e?.message || String(e) });
    }
  };

  const disk = settings.disk;
  return (
    <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1, p: 1.5, mb: 2 }}>
      <Typography variant="subtitle2">{t("backups.manageBackups.autoTitle")}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 1 }}>
        {t("backups.manageBackups.autoHelp")}
      </Typography>
      <FormControlLabel
        control={
          <Switch
            size="small"
            checked={draft.backup_daily_enabled}
            onChange={(e) => setDraft({ ...draft, backup_daily_enabled: e.target.checked })}
            inputProps={{ "aria-label": t("backups.manageBackups.dailySwitch") }}
          />
        }
        label={t("backups.manageBackups.dailySwitch")}
      />
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 1, mt: 1 }}>
        {NUMBER_FIELDS.map(([key, labelKey, unitKey]) => (
          <TextField
            key={key}
            size="small"
            type="number"
            inputProps={{ min: 0, step: 1, "aria-label": t(`backups.manageBackups.${labelKey}`) }}
            label={t(`backups.manageBackups.${labelKey}`)}
            helperText={t(`backups.manageBackups.${unitKey}`)}
            value={draft[key]}
            onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
          />
        ))}
      </Box>
      {disk && (
        <Typography variant="caption" color={disk.ok ? "text.secondary" : "error"} sx={{ display: "block", mt: 1 }}>
          {t("backups.manageBackups.diskLine", { free: formatBytes(disk.free), needed: formatBytes(disk.needed) })}
          {!disk.ok && ` ${t("backups.manageBackups.diskLow")}`}
        </Typography>
      )}
      {state.error && (
        <Alert severity="error" sx={{ mt: 1 }}>
          {t("backups.manageBackups.saveFailed", { error: state.error })}
        </Alert>
      )}
      <Box sx={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 1, mt: 1 }}>
        {state.saved && !dirty && (
          <Typography variant="caption" color="success.main">{t("backups.manageBackups.saved")}</Typography>
        )}
        <Button size="small" variant="outlined" onClick={save} disabled={!dirty || state.saving}>
          {t("backups.manageBackups.save")}
        </Button>
      </Box>
    </Box>
  );
}

const ManageBackupsModal = ({
  open,
  onClose,
  onRestore,
  onDelete,
  onDownload,
  onRefresh,
  onPin,
  onSaveSettings,
  settings = null,
  backups = [],
}) => {
  const { t } = useUiTranslation();
  const [confirmDelete, setConfirmDelete] = useState(null);
  const types = typeConfig();

  // Support both old (string[]) and new (object[]) API formats
  const normalizedBackups = backups.map((b) =>
    typeof b === "string" ? { name: b, size: 0, type: "data", modified: 0, pinned: false } : b
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
        <AutoBackupSettings settings={settings} onSave={onSaveSettings} />
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
              const pinned = Boolean(backup.pinned);
              return (
                <ListItem
                  key={backup.name}
                  sx={{
                    border: 1,
                    borderColor: "divider",
                    borderRadius: 1,
                    mb: 1,
                    pr: 20,
                  }}
                  secondaryAction={
                    <Box sx={{ display: "flex", gap: 0.5 }}>
                      <Tooltip title={pinned ? t("backups.manageBackups.unpin") : t("backups.manageBackups.pin")}>
                        <IconButton
                          onClick={() => onPin?.(backup.name, !pinned)}
                          color={pinned ? "primary" : "default"}
                          size="small"
                          aria-pressed={pinned}
                          aria-label={pinned ? t("backups.manageBackups.unpin") : t("backups.manageBackups.pin")}
                        >
                          {pinned ? <PushPinIcon fontSize="small" /> : <PushPinOutlinedIcon fontSize="small" />}
                        </IconButton>
                      </Tooltip>
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
                      <Tooltip
                        title={
                          pinned
                            ? t("backups.manageBackups.unpinFirst")
                            : isConfirming
                              ? t("backups.manageBackups.clickAgainToConfirm")
                              : t("dialogs.shared.delete")
                        }
                      >
                        <span>
                          <IconButton
                            onClick={() => handleDelete(backup.name)}
                            color={isConfirming ? "error" : "default"}
                            size="small"
                            disabled={pinned}
                            sx={isConfirming ? { animation: "pulse 0.5s" } : {}}
                          >
                            <DeleteIcon fontSize="small" />
                          </IconButton>
                        </span>
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
                        {pinned && (
                          <Chip
                            icon={<PushPinIcon />}
                            label={t("backups.manageBackups.pinned")}
                            size="small"
                            color="primary"
                          />
                        )}
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
