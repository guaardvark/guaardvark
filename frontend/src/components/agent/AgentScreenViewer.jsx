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
import KeyboardIcon from '@mui/icons-material/Keyboard';
import KeyboardOutlinedIcon from '@mui/icons-material/KeyboardOutlined';
import MouseIcon from '@mui/icons-material/Mouse';
import MouseOutlinedIcon from '@mui/icons-material/MouseOutlined';
import axios from 'axios';
import { useAppStore } from '../../stores/useAppStore';
import {
  startDisplay,
  armDisplayIdle,
  cancelDisplayIdle,
} from '../../api/agentDisplayService';
import { useUiTranslation } from '../../i18n';
import { attachMouseForwarding } from './screenInput';
import { openAgentScreenWindow } from '../../utils/agentScreenChannel';

const API_BASE = '/api';
const STORAGE_KEY = 'guaardvark_agent_screen_state';
// The agent display is square (1000x1000 by default, GUAARDVARK_AGENT_RESOLUTION
// in start_agent_display.sh), so the card's screen area is square and the card
// height follows its width. `h` is kept in saved state for older entries
// but the rendered height is always derived from `w`.
const DEFAULT_WIDTH = 380;
const DEFAULT_HEIGHT = DEFAULT_WIDTH;
const DOUBLE_CLICK_MS = 400;
// After an input, fetch frames sooner than the FPS timer would, so a click at
// 1-2 fps still shows its result right away.
const REFRESH_AFTER_INPUT_MS = [150, 600];
const NOTICE_MS = 8000;

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveState(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // localStorage write blocked or quota exceeded — non-fatal
  }
}

// Chip for the sidebar or any page header — toggles the floating window
export function AgentScreenChip({ onClick, streaming, inPopup }) {
  const { t } = useUiTranslation();
  return (
    <Tooltip title={t('agents.screen.chipTooltip')}>
      <Chip
        icon={<DesktopWindowsIcon sx={{ fontSize: '14px !important' }} />}
        label={inPopup ? t('agents.screen.popup') : streaming ? t('agents.screen.live') : t('agents.screen.screen')}
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
  const { t } = useUiTranslation();
  const saved = loadState();
  const [streaming, setStreaming] = useState(saved.streaming ?? true);
  const [fps, setFps] = useState(saved.fps ?? 2);
  const [collapsed, setCollapsed] = useState(saved.collapsed ?? false);
  const [imageSrc, setImageSrc] = useState(null);
  // The unmount-cleanup useEffect runs with an empty deps array, so it would
  // capture imageSrc at mount time (null) and never revoke the live blob URL.
  // Mirror imageSrc into a ref so cleanup can read the latest value.
  const imageSrcRef = useRef(null);
  // While the screen is in its own window (/agent-screen) this viewer hides.
  const popout = useAppStore((s) => s.agentScreenPopout);
  const setAgentScreenPopout = useAppStore((s) => s.setAgentScreenPopout);
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
  // Control mode: clicks, wheel and keys on the picture go to the display.
  // Starts off every time the viewer mounts; it is never saved.
  const [interactive, setInteractive] = useState(false);
  const intervalRef = useRef(null);
  const lastClickRef = useRef(0);
  const imgRef = useRef(null);
  const screenBoxRef = useRef(null);
  // Listeners attached outside React (native mouse and wheel events) read
  // these refs so they always see the current mode.
  const interactRef = useRef(false);
  const trainingRef = useRef(false);
  const captureFrameRef = useRef(null);

  // Persist state
  useEffect(() => {
    saveState({ streaming, fps, collapsed, x: position.x, y: position.y, w: size.w, h: size.h });
  }, [streaming, fps, collapsed, position, size]);

  // The store is now the source of truth for `agentScreenOpen`; the `open`
  // prop is driven by it. No mirror useEffect needed — that previous
  // pattern caused a one-frame `false` flicker when the viewer mounted
  // closed, which briefly flipped agent_screen_active off mid-request.
  const kbdForwarding = useAppStore((s) => s.keyboardForwardingEnabled);
  const toggleKbdForwarding = useAppStore((s) => s.toggleKeyboardForwarding);
  const setKbdForwarding = useAppStore((s) => s.setKeyboardForwardingEnabled);

  useEffect(() => { interactRef.current = interactive; }, [interactive]);
  useEffect(() => { trainingRef.current = isTraining; }, [isTraining]);

  // Control turns keyboard forwarding on with it, and off again when it ends.
  const setControl = useCallback((on) => {
    interactRef.current = on;
    setInteractive(on);
    setKbdForwarding(on);
  }, [setKbdForwarding]);

  const [captureError, setCaptureError] = useState(null);
  const [displayStarting, setDisplayStarting] = useState(false);
  const [displayReady, setDisplayReady] = useState(false);
  const consecutiveFailures = useRef(0);

  const captureFrame = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/agent-control/capture/raw`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ quality: 60, auto_start: true }),
      });
      if (response.ok) {
        const blob = await response.blob();
        if (blob.size < 100) {
          consecutiveFailures.current++;
          setCaptureError(t('agents.screen.emptyFrame'));
          return;
        }
        consecutiveFailures.current = 0;
        setCaptureError(null);
        const url = URL.createObjectURL(blob);
        setImageSrc((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
        imageSrcRef.current = url;
      } else if (response.status === 503) {
        consecutiveFailures.current++;
        setCaptureError(t('agents.screen.displayNotRunning'));
      } else {
        consecutiveFailures.current++;
        setCaptureError(t('agents.screen.captureFailed', { status: response.status }));
      }
    } catch (err) {
      consecutiveFailures.current++;
      setCaptureError(t('agents.screen.connectionLost'));
    }
  }, []);
  captureFrameRef.current = captureFrame;

  // Pointer moves come many a second; only real actions earn an early frame.
  const refreshSoon = useCallback((payload) => {
    if (payload?.action === 'move') return;
    REFRESH_AFTER_INPUT_MS.forEach((ms) => setTimeout(() => captureFrameRef.current?.(), ms));
  }, []);

  // Start display on open; arm idle shutdown when an open viewer closes.
  // Not on mount: every page mounts the viewer closed, and each arm restarts
  // the backend's shutdown timer.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (!open) {
      setDisplayReady(false);
      setDisplayStarting(false);
      if (interactRef.current) setControl(false);
      if (wasOpen.current) armDisplayIdle().catch(() => {});
      wasOpen.current = false;
      return undefined;
    }
    wasOpen.current = true;

    let cancelled = false;
    cancelDisplayIdle().catch(() => {});
    setDisplayStarting(true);
    setDisplayReady(false);
    setCaptureError(null);

    startDisplay()
      .then((result) => {
        if (cancelled) return;
        if (result?.success || result?.display_running) {
          setDisplayReady(true);
          setCaptureError(null);
        } else {
          setCaptureError(result?.error || t('agents.screen.startFailed'));
        }
      })
      .catch(() => {
        if (!cancelled) setCaptureError(t('agents.screen.startFailed'));
      })
      .finally(() => {
        if (!cancelled) setDisplayStarting(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (open && displayReady && streaming && !collapsed && !popout) {
      captureFrame();
      intervalRef.current = setInterval(captureFrame, 1000 / fps);
      return () => clearInterval(intervalRef.current);
    }
    if (intervalRef.current) clearInterval(intervalRef.current);
    return undefined;
  }, [open, displayReady, streaming, collapsed, fps, captureFrame, popout]);

  useEffect(() => {
    return () => {
      if (imageSrcRef.current) URL.revokeObjectURL(imageSrcRef.current);
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  // Popping out hands control to the window, so this page stops forwarding.
  // Coming back, the closing window may have armed the idle shutdown; the
  // screen is in view again, so disarm it.
  const wasPopout = useRef(false);
  useEffect(() => {
    if (popout) {
      if (interactRef.current) setControl(false);
    } else if (wasPopout.current && open) {
      cancelDisplayIdle().catch(() => {});
    }
    wasPopout.current = popout;
  }, [popout]);

  // Mouse input on the floating viewer's picture. In Control mode it
  // presses, moves and releases (hover and drag work); while a
  // demonstration is recorded it sends whole clicks the recorder can keep.
  useEffect(() => {
    const el = screenBoxRef.current;
    if (!open || collapsed || popout || !el) return undefined;
    return attachMouseForwarding(el, () => imgRef.current, {
      getMode: () => (trainingRef.current ? 'click' : interactRef.current ? 'full' : 'off'),
      onSent: refreshSoon,
      onError: (err) => console.error('Screen input failed:', err?.message || err),
    });
  }, [open, collapsed, popout, refreshSoon]);

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
      // Square card: the larger of the two drag deltas drives both sides so a
      // diagonal, horizontal or vertical drag all resize it evenly.
      const dw = e.clientX - resizeStart.x;
      const dh = e.clientY - resizeStart.y;
      const w = Math.max(200, resizeStart.w + Math.max(dw, dh));
      setSize({ w, h: w });
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

  // The window announces itself over the channel too; setting popout here
  // hides this viewer at once instead of after the window has loaded.
  const [notice, setNotice] = useState(null);
  useEffect(() => {
    if (!notice) return undefined;
    const id = setTimeout(() => setNotice(null), NOTICE_MS);
    return () => clearTimeout(id);
  }, [notice]);

  const openPopout = useCallback(() => {
    if (!openAgentScreenWindow()) {
      setNotice(t('agents.screen.popoutBlocked'));
      return;
    }
    setAgentScreenPopout(true);
  }, [setAgentScreenPopout]);

  if (!open || popout) return null;

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
        borderColor: isTraining ? 'error.dark' : interactive ? 'warning.main' : streaming ? 'success.dark' : 'divider',
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
            {t('agents.screen.title')}
          </Typography>
          {isTraining && (
            <Chip
              icon={<CircleIcon sx={{ fontSize: '5px !important', animation: 'pulse 1.5s ease-in-out infinite', '@keyframes pulse': { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.3 } } }} />}
              label={t('agents.screen.training')}
              size="small"
              color="error"
              sx={{ height: 16, fontSize: '0.55rem', '& .MuiChip-icon': { ml: 0.25 }, '& .MuiChip-label': { px: 0.5 } }}
            />
          )}
          {!isTraining && interactive && (
            <Chip
              label={t('agents.screen.control')}
              size="small"
              color="warning"
              sx={{ height: 16, fontSize: '0.55rem', '& .MuiChip-label': { px: 0.5 } }}
            />
          )}
          {!isTraining && streaming && (
            <Chip
              icon={<CircleIcon sx={{ fontSize: '5px !important' }} />}
              label={t('agents.screen.fpsValue', { fps })}
              size="small"
              color="success"
              sx={{ height: 16, fontSize: '0.55rem', '& .MuiChip-icon': { ml: 0.25 }, '& .MuiChip-label': { px: 0.5 } }}
            />
          )}
        </Box>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          <Tooltip title={interactive ? t('agents.screen.controlOn') : t('agents.screen.controlOff')}>
            <IconButton
              className="header-btn"
              size="small"
              onClick={() => setControl(!interactive)}
              sx={{ p: 0.2, color: interactive ? 'warning.main' : 'text.secondary' }}
            >
              {interactive ? <MouseIcon sx={{ fontSize: 13 }} /> : <MouseOutlinedIcon sx={{ fontSize: 13 }} />}
            </IconButton>
          </Tooltip>
          <Tooltip title={kbdForwarding ? t('agents.keyboard.forwardingOn') : t('agents.keyboard.forwardingOff')}>
            <IconButton
              className="header-btn"
              size="small"
              onClick={toggleKbdForwarding}
              sx={{ p: 0.2, color: kbdForwarding ? 'success.main' : 'text.secondary' }}
            >
              {kbdForwarding ? <KeyboardIcon sx={{ fontSize: 13 }} /> : <KeyboardOutlinedIcon sx={{ fontSize: 13 }} />}
            </IconButton>
          </Tooltip>
          <Tooltip title={t('agents.screen.popOut')}>
            <IconButton className="header-btn" size="small" onClick={openPopout} sx={{ p: 0.2 }}>
              <OpenInNewIcon sx={{ fontSize: 13 }} />
            </IconButton>
          </Tooltip>
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
        <Box ref={screenBoxRef} sx={{ position: 'relative', bgcolor: '#000' }}>
          <Box sx={{ aspectRatio: '1 / 1', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {imageSrc ? (
              <img
                ref={imgRef}
                src={imageSrc}
                alt={t('agents.screen.imageAlt')}
                draggable={false}
                style={{
                  maxWidth: '100%',
                  maxHeight: '100%',
                  objectFit: 'contain',
                  cursor: isTraining || interactive ? 'crosshair' : 'default',
                }}
              />
            ) : (
              <Typography variant="caption" color={captureError ? 'error.main' : 'grey.600'}>
                {captureError || (displayStarting ? t('agents.screen.starting') : streaming ? t('agents.screen.connecting') : t('agents.screen.paused'))}
              </Typography>
            )}
          </Box>

          {notice && (
            <Typography
              variant="caption"
              sx={{
                position: 'absolute', top: 4, left: 4, right: 4,
                bgcolor: 'rgba(0,0,0,0.75)', color: 'warning.light', borderRadius: 1, px: 0.75, py: 0.25,
              }}
            >
              {notice}
            </Typography>
          )}

          {/* FPS slider */}
          <Box sx={{
            position: 'absolute', bottom: 2, right: 4,
            display: 'flex', alignItems: 'center', gap: 0.5,
            bgcolor: 'rgba(0,0,0,0.5)', borderRadius: 1, px: 0.5, py: 0.1,
          }}>
            <Typography variant="caption" color="grey.500" sx={{ fontSize: '0.55rem' }}>{t('agents.screen.fps')}</Typography>
            <Slider value={fps} onChange={(_, v) => setFps(v)} min={1} max={10} step={1} size="small"
              sx={{ width: 40, color: 'grey.600', py: 0 }} />
          </Box>

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
