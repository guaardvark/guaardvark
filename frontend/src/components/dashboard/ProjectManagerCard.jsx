// frontend/src/components/dashboard/ProjectManagerCard.jsx
// Version 3.1: Make project list scrollable, remove item limit.

import React, { useState, useEffect, useCallback } from "react";
import {
  CircularProgress,
  Alert,
  List,
  ListItem,
  ListItemText,
  Typography,
  Avatar,
  ListItemAvatar,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import DashboardCardWrapper from "./DashboardCardWrapper";
import { getProjects } from "../../api";
import { getLogoUrl } from "../../config/logoConfig";

const ProjectManagerCard = React.forwardRef(
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
    const [projects, setProjects] = useState([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);
    const navigate = useNavigate();

    const fetchProjects = useCallback(async () => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await getProjects();
        if (result && result.error) {
          throw new Error(result.error);
        }
        setProjects(Array.isArray(result) ? result : []);
      } catch (err) {
        console.error("ProjectManagerCard fetch error:", err);
        setError(err.message || "Error loading project data.");
        setProjects([]);
      } finally {
        setIsLoading(false);
      }
    }, []);

    useEffect(() => {
      fetchProjects();
    }, [fetchProjects]);

    return (
      <DashboardCardWrapper
        ref={ref}
        style={style}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        title="Project Manager"
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
        {!isLoading && !error && projects.length === 0 && (
          <Typography
            variant="body2"
            sx={{ color: "text.secondary", mt: 2, textAlign: "center" }}
          >
            {" "}
            No projects found.{" "}
          </Typography>
        )}

        {!isLoading && !error && projects.length > 0 && (
          // --- MODIFIED: Removed slice() and "...and X more" ListItem ---
          <List
            dense
            sx={{ pt: 0, overflowY: "auto", maxHeight: "calc(100% - 16px)" }}
          >
            {projects.map((project) => (
              <ListItem
                key={project.id}
                disableGutters
                sx={{
                  py: 0.5,
                  cursor: "pointer",
                  "&:hover": {
                    backgroundColor: "action.hover",
                    borderRadius: 1,
                  },
                }}
                onClick={() => navigate(`/projects?projectId=${project.id}`)}
                className="non-draggable"
              >
                {project.client?.logo_path && (
                  <ListItemAvatar>
                    <Avatar
                      variant="rounded"
                      src={getLogoUrl(project.client.logo_path)}
                      alt={project.client.name}
                      sx={{ width: 32, height: 32 }}
                    />
                  </ListItemAvatar>
                )}
                <ListItemText
                  primary={project.name || "Unnamed Project"}
                  secondary={`Client: ${project.client?.name || "N/A"}`}
                  primaryTypographyProps={{
                    style: {
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    },
                  }}
                  secondaryTypographyProps={{
                    style: {
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    },
                  }}
                />
              </ListItem>
            ))}
            {/* Removed the "...and X more" ListItem */}
          </List>
          // --- END MODIFICATION ---
        )}
      </DashboardCardWrapper>
    );
  },
);

ProjectManagerCard.displayName = "ProjectManagerCard";
export default ProjectManagerCard;
