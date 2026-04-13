// AgentScreenViewer.jsx — Floating draggable agent screen viewer
// Works globally across all pages, same pattern as SystemMetricsModal
import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Paper,
  Tooltip,
  Slider,
  Chip,
} from '@mui/material';
import DesktopWindowsIcon from '@mui/icons-material/DesktopWindows';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import CloseIcon from '@mui/icons-material/Close';
import CircleIcon from '@mui/icons-material/Circle';
import axios from 'axios';
import { useAppStore } from '../../stores/useAppStore';

const API_BASE = '/api';
const STORAGE_KEY = 'guaardvark_agent_screen_state';
const DEFAULT_WIDTH = 380;
const DEFAULT_HEIGHT = 250;
const DOUBLE_CLICK_MS = 400;

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {}
}

// Chip for the sidebar or any page header — toggles the floating window
export function AgentScreenChip({ onClick, streaming, inPopup }) {
  return (
    <Tooltip title="Agent virtual screen">
      <Chip
        icon={<DesktopWindowsIcon sx={{ fontSize: '14px !important' }} />}
        label={inPopup ? 'Popup' : streaming ? 'Live' : 'Screen'}
        size="small"
        color={streaming || inPopup ? 'success' : 'default'}
        variant={streaming || inPopup ? 'filled' : 'outlined'}
        onClick={onClick}
        sx={{
          height: 24,
          fontSize: '0.7rem',
          cursor: 'pointer',
          '& .MuiChip-icon': { ml: 0.5 },
        }}
      />
    </Tooltip>
  );
}

// Global floating window — render once in App or layout
export default function AgentScreenViewer({ open, onClose }) {
  const saved = loadState();
  const [streaming, setStreaming] = useState(saved.streaming ?? true);
  const [fps, setFps] = useState(saved.fps ?? 2);
  const [collapsed, setCollapsed] = useState(saved.collapsed ?? false);
  const [imageSrc, setImageSrc] = useState(null);
  const [popupWindow, setPopupWindow] = useState(null);
  const [position, setPosition] = useState(() => {
    const x = saved.x ?? window.innerWidth - DEFAULT_WIDTH - 20;
    const y = saved.y ?? 60;
    return {
      x: Math.max(0, Math.min(x, window.innerWidth - 100)),
      y: Math.max(0, Math.min(y, window.innerHeight - 40)),
    };
  });
  const [size, setSize] = useState({ w: saved.w ?? DEFAULT_WIDTH, h: saved.h ?? DEFAULT_HEIGHT });
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const [isTraining, setIsTraining] = useState(false);
  const intervalRef = useRef(null);
  const popupIntervalRef = useRef(null);
  const lastClickRef = useRef(0);
  const imgRef = useRef(null);

  // Persist state
  useEffect(() => {
    saveState({ streaming, fps, collapsed, x: position.x, y: position.y, w: size.w, h: size.h });
  }, [streaming, fps, collapsed, position, size]);

  // Announce viewer visibility to the Zustand store so the chat sender knows
  // whether to flip agent_screen_active on its POST. When `open` is false the
  // viewer is hidden — backend should treat the screen as idle and route
  // models through the normal tool path.
  const setAgentScreenOpen = useAppStore((s) => s.setAgentScreenOpen);
  useEffect(() => {
    setAgentScreenOpen(!!open);
    return () => setAgentScreenOpen(false);
  }, [open, setAgentScreenOpen]);

  const [captureError, setCaptureError] = useState(null);
  const consecutiveFailures = useRef(0);

  const captureFrame = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/agent-control/capture/raw`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ quality: 60 }),
      });
      if (response.ok) {
        const blob = await response.blob();
        if (blob.size < 100) {
          consecutiveFailures.current++;
          setCaptureError('Empty frame received');
          return;
        }
        consecutiveFailures.current = 0;
        setCaptureError(null);
        const url = URL.createObjectURL(blob);
        setImageSrc((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      } else if (response.status === 503) {
        consecutiveFailures.current++;
        setCaptureError('Agent display not running');
      } else {
        consecutiveFailures.current++;
        setCaptureError(`Capture failed (${response.status})`);
      }
    } catch (err) {
      consecutiveFailures.current++;
      setCaptureError('Connection lost');
    }
  }, []);

  useEffect(() => {
    if (open && streaming && !collapsed && !popupWindow) {
      captureFrame();
      intervalRef.current = setInterval(captureFrame, 1000 / fps);
      return () => clearInterval(intervalRef.current);
    } else {
      if (intervalRef.current) clearInterval(intervalRef.current);
    }
  }, [open, streaming, collapsed, fps, captureFrame, popupWindow]);

  useEffect(() => {
    return () => {
      if (imageSrc) URL.revokeObjectURL(imageSrc);
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (popupIntervalRef.current) clearInterval(popupIntervalRef.current);
    };
  }, []);

  // Monitor popup close
  useEffect(() => {
    if (!popupWindow) return;
    const check = setInterval(() => {
      if (popupWindow.closed) {
        setPopupWindow(null);
        clearInterval(popupIntervalRef.current);
        setStreaming(true);
      }
    }, 1000);
    return () => clearInterval(check);
  }, [popupWindow]);

  // Drag handlers
  const handleHeaderMouseDown = useCallback((e) => {
    if (e.target.closest('.header-btn')) return;
    const now = Date.now();
    if (now - lastClickRef.current < DOUBLE_CLICK_MS) {
      setCollapsed((c) => !c);
      lastClickRef.current = 0;
      return;
    }
    lastClickRef.current = now;
    setIsDragging(true);
    setDragOffset({ x: e.clientX - position.x, y: e.clientY - position.y });
  }, [position]);

  useEffect(() => {
    if (!isDragging) return;
    const onMove = (e) => {
      const newX = Math.max(0, Math.min(e.clientX - dragOffset.x, window.innerWidth - 100));
      const newY = Math.max(0, Math.min(e.clientY - dragOffset.y, window.innerHeight - 40));
      setPosition({ x: newX, y: newY });
    };
    const onUp = () => setIsDragging(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [isDragging, dragOffset]);

  // Resize handlers
  const handleResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    setResizeStart({ x: e.clientX, y: e.clientY, w: size.w, h: size.h });
  }, [size]);

  useEffect(() => {
    if (!isResizing) return;
    const onMove = (e) => {
      const dw = e.clientX - resizeStart.x;
      const dh = e.clientY - resizeStart.y;
      setSize({
        w: Math.max(200, resizeStart.w + dw),
        h: Math.max(120, resizeStart.h + dh),
      });
    };
    const onUp = () => setIsResizing(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [isResizing, resizeStart]);

  // --- Training mode: poll learning status ---
  useEffect(() => {
    if (!open) return;
    const checkTraining = async () => {
      try {
        const res = await axios.get(`${API_BASE}/agent-control/learn/status`);
        setIsTraining(res.data?.learning === true);
      } catch { setIsTraining(false); }
    };
    checkTraining();
    const id = setInterval(checkTraining, 2000);
    return () => clearInterval(id);
  }, [open]);

  // --- Training mode: handle clicks on the screen image ---
  const handleScreenClick = useCallback((e) => {
    if (!isTraining || !imgRef.current) return;
    const img = imgRef.current;
    const rect = img.getBoundingClientRect();
    // Translate browser coords to 1024x1024 virtual display coords
    const scaleX = 1024 / rect.width;
    const scaleY = 1024 / rect.height;
    const x = Math.round((e.clientX - rect.left) * scaleX);
    const y = Math.round((e.clientY - rect.top) * scaleY);
    // Clamp to display bounds
    const cx = Math.max(0, Math.min(1024, x));
    const cy = Math.max(0, Math.min(1024, y));
    axios.post(`${API_BASE}/agent-control/learn/input`, {
      action: 'click', x: cx, y: cy,
    }).catch((err) => console.error('Training click failed:', err));
  }, [isTraining]);

  const openPopup = useCallback(() => {
    if (popupWindow && !popupWindow.closed) { popupWindow.focus(); return; }
    const w = 1064, h = 1064;
    const left = (window.screen.width - w) / 2, top = (window.screen.height - h) / 2;
    const win = window.open('', 'agent_screen',
      `width=${w},height=${h},left=${left},top=${top},resizable=yes,scrollbars=no,toolbar=no,menubar=no,location=no,status=no`);
    if (!win) return;
    win.document.title = 'Guaardvark Agent Screen';
    win.document.body.style.cssText = 'margin:0;padding:0;background:#000;display:flex;align-items:center;justify-content:center;height:100vh;overflow:hidden;';
    win.document.body.innerHTML = '<img id="screen" style="max-width:100%;max-height:100%;object-fit:contain;" />';
    const img = win.document.getElementById('screen');
    const stream = async () => {
      try {
        const res = await fetch(`${API_BASE}/agent-control/capture/raw`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ quality: 70 }),
        });
        if (res.ok) { const blob = await res.blob(); const url = URL.createObjectURL(blob); if (img.src) URL.revokeObjectURL(img.src); img.src = url; }
      } catch {}
    };
    stream();
    popupIntervalRef.current = setInterval(stream, 1000 / fps);
    setPopupWindow(win);
    setStreaming(false);
  }, [popupWindow, fps]);

  if (!open) return null;
  const isInPopup = popupWindow && !popupWindow.closed;

  return (
    <Paper
      elevation={8}
      sx={{
        position: 'fixed',
        left: position.x,
        top: position.y,
        width: size.w,
        zIndex: 1300,
        borderRadius: 1.5,
        overflow: 'hidden',
        border: 1,
        borderColor: isTraining ? 'error.dark' : streaming || isInPopup ? 'success.dark' : 'divider',
        userSelect: isDragging || isResizing ? 'none' : 'auto',
      }}
    >
      {/* Draggable header */}
      <Box
        onMouseDown={handleHeaderMouseDown}
        sx={{
          px: 1,
          py: 0.4,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          bgcolor: 'background.default',
          borderBottom: collapsed ? 0 : 1,
          borderColor: 'divider',
          cursor: isDragging ? 'grabbing' : 'grab',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
          <DesktopWindowsIcon sx={{ fontSize: 13, color: 'text.secondary' }} />
          <Typography variant="caption" sx={{ fontWeight: 500, color: 'text.secondary', fontSize: '0.7rem' }}>
            Agent Screen
          </Typography>
          {isTraining && (
            <Chip
              icon={<CircleIcon sx={{ fontSize: '5px !important', animation: 'pulse 1.5s ease-in-out infinite', '@keyframes pulse': { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.3 } } }} />}
              label="Training"
              size="small"
              color="error"
              sx={{ height: 16, fontSize: '0.55rem', '& .MuiChip-icon': { ml: 0.25 }, '& .MuiChip-label': { px: 0.5 } }}
            />
          )}
          {!isTraining && (streaming || isInPopup) && (
            <Chip
              icon={<CircleIcon sx={{ fontSize: '5px !important' }} />}
              label={isInPopup ? 'Popup' : `${fps}fps`}
              size="small"
              color="success"
              sx={{ height: 16, fontSize: '0.55rem', '& .MuiChip-icon': { ml: 0.25 }, '& .MuiChip-label': { px: 0.5 } }}
            />
          )}
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          <IconButton className="header-btn" size="small" onClick={openPopup} sx={{ p: 0.2 }}>
            <OpenInNewIcon sx={{ fontSize: 13 }} />
          </IconButton>
          <IconButton className="header-btn" size="small" onClick={() => setStreaming((s) => !s)} sx={{ p: 0.2 }}>
            {streaming ? <PauseIcon sx={{ fontSize: 13 }} /> : <PlayArrowIcon sx={{ fontSize: 13 }} />}
          </IconButton>
          <IconButton className="header-btn" size="small" onClick={onClose} sx={{ p: 0.2 }}>
            <CloseIcon sx={{ fontSize: 13 }} />
          </IconButton>
        </Box>
      </Box>

      {/* Screen */}
      {!collapsed && (
        <Box sx={{ position: 'relative', bgcolor: '#000' }}>
          <Box sx={{ aspectRatio: '16 / 9', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {isInPopup ? (
              <Typography variant="caption" color="grey.600">In popup</Typography>
            ) : imageSrc ? (
              <img
                ref={imgRef}
                src={imageSrc}
                alt="Agent screen"
                onClick={handleScreenClick}
                style={{
                  maxWidth: '100%',
                  maxHeight: '100%',
                  objectFit: 'contain',
                  cursor: isTraining ? 'crosshair' : 'default',
                }}
              />
            ) : (
              <Typography variant="caption" color={captureError ? 'error.main' : 'grey.600'}>{captureError || (streaming ? 'Connecting...' : 'Paused')}</Typography>
            )}
          </Box>

          {/* FPS slider */}
          {!isInPopup && (
            <Box sx={{
              position: 'absolute', bottom: 2, right: 4,
              display: 'flex', alignItems: 'center', gap: 0.5,
              bgcolor: 'rgba(0,0,0,0.5)', borderRadius: 1, px: 0.5, py: 0.1,
            }}>
              <Typography variant="caption" color="grey.500" sx={{ fontSize: '0.55rem' }}>FPS</Typography>
              <Slider value={fps} onChange={(_, v) => setFps(v)} min={1} max={10} step={1} size="small"
                sx={{ width: 40, color: 'grey.600', py: 0 }} />
            </Box>
          )}

          {/* Resize handle */}
          <Box
            onMouseDown={handleResizeMouseDown}
            sx={{
              position: 'absolute', bottom: 0, right: 0,
              width: 14, height: 14, cursor: 'nwse-resize',
              '&::after': {
                content: '""', position: 'absolute', bottom: 2, right: 2,
                width: 8, height: 8,
                borderRight: '2px solid', borderBottom: '2px solid',
                borderColor: 'grey.600', opacity: 0.5,
              },
            }}
          />
        </Box>
      )}
    </Paper>
  );
}
