// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from Dean (user/owner).
import React, { useState, useCallback, useEffect } from "react";
import {
  Box,
  Button,
  Typography,
  Paper,
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemIcon,
  ListItemText,
  // Progress moved to ProgressFooterBar
  Chip,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DescriptionIcon from "@mui/icons-material/Description";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import HourglassTopIcon from "@mui/icons-material/HourglassTop"; // Icon for indexing

// Import both API functions
import { uploadFile, getDocuments } from "../api";
import { triggerIndexing } from "../api/indexingService"; // Import directly to avoid static bundling
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";

const IndexStatusChip = ({ status }) => {
  let color = "default";
  let label = status || "Unknown";
  switch (status?.toUpperCase()) {
    case "INDEXED":
      color = "success";
      break;
    case "ERROR":
      color = "error";
      break;
    case "PENDING":
      color = "warning";
      break;
    case "INDEXING":
      color = "info";
      break;
    case "UPLOADED":
      color = "default";
      break;
    default:
      label = status ? String(status) : "Unknown";
      break;
  }
  return (
    <Chip
      label={label}
      color={color}
      size="small"
      sx={{ textTransform: "capitalize" }}
    />
  );
};

const UploadPage = () => {
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("idle"); // idle, uploading, success, indexing, error
  const [errorMessage, setErrorMessage] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [uploadProgress, setUploadProgress] = useState(0); // Progress state if using XHR
  const [recentDocs, setRecentDocs] = useState([]);
  const [isLoadingRecent, setIsLoadingRecent] = useState(true);
  const { activeModel, isLoadingModel, modelError } = useStatus();

  const fetchRecentDocs = useCallback(async () => {
    setIsLoadingRecent(true);
    try {
      const result = await getDocuments({ page: 1, perPage: 5 });
      if (result && result.error) throw new Error(result.error);
      setRecentDocs(result?.documents || result?.items || []);
    } catch (err) {
      console.error("Failed to fetch recent documents:", err);
      setRecentDocs([]);
    } finally {
      setIsLoadingRecent(false);
    }
  }, []);

  useEffect(() => {
    fetchRecentDocs();
  }, [fetchRecentDocs]);

  const handleFileChange = (event) => {
    if (event.target.files && event.target.files[0]) {
      setSelectedFile(event.target.files[0]);
      setUploadStatus("idle"); // Reset status when new file selected
      setErrorMessage("");
      setSuccessMessage("");
      setUploadProgress(0);
    }
  };

  const handleUpload = useCallback(async () => {
    if (!selectedFile) {
      setErrorMessage("Please select a file first.");
      return;
    }

    setUploadStatus("uploading");
    setErrorMessage("");
    setSuccessMessage("");
    setUploadProgress(0); // Reset progress
    
    // Progress events now handled by backend SocketIO system

    try {
      // --- Call uploadFile API ---
      const uploadResult = await uploadFile(
        selectedFile,
        null, // projectId 
        null, // tags
        {},   // metadata
        null, // signal
        null  // progress callback - handled elsewhere
      );

      if (uploadResult && uploadResult.error) {
        // Handle errors returned from uploadFile function itself
        throw new Error(uploadResult.error);
      }

      if (uploadResult && uploadResult.document_id) {
        setSuccessMessage(
          `File '${selectedFile.name}' uploaded successfully (ID: ${uploadResult.document_id}). Starting indexing...`,
        );
        setUploadStatus("indexing"); // Move to indexing status
        
        // Progress events now handled by backend SocketIO system

        // --- Call triggerIndexing API ---
        const indexingResult = await triggerIndexing(uploadResult.document_id);

        if (indexingResult && indexingResult.error) {
          setErrorMessage(
            `Upload succeeded, but indexing trigger failed: ${indexingResult.error}`,
          );
          setUploadStatus("error");
          
          // Error events now handled by backend SocketIO system
        } else {
          setSuccessMessage(
            `File '${selectedFile.name}' uploaded and indexing started.`,
          );
          setUploadStatus("success");
        }
        fetchRecentDocs();
      } else {
        // Handle unexpected success response format from upload
        console.warn(
          "Upload succeeded but response format unexpected:",
          uploadResult,
        );
        throw new Error(
          "Upload succeeded but received an unexpected response.",
        );
      }
    } catch (error) {
      console.error("Upload failed:", error);
      setErrorMessage(
        error.message || "An unknown error occurred during upload.",
      );
      setUploadStatus("error");
      
      // Error events now handled by backend SocketIO system
    } finally {
      // Reset selected file after attempt? Optional.
      // setSelectedFile(null);
      // event.target.value = null; // Need access to the input event target if doing this
      setUploadProgress(0); // Reset progress indicator
    }
  }, [selectedFile, fetchRecentDocs]);

  // Note: Basic fetch doesn't support upload progress easily.
  // For progress, you'd typically use XMLHttpRequest directly.
  // This example doesn't implement visual progress tracking during upload.

  return (
    <PageLayout
      title="Upload Documents"
      variant="standard"
      modelStatus
      activeModel={isLoadingModel ? "Loading..." : modelError ? "Error" : activeModel || "Default"}
    >
      <Paper elevation={2} sx={{ p: 3 }}>
        <Box
          sx={{
            border: "2px dashed",
            borderColor: "divider",
            borderRadius: 1,
            p: 3,
            textAlign: "center",
            mb: 2,
          }}
        >
          <input
            accept=".txt,.md,.pdf,.docx,.html,.htm,.json,.csv,.xml,.py,.js,.jsx,.java,.c,.cpp,.h,.cs,.go,.php,.rb,.swift,.kt,.rs,.scala,.zip" // Match ALLOWED_EXTENSIONS roughly
            style={{ display: "none" }}
            id="raised-button-file"
            type="file"
            onChange={handleFileChange}
          />
          <label htmlFor="raised-button-file">
            <Button
              variant="outlined"
              component="span"
              startIcon={<UploadFileIcon />}
            >
              Select File
            </Button>
          </label>
          {selectedFile && (
            <Typography variant="body1" sx={{ mt: 2 }}>
              Selected: {selectedFile.name} (
              {(selectedFile.size / 1024).toFixed(2)} KB)
            </Typography>
          )}
          {!selectedFile && (
            <Typography variant="body2" sx={{ color: "text.secondary", mt: 1 }}>
              Drag and drop or click to select a file.
            </Typography>
          )}
        </Box>

        <Button
          variant="contained"
          onClick={handleUpload}
          disabled={
            !selectedFile ||
            uploadStatus === "uploading" ||
            uploadStatus === "indexing"
          }
          fullWidth
          startIcon={
            uploadStatus === "uploading" || uploadStatus === "indexing" ? (
              <CircularProgress size={20} color="inherit" />
            ) : null
          }
        >
          {uploadStatus === "uploading"
            ? "Uploading..."
            : uploadStatus === "indexing"
              ? "Indexing..."
              : "Upload and Index"}
        </Button>

        {/* Status Messages */}
        {uploadStatus === "success" && successMessage && (
          <Alert
            severity="success"
            sx={{ mt: 2 }}
            icon={<CheckCircleIcon fontSize="inherit" />}
          >
            {successMessage}
          </Alert>
        )}
        {uploadStatus === "indexing" &&
          successMessage && ( // Show intermediate message during indexing trigger
            <Alert
              severity="info"
              sx={{ mt: 2 }}
              icon={<HourglassTopIcon fontSize="inherit" />}
            >
              {successMessage}
            </Alert>
          )}
        {uploadStatus === "error" && errorMessage && (
          <Alert
            severity="error"
            sx={{ mt: 2 }}
            icon={<ErrorIcon fontSize="inherit" />}
          >
            {errorMessage}
          </Alert>
        )}

        <Typography variant="h6" sx={{ mt: 4 }}>
          Recent Uploads
        </Typography>
        {isLoadingRecent ? (
          <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : recentDocs.length === 0 ? (
          <Typography variant="body2" sx={{ my: 2, fontStyle: "italic" }}>
            No recent uploads.
          </Typography>
        ) : (
          <List dense>
            {recentDocs.map((doc) => (
              <ListItem key={doc.id} divider>
                <ListItemIcon sx={{ minWidth: "36px" }}>
                  <DescriptionIcon
                    color={
                      doc.index_status === "INDEXED"
                        ? "success"
                        : doc.index_status === "ERROR"
                          ? "error"
                          : "action"
                    }
                    fontSize="small"
                  />
                </ListItemIcon>
                <ListItemText
                  primaryTypographyProps={{ variant: "body2" }}
                  secondaryTypographyProps={{ variant: "caption" }}
                  primary={doc.filename || `Document ${doc.id}`}
                  secondary={
                    <>
                      <IndexStatusChip status={doc.index_status} /> Uploaded:{" "}
                      {formatTimestamp(doc.uploaded_at)}
                    </>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </Paper>
    </PageLayout>
  );
};

export default UploadPage;
