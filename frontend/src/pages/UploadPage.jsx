// WARNING: Visual/UX changes to this file are forbidden without explicit written approval from the project maintainer.
import React, { useState, useCallback, useEffect, useRef } from "react";
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

import { uploadFile, getDocuments } from "../api";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { formatTimestamp } from "../utils/fileTypeUtils";
import useFileDropZone from "../hooks/useFileDropZone";
import { translate, useUiTranslation } from "../i18n";

// The picker's filter only. /api/docs/upload keeps no extension list of its
// own (the loaders decide what gets indexed), so neither the picker nor the
// drop zone refuses a file here; the server's answer is shown as it comes.
const ACCEPTED_EXTENSIONS =
  ".txt,.md,.pdf,.docx,.html,.htm,.json,.csv,.xml,.py,.js,.jsx,.java,.c,.cpp,.h,.cs,.go,.php,.rb,.swift,.kt,.rs,.scala,.zip";

// Recent Uploads refreshes itself while a row is still being indexed, so a
// new upload turns INDEXED without a reload. The cap counts every tick,
// answered or not, so a paused queue or a backend that is down stops the
// polling after two minutes instead of never.
const RECENT_REFRESH_MS = 3000;
const RECENT_REFRESH_MAX_POLLS = 40;
const statusOf = (doc) => String(doc?.index_status || "").toUpperCase();
const isInFlight = (doc) => ["INDEXING", "PENDING"].includes(statusOf(doc));
// The row this page just uploaded is followed until it shows one of these.
// STORED is not one: for a document type the backend's resume tick queues a
// STORED row like a PENDING one (text uploads sit at STORED until the worker
// takes them), so it means "stored, waiting for the indexer".
const WATCHED_SETTLED = new Set(["INDEXED", "ERROR", "STORAGE_FAILED", "STORED_TRUNCATED"]);

// The index_status values a row can carry, worded for the chip. The value
// itself is what the page compares; one missing here is shown as it is.
const indexStatusLabels = () => ({
  PENDING: translate("upload.page.indexStatus.pending"),
  INDEXING: translate("upload.page.indexStatus.indexing"),
  INDEXED: translate("upload.page.indexStatus.indexed"),
  STORED: translate("upload.page.indexStatus.stored"),
  STORED_TRUNCATED: translate("upload.page.indexStatus.storedTruncated"),
  STORAGE_FAILED: translate("upload.page.indexStatus.storageFailed"),
  NOT_INDEXED: translate("upload.page.indexStatus.notIndexed"),
  ERROR: translate("upload.page.indexStatus.error"),
  UPLOADED: translate("upload.page.indexStatus.uploaded"),
});
const indexStatusLabel = (status) =>
  status ? indexStatusLabels()[status] ?? String(status) : translate("upload.page.indexStatus.unknown");

const IndexStatusChip = ({ status }) => {
  useUiTranslation();
  let color = "default";
  let label = indexStatusLabel(status);
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
      label = indexStatusLabel(status);
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
  const { t } = useUiTranslation();
  const [selectedFile, setSelectedFile] = useState(null);
  const [uploadStatus, setUploadStatus] = useState("idle"); // idle, uploading, indexing, success, error
  // Our messages are kept as { note, ...values } and worded while rendering
  // (messageText), so they follow a language change; a string is the server's
  // own error text, shown as it is.
  const [errorMessage, setErrorMessage] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [_uploadProgress, setUploadProgress] = useState(0); // Progress state if using XHR
  const [recentDocs, setRecentDocs] = useState([]);
  const [isLoadingRecent, setIsLoadingRecent] = useState(true);
  // {id, name} of the file just uploaded here, until its row settles.
  const [watched, setWatched] = useState(null);
  const refreshCountRef = useRef(0);
  const { activeModel, isLoadingModel, modelError } = useStatus();
  const fileInputRef = useRef(null);

  // silent: refresh the rows in place without the loading spinner.
  const fetchRecentDocs = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setIsLoadingRecent(true);
    try {
      const result = await getDocuments({ page: 1, perPage: 5 });
      if (result && result.error) throw new Error(result.error);
      setRecentDocs(result?.documents || result?.items || []);
    } catch (err) {
      console.error("Failed to fetch recent documents:", err);
      if (!silent) setRecentDocs([]);
    } finally {
      if (!silent) setIsLoadingRecent(false);
    }
  }, []);

  useEffect(() => {
    fetchRecentDocs();
  }, [fetchRecentDocs]);

  // The row just uploaded here: once it is in the list with a settled
  // status, the upload's own message says how it ended.
  useEffect(() => {
    if (!watched) return;
    const row = recentDocs.find((doc) => doc.id === watched.id);
    if (!row || !WATCHED_SETTLED.has(statusOf(row))) return;
    setWatched(null);
    if (statusOf(row) === "INDEXED") {
      setSuccessMessage({ note: "indexed", name: watched.name });
      setUploadStatus("success");
    } else {
      setErrorMessage(
        row.error_message
          ? { note: "indexFailedReason", name: watched.name, reason: row.error_message }
          : { note: "indexFailed", name: watched.name },
      );
      setUploadStatus("error");
    }
  }, [recentDocs, watched]);

  // Keep refreshing while any recent row is still indexing, or while the
  // row just uploaded has not yet appeared with a settled status; stop when
  // neither holds or the cap trips.
  useEffect(() => {
    const busy = watched != null || recentDocs.some(isInFlight);
    if (!busy) {
      refreshCountRef.current = 0;
      return undefined;
    }
    const interval = setInterval(() => {
      refreshCountRef.current += 1;
      if (refreshCountRef.current > RECENT_REFRESH_MAX_POLLS) {
        clearInterval(interval);
        refreshCountRef.current = 0;
        if (watched) {
          setWatched(null);
          setSuccessMessage({ note: "stillIndexing", name: watched.name });
          setUploadStatus("success");
        }
        return;
      }
      fetchRecentDocs({ silent: true });
    }, RECENT_REFRESH_MS);
    return () => clearInterval(interval);
  }, [recentDocs, watched, fetchRecentDocs]);

  // One path for the picker and the drop zone.
  const selectFile = useCallback((file) => {
    if (!file) return;
    setSelectedFile(file);
    setUploadStatus("idle"); // Reset status when new file selected
    setErrorMessage("");
    setSuccessMessage("");
    setUploadProgress(0);
  }, []);

  const handleFileChange = (event) => {
    const file = event.target.files && event.target.files[0];
    // Clear the input so picking the same file again still fires onChange.
    event.target.value = "";
    selectFile(file);
  };

  const openPicker = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const drop = useFileDropZone({ onFiles: (files) => selectFile(files[0]) });

  const handleUpload = useCallback(async () => {
    if (!selectedFile) {
      setErrorMessage({ note: "selectFirst" });
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
        // /api/docs/upload queues the indexing task itself (auto_index), so
        // the page does not call /api/index/<id> on top of it: that queued the
        // same document a second time under a second footer job. Recent
        // Uploads below follows the row until it is INDEXED.
        if (uploadResult.indexing_queued === false) {
          setErrorMessage(
            uploadResult.indexing_error
              ? {
                note: "notQueuedReason",
                name: selectedFile.name,
                id: uploadResult.document_id,
                reason: uploadResult.indexing_error,
              }
              : { note: "notQueued", name: selectedFile.name, id: uploadResult.document_id },
          );
          setUploadStatus("error");
          fetchRecentDocs();
          return;
        }
        setSuccessMessage({ note: "queued", name: selectedFile.name, id: uploadResult.document_id });
        setUploadStatus("indexing");
        refreshCountRef.current = 0;
        setWatched({ id: uploadResult.document_id, name: selectedFile.name });
        fetchRecentDocs();
      } else {
        // Handle unexpected success response format from upload
        console.warn(
          "Upload succeeded but response format unexpected:",
          uploadResult,
        );
        throw Object.assign(
          new Error("Upload succeeded but received an unexpected response."),
          { note: "unexpectedResponse" },
        );
      }
    } catch (error) {
      console.error("Upload failed:", error);
      setErrorMessage(
        error.note ? { note: error.note } : error.message || { note: "unknownError" },
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

  const messageText = (message) => {
    if (!message || typeof message !== "object") return message;
    const { name, id, reason } = message;
    switch (message.note) {
      case "indexed":
        return t("upload.page.messages.indexed", { name });
      case "indexFailed":
        return t("upload.page.messages.indexFailed", { name });
      case "indexFailedReason":
        return t("upload.page.messages.indexFailedReason", { name, reason });
      case "stillIndexing":
        return t("upload.page.messages.stillIndexing", { name });
      case "selectFirst":
        return t("upload.page.messages.selectFirst");
      case "notQueued":
        return t("upload.page.messages.notQueued", { name, id });
      case "notQueuedReason":
        return t("upload.page.messages.notQueuedReason", { name, id, reason });
      case "queued":
        return t("upload.page.messages.queued", { name, id });
      case "unexpectedResponse":
        return t("upload.page.messages.unexpectedResponse");
      case "unknownError":
        return t("upload.page.messages.unknownError");
      default:
        return "";
    }
  };

  // Note: Basic fetch doesn't support upload progress easily.
  // For progress, you'd typically use XMLHttpRequest directly.
  // This example doesn't implement visual progress tracking during upload.

  return (
    <PageLayout
      title={t("upload.page.title")}
      variant="standard"
      modelStatus
      activeModel={isLoadingModel
        ? t("upload.page.modelLoading")
        : modelError
          ? t("upload.page.modelError")
          : activeModel || t("upload.page.modelDefault")}
    >
      <Paper elevation={2} sx={{ p: 3 }}>
        <Box
          {...drop.dropProps}
          onClick={openPicker}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              openPicker();
            }
          }}
          role="button"
          tabIndex={0}
          aria-label={t("upload.page.dropZoneLabel")}
          data-testid="upload-drop-zone"
          sx={{
            border: "2px dashed",
            borderColor: drop.isDragActive ? "primary.main" : "divider",
            borderRadius: 1,
            p: 3,
            textAlign: "center",
            mb: 2,
            cursor: "pointer",
          }}
        >
          <input
            ref={fileInputRef}
            accept={ACCEPTED_EXTENSIONS}
            style={{ display: "none" }}
            id="raised-button-file"
            type="file"
            onChange={handleFileChange}
          />
          <Button
            variant="outlined"
            startIcon={<UploadFileIcon />}
            onClick={(event) => {
              event.stopPropagation();
              openPicker();
            }}
          >
            {t("upload.page.selectFile")}
          </Button>
          {selectedFile && (
            <Typography variant="body1" sx={{ mt: 2 }}>
              {t("upload.page.selected", { name: selectedFile.name, size: (selectedFile.size / 1024).toFixed(2) })}
            </Typography>
          )}
          {!selectedFile && (
            <Typography variant="body2" sx={{ color: "text.secondary", mt: 1 }}>
              {t("upload.page.dropHint")}
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
            ? t("upload.page.importing")
            : uploadStatus === "indexing"
              ? t("upload.page.indexing")
              : t("upload.page.importAndIndex")}
        </Button>

        {/* Status Messages */}
        {uploadStatus === "success" && successMessage && (
          <Alert
            severity="success"
            sx={{ mt: 2 }}
            icon={<CheckCircleIcon fontSize="inherit" />}
          >
            {messageText(successMessage)}
          </Alert>
        )}
        {uploadStatus === "indexing" &&
          successMessage && ( // Shown while the uploaded row is being indexed
            <Alert
              severity="info"
              sx={{ mt: 2 }}
              icon={<HourglassTopIcon fontSize="inherit" />}
            >
              {messageText(successMessage)}
            </Alert>
          )}
        {uploadStatus === "error" && errorMessage && (
          <Alert
            severity="error"
            sx={{ mt: 2 }}
            icon={<ErrorIcon fontSize="inherit" />}
          >
            {messageText(errorMessage)}
          </Alert>
        )}

        <Typography variant="h6" sx={{ mt: 4 }}>
          {t("upload.page.recentUploads")}
        </Typography>
        {isLoadingRecent ? (
          <Box sx={{ display: "flex", justifyContent: "center", my: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : recentDocs.length === 0 ? (
          <Typography variant="body2" sx={{ my: 2, fontStyle: "italic" }}>
            {t("upload.page.noRecentUploads")}
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
                  primary={doc.filename || t("upload.page.documentFallback", { id: doc.id })}
                  secondary={
                    <>
                      <IndexStatusChip status={doc.index_status} />{" "}
                      {t("upload.page.importedAt", { time: formatTimestamp(doc.uploaded_at) })}
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
