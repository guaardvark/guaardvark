// frontend/src/components/modals/AgentsSettingsModal.jsx
// Modal with links to Agents, Tools, RAG Settings, Training, Tasks, Plugins, DevTools

import React from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  Box,
  Chip,
  Typography,
} from "@mui/material";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import ExtensionIcon from "@mui/icons-material/Extension";
import SchoolIcon from "@mui/icons-material/School";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import BugReportIcon from "@mui/icons-material/BugReport";
import BuildIcon from "@mui/icons-material/Build";
import { useNavigate } from "react-router-dom";

const AgentsSettingsModal = ({ open, onClose }) => {
  const navigate = useNavigate();

  const handleNavigate = (path) => {
    navigate(path);
    onClose();
  };

  const links = [
    { label: "Agents", path: "/agents", icon: <SmartToyIcon /> },
    { label: "Agent Tools", path: "/tools", icon: <ExtensionIcon /> },
    { label: "Training", path: "/training", icon: <SchoolIcon /> },
    { label: "Tasks", path: "/tasks", icon: <TaskAltIcon /> },
    { label: "Plugins", path: "/plugins", icon: <BuildIcon /> },
    { label: "System Dashboard", path: "/dev-tools", icon: <BugReportIcon /> },
  ];

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Agents & Tools</DialogTitle>
      <DialogContent dividers>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Quick access to agent configuration and related settings.
        </Typography>
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
          {links.map(({ label, path, icon }) => (
            <Chip
              key={path}
              icon={icon}
              label={label}
              onClick={() => handleNavigate(path)}
              size="small"
              variant="outlined"
              sx={{ cursor: "pointer" }}
            />
          ))}
        </Box>
      </DialogContent>
    </Dialog>
  );
};

export default AgentsSettingsModal;
