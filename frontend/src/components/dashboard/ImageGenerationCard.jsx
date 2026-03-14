// frontend/src/components/dashboard/ImageGenerationCard.jsx
// Image Generation Dashboard Card - Quick access to image generation functionality

import React, { useState, useEffect, useCallback } from "react";
import {
  CircularProgress,
  Alert,
  Box,
  Typography,
  List,
  ListItem,
  ListItemText,
  Button,
  Chip,
  IconButton,
  Tooltip,
  Avatar,
} from "@mui/material";
import {
  Image,
  Add,
  PlayArrow,
  Refresh,
  Download,
  Visibility,
} from "@mui/icons-material";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";

const ImageGenerationCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      ...props
    },
    ref,
  ) => {
    const [recentGenerations, setRecentGenerations] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);
    const navigate = useNavigate();

    const fetchRecentGenerations = useCallback(async () => {
      // No implementation - will be connected to actual batch image generation API
      setRecentGenerations([]);
    }, []);

    useEffect(() => {
      fetchRecentGenerations();
    }, [fetchRecentGenerations]);

    const getStatusColor = (status) => {
      switch (status?.toLowerCase()) {
        case "completed":
          return "success";
        case "running":
          return "primary";
        case "failed":
          return "error";
        case "pending":
          return "warning";
        default:
          return "default";
      }
    };

    const getStatusLabel = (status) => {
      switch (status?.toLowerCase()) {
        case "completed":
          return "Completed";
        case "running":
          return "Running";
        case "failed":
          return "Failed";
        case "pending":
          return "Pending";
        default:
          return status || "Unknown";
      }
    };

    const getStyleColor = (style) => {
      const colors = {
        realistic: "success.main",
        artistic: "warning.main",
        cartoon: "secondary.main",
        sketch: "secondary.dark",
        infographic: "primary.light",
        technical: "grey.600",
        abstract: "primary.dark",
        vintage: "grey.700",
      };
      return colors[style?.toLowerCase()] || "text.secondary";
    };

    const formatDate = (dateString) => {
      if (!dateString) return "";
      return new Date(dateString).toLocaleDateString();
    };

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title="Image Generation"
        {...props}
      >
        {isLoading && (
          <CircularProgress
            size={22}
            sx={{ display: "block", mx: "auto", my: 2 }}
          />
        )}
        {error && (
          <Alert severity="error" sx={{ my: 1 }}>
            {error}
          </Alert>
        )}

        {/* Quick Actions */}
        <Box sx={{ mb: 2, display: "flex", gap: 1, flexWrap: "wrap" }}>
          <Button
            variant="contained"
            size="small"
            startIcon={<Add />}
            onClick={() => navigate("/images")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            New Images
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => navigate("/images?mode=batch")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            Batch Mode
          </Button>
          <Tooltip title="Refresh data">
            <IconButton
              size="small"
              onClick={fetchRecentGenerations}
              className="non-draggable"
            >
              <Refresh fontSize="small" />
            </IconButton>
          </Tooltip>
        </Box>

        {!isLoading && !error && recentGenerations.length === 0 && (
          <Typography
            variant="body2"
            sx={{ color: "text.secondary", mt: 2, textAlign: "center" }}
          >
            No recent image generations found.
          </Typography>
        )}

        {!isLoading && !error && recentGenerations.length > 0 && (
          <List
            dense
            sx={{ pt: 0, overflowY: "auto", maxHeight: "calc(100% - 80px)" }}
          >
            {recentGenerations.slice(0, 5).map((generation) => (
              <ListItem
                key={generation.id}
                disableGutters
                sx={{
                  py: 0.5,
                  cursor: "pointer",
                  "&:hover": {
                    backgroundColor: "action.hover",
                    borderRadius: 1,
                  },
                }}
                onClick={() => navigate(`/images?id=${generation.id}`)}
                className="non-draggable"
              >
                <ListItemText
                  primary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: "medium",
                          fontSize: "0.8rem",
                          flexGrow: 1,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {generation.name || "Unnamed Generation"}
                      </Typography>
                      <Chip
                        label={getStatusLabel(generation.status)}
                        color={getStatusColor(generation.status)}
                        size="small"
                        sx={{
                          fontSize: "0.6rem",
                          height: "18px",
                          minWidth: "60px",
                        }}
                      />
                    </Box>
                  }
                  secondary={
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 0.5 }}>
                      <Typography
                        variant="caption"
                        sx={{
                          color: "text.secondary",
                          fontSize: "0.65rem",
                        }}
                      >
                        {formatDate(generation.created_at)}
                      </Typography>
                      {generation.image_count && (
                        <Typography
                          variant="caption"
                          sx={{
                            color: "text.secondary",
                            fontSize: "0.65rem",
                          }}
                        >
                          • {generation.image_count} images
                        </Typography>
                      )}
                      {generation.style && (
                        <Chip
                          label={generation.style}
                          size="small"
                          sx={{
                            fontSize: "0.6rem",
                            height: "16px",
                            backgroundColor: getStyleColor(generation.style),
                            color: "white",
                            fontWeight: "bold",
                          }}
                        />
                      )}
                      {generation.dimensions && (
                        <Typography
                          variant="caption"
                          sx={{
                            color: "text.secondary",
                            fontSize: "0.65rem",
                          }}
                        >
                          • {generation.dimensions}
                        </Typography>
                      )}
                    </Box>
                  }
                />
              </ListItem>
            ))}
          </List>
        )}

        {recentGenerations.length > 5 && (
          <Box sx={{ textAlign: "center", mt: 1 }}>
            <Button
              component={RouterLink}
              to="/images"
              variant="text"
              size="small"
              sx={{
                textDecoration: "none",
                fontSize: "0.75rem",
                textTransform: "none",
                "&:hover": {
                  textDecoration: "underline",
                },
              }}
            >
              View All Generations ({recentGenerations.length})
            </Button>
          </Box>
        )}
      </DashboardCardWrapper>
    );
  },
);

ImageGenerationCard.displayName = "ImageGenerationCard";
export default ImageGenerationCard;