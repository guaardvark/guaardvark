// ImageBatchContents.jsx
// Displays image thumbnails for a batch inside a window.
// Supports: thumbnail grid, lightbox, selection mode, context menu, keyboard nav.

import React, { useState, useEffect, useCallback } from 'react';
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
  CheckBox as CheckBoxIcon,
} from '@mui/icons-material';
import { useTheme } from '@mui/material/styles';
import ImageLightbox from './ImageLightbox';

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

  // Lightbox (unified with ImageLightbox component)
  const openLightbox = useCallback((image) => {
    if (!batch || !image?.imageFilename) return;
    const idx = images.findIndex(img => img.id === image.id);
    setLightboxImage({
      url: `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(image.imageFilename)}`,
      name: image.prompt || image.imageFilename || '',
      fileIndex: idx >= 0 ? idx : 0,
    });
  }, [batch, images]);

  const closeLightbox = useCallback(() => setLightboxImage(null), []);

  const handleLightboxPrev = useCallback(() => {
    if (!lightboxImage || lightboxImage.fileIndex <= 0) return;
    const newIdx = lightboxImage.fileIndex - 1;
    const img = images[newIdx];
    if (!img) return;
    setLightboxImage({
      url: `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(img.imageFilename)}`,
      name: img.prompt || img.imageFilename || '',
      fileIndex: newIdx,
    });
  }, [lightboxImage, images, batch]);

  const handleLightboxNext = useCallback(() => {
    if (!lightboxImage || lightboxImage.fileIndex >= images.length - 1) return;
    const newIdx = lightboxImage.fileIndex + 1;
    const img = images[newIdx];
    if (!img) return;
    setLightboxImage({
      url: `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(img.imageFilename)}`,
      name: img.prompt || img.imageFilename || '',
      fileIndex: newIdx,
    });
  }, [lightboxImage, images, batch]);

  const handleLightboxDownload = useCallback(() => {
    if (!lightboxImage || !batch) return;
    const img = images[lightboxImage.fileIndex];
    if (!img) return;
    const link = document.createElement('a');
    link.href = `${API_BASE}/batch-image/image/${batch.batch_id}/${encodeFilename(img.imageFilename)}`;
    link.download = img.imageFilename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightboxImage, images, batch]);

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

      {/* Unified Lightbox */}
      {lightboxImage && (
        <ImageLightbox
          imageUrl={lightboxImage.url}
          imageName={lightboxImage.name}
          onClose={closeLightbox}
          onPrev={handleLightboxPrev}
          onNext={handleLightboxNext}
          onDownload={handleLightboxDownload}
          hasPrev={lightboxImage.fileIndex > 0}
          hasNext={lightboxImage.fileIndex < images.length - 1}
        />
      )}
    </Box>
  );
};

export default ImageBatchContents;
