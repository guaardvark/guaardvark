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
import { translate, useUiTranslation } from "../../i18n";

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

const componentLabels = () => ({
  clients: translate("backups.createBackup.components.clients"),
  documents: translate("backups.createBackup.components.documents"),
  projects: translate("backups.createBackup.components.projects"),
  tasks: translate("backups.createBackup.components.tasks"),
  websites: translate("backups.createBackup.components.websites"),
  chats: translate("backups.createBackup.components.chats"),
  rules: translate("backups.createBackup.components.rules"),
  system_settings: translate("backups.createBackup.components.systemSettings"),
});

const CreateBackupModal = ({ open, onClose, onCreate, isProcessing }) => {
  const { t } = useUiTranslation();
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
      title: t("backups.createBackup.dataTitle"),
      subtitle: t("backups.createBackup.dataSubtitle"),
      description: t("backups.createBackup.dataDescription"),
      color: "primary",
    },
    {
      key: "full",
      icon: <CloudDoneIcon sx={{ fontSize: 36 }} />,
      title: t("backups.createBackup.fullTitle"),
      subtitle: t("backups.createBackup.fullSubtitle"),
      description: t("backups.createBackup.fullDescription"),
      color: "success",
    },
    {
      key: "code_release",
      icon: <CodeIcon sx={{ fontSize: 36 }} />,
      title: t("backups.createBackup.codeTitle"),
      subtitle: t("backups.createBackup.codeSubtitle"),
      description: t("backups.createBackup.codeDescription"),
      color: "warning",
    },
  ];
  const labels = componentLabels();

  return (
    <Dialog open={open} onClose={handleClose} fullWidth maxWidth="md">
      <DialogTitle>{t("backups.createBackup.title")}</DialogTitle>
      <DialogContent dividers>
        <Box sx={{ mb: 2 }}>
          <TextField
            fullWidth
            label={t("backups.createBackup.nameLabel")}
            variant="outlined"
            value={backupName}
            onChange={(e) => setBackupName(e.target.value)}
            helperText={t("backups.createBackup.nameHelp")}
            size="small"
          />
        </Box>

        {/* Type selector cards */}
        <Typography variant="subtitle2" color="text.secondary" gutterBottom>
          {t("backups.createBackup.selectType")}:
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
              {t("backups.createBackup.selectComponents")}
            </Typography>
            <Box sx={{ display: "flex", gap: 1, mb: 1 }}>
              <Button size="small" onClick={handleSelectAll}>{t("backups.createBackup.selectAll")}</Button>
              <Button size="small" onClick={handleSelectNone}>{t("backups.createBackup.selectNone")}</Button>
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
                  label={<Typography variant="body2">{labels[c] || c}</Typography>}
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
                  {t("backups.createBackup.includePlugins")}
                </Typography>
              }
            />
          </>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={handleClose} disabled={isProcessing}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          onClick={handleCreate}
          disabled={!selectedType || isProcessing}
          variant="contained"
          color={selectedType ? typeCards.find((c) => c.key === selectedType)?.color || "primary" : "primary"}
        >
          {isProcessing ? <CircularProgress size={20} sx={{ mr: 1 }} /> : null}
          {isProcessing ? t("backups.createBackup.creating") : selectedType ? t("backups.createBackup.createType", { type: typeCards.find((c) => c.key === selectedType)?.title }) : t("backups.createBackup.selectTypeButton")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default CreateBackupModal;
