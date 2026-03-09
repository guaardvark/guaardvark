// frontend/src/components/dashboard/CSVGenerationCard.jsx
// CSV Generation Dashboard Card - Quick access to CSV generation functionality

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
} from "@mui/material";
import {
  TableChart,
  Add,
  Download,
  Upload,
  Refresh,
} from "@mui/icons-material";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";

const CSVGenerationCard = React.forwardRef(
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
      // No implementation - CSV generation functionality not yet built
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
        title="CSV Generation"
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
            onClick={() => navigate("/csv-generation")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            New CSV
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<Upload />}
            onClick={() => navigate("/csv-generation?mode=import")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            Import
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
            No recent CSV generations found.
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
                onClick={() => navigate(`/csv-generation?id=${generation.id}`)}
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
                      {generation.record_count && (
                        <Typography
                          variant="caption"
                          sx={{
                            color: "text.secondary",
                            fontSize: "0.65rem",
                          }}
                        >
                          • {generation.record_count} records
                        </Typography>
                      )}
                      {generation.file_size && (
                        <Typography
                          variant="caption"
                          sx={{
                            color: "text.secondary",
                            fontSize: "0.65rem",
                          }}
                        >
                          • {generation.file_size}
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
              to="/csv-generation"
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

CSVGenerationCard.displayName = "CSVGenerationCard";
export default CSVGenerationCard;