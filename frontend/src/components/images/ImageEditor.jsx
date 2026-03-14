// frontend/src/components/images/ImageEditor.jsx
// Full-screen image editor with zoom, pan, rotate, flip, crop, and save-as.
// Uses react-image-crop for crop selection, backend Pillow for actual processing.

import React, { useState, useRef, useCallback, useEffect } from 'react';
import ReactCrop from 'react-image-crop';
import 'react-image-crop/dist/ReactCrop.css';
import {
  Box,
  IconButton,
  Typography,
  Tooltip,
  Slider,
  ToggleButton,
  ToggleButtonGroup,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  CircularProgress,
  Divider,
} from '@mui/material';
import {
  Close as CloseIcon,
  ZoomIn as ZoomInIcon,
  ZoomOut as ZoomOutIcon,
  RotateLeft as RotateLeftIcon,
  RotateRight as RotateRightIcon,
  Flip as FlipHIcon,
  Crop as CropIcon,
  Save as SaveIcon,
  SaveAs as SaveAsIcon,
  Undo as UndoIcon,
  FitScreen as FitScreenIcon,
  ArrowBack as BackIcon,
} from '@mui/icons-material';

const FlipVIcon = () => (
  <FlipHIcon sx={{ transform: 'rotate(90deg)' }} />
);

const API_BASE = '/api/files';

const ImageEditor = ({
  imageUrl,
  imageName = '',
  documentId,
  onClose,
  onSaved,
}) => {
  // View state
  const [zoom, setZoom] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [flipH, setFlipH] = useState(false);
  const [flipV, setFlipV] = useState(false);
  const [pan, setPan] = useState({ x: 0, y: 0 });

  // Crop state
  const [cropMode, setCropMode] = useState(false);
  const [crop, setCrop] = useState(undefined);
  const [completedCrop, setCompletedCrop] = useState(null);

  // Save dialog
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveMode, setSaveMode] = useState('copy');
  const [saveFormat, setSaveFormat] = useState('');
  const [saveQuality, setSaveQuality] = useState(90);
  const [saving, setSaving] = useState(false);

  // Image info
  const [imageInfo, setImageInfo] = useState(null);
  const [naturalSize, setNaturalSize] = useState({ w: 0, h: 0 });

  // Undo stack (stores operations)
  const [operations, setOperations] = useState([]);

  // Refs
  const imgRef = useRef(null);
  const containerRef = useRef(null);
  const isPanning = useRef(false);
  const panStart = useRef({ x: 0, y: 0 });

  // Fetch image info on mount
  useEffect(() => {
    if (!documentId) return;
    fetch(`${API_BASE}/image/info/${documentId}`)
      .then(r => r.json())
      .then(data => {
        if (data.success && data.data) {
          setImageInfo(data.data);
          setSaveFormat(data.data.format?.toLowerCase() === 'jpeg' ? 'jpeg' :
            data.data.format?.toLowerCase() || '');
        }
      })
      .catch(() => {});
  }, [documentId]);

  // Image load handler
  const handleImageLoad = useCallback((e) => {
    setNaturalSize({ w: e.target.naturalWidth, h: e.target.naturalHeight });
  }, []);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKey = (e) => {
      if (saveDialogOpen) return;
      if (e.key === 'Escape') {
        if (cropMode) {
          setCropMode(false);
          setCrop(undefined);
          setCompletedCrop(null);
        } else {
          onClose?.();
        }
      }
      else if (e.key === '+' || e.key === '=') setZoom(z => Math.min(z + 0.25, 5));
      else if (e.key === '-') setZoom(z => Math.max(z - 0.25, 0.1));
      else if (e.key === '0') { setZoom(1); setPan({ x: 0, y: 0 }); }
      else if (e.key === 'r' && !e.ctrlKey) handleRotateCW();
      else if (e.key === 'r' && e.ctrlKey) { e.preventDefault(); handleRotateCCW(); }
      else if (e.key === 'c' && !e.ctrlKey && !e.metaKey) setCropMode(m => !m);
      else if ((e.key === 'z' || e.key === 'Z') && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        handleUndo();
      }
      else if ((e.key === 's' || e.key === 'S') && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setSaveDialogOpen(true);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [cropMode, saveDialogOpen, onClose]);

  // Mouse wheel zoom
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const handleWheel = (e) => {
      e.preventDefault();
      const delta = e.deltaY > 0 ? -0.1 : 0.1;
      setZoom(z => Math.min(Math.max(z + delta, 0.1), 5));
    };
    container.addEventListener('wheel', handleWheel, { passive: false });
    return () => container.removeEventListener('wheel', handleWheel);
  }, []);

  // Pan handlers
  const handleMouseDown = useCallback((e) => {
    if (cropMode) return;
    isPanning.current = true;
    panStart.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
  }, [cropMode, pan]);

  const handleMouseMove = useCallback((e) => {
    if (!isPanning.current) return;
    setPan({ x: e.clientX - panStart.current.x, y: e.clientY - panStart.current.y });
  }, []);

  const handleMouseUp = useCallback(() => {
    isPanning.current = false;
  }, []);

  // Operations
  const handleRotateCW = useCallback(() => {
    setRotation(r => (r + 90) % 360);
    setOperations(ops => [...ops, { type: 'rotate', angle: 90 }]);
  }, []);

  const handleRotateCCW = useCallback(() => {
    setRotation(r => (r - 90 + 360) % 360);
    setOperations(ops => [...ops, { type: 'rotate', angle: -90 }]);
  }, []);

  const handleFlipH = useCallback(() => {
    setFlipH(f => !f);
    setOperations(ops => [...ops, { type: 'flip', direction: 'horizontal' }]);
  }, []);

  const handleFlipV = useCallback(() => {
    setFlipV(f => !f);
    setOperations(ops => [...ops, { type: 'flip', direction: 'vertical' }]);
  }, []);

  const handleUndo = useCallback(() => {
    setOperations(ops => {
      if (ops.length === 0) return ops;
      const last = ops[ops.length - 1];
      // Reverse the last operation's visual effect
      if (last.type === 'rotate') {
        setRotation(r => (r - last.angle + 360) % 360);
      } else if (last.type === 'flip') {
        if (last.direction === 'horizontal') setFlipH(f => !f);
        else setFlipV(f => !f);
      } else if (last.type === 'crop') {
        // Can't visually undo crop in preview, but remove from ops list
      }
      return ops.slice(0, -1);
    });
  }, []);

  const handleFitScreen = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  // Apply crop
  const handleApplyCrop = useCallback(() => {
    if (!completedCrop || !imgRef.current) return;

    const img = imgRef.current;
    const scaleX = naturalSize.w / img.width;
    const scaleY = naturalSize.h / img.height;

    const cropOp = {
      type: 'crop',
      x: Math.round(completedCrop.x * scaleX),
      y: Math.round(completedCrop.y * scaleY),
      width: Math.round(completedCrop.width * scaleX),
      height: Math.round(completedCrop.height * scaleY),
      unit: 'px',
    };

    setOperations(ops => [...ops, cropOp]);
    setCropMode(false);
    setCrop(undefined);
    setCompletedCrop(null);
  }, [completedCrop, naturalSize]);

  // Save
  const handleSave = useCallback(async () => {
    if (!documentId) return;
    setSaving(true);
    try {
      const body = {
        document_id: documentId,
        operations,
        save_mode: saveMode,
        quality: saveQuality,
      };
      if (saveFormat) body.format = saveFormat;

      const resp = await fetch(`${API_BASE}/image/edit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await resp.json();

      if (data.success) {
        setSaveDialogOpen(false);
        setOperations([]);
        setRotation(0);
        setFlipH(false);
        setFlipV(false);
        onSaved?.(data.data);
      } else {
        alert(`Save failed: ${data.message || data.error || 'Unknown error'}`);
      }
    } catch (err) {
      alert(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }, [documentId, operations, saveMode, saveFormat, saveQuality, onSaved]);

  const hasEdits = operations.length > 0;
  const transformStyle = {
    transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${rotation}deg) scaleX(${flipH ? -1 : 1}) scaleY(${flipV ? -1 : 1})`,
    transition: isPanning.current ? 'none' : 'transform 0.15s ease',
  };

  if (!imageUrl) return null;

  return (
    <Box
      sx={{
        position: 'fixed',
        top: 0, left: 0, right: 0, bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.95)',
        zIndex: 10000,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* Toolbar */}
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.5,
          px: 1,
          py: 0.5,
          backgroundColor: 'rgba(30,30,30,0.95)',
          borderBottom: '1px solid rgba(255,255,255,0.1)',
          flexShrink: 0,
        }}
      >
        {/* Back */}
        <Tooltip title="Back to viewer (Esc)">
          <IconButton onClick={onClose} sx={{ color: 'white' }} size="small">
            <BackIcon />
          </IconButton>
        </Tooltip>

        <Typography variant="body2" sx={{ color: 'white', opacity: 0.7, mr: 1, maxWidth: 200 }} noWrap>
          {imageName}
        </Typography>

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Zoom controls */}
        <Tooltip title="Zoom out (-)">
          <IconButton onClick={() => setZoom(z => Math.max(z - 0.25, 0.1))} sx={{ color: 'white' }} size="small">
            <ZoomOutIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Slider
          value={zoom}
          min={0.1}
          max={5}
          step={0.05}
          onChange={(_, v) => setZoom(v)}
          sx={{ width: 100, color: 'white', mx: 0.5 }}
          size="small"
        />
        <Tooltip title="Zoom in (+)">
          <IconButton onClick={() => setZoom(z => Math.min(z + 0.25, 5))} sx={{ color: 'white' }} size="small">
            <ZoomInIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography variant="caption" sx={{ color: 'white', opacity: 0.6, minWidth: 35, textAlign: 'center' }}>
          {Math.round(zoom * 100)}%
        </Typography>
        <Tooltip title="Fit to screen (0)">
          <IconButton onClick={handleFitScreen} sx={{ color: 'white' }} size="small">
            <FitScreenIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Rotate & Flip */}
        <Tooltip title="Rotate left (Ctrl+R)">
          <IconButton onClick={handleRotateCCW} sx={{ color: 'white' }} size="small">
            <RotateLeftIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Rotate right (R)">
          <IconButton onClick={handleRotateCW} sx={{ color: 'white' }} size="small">
            <RotateRightIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Flip horizontal">
          <IconButton onClick={handleFlipH} sx={{ color: 'white' }} size="small">
            <FlipHIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title="Flip vertical">
          <IconButton onClick={handleFlipV} sx={{ color: 'white' }} size="small">
            <FlipVIcon />
          </IconButton>
        </Tooltip>

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Crop */}
        <Tooltip title={cropMode ? "Cancel crop (Esc)" : "Crop (C)"}>
          <IconButton
            onClick={() => {
              if (cropMode) {
                setCropMode(false);
                setCrop(undefined);
                setCompletedCrop(null);
              } else {
                setCropMode(true);
                setZoom(1);
                setPan({ x: 0, y: 0 });
                setRotation(0);
                // Set initial crop selection (centered 80%)
                setCrop({ unit: '%', x: 10, y: 10, width: 80, height: 80 });
              }
            }}
            sx={{ color: cropMode ? '#4CAF50' : 'white' }}
            size="small"
          >
            <CropIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        {cropMode && completedCrop && completedCrop.width > 0 && (
          <Button
            variant="contained"
            size="small"
            color="success"
            onClick={handleApplyCrop}
            sx={{ ml: 0.5, textTransform: 'none', fontSize: '0.75rem', py: 0.25 }}
          >
            Apply Crop
          </Button>
        )}

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Undo */}
        <Tooltip title="Undo (Ctrl+Z)">
          <span>
            <IconButton onClick={handleUndo} disabled={!hasEdits} sx={{ color: hasEdits ? 'white' : 'rgba(255,255,255,0.3)' }} size="small">
              <UndoIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>

        <Box sx={{ flex: 1 }} />

        {/* Image dimensions */}
        {naturalSize.w > 0 && (
          <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', mr: 1 }}>
            {naturalSize.w} x {naturalSize.h}
          </Typography>
        )}

        {/* Save buttons */}
        <Tooltip title="Save (Ctrl+S)">
          <span>
            <IconButton
              onClick={() => setSaveDialogOpen(true)}
              disabled={!hasEdits}
              sx={{ color: hasEdits ? '#4CAF50' : 'rgba(255,255,255,0.3)' }}
              size="small"
            >
              <SaveIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {/* Canvas area */}
      <Box
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        sx={{
          flex: 1,
          overflow: 'hidden',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: cropMode ? 'crosshair' : (isPanning.current ? 'grabbing' : 'grab'),
          userSelect: 'none',
        }}
      >
        {cropMode ? (
          <Box sx={transformStyle}>
            <ReactCrop
              crop={crop}
              onChange={(c) => setCrop(c)}
              onComplete={(c) => setCompletedCrop(c)}
            >
              <Box
                component="img"
                ref={imgRef}
                src={imageUrl}
                alt={imageName}
                onLoad={handleImageLoad}
                draggable={false}
                sx={{
                  maxWidth: '90vw',
                  maxHeight: 'calc(100vh - 52px)',
                  objectFit: 'contain',
                  display: 'block',
                }}
              />
            </ReactCrop>
          </Box>
        ) : (
          <Box
            component="img"
            ref={imgRef}
            src={imageUrl}
            alt={imageName}
            onLoad={handleImageLoad}
            draggable={false}
            sx={{
              maxWidth: '90vw',
              maxHeight: 'calc(100vh - 52px)',
              objectFit: 'contain',
              ...transformStyle,
            }}
          />
        )}
      </Box>

      {/* Bottom hint bar */}
      <Box sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        py: 0.5,
        backgroundColor: 'rgba(30,30,30,0.8)',
        borderTop: '1px solid rgba(255,255,255,0.05)',
        flexShrink: 0,
      }}>
        <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>
          Scroll to zoom &bull; Drag to pan &bull; R rotate &bull; C crop &bull; Ctrl+Z undo &bull; Ctrl+S save
        </Typography>
      </Box>

      {/* Save Dialog */}
      <Dialog
        open={saveDialogOpen}
        onClose={() => !saving && setSaveDialogOpen(false)}
        maxWidth="xs"
        fullWidth
        PaperProps={{ sx: { backgroundColor: '#1e1e1e', color: 'white' } }}
      >
        <DialogTitle>Save Image</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, mt: 1 }}>
            <ToggleButtonGroup
              value={saveMode}
              exclusive
              onChange={(_, v) => v && setSaveMode(v)}
              size="small"
              fullWidth
              sx={{
                '& .MuiToggleButton-root': { color: 'rgba(255,255,255,0.7)', borderColor: 'rgba(255,255,255,0.2)' },
                '& .Mui-selected': { color: 'white', backgroundColor: 'rgba(76,175,80,0.3)' },
              }}
            >
              <ToggleButton value="copy">
                <SaveAsIcon sx={{ mr: 0.5, fontSize: 18 }} /> Save as Copy
              </ToggleButton>
              <ToggleButton value="overwrite">
                <SaveIcon sx={{ mr: 0.5, fontSize: 18 }} /> Overwrite
              </ToggleButton>
            </ToggleButtonGroup>

            <FormControl size="small" fullWidth>
              <InputLabel sx={{ color: 'rgba(255,255,255,0.5)' }}>Format</InputLabel>
              <Select
                value={saveFormat}
                onChange={(e) => setSaveFormat(e.target.value)}
                label="Format"
                sx={{ color: 'white', '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.2)' } }}
              >
                <MenuItem value="">Keep Original</MenuItem>
                <MenuItem value="png">PNG (lossless)</MenuItem>
                <MenuItem value="jpeg">JPEG (smaller)</MenuItem>
                <MenuItem value="webp">WebP (modern)</MenuItem>
              </Select>
            </FormControl>

            {(saveFormat === 'jpeg' || saveFormat === 'webp') && (
              <Box>
                <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.6)', mb: 0.5, display: 'block' }}>
                  Quality: {saveQuality}%
                </Typography>
                <Slider
                  value={saveQuality}
                  min={10}
                  max={100}
                  step={5}
                  onChange={(_, v) => setSaveQuality(v)}
                  sx={{ color: '#4CAF50' }}
                  size="small"
                />
              </Box>
            )}

            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>
              {operations.length} edit{operations.length !== 1 ? 's' : ''} to apply:
              {' '}{operations.map(op => op.type).join(', ') || 'none'}
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)} disabled={saving} sx={{ color: 'rgba(255,255,255,0.7)' }}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            variant="contained"
            color="success"
            disabled={saving || !hasEdits}
            startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          >
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ImageEditor;
