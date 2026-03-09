// frontend/src/components/filesystem/FolderIcon.jsx
// Custom folder icons with Ubuntu-style design and status indicators

import React from "react";
import { Box, Badge } from "@mui/material";
import {
  Folder as FolderIcon,
  FolderOpen as FolderOpenIcon,
  Dashboard as DashboardIcon,
  Article as ArticleIcon,
  FolderSpecial as ProjectsIcon,
  AccountBox as ClientsIcon,
  Language as WebsitesIcon,
  Image as ImagesIcon,
  Code as CodeIcon,
  TaskAlt as TasksIcon,
  RuleFolder as RulesIcon,
  CloudUpload as UploadsIcon,
  Assessment as AnalysisIcon,
  Memory as ContextIcon,
  School as TrainingIcon,
  Delete as TrashIcon,
} from "@mui/icons-material";

const folderIconMap = {
  Desktop: { Icon: DashboardIcon, color: "#7C4DFF" },
  Documents: { Icon: ArticleIcon, color: "#2196F3" },
  Projects: { Icon: ProjectsIcon, color: "#FF9800" },
  Clients: { Icon: ClientsIcon, color: "#4CAF50" },
  Websites: { Icon: WebsitesIcon, color: "#00BCD4" },
  Images: { Icon: ImagesIcon, color: "#E91E63" },
  Code: { Icon: CodeIcon, color: "#4CAF50" },
  Tasks: { Icon: TasksIcon, color: "#9C27B0" },
  Rules: { Icon: RulesIcon, color: "#FF5722" },
  Uploads: { Icon: UploadsIcon, color: "#607D8B" },
  Analysis: { Icon: AnalysisIcon, color: "#3F51B5" },
  Context: { Icon: ContextIcon, color: "#00897B" },
  Training: { Icon: TrainingIcon, color: "#FFC107" },
  Trash: { Icon: TrashIcon, color: "#F44336" },
};

const FolderIconComponent = ({ 
  folderName, 
  isOpen = false, 
  count = null,
  size = "medium",
  showBadge = true 
}) => {
  const folderConfig = folderIconMap[folderName];
  
  const sizeMap = {
    small: 20,
    medium: 28,
    large: 40,
  };

  const iconSize = sizeMap[size] || sizeMap.medium;

  if (folderConfig) {
    const { Icon, color } = folderConfig;
    
    const iconElement = (
      <Icon 
        sx={{ 
          fontSize: iconSize, 
          color: color,
          filter: isOpen ? "brightness(1.2)" : "none",
        }} 
      />
    );

    if (showBadge && count !== null && count > 0) {
      return (
        <Badge 
          badgeContent={count} 
          color="primary" 
          max={999}
          sx={{
            "& .MuiBadge-badge": {
              fontSize: "0.65rem",
              height: 16,
              minWidth: 16,
            }
          }}
        >
          {iconElement}
        </Badge>
      );
    }

    return iconElement;
  }

  // Default folder icon for custom folders
  const DefaultIcon = isOpen ? FolderOpenIcon : FolderIcon;
  const iconElement = (
    <DefaultIcon 
      sx={{ 
        fontSize: iconSize, 
        color: "#FFA726",
        filter: isOpen ? "brightness(1.2)" : "none",
      }} 
    />
  );

  if (showBadge && count !== null && count > 0) {
    return (
      <Badge 
        badgeContent={count} 
        color="primary" 
        max={999}
        sx={{
          "& .MuiBadge-badge": {
            fontSize: "0.65rem",
            height: 16,
            minWidth: 16,
          }
        }}
      >
        {iconElement}
      </Badge>
    );
  }

  return iconElement;
};

export default FolderIconComponent;


