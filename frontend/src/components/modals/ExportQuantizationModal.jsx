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

const QUANTIZATION_OPTIONS = [
  {
    value: "q2_k",
    label: "Q2_K",
    description: "Smallest, lower quality",
    sizeMultiplier: 0.25,
    quality: 1,
  },
  {
    value: "q3_k_m",
    label: "Q3_K_M",
    description: "Very small",
    sizeMultiplier: 0.35,
    quality: 2,
  },
  {
    value: "q4_k_m",
    label: "Q4_K_M",
    description: "Balanced (Recommended)",
    sizeMultiplier: 0.5,
    quality: 3,
    recommended: true,
  },
  {
    value: "q5_k_m",
    label: "Q5_K_M",
    description: "Good quality",
    sizeMultiplier: 0.6,
    quality: 4,
  },
  {
    value: "q6_k",
    label: "Q6_K",
    description: "High quality",
    sizeMultiplier: 0.75,
    quality: 5,
  },
  {
    value: "q8_0",
    label: "Q8_0",
    description: "Near full precision",
    sizeMultiplier: 1.0,
    quality: 6,
  },
  {
    value: "f16",
    label: "F16",
    description: "Full precision (largest)",
    sizeMultiplier: 2.0,
    quality: 7,
  },
];

const ExportQuantizationModal = ({
  open,
  onClose,
  job,
  onExport,
  isExporting,
  isReQuantize = false,
  currentQuantization = null,
}) => {
  const [quantization, setQuantization] = useState("q4_k_m");
  const [modelName, setModelName] = useState("");
  const [formError, setFormError] = useState(null);

  const generateDefaultName = (job) => {
    if (!job) return "";
    if (job.output_model_name) return job.output_model_name;
    if (job.ollama_model_name) return job.ollama_model_name;
    const baseName = job.name?.toLowerCase().replace(/\s+/g, "-") || "model";
    return `guaardvark-${baseName}`;
  };

  useEffect(() => {
    if (open && job) {
      setModelName(generateDefaultName(job));
      setQuantization(currentQuantization || "q4_k_m");
      setFormError(null);
    }
  }, [open, job, currentQuantization]);

  const handleExport = () => {
    if (!modelName.trim()) {
      setFormError("Model name is required");
      return;
    }
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(modelName)) {
      setFormError("Model name must be lowercase, start with letter/number, and contain only a-z, 0-9, -, _");
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
    const option = QUANTIZATION_OPTIONS.find(o => o.value === quant);
    if (!option) return null;

    const sizeGB = (params * 2 * option.sizeMultiplier).toFixed(1);
    return `~${sizeGB} GB`;
  };

  const selectedOption = QUANTIZATION_OPTIONS.find(o => o.value === quantization);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>
        {isReQuantize ? "Re-Quantize Model" : "Export to Ollama"}
      </DialogTitle>
      <DialogContent dividers>
        {formError && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {formError}
          </Alert>
        )}

        {isReQuantize && currentQuantization && (
          <Alert severity="info" sx={{ mb: 2 }}>
            Current quantization: <strong>{currentQuantization.toUpperCase()}</strong>
            {currentQuantization === "f16" && (
              <> - This is full precision. Quantizing to Q4_K_M will reduce size by ~75% and improve load times.</>
            )}
          </Alert>
        )}

        <Grid container spacing={2} sx={{ mt: 0 }}>
          <Grid item xs={12}>
            <TextField
              required
              fullWidth
              label="Ollama Model Name"
              value={modelName}
              onChange={(e) => setModelName(e.target.value.toLowerCase())}
              disabled={isExporting}
              helperText="Name to register in Ollama (e.g., guaardvark-mymodel)"
              autoFocus
            />
          </Grid>

          <Grid item xs={12}>
            <FormControl fullWidth>
              <InputLabel>Quantization Level</InputLabel>
              <Select
                value={quantization}
                label="Quantization Level"
                onChange={(e) => setQuantization(e.target.value)}
                disabled={isExporting}
              >
                {QUANTIZATION_OPTIONS.map((option) => (
                  <MenuItem key={option.value} value={option.value}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%" }}>
                      <Typography sx={{ minWidth: 70 }}>{option.label}</Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ flex: 1 }}>
                        {option.description}
                      </Typography>
                      {option.recommended && (
                        <Chip label="Recommended" size="small" color="primary" />
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
                    Size: {estimateFileSize(quantization) || "Unknown"}
                  </Typography>
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <SpeedIcon fontSize="small" color="action" />
                  <Typography variant="body2">
                    Quality: {"★".repeat(selectedOption.quality)}{"☆".repeat(7 - selectedOption.quality)}
                  </Typography>
                </Box>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <MemoryIcon fontSize="small" color="action" />
                  <Typography variant="body2">
                    VRAM: {selectedOption.sizeMultiplier <= 0.5 ? "Low" : selectedOption.sizeMultiplier <= 0.75 ? "Medium" : "High"}
                  </Typography>
                </Box>
              </Box>
            </Grid>
          )}

          {job?.base_model && (
            <Grid item xs={12}>
              <Typography variant="caption" color="text.secondary">
                Base model: {job.base_model}
              </Typography>
            </Grid>
          )}
        </Grid>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isExporting}>
          Cancel
        </Button>
        <Button
          onClick={handleExport}
          variant="contained"
          disabled={isExporting}
          startIcon={isExporting ? <CircularProgress size={16} /> : null}
        >
          {isExporting ? "Exporting..." : isReQuantize ? "Re-Quantize" : "Export"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ExportQuantizationModal;
