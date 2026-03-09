
import React, { useState, useRef } from 'react';
import {
  Typography,
  Button,
  Paper,
  Grid,
  CircularProgress
} from '@mui/material';
import FileUploadIcon from '@mui/icons-material/FileUpload';
import FileDownloadIcon from '@mui/icons-material/FileDownload';
import { useSnackbar } from '../../contexts/SnackbarProvider';
import apiService from '../../api/apiService';

const RulesImportExportSection = ({ isLoading }) => {
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [selectedFileForImport, setSelectedFileForImport] = useState(null);
  const [selectedFileNameForImport, setSelectedFileNameForImport] = useState('');
  const fileImportInputRef = useRef(null);
  const { showMessage } = useSnackbar();

  const handleExportRulesClick = async () => {
    setIsExporting(true);
    showMessage("Exporting rules...", "info");
    try {
      const result = await apiService.exportRules();
      if (result?.error) throw new Error(result.error);
      if (!result?.rules || !Array.isArray(result.rules))
        throw new Error("Invalid export format received from server.");

      const jsonString = JSON.stringify(result, null, 2);
      const blob = new Blob([jsonString], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const date = new Date().toISOString().slice(0, 19).replace(/:/g, "-");
      a.download = `guaardvark_rules_export_${date}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showMessage(
        `Successfully exported ${result.rules.length} rules.`,
        "success",
      );
    } catch (err) {
      console.error("Error exporting rules:", err);
      showMessage(`Export failed: ${err.message}`, "error");
    } finally {
      setIsExporting(false);
    }
  };

  const handleFileSelectForImport = (event) => {
    const file = event.target.files?.[0];
    if (file) {
      setSelectedFileForImport(file);
      setSelectedFileNameForImport(file.name);
    } else {
      setSelectedFileForImport(null);
      setSelectedFileNameForImport("");
    }
  };

  const handleImportRulesClick = async () => {
    if (!selectedFileForImport) {
      showMessage("Please select a file first.", "warning");
      return;
    }

    setIsImporting(true);
    showMessage("Importing rules...", "info");
    try {
      const formData = new FormData();
      formData.append("file", selectedFileForImport);
      
      const result = await apiService.importRules(formData);
      if (result?.error && result.error !== "User aborted") {
        throw new Error(result.error.message || result.error);
      }

      const message = result?.warning || result?.message || "Rules imported successfully.";
      const severity = result?.warning ? "warning" : "success";
      showMessage(message, severity);
      
      setSelectedFileForImport(null);
      setSelectedFileNameForImport("");
      if (fileImportInputRef.current) {
        fileImportInputRef.current.value = "";
      }
    } catch (err) {
      if (err.message !== "User aborted") {
        showMessage(`Import failed: ${err.message}`, "error");
      }
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <Paper elevation={3} sx={{ p: 2 }}>
      <Typography variant="h6" gutterBottom>
        Rules Import/Export
      </Typography>
      <Grid container spacing={2}>
        <Grid item xs={12}>
          <Button
            variant="outlined"
            startIcon={<FileDownloadIcon />}
            onClick={handleExportRulesClick}
            disabled={isExporting || isLoading}
            fullWidth
          >
            {isExporting ? (
              <CircularProgress size={24} />
            ) : (
              "Export All Rules"
            )}
          </Button>
        </Grid>
        <Grid item xs={12}>
          <Typography variant="subtitle2" gutterBottom>
            Import Rules
          </Typography>
          <Grid container spacing={2} alignItems="center">
            <Grid item xs={12} sm={6}>
              <input
                accept=".json"
                style={{ display: "none" }}
                id="import-rules-file"
                type="file"
                ref={fileImportInputRef}
                onChange={handleFileSelectForImport}
              />
              <label htmlFor="import-rules-file" style={{ width: "100%" }}>
                <Button
                  variant="outlined"
                  component="span"
                  startIcon={<FileUploadIcon />}
                  disabled={isImporting || isLoading}
                  fullWidth
                >
                  Choose File
                </Button>
              </label>
            </Grid>
            <Grid item xs={12} sm={6}>
              <Button
                variant="contained"
                onClick={handleImportRulesClick}
                disabled={!selectedFileForImport || isImporting || isLoading}
                fullWidth
              >
                {isImporting ? (
                  <CircularProgress size={24} color="inherit" />
                ) : (
                  "Import Rules"
                )}
              </Button>
            </Grid>
          </Grid>
          {selectedFileNameForImport && (
            <Grid item xs={12}>
              <Typography
                variant="caption"
                display="block"
                sx={{ textAlign: "center", mt: 1 }}
              >
                Selected: {selectedFileNameForImport}
              </Typography>
            </Grid>
          )}
        </Grid>
      </Grid>
    </Paper>
  );
};

export default RulesImportExportSection; 
