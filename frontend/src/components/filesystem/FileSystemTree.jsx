// frontend/src/components/filesystem/FileSystemTree.jsx
// Left sidebar tree navigation for Linux-style file manager

import React, { useState } from "react";
import {
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Collapse,
  Typography,
  Divider,
} from "@mui/material";
import {
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
  Computer as ComputerIcon,
} from "@mui/icons-material";
import FolderIconComponent from "./FolderIcon";

const systemFolders = [
  { name: "Desktop", path: "/Desktop", count: 0 },
  { name: "Documents", path: "/Documents", count: 0 },
  { name: "Projects", path: "/Projects", count: 0 },
  { name: "Clients", path: "/Clients", count: 0 },
  { name: "Websites", path: "/Websites", count: 0 },
  { name: "Images", path: "/Images", count: 0 },
  { name: "Code", path: "/Code", count: 0 },
  { name: "Tasks", path: "/Tasks", count: 0 },
  { name: "Rules", path: "/Rules", count: 0 },
  { name: "Uploads", path: "/Uploads", count: 0 },
  { name: "Analysis", path: "/Analysis", count: 0 },
  { name: "Context", path: "/Context", count: 0 },
  { name: "Training", path: "/Training", count: 0 },
  { name: "Trash", path: "/Trash", count: 0 },
];

const FileSystemTree = ({ currentPath, onNavigate, folderCounts = {} }) => {
  const [expandedFolders, setExpandedFolders] = useState(new Set(["root"]));

  const toggleExpand = (folderId) => {
    setExpandedFolders((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(folderId)) {
        newSet.delete(folderId);
      } else {
        newSet.add(folderId);
      }
      return newSet;
    });
  };

  const isExpanded = (folderId) => expandedFolders.has(folderId);

  const handleFolderClick = (path) => {
    onNavigate(path);
  };

  const isActive = (path) => currentPath === path;

  return (
    <Box
      sx={{
        height: "100%",
        overflowY: "auto",
        bgcolor: "background.paper",
        borderRight: 1,
        borderColor: "divider",
      }}
    >
      <List dense sx={{ pt: 1 }}>
        {/* Root Computer */}
        <ListItem disablePadding>
          <ListItemButton
            onClick={() => toggleExpand("root")}
            sx={{
              py: 0.5,
              px: 1,
            }}
          >
            <Box sx={{ minWidth: 24, display: "flex", alignItems: "center" }}>
              {isExpanded("root") ? (
                <ExpandMoreIcon fontSize="small" />
              ) : (
                <ChevronRightIcon fontSize="small" />
              )}
            </Box>
            <ListItemIcon sx={{ minWidth: 32 }}>
              <ComputerIcon sx={{ fontSize: 20, color: "text.secondary" }} />
            </ListItemIcon>
            <ListItemText
              primary={
                <Typography variant="body2" fontWeight={600}>
                  Computer
                </Typography>
              }
            />
          </ListItemButton>
        </ListItem>

        <Collapse in={isExpanded("root")} timeout="auto" unmountOnExit>
          <List component="div" disablePadding>
            {systemFolders.map((folder) => (
              <ListItem key={folder.path} disablePadding>
                <ListItemButton
                  onClick={() => handleFolderClick(folder.path)}
                  selected={isActive(folder.path)}
                  sx={{
                    py: 0.5,
                    pl: 5,
                    pr: 1,
                    bgcolor: isActive(folder.path) ? "action.selected" : "transparent",
                    "&:hover": {
                      bgcolor: isActive(folder.path) 
                        ? "action.selected" 
                        : "action.hover",
                    },
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 32 }}>
                    <FolderIconComponent
                      folderName={folder.name}
                      isOpen={isActive(folder.path)}
                      count={folderCounts[folder.name] || 0}
                      size="small"
                      showBadge={false}
                    />
                  </ListItemIcon>
                  <ListItemText
                    primary={
                      <Typography
                        variant="body2"
                        sx={{
                          fontWeight: isActive(folder.path) ? 600 : 400,
                        }}
                      >
                        {folder.name}
                      </Typography>
                    }
                    secondary={
                      folderCounts[folder.name] > 0 && (
                        <Typography
                          variant="caption"
                          sx={{ color: "text.secondary" }}
                        >
                          {folderCounts[folder.name]} items
                        </Typography>
                      )
                    }
                  />
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </Collapse>

        <Divider sx={{ my: 1 }} />

        {/* Quick Access / Bookmarks could go here */}
      </List>
    </Box>
  );
};

export default FileSystemTree;


