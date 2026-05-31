// frontend/src/components/videoeditor/ProjectBar.jsx
//
// Slim horizontal bar that sits above the editor's card grid for the
// "named projects" feature. Presentational + callbacks only — it does NO
// data fetching and makes NO service calls. The parent (VideoEditorPage)
// owns project state and supplies the handlers.
//
// Renders the current project name (with a dirty "•" dot + a Saving…/Saved
// caption), and a "File" menu: New Project, Open…, Save (Ctrl+S),
// Save As…, Rename…. Save As / Rename open a small inline Dialog with a
// TextField rather than window.prompt. Ctrl/Cmd+S is wired to onSave while
// the bar is mounted.
import React, { useEffect, useRef, useState } from "react";
import {
  Box,
  Paper,
  Stack,
  Typography,
  Button,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
} from "@mui/material";
import {
  InsertDriveFile as FileIcon,
  Add as NewIcon,
  FolderOpen as OpenIcon,
  Save as SaveIcon,
  SaveAs as SaveAsIcon,
  DriveFileRenameOutline as RenameIcon,
} from "@mui/icons-material";

const ProjectBar = ({
  projectName = "Untitled",
  isDirty = false,
  isSaving = false,
  onNew,
  onOpen,
  onSave,
  onSaveAs,
  onRename,
}) => {
  const [menuAnchor, setMenuAnchor] = useState(null);
  // dialog state: { mode: "saveAs" | "rename" } | null
  const [dialog, setDialog] = useState(null);
  const [draftName, setDraftName] = useState("");

  const menuOpen = Boolean(menuAnchor);

  // Keyboard: Ctrl/Cmd+S → onSave. Mounted-only; cleaned up on unmount.
  const onSaveRef = useRef(onSave);
  useEffect(() => {
    onSaveRef.current = onSave;
  }, [onSave]);

  useEffect(() => {
    const handler = (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        onSaveRef.current?.();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const openMenu = (e) => setMenuAnchor(e.currentTarget);
  const closeMenu = () => setMenuAnchor(null);

  const handleNew = () => {
    closeMenu();
    onNew?.();
  };

  const handleOpen = () => {
    closeMenu();
    onOpen?.();
  };

  const handleSave = () => {
    closeMenu();
    onSave?.();
  };

  const openDialog = (mode) => {
    closeMenu();
    setDraftName(mode === "rename" ? projectName : `${projectName} copy`);
    setDialog({ mode });
  };

  const closeDialog = () => setDialog(null);

  const submitDialog = () => {
    const name = draftName.trim();
    if (!name) return;
    if (dialog?.mode === "saveAs") onSaveAs?.(name);
    else if (dialog?.mode === "rename") onRename?.(name);
    closeDialog();
  };

  const handleDialogKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitDialog();
    }
  };

  const savingCaption = isSaving ? "Saving…" : isDirty ? "Unsaved" : "Saved";

  return (
    <>
      <Paper
        elevation={0}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 1,
          py: 0.5,
          borderBottom: 1,
          borderColor: "divider",
          bgcolor: "transparent",
          backgroundImage: "none",
          minHeight: 40,
        }}
        className="non-draggable"
      >
        <Button
          size="small"
          variant="text"
          color="inherit"
          startIcon={<FileIcon fontSize="small" />}
          onClick={openMenu}
          sx={{ textTransform: "none", fontSize: "0.8rem" }}
        >
          File
        </Button>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0 }}>
            {isDirty && (
              <Box
                component="span"
                aria-label="Unsaved changes"
                sx={{ color: "warning.main", fontSize: "1rem", lineHeight: 1 }}
              >
                •
              </Box>
            )}
            <Typography
              variant="subtitle2"
              fontWeight={600}
              noWrap
              title={projectName}
              sx={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {projectName}
            </Typography>
          </Stack>
        </Box>

        <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
          {savingCaption}
        </Typography>
      </Paper>

      <Menu
        anchorEl={menuAnchor}
        open={menuOpen}
        onClose={closeMenu}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        <MenuItem onClick={handleNew}>
          <ListItemIcon>
            <NewIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>New Project</ListItemText>
        </MenuItem>
        <MenuItem onClick={handleOpen}>
          <ListItemIcon>
            <OpenIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Open…</ListItemText>
        </MenuItem>
        <Divider />
        <MenuItem onClick={handleSave} disabled={!isDirty}>
          <ListItemIcon>
            <SaveIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Save</ListItemText>
          <Typography variant="caption" color="text.secondary" sx={{ ml: 2 }}>
            Ctrl+S
          </Typography>
        </MenuItem>
        <MenuItem onClick={() => openDialog("saveAs")}>
          <ListItemIcon>
            <SaveAsIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Save As…</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => openDialog("rename")}>
          <ListItemIcon>
            <RenameIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>Rename…</ListItemText>
        </MenuItem>
      </Menu>

      <Dialog open={Boolean(dialog)} onClose={closeDialog} maxWidth="xs" fullWidth>
        <DialogTitle sx={{ pb: 1 }}>
          {dialog?.mode === "saveAs" ? "Save project as" : "Rename project"}
        </DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            fullWidth
            size="small"
            margin="dense"
            label="Project name"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            onKeyDown={handleDialogKeyDown}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={closeDialog} color="inherit">
            Cancel
          </Button>
          <Button onClick={submitDialog} variant="contained" disabled={!draftName.trim()}>
            {dialog?.mode === "saveAs" ? "Save As" : "Rename"}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
};

export default ProjectBar;
