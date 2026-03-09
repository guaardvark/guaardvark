// frontend/src/components/cards/BatchImageCard.jsx
// BatchImageCard component for displaying image generation batches

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
  LinearProgress,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import PendingIcon from "@mui/icons-material/Pending";
import ErrorIcon from "@mui/icons-material/Error";
import AccessTimeIcon from "@mui/icons-material/AccessTime";
import CloseIcon from "@mui/icons-material/Close";
import DownloadIcon from "@mui/icons-material/Download";
import ImageIcon from "@mui/icons-material/Image";

const BatchImageCard = ({ 
  batch, 
  onView, 
  onDelete,
  onDownload
}) => {
  // Guard clause for null/undefined batch
  if (!batch) {
    return null;
  }
  
  const theme = useTheme();
  
  // Calculate status and color
  const getStatusConfig = () => {
    switch (batch.status) {
      case 'completed':
        return { 
          color: "success", 
          label: "Complete", 
          icon: <CheckCircleIcon fontSize="small" />,
          variant: "filled" 
        };
      case 'running':
        return { 
          color: "primary", 
          label: "Running", 
          icon: <PendingIcon fontSize="small" />,
          variant: "filled" 
        };
      case 'error':
        return { 
          color: "error", 
          label: "Error", 
          icon: <ErrorIcon fontSize="small" />,
          variant: "filled" 
        };
      case 'cancelled':
        return { 
          color: "default", 
          label: "Cancelled", 
          icon: <CloseIcon fontSize="small" />,
          variant: "outlined" 
        };
      default:
        return { 
          color: "default", 
          label: batch.status || "Unknown", 
          icon: <PendingIcon fontSize="small" />,
          variant: "outlined" 
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

  const handleCardClick = () => {
    if (onView && typeof onView === 'function') {
      onView(batch);
    }
  };

  const handleDownloadClick = (event) => {
    event.stopPropagation(); // Prevent card click
    if (onDownload && typeof onDownload === 'function') {
      onDownload(batch);
    }
  };

  const handleDeleteClick = (event) => {
    event.stopPropagation(); // Prevent card click
    if (onDelete && typeof onDelete === 'function') {
      onDelete(batch);
    }
  };

  const progress = batch.progress_percentage || 0;

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
      {/* Download button positioned in top left (only for completed batches) */}
      {batch.status === 'completed' && (
        <Tooltip title="Download batch results">
          <IconButton
            size="small"
            onClick={handleDownloadClick}
            sx={{
              position: "absolute",
              top: 8,
              left: 8,
              zIndex: 1,
            }}
          >
            <DownloadIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}

      {/* Delete button positioned in top right */}
      <Tooltip title="Delete batch">
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
          {/* Header with Batch ID */}
          <Box sx={{ display: "flex", justifyContent: "center", alignItems: "flex-start", mb: 2, mt: 2 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <ImageIcon fontSize="small" sx={{ color: "text.secondary" }} />
              <Typography
                variant="subtitle2"
                sx={{
                  fontWeight: "bold",
                  color: "text.primary",
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                }}
              >
                {batch.batch_id}
              </Typography>
            </Box>
          </Box>

          {/* Timestamp and Status */}
          <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 2 }}>
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <AccessTimeIcon fontSize="small" sx={{ mr: 0.5, color: "text.secondary" }} />
              <Typography variant="caption" color="text.secondary">
                {formatTimestamp(batch.start_time)}
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
                  {batch.completed_images || 0}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Completed
                </Typography>
              </Box>
            </Grid>
            <Grid item xs={6}>
              <Box sx={{ textAlign: "center" }}>
                <Typography variant="h6" sx={{ fontWeight: "bold", color: batch.failed_images > 0 ? "error.main" : "text.secondary" }}>
                  {batch.failed_images || 0}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Failed
                </Typography>
              </Box>
            </Grid>
          </Grid>

          {/* Progress Bar */}
          {batch.status === 'running' && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
                Progress: {batch.completed_images || 0}/{batch.total_images || 0}
              </Typography>
              <LinearProgress
                variant="determinate"
                value={progress}
                sx={{
                  height: 6,
                  borderRadius: 3,
                }}
              />
              <Typography variant="caption" sx={{ fontWeight: "medium", display: "block", mt: 0.5, textAlign: "right" }}>
                {progress}%
              </Typography>
            </Box>
          )}

          {/* Total Images */}
          <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <Typography variant="caption" color="text.secondary">
              Total Images
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontWeight: "medium" }}>
              {batch.total_images || 0}
            </Typography>
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );
};

export default BatchImageCard;

