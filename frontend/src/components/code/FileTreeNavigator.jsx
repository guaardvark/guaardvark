// frontend/src/components/code/FileTreeNavigator.jsx
// File tree navigation component for code editor
// Integrates with existing project and document systems

import React, { useState, useCallback, useEffect } from "react";
import {
  Box,
  Typography,
  IconButton,
  Tooltip,
  Menu,
  MenuItem,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Alert,
  Collapse,
} from "@mui/material";
import {
  FolderOpen as FolderOpenIcon,
  Folder as FolderIcon,
  InsertDriveFile as FileIcon,
  Add as AddIcon,
  CreateNewFolder as CreateFolderIcon,
  NoteAdd as CreateFileIcon,
  Delete as DeleteIcon,
  Edit as RenameIcon,
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
  Code as CodeIcon,
  Image as ImageIcon,
  Description as DocumentIcon,
} from "@mui/icons-material";
import { useUnifiedProgress } from "../../contexts/UnifiedProgressContext";

// File type detection
const getFileIcon = (filename) => {
  const ext = filename.toLowerCase().substring(filename.lastIndexOf("."));

  // Code files
  if ([".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".php", ".rb"].includes(ext)) {
    return <CodeIcon fontSize="small" sx={{ color: "success.main" }} />;
  }

  // Image files
  if ([".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"].includes(ext)) {
    return <ImageIcon fontSize="small" sx={{ color: "warning.main" }} />;
  }

  // Document files
  if ([".md", ".txt", ".pdf", ".doc", ".docx"].includes(ext)) {
    return <DocumentIcon fontSize="small" sx={{ color: "info.main" }} />;
  }

  // Default file icon
  return <FileIcon fontSize="small" sx={{ color: "action.active" }} />;
};

// Tree node component
const TreeNode = ({
  node,
  level = 0,
  onFileSelect,
  onFileAction,
  expandedNodes,
  onToggleExpand
}) => {
  const [contextMenu, setContextMenu] = useState(null);

  const handleContextMenu = useCallback((event) => {
    event.preventDefault();
    setContextMenu({
      mouseX: event.clientX - 2,
      mouseY: event.clientY - 4,
    });
  }, []);

  const handleCloseContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleAction = useCallback((action) => {
    onFileAction(action, node);
    handleCloseContextMenu();
  }, [node, onFileAction]);

  const isExpanded = expandedNodes.has(node.id);
  const hasChildren = node.children && node.children.length > 0;

  return (
    <>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          py: 0.5,
          px: 1,
          pl: level * 2 + 1,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.hover" },
          minHeight: 32,
        }}
        onClick={() => {
          if (node.type === "file") {
            onFileSelect(node);
          } else if (hasChildren) {
            onToggleExpand(node.id);
          }
        }}
        onContextMenu={handleContextMenu}
      >
        {/* Expand/Collapse Icon */}
        {node.type === "folder" && (
          <IconButton
            size="small"
            sx={{ p: 0.25, mr: 0.5 }}
            onClick={(e) => {
              e.stopPropagation();
              onToggleExpand(node.id);
            }}
          >
            {hasChildren ? (
              isExpanded ? <ExpandMoreIcon fontSize="small" /> : <ChevronRightIcon fontSize="small" />
            ) : (
              <Box sx={{ width: 16 }} />
            )}
          </IconButton>
        )}

        {/* File/Folder Icon */}
        <Box sx={{ mr: 1, display: "flex", alignItems: "center" }}>
          {node.type === "folder" ? (
            isExpanded ? <FolderOpenIcon fontSize="small" sx={{ color: "warning.main" }} /> : <FolderIcon fontSize="small" sx={{ color: "warning.main" }} />
          ) : (
            getFileIcon(node.name)
          )}
        </Box>

        {/* Name */}
        <Typography
          variant="body2"
          sx={{
            flexGrow: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {node.name}
        </Typography>
      </Box>

      {/* Context Menu */}
      <Menu
        open={contextMenu !== null}
        onClose={handleCloseContextMenu}
        anchorReference="anchorPosition"
        anchorPosition={
          contextMenu !== null
            ? { top: contextMenu.mouseY, left: contextMenu.mouseX }
            : undefined
        }
      >
        {node.type === "folder" && [
          <MenuItem key="new-file" onClick={() => handleAction("create-file")}>
            <CreateFileIcon fontSize="small" sx={{ mr: 1 }} />
            New File
          </MenuItem>,
          <MenuItem key="new-folder" onClick={() => handleAction("create-folder")}>
            <CreateFolderIcon fontSize="small" sx={{ mr: 1 }} />
            New Folder
          </MenuItem>
        ]}
        <MenuItem onClick={() => handleAction("rename")}>
          <RenameIcon fontSize="small" sx={{ mr: 1 }} />
          Rename
        </MenuItem>
        <MenuItem onClick={() => handleAction("delete")} sx={{ color: "error.main" }}>
          <DeleteIcon fontSize="small" sx={{ mr: 1 }} />
          Delete
        </MenuItem>
      </Menu>

      {/* Children */}
      {node.type === "folder" && hasChildren && (
        <Collapse in={isExpanded}>
          {node.children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              level={level + 1}
              onFileSelect={onFileSelect}
              onFileAction={onFileAction}
              expandedNodes={expandedNodes}
              onToggleExpand={onToggleExpand}
            />
          ))}
        </Collapse>
      )}
    </>
  );
};

// Create/Rename dialog
const FileActionDialog = ({ open, action, onClose, onConfirm, initialValue = "" }) => {
  const [value, setValue] = useState(initialValue);
  const [error, setError] = useState("");

  useEffect(() => {
    setValue(initialValue);
    setError("");
  }, [initialValue, open]);

  const handleSubmit = useCallback(() => {
    if (!value.trim()) {
      setError("Name is required");
      return;
    }

    if (action === "create-file" && !value.includes(".")) {
      setError("File must have an extension");
      return;
    }

    onConfirm(value.trim());
  }, [value, action, onConfirm]);

  const getTitle = () => {
    switch (action) {
      case "create-file": return "Create New File";
      case "create-folder": return "Create New Folder";
      case "rename": return "Rename";
      default: return "File Action";
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{getTitle()}</DialogTitle>
      <DialogContent>
        <TextField
          autoFocus
          margin="dense"
          label={action === "create-folder" ? "Folder Name" : "File Name"}
          type="text"
          fullWidth
          variant="outlined"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          error={!!error}
          helperText={error}
          onKeyPress={(e) => {
            if (e.key === "Enter") {
              handleSubmit();
            }
          }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button onClick={handleSubmit} variant="contained">
          {action === "rename" ? "Rename" : "Create"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

const FileTreeNavigator = ({
  _projectId = null,
  onFileSelect = null,
  onFileCreate = null,
  files = [],
  loading = false
}) => {
  const [treeData, setTreeData] = useState([]);
  const [expandedNodes, setExpandedNodes] = useState(new Set());
  const [actionDialog, setActionDialog] = useState({ open: false, action: null, node: null });
  const [error, setError] = useState(null);

  const { startProcess, completeProcess, errorProcess } = useUnifiedProgress();

  // Convert flat file list to tree structure
  const buildTreeFromFiles = useCallback((fileList) => {
    const tree = [];
    const folders = new Map();

    // Create folder structure
    fileList.forEach(file => {
      const pathParts = file.path ? file.path.split("/") : [file.name];
      let currentLevel = tree;
      let currentPath = "";

      // Process each path segment except the last (which is the file)
      for (let i = 0; i < pathParts.length - 1; i++) {
        const folderName = pathParts[i];
        currentPath = currentPath ? `${currentPath}/${folderName}` : folderName;

        let folder = currentLevel.find(item => item.name === folderName && item.type === "folder");

        if (!folder) {
          folder = {
            id: `folder-${currentPath}`,
            name: folderName,
            type: "folder",
            path: currentPath,
            children: []
          };
          currentLevel.push(folder);
          folders.set(currentPath, folder);
        }

        currentLevel = folder.children;
      }

      // Add the file
      const fileName = pathParts[pathParts.length - 1];
      currentLevel.push({
        id: file.id || `file-${file.path || file.name}`,
        name: fileName,
        type: "file",
        path: file.path || file.name,
        content: file.content || "",
        language: file.language || "plaintext",
        ...file
      });
    });

    return tree;
  }, []);

  // Update tree when files change
  useEffect(() => {
    if (files.length > 0) {
      const tree = buildTreeFromFiles(files);
      setTreeData(tree);

      // Auto-expand root folders
      const rootFolders = tree.filter(item => item.type === "folder").map(folder => folder.id);
      setExpandedNodes(new Set(rootFolders));
    }
  }, [files, buildTreeFromFiles]);

  // Toggle node expansion
  const handleToggleExpand = useCallback((nodeId) => {
    setExpandedNodes(prev => {
      const newSet = new Set(prev);
      if (newSet.has(nodeId)) {
        newSet.delete(nodeId);
      } else {
        newSet.add(nodeId);
      }
      return newSet;
    });
  }, []);

  // Handle file selection
  const handleFileSelect = useCallback((file) => {
    if (onFileSelect) {
      onFileSelect(file);
    }
  }, [onFileSelect]);

  // Handle file actions
  const handleFileAction = useCallback((action, node) => {
    setActionDialog({
      open: true,
      action,
      node,
      initialValue: action === "rename" ? node.name : ""
    });
  }, []);

  // Handle action dialog confirm
  const handleActionConfirm = useCallback(async (value) => {
    const { action, node } = actionDialog;

    try {
      const processId = startProcess(`file-${action}`, `${action} in progress...`, "file_generation");

      switch (action) {
        case "create-file":
          if (onFileCreate) {
            const newFile = {
              name: value,
              path: node ? `${node.path}/${value}` : value,
              content: "",
              type: "file"
            };
            onFileCreate(newFile);
          }
          break;

        case "create-folder":
          // Handle folder creation
          console.log("Create folder:", value, "in:", node);
          break;

        case "rename":
          // Handle rename via API if needed
          console.log("Rename:", node.name, "to:", value);
          break;

        case "delete":
          // Handle delete via API if needed
          console.log("Delete:", node.name);
          break;
      }

      completeProcess(processId, `${action} completed`);
      setActionDialog({ open: false, action: null, node: null });

    } catch (err) {
      console.error(`File ${action} failed:`, err);
      errorProcess(`file-${action}`, err.message);
      setError(`Failed to ${action} file`);
    }
  }, [actionDialog, onFileCreate, startProcess, completeProcess, errorProcess]);

  // Create new file in root
  const handleCreateFile = useCallback(() => {
    setActionDialog({
      open: true,
      action: "create-file",
      node: null,
      initialValue: ""
    });
  }, []);

  return (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <Box sx={{
        p: 2,
        borderBottom: 1,
        borderColor: "divider",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between"
      }}>
        <Typography variant="h6">Files</Typography>
        <Tooltip title="New File">
          <IconButton size="small" onClick={handleCreateFile}>
            <AddIcon />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Error Display */}
      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ m: 1 }}>
          {error}
        </Alert>
      )}

      {/* File Tree */}
      <Box sx={{ flexGrow: 1, overflow: "auto" }}>
        {loading ? (
          <Box sx={{ p: 2, textAlign: "center" }}>
            <Typography color="text.secondary">Loading files...</Typography>
          </Box>
        ) : treeData.length > 0 ? (
          treeData.map((node) => (
            <TreeNode
              key={node.id}
              node={node}
              level={0}
              onFileSelect={handleFileSelect}
              onFileAction={handleFileAction}
              expandedNodes={expandedNodes}
              onToggleExpand={handleToggleExpand}
            />
          ))
        ) : (
          <Box sx={{ p: 2, textAlign: "center" }}>
            <Typography color="text.secondary" variant="body2">
              No files found
            </Typography>
            <Button
              size="small"
              startIcon={<AddIcon />}
              onClick={handleCreateFile}
              sx={{ mt: 1 }}
            >
              Create File
            </Button>
          </Box>
        )}
      </Box>

      {/* Action Dialog */}
      <FileActionDialog
        open={actionDialog.open}
        action={actionDialog.action}
        initialValue={actionDialog.initialValue}
        onClose={() => setActionDialog({ open: false, action: null, node: null })}
        onConfirm={handleActionConfirm}
      />
    </Box>
  );
};

export default FileTreeNavigator;