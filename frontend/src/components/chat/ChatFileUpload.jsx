// ChatFileUpload.jsx   Version 3.000
// Enhanced file upload component with unified API service integration

import * as apiService from "../../api";
import React, { useRef, useState } from "react";
import { useTheme } from "@mui/material/styles";

const ChatFileUpload = ({ onFileUploaded, sessionId, projectId }) => {
  const theme = useTheme();
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState({});
  const [error, setError] = useState(null);
  const fileInputRef = useRef(null);

  // Supported file types for chat analysis
  const supportedFileTypes = {
    // Programming files
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "React JSX",
    ".ts": "TypeScript",
    ".tsx": "TypeScript React",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".json": "JSON",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".ini": "INI Config",
    ".conf": "Config",
    ".env": "Environment",

    // Data files
    ".csv": "CSV Data",
    ".xlsx": "Excel",
    ".xls": "Excel",

    // Documents
    ".pdf": "PDF Document",
    ".txt": "Text File",
    ".md": "Markdown",
    ".rst": "ReStructuredText",

    // Other common formats
    ".sql": "SQL",
    ".sh": "Shell Script",
    ".bat": "Batch File",
    ".ps1": "PowerShell",
    ".dockerfile": "Dockerfile",
    ".gitignore": "Git Ignore",
    ".gitattributes": "Git Attributes",
  };

  const handleFileSelect = (event) => {
    const files = Array.from(event.target.files);
    const validFiles = files.filter((file) => {
      const extension = "." + file.name.split(".").pop().toLowerCase();
      return supportedFileTypes[extension] || file.type.startsWith("text/");
    });

    if (validFiles.length !== files.length) {
      setError(
        `Some files were skipped. Supported types: ${Object.keys(
          supportedFileTypes
        ).join(", ")}`
      );
    } else {
      setError(null);
    }

    setSelectedFiles(validFiles);
  };

  const uploadFile = async (file) => {
    // Add tags for better organization
    const extension = "." + file.name.split(".").pop().toLowerCase();
    const fileType = supportedFileTypes[extension] || "Unknown";
    const tags = `chat-upload,${fileType.toLowerCase()},${sessionId}`;

    try {
      const result = await apiService.uploadFile(
        file,
        projectId,
        tags,
        {},
        null, // signal
        (progressData) => {
          setUploadProgress((prev) => ({
            ...prev,
            [file.name]: progressData.percentage,
          }));
        }
      );

      if (result.error) {
        throw new Error(result.error);
      }

      return {
        success: true,
        file: file,
        uploadData: {
          id: result.document_id,
          filename: result.filename,
          project_id: result.project_id,
          tags: result.tags,
        },
        documentId: result.document_id,
      };
    } catch (error) {
      console.error(`Upload failed for ${file.name}:`, error);
      return {
        success: false,
        file: file,
        error: error.message,
      };
    }
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setError("Please select at least one file to upload.");
      return;
    }

    setUploading(true);
    setError(null);
    setUploadProgress({});

    const results = [];

    for (const file of selectedFiles) {
      const result = await uploadFile(file);
      results.push(result);
    }

    setUploading(false);

    // Check for any failed uploads
    const failedUploads = results.filter((r) => !r.success);
    if (failedUploads.length > 0) {
      setError(
        `Failed to upload: ${failedUploads.map((r) => r.file.name).join(", ")}`
      );
    }

    // Get successful uploads
    const successfulUploads = results.filter((r) => r.success);

    if (successfulUploads.length > 0) {
      // Generate chat message about uploaded files
      const _fileList = successfulUploads.map((r) => r.file.name).join(", ");
      const chatMessage = `**Files Uploaded Successfully:**\n\n${successfulUploads
        .map(
          (r) =>
            `• **${r.file.name}** (${
              supportedFileTypes[
                "." + r.file.name.split(".").pop().toLowerCase()
              ] || "Unknown Type"
            })`
        )
        .join("\n")}\n\nPlease analyze these files and suggest improvements.`;

      // Call the callback to send message to chat
      if (onFileUploaded) {
        onFileUploaded(chatMessage, successfulUploads);
      }

      // Clear selected files
      setSelectedFiles([]);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const removeFile = (index) => {
    setSelectedFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const clearAll = () => {
    setSelectedFiles([]);
    setError(null);
    setUploadProgress({});
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  return (
    <div
      className="chat-file-upload"
      style={{
        padding: "1rem",
        background: theme.palette.background.paper,
        color: theme.palette.text.primary,
        borderRadius: "8px",
        marginBottom: "1rem",
      }}
    >
      <h4 style={{ margin: "0 0 1rem 0", color: theme.palette.success.light }}>
        Upload Files for Chat Analysis
      </h4>

      {/* File Input */}
      <div style={{ marginBottom: "1rem" }}>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={Object.keys(supportedFileTypes).join(",")}
          onChange={handleFileSelect}
          style={{
            width: "100%",
            padding: "0.5rem",
            background: theme.palette.background.default,
            border: `1px solid ${theme.palette.divider}`,
            borderRadius: "4px",
            color: theme.palette.text.primary,
          }}
        />
        <small style={{ color: theme.palette.text.secondary, fontSize: "0.8rem" }}>
          Supported: {Object.keys(supportedFileTypes).slice(0, 10).join(", ")}
          ... and more
        </small>
      </div>

      {/* Selected Files */}
      {selectedFiles.length > 0 && (
        <div style={{ marginBottom: "1rem" }}>
          <h5 style={{ margin: "0 0 0.5rem 0" }}>
            Selected Files ({selectedFiles.length}):
          </h5>
          {selectedFiles.map((file, index) => (
            <div
              key={index}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "0.5rem",
                background: theme.palette.background.default,
                marginBottom: "0.25rem",
                borderRadius: "4px",
              }}
            >
              <span style={{ fontSize: "0.9rem" }}>
                {file.name} ({(file.size / 1024).toFixed(1)} KB)
              </span>
              <button
                onClick={() => removeFile(index)}
                style={{
                  background: theme.palette.error.main,
                  color: theme.palette.common.white,
                  border: "none",
                  borderRadius: "4px",
                  padding: "0.25rem 0.5rem",
                  cursor: "pointer",
                }}
              >
                ✕
              </button>
            </div>
          ))}

          {/* Upload Progress */}
          {uploading && Object.keys(uploadProgress).length > 0 && (
            <div style={{ marginTop: "0.5rem" }}>
              {Object.entries(uploadProgress).map(([filename, progress]) => (
                <div key={filename} style={{ marginBottom: "0.25rem" }}>
                  <div style={{ fontSize: "0.8rem", marginBottom: "0.25rem" }}>
                    {filename}: {progress}%
                  </div>
                  <div
                    style={{
                      width: "100%",
                      height: "4px",
                      background: theme.palette.divider,
                      borderRadius: "2px",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${progress}%`,
                        height: "100%",
                        background: theme.palette.success.light,
                        transition: "width 0.3s ease",
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Action Buttons */}
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button
          onClick={handleUpload}
          disabled={uploading || selectedFiles.length === 0}
          style={{
            background: uploading ? theme.palette.action.disabled : theme.palette.success.main,
            color: theme.palette.common.white,
            border: "none",
            borderRadius: "4px",
            padding: "0.5rem 1rem",
            cursor: uploading ? "not-allowed" : "pointer",
            flex: "1",
          }}
        >
          {uploading ? "Uploading..." : "Upload & Analyze"}
        </button>

        {selectedFiles.length > 0 && (
          <button
            onClick={clearAll}
            disabled={uploading}
            style={{
              background: theme.palette.action.disabled,
              color: theme.palette.common.white,
              border: "none",
              borderRadius: "4px",
              padding: "0.5rem 1rem",
              cursor: uploading ? "not-allowed" : "pointer",
            }}
          >
            Clear All
          </button>
        )}
      </div>

      {/* Error Display */}
      {error && (
        <div
          style={{
            marginTop: "1rem",
            padding: "0.5rem",
            background: `${theme.palette.error.main}20`,
            border: `1px solid ${theme.palette.error.main}`,
            borderRadius: "4px",
            color: theme.palette.error.light,
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
};

export default ChatFileUpload;