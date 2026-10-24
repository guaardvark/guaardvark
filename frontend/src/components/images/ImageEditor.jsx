// frontend/src/components/images/ImageEditor.jsx
// Full-screen image editor with zoom, pan, rotate, flip, crop, and save-as.
// Uses react-image-crop for crop selection, backend Pillow for actual processing.

import React, { useState, useRef, useCallback, useEffect, useMemo } from 'react';
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
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  CircularProgress,
  Divider,
  Alert,
} from '@mui/material';
import {
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

import {
  cropRectFromOperations, cropIsAvailable, fitScale, cropAfterTransformReason,
} from '../../utils/imageEditOps';
import { useSnackbar } from '../common/SnackbarProvider';
import { useUiTranslation } from '../../i18n';

const FlipVIcon = () => (
  <FlipHIcon sx={{ transform: 'rotate(90deg)' }} />
);

const API_BASE = '/api/files';
// The editor covers the viewer (9999); its own popups must sit above it.
const EDITOR_Z = 10000;

const ImageEditor = ({
  imageUrl,
  imageName = '',
  documentId,
  onClose,
  onSaved,
}) => {
  const { t } = useUiTranslation();
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
  const [saveError, setSaveError] = useState(null);
  // Null outside the app's provider (component tests); the dialog shows the
  // error either way.
  const snackbar = useSnackbar();

  // Image info
  const [_imageInfo, setImageInfo] = useState(null);
  const [naturalSize, setNaturalSize] = useState({ w: 0, h: 0 });

  // Undo stack (stores operations)
  const [operations, setOperations] = useState([]);

  // The preview shows what the applied crops keep; undoing a crop restores
  // the earlier region because the rectangle is derived from the list.
  const cropRect = useMemo(() => cropRectFromOperations(operations), [operations]);
  const canCrop = cropIsAvailable(operations);

  // A rotate or flip applied while cropping (R key) would put the crop after
  // it; leave crop mode instead.
  useEffect(() => {
    if (!canCrop && cropMode) {
      setCropMode(false);
      setCrop(undefined);
      setCompletedCrop(null);
    }
  }, [canCrop, cropMode]);

  // Viewport size, for fitting the cropped preview the way CSS fits the
  // uncropped one.
  const [viewport, setViewport] = useState({ w: window.innerWidth, h: window.innerHeight });
  useEffect(() => {
    const onResize = () => setViewport({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

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
      else if (e.key === 'c' && !e.ctrlKey && !e.metaKey) { if (canCrop) setCropMode(m => !m); }
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
  }, [cropMode, saveDialogOpen, onClose, canCrop]);

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
      }
      // A crop needs nothing here: the preview's crop rectangle follows the list.
      return ops.slice(0, -1);
    });
  }, []);

  const handleFitScreen = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  // Apply crop
  const handleApplyCrop = useCallback(() => {
    if (!completedCrop || !imgRef.current || !canCrop) return;

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
  }, [completedCrop, naturalSize, canCrop]);

  // Save
  const handleSave = useCallback(async () => {
    if (!documentId) return;
    setSaving(true);
    setSaveError(null);
    const fail = (reason) => {
      const message = t('images.editor.saveFailed', { reason });
      setSaveError(message);
      snackbar?.showMessage?.(message, 'error');
    };
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
        fail(data.message || data.error || t('images.editor.unknownError'));
      }
    } catch (err) {
      fail(err.message);
    } finally {
      setSaving(false);
    }
  }, [documentId, operations, saveMode, saveFormat, saveQuality, onSaved, snackbar]);

  const hasEdits = operations.length > 0;
  const transformStyle = {
    transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${rotation}deg) scaleX(${flipH ? -1 : 1}) scaleY(${flipV ? -1 : 1})`,
    transition: isPanning.current ? 'none' : 'transform 0.15s ease',
  };

  if (!imageUrl) return null;

  // The image as the applied operations leave it. With a crop applied, a
  // clipped box shows only the kept region, scaled as CSS would scale the whole
  // image. The <img> keeps a layout width of natural width x scale, so
  // handleApplyCrop's naturalSize / img.width conversion holds in both forms,
  // and a crop drawn on the clipped box is relative to the cropped image, which
  // is how the backend applies a second crop.
  const imageProps = {
    component: 'img',
    ref: imgRef,
    src: imageUrl,
    alt: imageName,
    onLoad: handleImageLoad,
    draggable: false,
  };
  let preview;
  if (cropRect) {
    const s = fitScale(cropRect.width, cropRect.height, viewport.w, viewport.h);
    preview = (
      <Box sx={{ width: cropRect.width * s, height: cropRect.height * s, overflow: 'hidden', position: 'relative' }}>
        <Box
          {...imageProps}
          sx={{
            position: 'absolute',
            left: -cropRect.x * s,
            top: -cropRect.y * s,
            width: naturalSize.w * s,
            height: naturalSize.h * s,
            maxWidth: 'none',
            maxHeight: 'none',
            display: 'block',
          }}
        />
      </Box>
    );
  } else {
    preview = (
      <Box
        {...imageProps}
        sx={{
          maxWidth: '90vw',
          maxHeight: 'calc(100vh - 52px)',
          objectFit: 'contain',
          display: 'block',
        }}
      />
    );
  }

  return (
    <Box
      sx={{
        position: 'fixed',
        top: 0, left: 0, right: 0, bottom: 0,
        backgroundColor: 'rgba(0,0,0,0.95)',
        zIndex: EDITOR_Z,
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
        <Tooltip title={t('images.editor.back')}>
          <IconButton onClick={onClose} sx={{ color: 'white' }} size="small">
            <BackIcon />
          </IconButton>
        </Tooltip>

        <Typography variant="body2" sx={{ color: 'white', opacity: 0.7, mr: 1, maxWidth: 200 }} noWrap>
          {imageName}
        </Typography>

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Zoom controls */}
        <Tooltip title={t('images.editor.zoomOut')}>
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
        <Tooltip title={t('images.editor.zoomIn')}>
          <IconButton onClick={() => setZoom(z => Math.min(z + 0.25, 5))} sx={{ color: 'white' }} size="small">
            <ZoomInIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Typography variant="caption" sx={{ color: 'white', opacity: 0.6, minWidth: 35, textAlign: 'center' }}>
          {Math.round(zoom * 100)}%
        </Typography>
        <Tooltip title={t('images.editor.fit')}>
          <IconButton onClick={handleFitScreen} sx={{ color: 'white' }} size="small">
            <FitScreenIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Rotate & Flip */}
        <Tooltip title={t('images.editor.rotateLeft')}>
          <IconButton onClick={handleRotateCCW} sx={{ color: 'white' }} size="small">
            <RotateLeftIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t('images.editor.rotateRight')}>
          <IconButton onClick={handleRotateCW} sx={{ color: 'white' }} size="small">
            <RotateRightIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t('images.editor.flipHorizontal')}>
          <IconButton onClick={handleFlipH} sx={{ color: 'white' }} size="small">
            <FlipHIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t('images.editor.flipVertical')}>
          <IconButton onClick={handleFlipV} sx={{ color: 'white' }} size="small">
            <FlipVIcon />
          </IconButton>
        </Tooltip>

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Crop */}
        <Tooltip title={!canCrop ? cropAfterTransformReason() : cropMode ? t('images.editor.cancelCrop') : t('images.editor.crop')}>
          <span>
          <IconButton
            disabled={!canCrop}
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
            sx={{ color: !canCrop ? 'rgba(255,255,255,0.3)' : cropMode ? '#4CAF50' : 'white' }}
            size="small"
          >
            <CropIcon fontSize="small" />
          </IconButton>
          </span>
        </Tooltip>
        {cropMode && completedCrop && completedCrop.width > 0 && (
          <Button
            variant="contained"
            size="small"
            color="success"
            onClick={handleApplyCrop}
            sx={{ ml: 0.5, textTransform: 'none', fontSize: '0.75rem', py: 0.25 }}
          >
            {t('images.editor.applyCrop')}
          </Button>
        )}

        <Divider orientation="vertical" flexItem sx={{ borderColor: 'rgba(255,255,255,0.15)', mx: 0.5 }} />

        {/* Undo */}
        <Tooltip title={t('images.editor.undo')}>
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
            {cropRect ? `${cropRect.width} x ${cropRect.height}` : `${naturalSize.w} x ${naturalSize.h}`}
          </Typography>
        )}

        {/* Save buttons */}
        <Tooltip title={t('images.editor.save')}>
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
              {preview}
            </ReactCrop>
          </Box>
        ) : (
          <Box sx={transformStyle}>{preview}</Box>
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
          {t('images.editor.hints')}
        </Typography>
      </Box>

      {/* Save Dialog: above the editor, or it opens hidden behind it */}
      <Dialog
        open={saveDialogOpen}
        onClose={() => !saving && setSaveDialogOpen(false)}
        maxWidth="xs"
        fullWidth
        sx={{ zIndex: EDITOR_Z + 1 }}
        PaperProps={{ sx: { backgroundColor: '#1e1e1e', color: 'white' } }}
      >
        <DialogTitle>{t('images.editor.saveTitle')}</DialogTitle>
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
                <SaveAsIcon sx={{ mr: 0.5, fontSize: 18 }} /> {t('images.editor.saveAsCopy')}
              </ToggleButton>
              <ToggleButton value="overwrite">
                <SaveIcon sx={{ mr: 0.5, fontSize: 18 }} /> {t('images.editor.overwrite')}
              </ToggleButton>
            </ToggleButtonGroup>

            <FormControl size="small" fullWidth>
              <InputLabel sx={{ color: 'rgba(255,255,255,0.5)' }}>{t('images.editor.format')}</InputLabel>
              <Select
                value={saveFormat}
                onChange={(e) => setSaveFormat(e.target.value)}
                label={t('images.editor.format')}
                MenuProps={{ sx: { zIndex: EDITOR_Z + 2 } }}
                sx={{ color: 'white', '& .MuiOutlinedInput-notchedOutline': { borderColor: 'rgba(255,255,255,0.2)' } }}
              >
                <MenuItem value="">{t('images.editor.keepOriginal')}</MenuItem>
                <MenuItem value="png">{t('images.editor.png')}</MenuItem>
                <MenuItem value="jpeg">{t('images.editor.jpeg')}</MenuItem>
                <MenuItem value="webp">{t('images.editor.webp')}</MenuItem>
              </Select>
            </FormControl>

            {(saveFormat === 'jpeg' || saveFormat === 'webp') && (
              <Box>
                <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.6)', mb: 0.5, display: 'block' }}>
                  {t('images.editor.quality', { quality: saveQuality })}
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
              {t('images.editor.editsToApply', { count: operations.length, edits: operations.map(op => op.type).join(', ') || t('images.editor.noEdits') })}
            </Typography>

            {saveError && (
              <Alert severity="error" variant="outlined" onClose={() => setSaveError(null)}>
                {saveError}
              </Alert>
            )}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)} disabled={saving} sx={{ color: 'rgba(255,255,255,0.7)' }}>
            {t('images.shared.cancel')}
          </Button>
          <Button
            onClick={handleSave}
            variant="contained"
            color="success"
            disabled={saving || !hasEdits}
            startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveIcon />}
          >
            {saving ? t('images.editor.saving') : t('images.editor.saveButton')}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ImageEditor;
