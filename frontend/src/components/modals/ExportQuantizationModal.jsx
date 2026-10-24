import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
  Grid,
  Alert,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Typography,
  Box,
  Chip,
  CircularProgress,
} from "@mui/material";
import MemoryIcon from "@mui/icons-material/Memory";
import SpeedIcon from "@mui/icons-material/Speed";
import StorageIcon from "@mui/icons-material/Storage";
import { translate, useUiTranslation, Trans } from "../../i18n";

// Words for each level. Which levels are offered, which is the default and the
// share of the bf16 weights each file comes to are the backend's
// (job.export_levels, from LEVELS in backend/services/training_gguf_converter.py);
// the backend refuses any other level.
const levelWords = () => ({
  q8_0: { description: translate("dialogs.exportQuantization.levelNearFull"), quality: 6 },
  f16: { description: translate("dialogs.exportQuantization.levelFull"), quality: 7 },
});

const toOptions = (levels) => {
  const words = levelWords();
  return (levels || []).map((level) => ({
    value: level.value,
    label: level.label,
    sizeShare: level.size_share,
    recommended: Boolean(level.default),
    description: words[level.value]?.description || "",
    quality: words[level.value]?.quality || 5,
  }));
};

const ExportQuantizationModal = ({
  open,
  onClose,
  job,
  onExport,
  isExporting,
  isReQuantize = false,
  currentQuantization = null,
  levels = [],
}) => {
  const { t } = useUiTranslation();
  const options = toOptions(levels);
  const defaultLevel = (options.find((o) => o.recommended) || options[0])?.value || "";
  const [quantization, setQuantization] = useState(defaultLevel);
  const [modelName, setModelName] = useState("");
  const [formError, setFormError] = useState(null);

  const generateDefaultName = (job) => {
    if (!job) return "";
    if (job.output_model_name) return job.output_model_name;
    if (job.ollama_model_name) return job.ollama_model_name;
    const baseName = job.name?.toLowerCase().replace(/\s+/g, "-") || "model";
    return `guaardvark-${baseName}`;
  };

  const offeredValues = options.map((o) => o.value).join(",");
  useEffect(() => {
    if (open && job) {
      setModelName(generateDefaultName(job));
      const offered = offeredValues.split(",").includes(currentQuantization);
      setQuantization(offered ? currentQuantization : defaultLevel);
      setFormError(null);
    }
  }, [open, job, currentQuantization, defaultLevel, offeredValues]);

  const handleExport = () => {
    if (!modelName.trim()) {
      setFormError(t("dialogs.exportQuantization.nameRequired"));
      return;
    }
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(modelName)) {
      setFormError(t("dialogs.exportQuantization.nameInvalid"));
      return;
    }
    setFormError(null);
    onExport(quantization, modelName);
  };

  const estimateFileSize = (quant) => {
    if (!job?.base_model) return null;

    const match = job.base_model.match(/(\d+(?:\.\d+)?)\s*[bB]/);
    if (!match) return null;

    const params = parseFloat(match[1]);
    const option = options.find((o) => o.value === quant);
    if (!option || !option.sizeShare) return null;

    const sizeGB = (params * 2 * option.sizeShare).toFixed(1);
    return `~${sizeGB} GB`;
  };

  const selectedOption = options.find((o) => o.value === quantization);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {isReQuantize ? t("dialogs.exportQuantization.reQuantizeTitle") : t("dialogs.exportQuantization.exportTitle")}
      </DialogTitle>
      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}

        {isReQuantize && currentQuantization && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <Trans
              i18nKey="dialogs.exportQuantization.currentLevel"
              values={{ level: currentQuantization.toUpperCase() }}
              components={{ bold: <strong /> }}
            />
            {currentQuantization === "f16" && options.some((o) => o.value === "q8_0") && (
              <> - {t("dialogs.exportQuantization.fullPrecisionNote")}</>
            )}
          </Alert>
        )}

        {options.length === 0 && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {t("dialogs.exportQuantization.noLevels")}
          </Alert>
        )}

        <Grid container spacing={2} sx={{ mt: 0 }}>
          <Grid item xs={12}>
            <TextField
              required
              fullWidth
              label={t("dialogs.exportQuantization.nameLabel")}
              value={modelName}
              onChange={(e) => setModelName(e.target.value.toLowerCase())}
              disabled={isExporting}
              helperText={t("dialogs.exportQuantization.nameHelp")}
              autoFocus
            />
          </Grid>

          <Grid item xs={12}>
            <FormControl fullWidth>
              <InputLabel>{t("dialogs.exportQuantization.levelLabel")}</InputLabel>
              <Select
                value={quantization}
                label={t("dialogs.exportQuantization.levelLabel")}
                onChange={(e) => setQuantization(e.target.value)}
                disabled={isExporting}
              >
                {options.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%" }}>
                      <Typography sx={{ minWidth: 70 }}>{option.label}</Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
                        {option.description}
                      </Typography>
                      {option.recommended && (
                        <Chip label={t("dialogs.exportQuantization.recommended")} size="small" color="primary" />
                      )}
                    </Box>
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Grid>

          {selectedOption && (
            <Grid item xs={12}>
              <Box
                sx={{
                  p: 2,
                  bgcolor: "action.hover",
                  borderRadius: 1,
                  display: "flex",
                  gap: 3,
                  flexWrap: "wrap",
                }}
              >
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <StorageIcon fontSize="small" color="action" />
                  <Typography variant="body2">
                    {t("dialogs.exportQuantization.size", { size: estimateFileSize(quantization) || t("dialogs.exportQuantization.unknown") })}
                  </Typography>
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <SpeedIcon fontSize="small" color="action" />
                  <Typography variant="body2">
                    {t("dialogs.exportQuantization.quality")}: {"★".repeat(selectedOption.quality)}{"☆".repeat(7 - selectedOption.quality)}
                  </Typography>
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <MemoryIcon fontSize="small" color="action" />
                  <Typography variant="body2">
                    VRAM: {selectedOption.sizeShare <= 0.35 ? t("dialogs.exportQuantization.vramLow") : selectedOption.sizeShare <= 0.75 ? t("dialogs.exportQuantization.vramMedium") : t("dialogs.exportQuantization.vramHigh")}
                  </Typography>
                </Box>
              </Box>
            </Grid>
          )}

          {job?.base_model && (
            <Grid item xs={12}>
              <Typography variant="caption" color="text.secondary">
                {t("dialogs.exportQuantization.baseModel", { model: job.base_model })}
              </Typography>
            </Grid>
          )}
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isExporting}>
          {t("dialogs.shared.cancel")}
        </Button>
        <Button
          onClick={handleExport}
          variant="contained"
          disabled={isExporting || !selectedOption}
          startIcon={isExporting ? <CircularProgress size={16} /> : null}
        >
          {isExporting ? t("dialogs.exportQuantization.exporting") : isReQuantize ? t("dialogs.exportQuantization.reQuantize") : t("dialogs.exportQuantization.export")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ExportQuantizationModal;
