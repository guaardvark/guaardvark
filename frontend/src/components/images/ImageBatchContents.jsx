// ImageBatchContents.jsx
// Displays image thumbnails for a batch inside a window.
// Supports: thumbnail grid, lightbox, selection mode, context menu, keyboard nav.

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Box,
  Grid,
  Card,
  CardContent,
  CardActionArea,
  Typography,
  CircularProgress,
  IconButton,
  Checkbox,
  Tooltip,
  Menu,
  MenuItem,
  ListItemIcon,
  ListItemText,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  TextField,
} from '@mui/material';
import {
  Image as ImageIcon,
  Visibility as ViewIcon,
  Delete as DeleteIcon,
  DriveFileRenameOutline as RenameIcon,
  MoreVert as MoreVertIcon,
  Close as CloseIcon,
  CheckBox as CheckBoxIcon,
  Download as DownloadIcon,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

const encodeFilename = (filename) => {
  if (!filename) return '';
  return filename.split('/').map(part => encodeURIComponent(part)).join('/');
};

const ImageBatchContents = ({ batch, onFeedback }) => {
  const theme = useTheme();
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [lightboxImage, setLightboxImage] = useState(null);
  const [imageSelectMode, setImageSelectMode] = useState(false);
  const [selectedImages, setSelectedImages] = useState(new Set());
  const [lastSelectedIndex, setLastSelectedIndex] = useState(null);
  const [contextMenu, setContextMenu] = useState(null);
  const [contextImage, setContextImage] = useState(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [newImageName, setNewImageName] = useState('');
  const [renameTarget, setRenameTarget] = useState(null);

  // Fetch batch images
  const fetchImages = useCallback(async () => {
    if (!batch?.batch_id) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batch.batch_id}?include_results=true`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();

      if (data.success && data.data.results) {
        const imgs = data.data.results
          .filter(r => r.success && r.image_path)
          .map(r => {
            const getFilename = (path) => {
              if (!path) return null;
              return path.replace(/\\/g, '/').split('/').pop();
            };
            return {
              id: r.prompt_id,
              path: r.image_path,
              imageFilename: getFilename(r.image_path),
              thumbnailFilename: r.thumbnail_path ? getFilename(r.thumbnail_path) : null,
              prompt: r.metadata?.original_prompt || r.metadata?.prompt || '',
              metadata: r.metadata,
            };
          });
        setImages(imgs);
      }
    } catch (err) {
      onFeedback?.(`Failed to load images: ${err.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }, [batch?.batch_id, onFeedback]);

  useEffect(() => {
    fetchImages();
  }, [fetchImages]);

  // Lightbox
  const openLightbox = useCallback((image) => {
    if (!batch || !image?.imageFilename) return;
    setLightboxImage({
      url: `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(image.imageFilename)}`,
      prompt: image.prompt || '',
      id: image.id,
    });
  }, [batch]);

  const closeLightbox = useCallback(() => setLightboxImage(null), []);

  const navigateLightbox = useCallback((direction) => {
    if (!lightboxImage || !images.length) return;
    const idx = images.findIndex(img => img.id === lightboxImage.id);
    if (idx === -1) return;
    const newIdx = direction === 'next'
      ? (idx + 1) % images.length
      : (idx - 1 + images.length) % images.length;
    openLightbox(images[newIdx]);
  }, [lightboxImage, images, openLightbox]);

  // Keyboard nav for lightbox
  useEffect(() => {
    if (!lightboxImage) return;
    const handler = (e) => {
      if (e.key === 'Escape') closeLightbox();
      else if (e.key === 'ArrowLeft') navigateLightbox('prev');
      else if (e.key === 'ArrowRight') navigateLightbox('next');
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [lightboxImage, closeLightbox, navigateLightbox]);

  // Selection
  const toggleSelectMode = () => {
    setImageSelectMode(prev => !prev);
    setSelectedImages(new Set());
    setLastSelectedIndex(null);
  };

  const handleSelectionClick = (event, image, index) => {
    if (!imageSelectMode) return;
    setSelectedImages(prev => {
      const next = new Set(prev);
      if (event.shiftKey && lastSelectedIndex !== null) {
        const start = Math.min(lastSelectedIndex, index);
        const end = Math.max(lastSelectedIndex, index);
        for (let i = start; i <= end; i++) {
          if (images[i]) next.add(images[i].id);
        }
      } else {
        if (next.has(image.id)) next.delete(image.id);
        else next.add(image.id);
        setLastSelectedIndex(index);
      }
      return next;
    });
    if (!event.shiftKey) setLastSelectedIndex(index);
  };

  const selectAll = () => {
    if (selectedImages.size === images.length) setSelectedImages(new Set());
    else setSelectedImages(new Set(images.map(img => img.id)));
    setLastSelectedIndex(null);
  };

  // Delete
  const deleteImage = async (image) => {
    if (!batch || !image) return;
    if (!window.confirm('Delete this image?')) return;
    try {
      const filename = image.imageFilename || image.thumbnailFilename;
      if (!filename) throw new Error('No filename');
      const resp = await fetch(`${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(filename)}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setImages(prev => prev.filter(img => img.id !== image.id));
      onFeedback?.('Image deleted', 'success');
    } catch (err) {
      onFeedback?.(`Delete failed: ${err.message}`, 'error');
    }
  };

  const bulkDelete = async () => {
    if (selectedImages.size === 0) return;
    if (!window.confirm(`Delete ${selectedImages.size} image(s)?`)) return;
    const toDelete = images.filter(img => selectedImages.has(img.id));
    let ok = 0, fail = 0;
    for (const img of toDelete) {
      try {
        const filename = img.imageFilename || img.thumbnailFilename;
        if (!filename) { fail++; continue; }
        const resp = await fetch(`${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(filename)}`, { method: 'DELETE' });
        if (resp.ok) ok++; else fail++;
      } catch { fail++; }
    }
    await fetchImages();
    setSelectedImages(new Set());
    setImageSelectMode(false);
    onFeedback?.(fail === 0 ? `Deleted ${ok} image(s)` : `Deleted ${ok}, ${fail} failed`, fail === 0 ? 'success' : 'warning');
  };

  // Context menu
  const handleContextMenu = useCallback((e, image) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4 });
    setContextImage(image);
  }, []);

  const closeContextMenu = () => { setContextMenu(null); setContextImage(null); };

  const handleMenuAction = (action) => {
    if (!contextImage) return;
    const img = contextImage;
    closeContextMenu();
    switch (action) {
      case 'view': openLightbox(img); break;
      case 'rename':
        setRenameTarget(img);
        setNewImageName(img.imageFilename || img.thumbnailFilename || '');
        setRenameOpen(true);
        break;
      case 'delete': deleteImage(img); break;
    }
  };

  // Rename
  const handleRename = async () => {
    if (!renameTarget || !newImageName.trim() || !batch) return;
    try {
      const oldName = renameTarget.imageFilename || renameTarget.thumbnailFilename;
      if (!oldName) throw new Error('No filename');
      const resp = await fetch(`${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(oldName)}/rename`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_name: newImageName.trim() }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setRenameOpen(false);
      onFeedback?.('Image renamed', 'success');
      await fetchImages();
    } catch (err) {
      onFeedback?.(`Rename failed: ${err.message}`, 'error');
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', py: 4 }}>
        <CircularProgress size={32} />
        <Typography variant="body2" color="text.secondary" sx={{ ml: 2 }}>Loading images...</Typography>
      </Box>
    );
  }

  if (images.length === 0) {
    return (
      <Box sx={{ p: 3, textAlign: 'center' }}>
        <ImageIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
        <Typography variant="body2" color="text.secondary">No images in this batch</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Toolbar */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1, py: 0.5, borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}>
        <Typography variant="caption" color="text.secondary">
          {images.length} image{images.length !== 1 ? 's' : ''}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        {imageSelectMode && selectedImages.size > 0 && (
          <Button size="small" color="error" startIcon={<DeleteIcon />} onClick={bulkDelete} sx={{ textTransform: 'none', fontSize: '0.7rem' }}>
            Delete ({selectedImages.size})
          </Button>
        )}
        <Tooltip title={imageSelectMode ? "Exit select mode" : "Select mode"}>
          <IconButton size="small" onClick={toggleSelectMode} color={imageSelectMode ? "primary" : "default"}>
            <CheckBoxIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Select all bar */}
      {imageSelectMode && (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 1, py: 0.5, flexShrink: 0 }}>
          <Checkbox
            size="small"
            indeterminate={selectedImages.size > 0 && selectedImages.size < images.length}
            checked={selectedImages.size === images.length}
            onChange={selectAll}
          />
          <Typography variant="caption">
            {selectedImages.size > 0 ? `${selectedImages.size} of ${images.length}` : `Select all (${images.length})`}
          </Typography>
        </Box>
      )}

      {/* Image grid */}
      <Box sx={{ flex: 1, overflow: 'auto', p: 1 }}>
        <Grid container spacing={1}>
          {images.map((image, index) => {
            const thumbnailUrl = image.thumbnailFilename
              ? `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(image.thumbnailFilename)}?thumbnail=true`
              : image.imageFilename
              ? `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(image.imageFilename)}?thumbnail=true`
              : null;
            const isSelected = selectedImages.has(image.id);

            return (
              <Grid item xs={6} sm={4} md={3} key={image.id}>
                <Card
                  sx={{
                    cursor: imageSelectMode ? 'default' : 'pointer',
                    transition: 'all 0.15s',
                    border: isSelected ? '2px solid' : '1px solid',
                    borderColor: isSelected ? 'primary.main' : 'divider',
                    bgcolor: isSelected ? 'action.selected' : 'background.paper',
                    position: 'relative',
                    '&:hover': { boxShadow: theme.shadows[4] },
                  }}
                  onClick={(e) => {
                    if (imageSelectMode) handleSelectionClick(e, image, index);
                    else openLightbox(image);
                  }}
                  onContextMenu={(e) => handleContextMenu(e, image)}
                >
                  {imageSelectMode && (
                    <Checkbox
                      size="small"
                      checked={isSelected}
                      onClick={(e) => { e.stopPropagation(); handleSelectionClick(e, image, index); }}
                      sx={{ position: 'absolute', top: 2, left: 2, zIndex: 2, bgcolor: 'background.paper', borderRadius: '50%' }}
                    />
                  )}
                  {!imageSelectMode && (
                    <IconButton
                      size="small"
                      onClick={(e) => { e.stopPropagation(); handleContextMenu(e, image); }}
                      sx={{ position: 'absolute', top: 2, right: 2, zIndex: 2, bgcolor: 'rgba(255,255,255,0.8)', '&:hover': { bgcolor: 'rgba(255,255,255,0.95)' } }}
                    >
                      <MoreVertIcon fontSize="small" />
                    </IconButton>
                  )}
                  <CardActionArea disabled={imageSelectMode}>
                    <CardContent sx={{ p: 0.5 }}>
                      <Box sx={{ width: '100%', aspectRatio: '1', bgcolor: 'transparent', borderRadius: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden', mb: 0.5 }}>
                        {thumbnailUrl ? (
                          <img
                            src={thumbnailUrl}
                            alt={image.prompt || image.id}
                            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                            onError={(e) => {
                              if (image.imageFilename && !e.target.dataset.fallbackAttempted) {
                                e.target.src = `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(image.imageFilename)}?thumbnail=true`;
                                e.target.dataset.fallbackAttempted = 'true';
                              } else {
                                e.target.style.display = 'none';
                              }
                            }}
                          />
                        ) : (
                          <ImageIcon sx={{ fontSize: 36, color: 'text.disabled' }} />
                        )}
                      </Box>
                      {image.prompt && (
                        <Typography variant="caption" sx={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: '0.65rem' }} title={image.prompt}>
                          {image.prompt}
                        </Typography>
                      )}
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Grid>
            );
          })}
        </Grid>
      </Box>

      {/* Context Menu */}
      <Menu
        open={contextMenu !== null}
        onClose={closeContextMenu}
        anchorReference="anchorPosition"
        anchorPosition={contextMenu ? { top: contextMenu.mouseY, left: contextMenu.mouseX } : undefined}
      >
        <MenuItem onClick={() => handleMenuAction('view')}>
          <ListItemIcon><ViewIcon fontSize="small" /></ListItemIcon>
          <ListItemText>View</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => handleMenuAction('rename')}>
          <ListItemIcon><RenameIcon fontSize="small" /></ListItemIcon>
          <ListItemText>Rename</ListItemText>
        </MenuItem>
        <MenuItem onClick={() => handleMenuAction('delete')} sx={{ color: 'error.main' }}>
          <ListItemIcon><DeleteIcon fontSize="small" color="error" /></ListItemIcon>
          <ListItemText>Delete</ListItemText>
        </MenuItem>
      </Menu>

      {/* Rename Dialog */}
      <Dialog open={renameOpen} onClose={() => setRenameOpen(false)}>
        <DialogTitle>Rename Image</DialogTitle>
        <DialogContent>
          <TextField
            autoFocus
            margin="dense"
            label="New Name"
            fullWidth
            variant="outlined"
            value={newImageName}
            onChange={(e) => setNewImageName(e.target.value)}
            onKeyPress={(e) => { if (e.key === 'Enter') handleRename(); }}
            helperText="Extension will be preserved"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRenameOpen(false)}>Cancel</Button>
          <Button onClick={handleRename} variant="contained">Rename</Button>
        </DialogActions>
      </Dialog>

      {/* Lightbox */}
      <Dialog
        open={!!lightboxImage}
        onClose={closeLightbox}
        maxWidth="lg"
        fullWidth
        PaperProps={{ sx: { bgcolor: 'rgba(0,0,0,0.9)', maxHeight: '95vh' } }}
      >
        <DialogTitle sx={{ color: 'white', pb: 1 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography variant="h6" sx={{ color: 'white', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', mr: 2 }}>
              {lightboxImage?.prompt || 'Image'}
            </Typography>
            <IconButton onClick={closeLightbox} size="small" sx={{ color: 'white' }}>
              <CloseIcon />
            </IconButton>
          </Box>
        </DialogTitle>
        <DialogContent sx={{ p: 0, position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '60vh' }}>
          {lightboxImage && (
            <>
              <IconButton
                onClick={() => navigateLightbox('prev')}
                sx={{ position: 'absolute', left: 16, top: '50%', transform: 'translateY(-50%)', color: 'white', bgcolor: 'rgba(0,0,0,0.5)', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' }, zIndex: 2 }}
              >
                <Typography variant="h4">&#8249;</Typography>
              </IconButton>
              <Box sx={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', p: 2 }}>
                <img
                  src={lightboxImage.url}
                  alt={lightboxImage.prompt || 'Image'}
                  style={{ maxWidth: '100%', maxHeight: '75vh', objectFit: 'contain' }}
                />
              </Box>
              <IconButton
                onClick={() => navigateLightbox('next')}
                sx={{ position: 'absolute', right: 16, top: '50%', transform: 'translateY(-50%)', color: 'white', bgcolor: 'rgba(0,0,0,0.5)', '&:hover': { bgcolor: 'rgba(0,0,0,0.7)' }, zIndex: 2 }}
              >
                <Typography variant="h4">&#8250;</Typography>
              </IconButton>
            </>
          )}
        </DialogContent>
        {lightboxImage && images.length > 1 && (
          <DialogActions sx={{ justifyContent: 'center', pb: 2 }}>
            <Typography variant="body2" sx={{ color: 'white' }}>
              {images.findIndex(img => img.id === lightboxImage.id) + 1} of {images.length}
            </Typography>
          </DialogActions>
        )}
      </Dialog>
    </Box>
  );
};

export default ImageBatchContents;
