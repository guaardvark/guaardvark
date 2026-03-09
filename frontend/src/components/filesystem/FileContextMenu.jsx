// frontend/src/components/filesystem/FileContextMenu.jsx
// Linux-style context menu for files and folders

import React from "react";
import { Menu, MenuItem, ListItemIcon, ListItemText, Divider } from "@mui/material";
import {
  Edit as EditIcon,
  Delete as DeleteIcon,
  FileCopy as CopyIcon,
  DriveFileMove as MoveIcon,
  Info as InfoIcon,
  Download as DownloadIcon,
  Refresh as RefreshIcon,
  OpenInNew as OpenIcon,
  Visibility as ViewIcon,
} from "@mui/icons-material";

const FileContextMenu = ({ 
  anchorPosition, 
  onClose, 
  item, 
  onAction 
}) => {
  const handleAction = (action) => {
    if (onAction) {
      onAction(action, item);
    }
    onClose();
  };

  const isFolder = item?.type === "folder";
  const isFile = item?.type === "file";

  return (
    <Menu
      open={Boolean(anchorPosition)}
      onClose={onClose}
      anchorReference="anchorPosition"
      anchorPosition={anchorPosition}
      slotProps={{
        paper: {
          sx: {
            minWidth: 200,
            boxShadow: 3,
          }
        }
      }}
    >
      {isFile && (
        <MenuItem onClick={() => handleAction("open")}>
          <ListItemIcon>
            <OpenIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Open</ListItemText>
        </MenuItem>
      )}

      {isFile && (
        <MenuItem onClick={() => handleAction("view")}>
          <ListItemIcon>
            <ViewIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>View Details</ListItemText>
        </MenuItem>
      )}

      {isFolder && (
        <MenuItem onClick={() => handleAction("open")}>
          <ListItemIcon>
            <OpenIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Open Folder</ListItemText>
        </MenuItem>
      )}

      <Divider />

      <MenuItem onClick={() => handleAction("edit")}>
        <ListItemIcon>
          <EditIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>Rename</ListItemText>
      </MenuItem>

      <MenuItem onClick={() => handleAction("copy")}>
        <ListItemIcon>
          <CopyIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>Copy</ListItemText>
      </MenuItem>

      <MenuItem onClick={() => handleAction("move")}>
        <ListItemIcon>
          <MoveIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>Move</ListItemText>
      </MenuItem>

      {isFile && (
        <MenuItem onClick={() => handleAction("download")}>
          <ListItemIcon>
            <DownloadIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Download</ListItemText>
        </MenuItem>
      )}

      <Divider />

      {isFile && (
        <MenuItem onClick={() => handleAction("reindex")}>
          <ListItemIcon>
            <RefreshIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Reindex</ListItemText>
        </MenuItem>
      )}

      <MenuItem onClick={() => handleAction("info")}>
        <ListItemIcon>
          <InfoIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>Properties</ListItemText>
      </MenuItem>

      <Divider />

      <MenuItem 
        onClick={() => handleAction("delete")}
        sx={{ color: "error.main" }}
      >
        <ListItemIcon>
          <DeleteIcon fontSize="small" color="error" />
        </ListItemIcon>
        <ListItemText>Delete</ListItemText>
      </MenuItem>
    </Menu>
  );
};

export default FileContextMenu;


