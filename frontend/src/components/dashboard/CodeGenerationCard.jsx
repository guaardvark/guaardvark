// frontend/src/components/dashboard/CodeGenerationCard.jsx
// Code Generation Dashboard Card - Quick access to code generation functionality

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
  Add,
  PlayArrow,
  Refresh,
} from "@mui/icons-material";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";

const CodeGenerationCard = React.forwardRef(
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
    const [isLoading, _setIsLoading] = useState(false);
    const [error, _setError] = useState(null);
    const navigate = useNavigate();

    const fetchRecentGenerations = useCallback(async () => {
      // No implementation - code generation functionality not yet built
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

    const getLanguageColor = (language) => {
      const colors = {
        python: "primary.dark",
        javascript: "warning.light",
        typescript: "info.main",
        sql: "primary.dark",
        java: "warning.main",
        csharp: "success.dark",
        go: "info.light",
        rust: "common.black",
        php: "secondary.main",
        ruby: "error.main",
      };
      return colors[language?.toLowerCase()] || "text.secondary";
    };

    const getComplexityColor = (complexity) => {
      switch (complexity?.toLowerCase()) {
        case "low":
          return "success";
        case "medium":
          return "warning";
        case "high":
          return "error";
        default:
          return "default";
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
        title="Code Generation"
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
            onClick={() => navigate("/code-generation")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            New Code
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<PlayArrow />}
            onClick={() => navigate("/code-generation?mode=debug")}
            sx={{
              minWidth: "100px",
              textTransform: "none",
              fontSize: "0.75rem",
              py: 0.5,
            }}
            className="non-draggable"
          >
            Debug
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
            No recent code generations found.
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
                onClick={() => navigate(`/code-generation?id=${generation.id}`)}
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
                      {generation.language && (
                        <Chip
                          label={generation.language.toUpperCase()}
                          size="small"
                          sx={{
                            fontSize: "0.6rem",
                            height: "16px",
                            backgroundColor: getLanguageColor(generation.language),
                            color: "white",
                            fontWeight: "bold",
                          }}
                        />
                      )}
                      {generation.lines_of_code && (
                        <Typography
                          variant="caption"
                          sx={{
                            color: "text.secondary",
                            fontSize: "0.65rem",
                          }}
                        >
                          • {generation.lines_of_code} lines
                        </Typography>
                      )}
                      {generation.complexity && (
                        <Chip
                          label={generation.complexity}
                          color={getComplexityColor(generation.complexity)}
                          size="small"
                          sx={{
                            fontSize: "0.6rem",
                            height: "16px",
                          }}
                        />
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
              to="/code-generation"
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

CodeGenerationCard.displayName = "CodeGenerationCard";
export default CodeGenerationCard;