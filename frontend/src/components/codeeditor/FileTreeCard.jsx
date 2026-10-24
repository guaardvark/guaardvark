// frontend/src/components/codeeditor/FileTreeCard.jsx
// File tree navigation card for the code editor

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Typography,
  IconButton,
  Menu,
  MenuItem,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Chip,
  Divider,
} from "@mui/material";
import {
  Folder,
  FolderOpen,
  InsertDriveFile,
  MoreVert,
  Add,
  CreateNewFolder,
  Delete,
  DriveFileRenameOutline,
  Storage,
  KeyboardArrowUp,
} from "@mui/icons-material";

import DashboardCardWrapper from "../dashboard/DashboardCardWrapper";
import * as documentService from "../../api/documentService";
import * as fileOperationsService from "../../api/fileOperationsService";
import UnifiedUploadModal from "../modals/UnifiedUploadModal";
import { getLanguageFromFilename } from "../../utils/languageDetector";
import { loadingTab, loadedTab, failedTab } from "./tabOrigin";
import { useUiTranslation } from "../../i18n";

const FileTreeCard = React.forwardRef(
  (
    {
      style,
      isMinimized,
      onToggleMinimize,
      cardColor,
      onCardColorChange,
      fileTree,
      setFileTree,
      openTabs,
      setOpenTabs,
      activeTabIndex,
      setActiveTabIndex,
      projectId,  // Add projectId prop for filtering documents
      ...props
    },
    ref
  ) => {
    const { t } = useUiTranslation();
    // Remove activeTab - we'll show everything in one merged view
    const [expandedFolders, setExpandedFolders] = useState(new Set());
    const [contextMenu, setContextMenu] = useState(null);
    const [selectedItem, setSelectedItem] = useState(null);
    const [createDialog, setCreateDialog] = useState({ open: false, type: null });
    const [newItemName, setNewItemName] = useState("");
    const createInputRef = useRef(null);
    const [renameDialog, setRenameDialog] = useState({ open: false, item: null });
    const [renameValue, setRenameValue] = useState("");

    // Documents state
    const [documents, setDocuments] = useState([]);
    const [documentsLoading, setDocumentsLoading] = useState(false);
    const [documentsError, setDocumentsError] = useState(null);

    // Upload modal state
    const [uploadModalOpen, setUploadModalOpen] = useState(false);

    // Use centralized language detector (imported from utils/languageDetector.js)
    // This provides comprehensive extension mapping like Cursor IDE
    const getLanguageFromExtension = useCallback((filename) => {
      return getLanguageFromFilename(filename);
    }, []);

    // Load saved files from localStorage
    useEffect(() => {
      const loadSavedFiles = () => {
        try {
          const savedFiles = JSON.parse(localStorage.getItem('codeEditorFiles') || '{}');
          const fileList = Object.values(savedFiles);

          if (fileList.length > 0) {
            setFileTree(fileList.map(file => ({
              name: file.path.split('/').pop(),
              path: file.path,
              type: 'file',
              language: file.language,
              content: file.content,
              source: 'local'
            })));
          }
        } catch (error) {
          console.error('Error loading saved files:', error);
        }
      };

      loadSavedFiles();
    }, [setFileTree]);

    // Load documents from backend - filtered by project if projectId is provided
    const loadDocuments = useCallback(async () => {
      setDocumentsLoading(true);
      setDocumentsError(null);

      // Create an AbortController for cleanup
      const controller = new AbortController();

      try {
        // Pass projectId to filter documents by current project
        const result = await documentService.getDocuments({
          page: 1,
          perPage: 1000,
          projectId: projectId  // Filter by project
        });
        if (controller.signal.aborted) return; // Check if component was unmounted

        if (result && result.error) {
          throw new Error(result.error);
        }
        const docs = result?.documents || result?.items || [];

        // Filter for code files and convert to file tree format
        const codeFiles = docs
          .filter(doc => {
            const fileName = doc.filename || doc.name || '';
            return fileName.match(/\.(js|jsx|ts|tsx|py|php|java|cpp|c|h|css|html|json|xml|yaml|yml|md|txt|sql|sh|bash|go|rs|rb|swift|kt|dart|scala|r|m|mm|cs|vb|pl|lua|vim|conf|cfg|ini|toml|env)$/i);
          })
          .map(doc => ({
            id: doc.id,
            name: doc.filename || doc.name || `document_${doc.id}`,
            path: doc.filename || doc.name || `document_${doc.id}`,
            type: 'file',
            language: getLanguageFromExtension(doc.filename || doc.name || ''),
            content: '', // Will be loaded when opened
            source: 'document',
            document: doc,
            status: doc.status || 'READY'
          }));

        if (controller.signal.aborted) return; // Check if component was unmounted
        setDocuments(codeFiles);
      } catch (error) {
        // Only log errors if they're not due to component unmounting
        if (error.name !== 'AbortError' && !controller.signal.aborted) {
          console.error('Error loading documents:', error);
          setDocumentsError(error.message);
        }
      } finally {
        if (!controller.signal.aborted) {
          setDocumentsLoading(false);
        }
      }

      // Cleanup function
      return () => {
        controller.abort();
      };
    }, [getLanguageFromExtension, projectId]);  // Add projectId to dependencies

    useEffect(() => {
      // Always load documents since we're merging both views
      let cleanup;
      const runLoadDocuments = async () => {
        cleanup = await loadDocuments();
      };
      runLoadDocuments();

      return () => {
        if (cleanup) {
          cleanup();
        }
      };
    }, [loadDocuments]);

    // Upload handlers
    const handleOpenUploadModal = useCallback(() => {
      setUploadModalOpen(true);
    }, []);

    const handleCloseUploadModal = useCallback(() => {
      setUploadModalOpen(false);
    }, []);

    const handleUploadComplete = useCallback((uploadResult) => {
      // Refresh documents list after upload
      loadDocuments();
      console.log("Upload completed:", uploadResult);
    }, [loadDocuments]);


    const handleFileClick = useCallback(async (file) => {
      // Check if file is already open - use functional update to get latest state
      // This prevents duplicate tabs from race conditions with rapid clicks
      let alreadyOpen = false;
      let existingIndex = -1;

      setOpenTabs(currentTabs => {
        existingIndex = currentTabs.findIndex(tab => {
          // Check by file path first
          if (tab.filePath && file.path && tab.filePath === file.path) {
            return true;
          }
          // Check by document ID for backend documents
          if (file.id && tab.documentId === file.id) {
            return true;
          }
          // Check by document reference for backend documents
          if (file.document && tab.document && file.document.id === tab.document.id) {
            return true;
          }
          // Check by file name as fallback
          const tabName = tab.filePath?.split('/').pop() || tab.filePath;
          const fileName = file.path?.split('/').pop() || file.name || file.filename;
          if (tabName && fileName && tabName === fileName && tab.source === (file.source || 'local')) {
            return true;
          }
          return false;
        });

        if (existingIndex !== -1) {
          alreadyOpen = true;
          setActiveTabIndex(existingIndex);
        }
        return currentTabs; // Don't modify tabs, just check
      });

      if (alreadyOpen) {
        return;
      }

      const baseTab = {
        id: Math.random().toString(36).substr(2, 9),
        filePath: file.path,
        language: file.language || getLanguageFromExtension(file.path || file.name || ''),
        isNew: false,
        source: file.source || 'local',
        documentId: file.id || null,
        document: file.document || null,
      };

      // A library document is fetched after its tab opens. The tab starts
      // empty in the loading state; placeholder and error text never become
      // its content, so a save can never write them over the document.
      if (file.source === 'document' && !file.content) {
        const fileName = file.name || file.filename || '';
        const isCodeFile = fileName.match(/\.(js|jsx|ts|tsx|py|php|java|cpp|c|h|css|html|json|xml|yaml|yml|md|txt|sql|sh|bash|go|rs|rb|swift|kt|dart|scala|r|m|mm|cs|vb|pl|lua|vim|conf|cfg|ini|toml|env)$/i);
        const pendingTab = isCodeFile
          ? loadingTab(baseTab)
          : failedTab(baseTab, t("codeEditor.fileTree.notCodeFile", { name: fileName }));

        setOpenTabs(prev => {
          const existingIdx = prev.findIndex(tab =>
            (tab.filePath === file.path) || (file.id && tab.documentId === file.id)
          );
          if (existingIdx !== -1) {
            setActiveTabIndex(existingIdx);
            return prev; // Don't add duplicate
          }
          const newTabs = [...prev, pendingTab];
          setActiveTabIndex(newTabs.length - 1);
          return newTabs;
        });
        if (!isCodeFile) return;

        const settle = (finished) => {
          setOpenTabs(prev => prev.map(tab => (tab.id === pendingTab.id ? finished(tab) : tab)));
        };
        try {
          const result = await documentService.getDocumentContent(file.id);
          if (result.error) {
            settle(tab => failedTab(tab, result.error));
          } else {
            settle(tab => loadedTab(tab, result.content || ''));
          }
        } catch (fetchError) {
          console.error('Error fetching document content:', fetchError);
          settle(tab => failedTab(tab, fetchError));
        }
        return;
      }

      // Local files and documents handed over with their content are loaded already
      const newTab = loadedTab(baseTab, file.content || "");

      setOpenTabs(prev => {
        // Final duplicate check before adding
        const alreadyExists = prev.some(tab =>
          (tab.filePath && file.path && tab.filePath === file.path) ||
          (file.id && tab.documentId === file.id)
        );
        if (alreadyExists) {
          const existingIdx = prev.findIndex(tab =>
            (tab.filePath === file.path) || (file.id && tab.documentId === file.id)
          );
          if (existingIdx !== -1) setActiveTabIndex(existingIdx);
          return prev; // Don't add duplicate
        }
        const newTabs = [...prev, newTab];
        // Set the active tab to the newly added tab
        setActiveTabIndex(newTabs.length - 1);
        return newTabs;
      });
    }, [setOpenTabs, setActiveTabIndex, t]);

    const handleFolderClick = useCallback((folderPath) => {
      setExpandedFolders(prev => {
        const newSet = new Set(prev);
        if (newSet.has(folderPath)) {
          newSet.delete(folderPath);
        } else {
          newSet.add(folderPath);
        }
        return newSet;
      });
    }, []);

    const handleContextMenu = useCallback((event, item) => {
      event.preventDefault();
      event.stopPropagation();
      setContextMenu({
        mouseX: event.clientX - 2,
        mouseY: event.clientY - 4,
      });
      setSelectedItem(item);
    }, []);

    const handleCloseContextMenu = useCallback(() => {
      setContextMenu(null);
      setSelectedItem(null);
    }, []);

    const handleCreateNew = useCallback((type) => {
      setCreateDialog({ open: true, type });
      handleCloseContextMenu();
    }, [handleCloseContextMenu]);

    const handleCreateConfirm = useCallback(() => {
      if (!newItemName.trim()) return;

      try {
        // Validate filename
        const trimmedName = newItemName.trim();
        // eslint-disable-next-line no-control-regex -- intentional: matches OS-illegal filename control chars
        const invalidChars = /[<>:"/\\|?*\x00-\x1f]/;
        if (invalidChars.test(trimmedName)) {
          alert(t("codeEditor.rename.invalidChars"));
          return;
        }

        if (trimmedName.length > 255) {
          alert(t("codeEditor.rename.tooLong"));
          return;
        }

        // Reserved names check
        const reservedNames = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'];
        if (reservedNames.includes(trimmedName.toUpperCase())) {
          alert(t("codeEditor.fileTree.reservedName"));
          return;
        }

        const _timestamp = Date.now();
        const extension = createDialog.type === 'file' ?
          (trimmedName.includes('.') ? '' : '.js') : '';
        const fullName = trimmedName + extension;

        // Check if file already exists
        const existingFilesData = localStorage.getItem('codeEditorFiles') || '{}';
        let existingFiles;
        try {
          existingFiles = JSON.parse(existingFilesData);
        } catch (parseError) {
          console.warn('Invalid JSON in localStorage, resetting:', parseError);
          existingFiles = {};
        }

        if (existingFiles[fullName]) {
          alert(t("codeEditor.fileTree.alreadyExists", { name: fullName }));
          return;
        }

        if (createDialog.type === 'file') {
          const detectedLanguage = getLanguageFromExtension(fullName);
          const newFile = {
            name: fullName,
            path: fullName,
            type: 'file',
            language: detectedLanguage,
            content: ''
          };

          // Save to localStorage with error handling
          existingFiles[fullName] = {
            path: fullName,
            content: '',
            language: detectedLanguage,
            lastModified: new Date().toISOString()
          };
          localStorage.setItem('codeEditorFiles', JSON.stringify(existingFiles));

          setFileTree(prev => [...prev, newFile]);
          handleFileClick(newFile);
        }

        setCreateDialog({ open: false, type: null });
        setNewItemName("");
      } catch (error) {
        console.error('Error creating file:', error);
        alert(t("codeEditor.fileTree.createFailed"));
      }
    }, [newItemName, createDialog.type, setFileTree, handleFileClick, t]);

    const handleDelete = useCallback(async () => {
      if (!selectedItem) return;

      try {
        if (!confirm(t("codeEditor.fileTree.deleteConfirm", { name: selectedItem.name }))) {
          return;
        }

        // Handle different file sources
        if (selectedItem.source === 'local') {
          // Remove from localStorage
          const existingFiles = JSON.parse(localStorage.getItem('codeEditorFiles') || '{}');
          delete existingFiles[selectedItem.path];
          localStorage.setItem('codeEditorFiles', JSON.stringify(existingFiles));
        } else if (selectedItem.source === 'document') {
          // A library document is deleted by id. Its bare name must never reach
          // /api/files/delete, which resolves names under the repo root.
          if (!selectedItem.id) {
            alert(t("codeEditor.fileTree.deleteNoId"));
            return;
          }
          const result = await documentService.deleteDocument(selectedItem.id);
          if (result && result.error) {
            alert(t("codeEditor.fileTree.deleteDocumentFailed", { error: result.error }));
            return;
          }
        } else if (selectedItem.source === 'filesystem') {
          // Delete from filesystem
          const result = await fileOperationsService.deleteFile(selectedItem.path);
          if (!result.success) {
            alert(t("codeEditor.fileTree.deleteFileFailed", { error: result.error }));
            return;
          }
        }

        // Remove from file tree
        setFileTree(prev => prev.filter(item => item.path !== selectedItem.path));

        // Close tab if open
        const tabIndex = openTabs.findIndex(tab => tab.filePath === selectedItem.path);
        if (tabIndex !== -1) {
          setOpenTabs(prev => {
            const newTabs = prev.filter((_, index) => index !== tabIndex);
            // Adjust active tab index if necessary
            if (tabIndex < activeTabIndex) {
              setActiveTabIndex(prevIndex => Math.max(0, prevIndex - 1));
            } else if (tabIndex === activeTabIndex) {
              // If we deleted the active tab, set to the last tab or previous tab
              setActiveTabIndex(Math.max(0, Math.min(tabIndex, newTabs.length - 1)));
            }
            return newTabs;
          });
        }

        handleCloseContextMenu();
      } catch (error) {
        console.error('Error deleting file:', error);
        alert(t("codeEditor.fileTree.deleteFailed"));
      }
    }, [selectedItem, setFileTree, openTabs, activeTabIndex, setOpenTabs, setActiveTabIndex, handleCloseContextMenu, t]);

    const handleRename = useCallback(() => {
      if (!selectedItem) return;
      setRenameValue(selectedItem.name);
      setRenameDialog({ open: true, item: selectedItem });
      handleCloseContextMenu();
    }, [selectedItem, handleCloseContextMenu]);

    const handleRenameConfirm = useCallback(async () => {
      if (!renameDialog.item || !renameValue.trim()) return;

      try {
        const trimmedName = renameValue.trim();
        // eslint-disable-next-line no-control-regex -- intentional: matches OS-illegal filename control chars
        const invalidChars = /[<>:"/\\|?*\x00-\x1f]/;
        if (invalidChars.test(trimmedName)) {
          alert(t("codeEditor.rename.invalidChars"));
          return;
        }

        if (trimmedName.length > 255) {
          alert(t("codeEditor.rename.tooLong"));
          return;
        }

        const oldPath = renameDialog.item.path;
        const newPath = trimmedName;

        // Handle different file sources
        if (renameDialog.item.source === 'local') {
          // Update localStorage
          const existingFiles = JSON.parse(localStorage.getItem('codeEditorFiles') || '{}');
          if (existingFiles[oldPath]) {
            const fileData = existingFiles[oldPath];
            delete existingFiles[oldPath];
            existingFiles[newPath] = {
              ...fileData,
              path: newPath,
              lastModified: new Date().toISOString()
            };
            localStorage.setItem('codeEditorFiles', JSON.stringify(existingFiles));
          }
        } else if (renameDialog.item.source === 'document') {
          // A library document is renamed by id, not by a path under the repo root.
          if (!renameDialog.item.id) {
            alert(t("codeEditor.fileTree.renameNoId"));
            return;
          }
          const result = await documentService.renameDocument(renameDialog.item.id, newPath);
          if (result && result.error) {
            alert(t("codeEditor.fileTree.renameDocumentFailed", { error: result.error }));
            return;
          }
        } else if (renameDialog.item.source === 'filesystem') {
          // Rename a repo file by its repo-relative path
          const result = await fileOperationsService.renameFile(oldPath, newPath);
          if (!result.success) {
            alert(t("codeEditor.fileTree.renameFileFailed", { error: result.error }));
            return;
          }
        }

        // Update file tree
        setFileTree(prev => prev.map(item =>
          item.path === oldPath
            ? { ...item, name: trimmedName, path: newPath }
            : item
        ));

        // Update open tabs
        setOpenTabs(prev => prev.map(tab =>
          tab.filePath === oldPath
            ? { ...tab, filePath: newPath }
            : tab
        ));

        setRenameDialog({ open: false, item: null });
        setRenameValue("");
      } catch (error) {
        console.error('Error renaming file:', error);
        alert(t("codeEditor.rename.failed"));
      }
    }, [renameDialog.item, renameValue, setFileTree, setOpenTabs, t]);

    const getFileIcon = (file) => {
      if (file.type === 'folder') {
        return expandedFolders.has(file.path) ? <FolderOpen /> : <Folder />;
      }
      if (file.source === 'document') {
        return <Storage />;
      }
      return <InsertDriveFile />;
    };

    const getStatusChip = (file) => {
      if (file.source === 'document' && file.status) {
        const statusColors = {
          'READY': 'success',
          'INDEXING': 'warning',
          'ERROR': 'error',
          'PENDING': 'default'
        };
        return (
          <Chip
            label={file.status}
            size="small"
            color={statusColors[file.status] || 'default'}
            sx={{ ml: 1, fontSize: '0.6rem', height: 16 }}
          />
        );
      }
      return null;
    };

    const renderFileTree = (items, level = 0) => {
      return items.map((item) => (
        <ListItem key={item.id || item.path} sx={{ pl: level * 2 }}>
          <ListItemButton
            onClick={() => item.type === 'folder' ? handleFolderClick(item.path) : handleFileClick(item)}
            onContextMenu={(e) => handleContextMenu(e, item)}
            sx={{
              borderRadius: 1,
              '&:hover': {
                bgcolor: 'action.hover',
              }
            }}
          >
            <ListItemIcon sx={{ minWidth: 32 }}>
              {getFileIcon(item)}
            </ListItemIcon>
            <ListItemText
              primary={
                <Box sx={{ display: 'flex', alignItems: 'center', flex: 1 }}>
                  <Typography
                    variant="body2"
                    sx={{
                      fontFamily: 'monospace',
                      flex: 1,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis'
                    }}
                  >
                    {item.name}
                  </Typography>
                  {getStatusChip(item)}
                </Box>
              }
            />
            <IconButton
              size="small"
              onClick={(e) => handleContextMenu(e, item)}
              sx={{ opacity: 0.7 }}
            >
              <MoreVert fontSize="small" />
            </IconButton>
          </ListItemButton>
        </ListItem>
      ));
    };

    return (
      <DashboardCardWrapper
        ref={ref}
        title={t("codeEditor.fileTree.title")}
        cardColor={cardColor}
        onCardColorChange={onCardColorChange}
        isMinimized={isMinimized}
        onToggleMinimize={onToggleMinimize}
        style={style}
        titleBarActions={
          <IconButton
            size="small"
            onClick={handleOpenUploadModal}
            title={t("codeEditor.fileTree.upload")}
            sx={{
              color: 'text.secondary',
              '&:hover': { color: 'primary.main' }
            }}
          >
            <KeyboardArrowUp fontSize="small" />
          </IconButton>
        }
        {...props}
      >
        <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
          {/* Action Bar */}
          <Box sx={{ p: 1, borderBottom: 1, borderColor: 'divider', display: 'flex', gap: 1 }}>
            <Button
              size="small"
              startIcon={<Add />}
              onClick={() => handleCreateNew('file')}
              variant="outlined"
              sx={{ flex: 1 }}
            >
              {t("codeEditor.card.newFile")}
            </Button>
          </Box>

          {/* Merged File List */}
          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {documentsLoading && (
              <Box sx={{ p: 2, textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  {t("codeEditor.fileTree.loadingDocuments")}
                </Typography>
              </Box>
            )}

            {documentsError && (
              <Box sx={{ p: 2, textAlign: 'center' }}>
                <Typography variant="body2" color="error">
                  {documentsError}
                </Typography>
                <Button size="small" onClick={loadDocuments} sx={{ mt: 1 }}>
                  {t("codeEditor.actions.retry")}
                </Button>
              </Box>
            )}

            {(fileTree.length === 0 && documents.length === 0 && !documentsLoading && !documentsError) ? (
              <Box sx={{ p: 2, textAlign: 'center' }}>
                <Typography variant="body2" color="text.secondary">
                  {t("codeEditor.fileTree.empty")}
                </Typography>
              </Box>
            ) : (
              <List dense sx={{ py: 0 }}>
                {/* Local Files Section */}
                {fileTree.length > 0 && (
                  <>
                    <ListItem sx={{ py: 0.5, bgcolor: 'action.hover' }}>
                      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
                        {t("codeEditor.fileTree.localFiles")}
                      </Typography>
                    </ListItem>
                    {renderFileTree(fileTree)}
                  </>
                )}

                {/* Documents Section */}
                {documents.length > 0 && (
                  <>
                    {fileTree.length > 0 && <Divider sx={{ my: 1 }} />}
                    <ListItem sx={{ py: 0.5, bgcolor: 'action.hover' }}>
                      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 'bold' }}>
                        {t("codeEditor.fileTree.documents")}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
                        {projectId ? t("codeEditor.fileTree.projectScope", { id: projectId }) : t("codeEditor.fileTree.libraryScope")}
                      </Typography>
                    </ListItem>
                    {renderFileTree(documents)}
                  </>
                )}
              </List>
            )}
          </Box>
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
          {/* Create options available for all files */}
          <MenuItem onClick={() => handleCreateNew('file')}>
            <ListItemIcon>
              <InsertDriveFile fontSize="small" />
            </ListItemIcon>
            <ListItemText>{t("codeEditor.card.newFile")}</ListItemText>
          </MenuItem>
          {/* Files here are a flat list (browser storage and the library), so a folder has nowhere to go. */}
          <MenuItem disabled>
            <ListItemIcon>
              <CreateNewFolder fontSize="small" />
            </ListItemIcon>
            <ListItemText primary={t("codeEditor.fileTree.newFolder")} secondary={t("codeEditor.fileTree.noFolders")} />
          </MenuItem>

          {/* Rename option - only for local files, not documents */}
          {selectedItem && selectedItem.source !== 'document' && (
            <MenuItem onClick={handleRename}>
              <ListItemIcon>
                <DriveFileRenameOutline fontSize="small" />
              </ListItemIcon>
              <ListItemText>{t("codeEditor.rename.confirm")}</ListItemText>
            </MenuItem>
          )}

          {/* Delete option - only for local files, not documents */}
          {selectedItem && selectedItem.source !== 'document' && (
            <MenuItem onClick={handleDelete}>
              <ListItemIcon>
                <Delete fontSize="small" />
              </ListItemIcon>
              <ListItemText>{t("codeEditor.fileTree.delete")}</ListItemText>
            </MenuItem>
          )}

          {/* Document-specific options */}
          {selectedItem && selectedItem.source === 'document' && (
            <MenuItem disabled>
              <ListItemIcon>
                <Storage fontSize="small" />
              </ListItemIcon>
              <ListItemText>{t("codeEditor.fileTree.fromBackend")}</ListItemText>
            </MenuItem>
          )}
        </Menu>

        {/* Create Dialog */}
        <Dialog
          open={createDialog.open}
          onClose={() => setCreateDialog({ open: false, type: null })}
          // autoFocus alone can lose focus to whatever had it before the
          // dialog opened; focus the field once the dialog is in.
          TransitionProps={{ onEntered: () => createInputRef.current?.focus() }}
        >
          <DialogTitle>
            {createDialog.type === 'file' ? t("codeEditor.fileTree.createFileTitle") : t("codeEditor.fileTree.createFolderTitle")}
          </DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              inputRef={createInputRef}
              margin="dense"
              label={createDialog.type === 'file' ? t("codeEditor.fileTree.fileNameLabel") : t("codeEditor.fileTree.folderNameLabel")}
              fullWidth
              variant="outlined"
              value={newItemName}
              onChange={(e) => setNewItemName(e.target.value)}
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  handleCreateConfirm();
                }
              }}
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setCreateDialog({ open: false, type: null })}>
              {t("codeEditor.actions.cancel")}
            </Button>
            <Button onClick={handleCreateConfirm} variant="contained">
              {t("codeEditor.fileTree.create")}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Rename Dialog */}
        <Dialog open={renameDialog.open} onClose={() => setRenameDialog({ open: false, item: null })}>
          <DialogTitle>
            {renameDialog.item?.type === 'file' ? t("codeEditor.rename.title") : t("codeEditor.fileTree.renameFolderTitle")}
          </DialogTitle>
          <DialogContent>
            <TextField
              autoFocus
              margin="dense"
              label={t("codeEditor.fileTree.newName")}
              fullWidth
              variant="outlined"
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyPress={(e) => {
                if (e.key === 'Enter') {
                  handleRenameConfirm();
                }
              }}
              helperText={t("codeEditor.fileTree.renameHelper")}
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setRenameDialog({ open: false, item: null })}>
              {t("codeEditor.actions.cancel")}
            </Button>
            <Button onClick={handleRenameConfirm} variant="contained">
              {t("codeEditor.rename.confirm")}
            </Button>
          </DialogActions>
        </Dialog>

        {/* Unified Upload Modal */}
        <UnifiedUploadModal
          open={uploadModalOpen}
          onClose={handleCloseUploadModal}
          onUploadComplete={handleUploadComplete}
          mode="document"
        />
      </DashboardCardWrapper>
    );
  }
);

FileTreeCard.displayName = "FileTreeCard";

export default FileTreeCard;