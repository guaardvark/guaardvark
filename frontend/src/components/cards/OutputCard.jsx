// frontend/src/components/cards/OutputCard.jsx
// OutputCard component for displaying bulk generation outputs

import React from "react";
import {
  Card,
  CardContent,
  CardActionArea,
  Typography,
  Box,
  Chip,
  Grid,
  IconButton,
  Tooltip,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import WarningIcon from "@mui/icons-material/Warning";
import ErrorIcon from "@mui/icons-material/Error";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import RefreshIcon from "@mui/icons-material/Refresh";
import CloseIcon from "@mui/icons-material/Close";

const OutputCard = ({ 
  output, 
  onView, 
  onDelete,
  onRetry
}) => {
  // Guard clause for null/undefined output
  if (!output) {
    return null;
  }
  
  const theme = useTheme();
  
  // Calculate status and color
  const getStatusConfig = () => {
    if (output.failed_rows > 0) {
      return { 
        color: "error", 
        label: "Failed", 
        icon: <ErrorIcon fontSize="small" />,
        variant: "filled" 
      };
    } else if (output.replaced_rows > 0) {
      return { 
        color: "warning", 
        label: "Replaced", 
        icon: <WarningIcon fontSize="small" />,
        variant: "filled" 
      };
    } else {
      return { 
        color: "success", 
        label: "Complete", 
        icon: <CheckCircleIcon fontSize="small" />,
        variant: "filled" 
      };
    }
  };

  const statusConfig = getStatusConfig();
  
  // Format timestamp
  const formatTimestamp = (timestamp) => {
    if (!timestamp) return "-";
    try {
      const date = new Date(timestamp);
      return date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    } catch (error) {
      return "-";
    }
  };

  // Format file size
  const formatFileSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleCardClick = () => {
    if (onView && typeof onView === 'function') {
      onView(output);
    }
  };

  const handleRetryClick = (event) => {
    event.stopPropagation(); // Prevent card click
    if (onRetry && typeof onRetry === 'function') {
      onRetry(output);
    }
  };

  const handleDeleteClick = (event) => {
    event.stopPropagation(); // Prevent card click
    if (onDelete && typeof onDelete === 'function') {
      onDelete(output);
    }
  };

  return (
    <Card
      sx={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        transition: "all 0.2s ease-in-out",
        "&:hover": {
          transform: "translateY(-2px)",
          boxShadow: theme.shadows[8],
        },
        cursor: "pointer",
        position: "relative", // For absolute positioning of buttons
      }}
    >
      {/* Retry button positioned in top left */}
      {output.inactive_rows > 0 && (
        <Tooltip title={`Retry ${output.inactive_rows} failed rows`}>
          <IconButton
            size="small"
            onClick={handleRetryClick}
            sx={{
              position: "absolute",
              top: 8,
              left: 8,
              zIndex: 1,
            }}
          >
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}

      {/* Delete button positioned in top right */}
      <Tooltip title="Delete output">
        <IconButton
          size="small"
          onClick={handleDeleteClick}
          sx={{
            position: "absolute",
            top: 8,
            right: 8,
            zIndex: 1,
          }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Tooltip>

      <CardActionArea
        onClick={handleCardClick}
        sx={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          p: 0,
        }}
      >
        <CardContent sx={{ flexGrow: 1, p: 2 }}>
          {/* Header with Job ID */}
          <Box sx={{ display: "flex", justifyContent: "center", alignItems: "flex-start", mb: 2, mt: 2 }}>
            <Typography
              variant="subtitle2"
              sx={{
                fontWeight: "bold",
                color: "text.primary",
                fontFamily: "monospace",
                fontSize: "0.75rem",
              }}
            >
              {output.job_id}
            </Typography>
          </Box>

          {/* Timestamp and Status */}
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 2 }}>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <AccessTimeIcon fontSize="small" sx={{ mr: 0.5, color: "text.secondary" }} />
              <Typography variant="caption" color="text.secondary">
                {formatTimestamp(output.export_timestamp)}
              </Typography>
            </Box>
            <Chip
              label={statusConfig.label}
              color={statusConfig.color}
              variant={statusConfig.variant}
              size="small"
              icon={statusConfig.icon}
              sx={{ fontWeight: "medium", minWidth: 80 }}
            />
          </Box>

          {/* Statistics Grid */}
          <Grid container spacing={1} sx={{ mb: 2 }}>
            <Grid item xs={6}>
              <Box sx={{ textAlign: "center" }}>
                <Typography variant="h6" sx={{ fontWeight: "bold", color: "success.main" }}>
                  {output.active_rows}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Generated
                </Typography>
              </Box>
            </Grid>
            <Grid item xs={6}>
              <Box sx={{ textAlign: "center" }}>
                <Typography variant="h6" sx={{ fontWeight: "bold", color: "warning.main" }}>
                  {output.replaced_rows}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Replaced
                </Typography>
              </Box>
            </Grid>
          </Grid>

          {/* Success Rate */}
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
              Success Rate
            </Typography>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Box
                sx={{
                  flexGrow: 1,
                  height: 6,
                  backgroundColor: "grey.200",
                  borderRadius: 3,
                  overflow: "hidden",
                  mr: 1,
                }}
              >
                <Box
                  sx={{
                    height: "100%",
                    backgroundColor: output.failed_rows > 0 ? "error.main" : "success.main",
                    width: `${output.success_rate}%`,
                    transition: "width 0.3s ease",
                  }}
                />
              </Box>
              <Typography variant="caption" sx={{ fontWeight: "medium", minWidth: 35 }}>
                {output.success_rate.toFixed(0)}%
              </Typography>
            </Box>
          </Box>

          {/* File Info */}
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <Typography variant="caption" color="text.secondary">
              {formatFileSize(output.file_size)}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {output.total_rows} rows
            </Typography>
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

export default OutputCard;
