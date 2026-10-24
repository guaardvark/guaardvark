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
import { useUiTranslation } from "../../i18n";

const FileContextMenu = ({ 
  anchorPosition, 
  onClose, 
  item, 
  onAction 
}) => {
  const { t } = useUiTranslation();
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
          <ListItemText>{t("files.menu.open")}</ListItemText>
        </MenuItem>
      )}

      {isFile && (
        <MenuItem onClick={() => handleAction("view")}>
          <ListItemIcon>
            <ViewIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("files.menu.viewDetails")}</ListItemText>
        </MenuItem>
      )}

      {isFolder && (
        <MenuItem onClick={() => handleAction("open")}>
          <ListItemIcon>
            <OpenIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("files.menu.openFolder")}</ListItemText>
        </MenuItem>
      )}

      <Divider />

      <MenuItem onClick={() => handleAction("edit")}>
        <ListItemIcon>
          <EditIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>{t("files.menu.rename")}</ListItemText>
      </MenuItem>

      <MenuItem onClick={() => handleAction("copy")}>
        <ListItemIcon>
          <CopyIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>{t("files.menu.copy")}</ListItemText>
      </MenuItem>

      <MenuItem onClick={() => handleAction("move")}>
        <ListItemIcon>
          <MoveIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>{t("files.menu.move")}</ListItemText>
      </MenuItem>

      {isFile && (
        <MenuItem onClick={() => handleAction("download")}>
          <ListItemIcon>
            <DownloadIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("files.menu.download")}</ListItemText>
        </MenuItem>
      )}

      <Divider />

      {isFile && (
        <MenuItem onClick={() => handleAction("reindex")}>
          <ListItemIcon>
            <RefreshIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>{t("files.menu.reindex")}</ListItemText>
        </MenuItem>
      )}

      <MenuItem onClick={() => handleAction("info")}>
        <ListItemIcon>
          <InfoIcon fontSize="small" />
        </ListItemIcon>
        <ListItemText>{t("files.menu.properties")}</ListItemText>
      </MenuItem>

      <Divider />

      <MenuItem 
        onClick={() => handleAction("delete")}
        sx={{ color: "error.main" }}
      >
        <ListItemIcon>
          <DeleteIcon fontSize="small" color="error" />
        </ListItemIcon>
        <ListItemText>{t("files.menu.delete")}</ListItemText>
      </MenuItem>
    </Menu>
  );
};

export default FileContextMenu;


