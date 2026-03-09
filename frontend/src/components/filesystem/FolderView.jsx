// frontend/src/components/filesystem/FolderView.jsx
// Individual folder content display with grid/list view options

import React, { useState } from "react";
import {
  Box,
  Grid,
  Paper,
  Typography,
  IconButton,
  Tooltip,
  ToggleButton,
  ToggleButtonGroup,
  Chip,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
} from "@mui/material";
import {
  ViewModule as GridViewIcon,
  ViewList as ListViewIcon,
  InsertDriveFile as FileIcon,
  CheckCircle as CheckCircleIcon,
  Schedule as ScheduleIcon,
  Error as ErrorIcon,
} from "@mui/icons-material";
import FolderIconComponent from "./FolderIcon";
import { getFileTypeInfo, formatTimestamp } from "../../utils/fileTypeUtils";

const FolderView = ({ 
  folderName, 
  items = [], 
  onItemClick, 
  onItemContextMenu,
  loading = false 
}) => {
  const [viewMode, setViewMode] = useState("grid"); // "grid" or "list"

  const handleViewChange = (event, newView) => {
    if (newView !== null) {
      setViewMode(newView);
    }
  };

  const getFileIcon = (item) => {
    if (item.type === "folder") {
      return (
        <FolderIconComponent 
          folderName={item.name} 
          size="large"
          count={item.count}
          showBadge={true}
        />
      );
    }

    const fileTypeInfo = getFileTypeInfo(item.name);
    return (
      <FileIcon 
        sx={{ 
          fontSize: 40, 
          color: fileTypeInfo.color || "action.active" 
        }} 
      />
    );
  };

  const getIndexingStatus = (item) => {
    if (item.type === "folder") return null;
    
    const status = item.indexing_status || item.indexed;
    
    if (status === "completed" || status === true) {
      return (
        <Chip
          icon={<CheckCircleIcon />}
          label="Indexed"
          size="small"
          color="success"
          variant="outlined"
        />
      );
    } else if (status === "pending" || status === "processing") {
      return (
        <Chip
          icon={<ScheduleIcon />}
          label="Indexing"
          size="small"
          color="warning"
          variant="outlined"
        />
      );
    } else if (status === "failed") {
      return (
        <Chip
          icon={<ErrorIcon />}
          label="Failed"
          size="small"
          color="error"
          variant="outlined"
        />
      );
    }
    
    return (
      <Chip
        label="Not Indexed"
        size="small"
        variant="outlined"
        sx={{ opacity: 0.7 }}
      />
    );
  };

  const handleItemClick = (item) => {
    if (onItemClick) {
      onItemClick(item);
    }
  };

  const handleContextMenu = (event, item) => {
    event.preventDefault();
    if (onItemContextMenu) {
      onItemContextMenu(event, item);
    }
  };

  // Grid View
  if (viewMode === "grid") {
    return (
      <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
        {/* Toolbar */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            p: 1.5,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {items.length} items
          </Typography>
          <ToggleButtonGroup
            value={viewMode}
            exclusive
            onChange={handleViewChange}
            size="small"
          >
            <ToggleButton value="grid">
              <Tooltip title="Grid View">
                <GridViewIcon fontSize="small" />
              </Tooltip>
            </ToggleButton>
            <ToggleButton value="list">
              <Tooltip title="List View">
                <ListViewIcon fontSize="small" />
              </Tooltip>
            </ToggleButton>
          </ToggleButtonGroup>
        </Box>

        {/* Grid Content */}
        <Box sx={{ flexGrow: 1, overflowY: "auto", p: 2 }}>
          {items.length === 0 ? (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                height: "100%",
              }}
            >
              <Typography variant="body2" color="text.secondary" fontStyle="italic">
                This folder is empty
              </Typography>
            </Box>
          ) : (
            <Grid container spacing={2}>
              {items.map((item) => (
                <Grid item xs={6} sm={4} md={3} lg={2} key={item.id || item.path}>
                  <Paper
                    elevation={0}
                    sx={{
                      p: 2,
                      textAlign: "center",
                      cursor: "pointer",
                      border: 1,
                      borderColor: "divider",
                      transition: "all 0.2s",
                      "&:hover": {
                        borderColor: "primary.main",
                        bgcolor: "action.hover",
                        transform: "translateY(-2px)",
                        boxShadow: 2,
                      },
                    }}
                    onClick={() => handleItemClick(item)}
                    onContextMenu={(e) => handleContextMenu(e, item)}
                  >
                    <Box sx={{ mb: 1 }}>
                      {getFileIcon(item)}
                    </Box>
                    <Typography
                      variant="body2"
                      sx={{
                        fontWeight: 500,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        mb: 0.5,
                      }}
                    >
                      {item.name}
                    </Typography>
                    {item.type === "file" && (
                      <Box sx={{ mt: 1 }}>
                        {getIndexingStatus(item)}
                      </Box>
                    )}
                    {item.type === "file" && item.size && (
                      <Typography variant="caption" color="text.secondary" display="block">
                        {(item.size / 1024).toFixed(1)} KB
                      </Typography>
                    )}
                  </Paper>
                </Grid>
              ))}
            </Grid>
          )}
        </Box>
      </Box>
    );
  }

  // List View
  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Toolbar */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          p: 1.5,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography variant="body2" color="text.secondary">
          {items.length} items
        </Typography>
        <ToggleButtonGroup
          value={viewMode}
          exclusive
          onChange={handleViewChange}
          size="small"
        >
          <ToggleButton value="grid">
            <Tooltip title="Grid View">
              <GridViewIcon fontSize="small" />
            </Tooltip>
          </ToggleButton>
          <ToggleButton value="list">
            <Tooltip title="List View">
              <ListViewIcon fontSize="small" />
            </Tooltip>
          </ToggleButton>
        </ToggleButtonGroup>
      </Box>

      {/* List Content */}
      <Box sx={{ flexGrow: 1, overflowY: "auto" }}>
        {items.length === 0 ? (
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "100%",
              p: 4,
            }}
          >
            <Typography variant="body2" color="text.secondary" fontStyle="italic">
              This folder is empty
            </Typography>
          </Box>
        ) : (
          <List>
            {items.map((item) => (
              <ListItem
                key={item.id || item.path}
                disablePadding
                onContextMenu={(e) => handleContextMenu(e, item)}
              >
                <ListItemButton
                  onClick={() => handleItemClick(item)}
                  sx={{
                    py: 1,
                    "&:hover": {
                      bgcolor: "action.hover",
                    },
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 48 }}>
                    {getFileIcon(item)}
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Typography variant="body2" fontWeight={500}>
                        {item.name}
                      </Typography>
                    }
                    secondary={
                      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mt: 0.5 }}>
                        {item.type === "file" && getIndexingStatus(item)}
                        {item.type === "file" && item.size && (
                          <Typography variant="caption" color="text.secondary">
                            {(item.size / 1024).toFixed(1)} KB
                          </Typography>
                        )}
                        {item.type === "file" && item.uploaded_at && (
                          <Typography variant="caption" color="text.secondary">
                            {formatTimestamp(item.uploaded_at)}
                          </Typography>
                        )}
                      </Box>
                    }
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        )}
      </Box>
    </Box>
  );
};

export default FolderView;


