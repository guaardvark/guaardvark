// LessonPearlsFloater — live view of pearls accumulating inside a Begin/End
// Lesson bracket. Draggable, resizable, collapsible; position persists to
// localStorage. Subscribes to `lesson:pearl_added` / `lesson:ended` socket
// events. Only mounted by ChatPage when activeLessonId is set.
//
// Drag/resize skeleton cloned from TrainingFloater.jsx (copied, not shared,
// to keep concerns separate during the ship window — extract a shared hook
// post-release if either diverges).
/* eslint-env browser */
import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Paper,
  Tooltip,
} from '@mui/material';
import SchoolIcon from '@mui/icons-material/School';
import CloseIcon from '@mui/icons-material/Close';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import HourglassTopIcon from '@mui/icons-material/HourglassTop';
import { io } from 'socket.io-client';
import { useAppStore } from '../../stores/useAppStore';

const STORAGE_KEY = 'guaardvark_lesson_floater_state';
const DEFAULT_WIDTH = 340;
const DEFAULT_HEIGHT = 380;
const MIN_WIDTH = 280;
const MIN_HEIGHT = 200;
const DOUBLE_CLICK_MS = 400;

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function saveState(state) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* localStorage may be blocked in private mode */ }
}

function fmtElapsed(ms) {
  if (!ms || ms < 0) return '0:00';
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function LessonPearlsFloater({ onClose }) {
  const activeLessonId = useAppStore((s) => s.activeLessonId);
  const pearls = useAppStore((s) => s.lessonPearls);
  const addLessonPearl = useAppStore((s) => s.addLessonPearl);

  // --- Persisted window state ---
  const saved = loadState();
  const [collapsed, setCollapsed] = useState(saved.collapsed ?? false);
  const [position, setPosition] = useState({
    x: saved.x ?? Math.max(20, window.innerWidth - DEFAULT_WIDTH - 20),
    y: saved.y ?? 120,
  });
  const [size, setSize] = useState({
    w: saved.w ?? DEFAULT_WIDTH,
    h: saved.h ?? DEFAULT_HEIGHT,
  });

  // --- Drag/resize state ---
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [resizeStart, setResizeStart] = useState({ x: 0, y: 0, w: 0, h: 0 });
  const lastClickRef = useRef(0);

  // --- Lesson state ---
  const [finalizing, setFinalizing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const startedAtRef = useRef(Date.now());
  const lessonIdRef = useRef(activeLessonId);
  const socketRef = useRef(null);

  // Keep lessonIdRef current so the socket callback reads the right value
  // without re-registering the listener on every pearl (stale-closure trap).
  useEffect(() => {
    lessonIdRef.current = activeLessonId;
    if (activeLessonId) {
      startedAtRef.current = Date.now();
      setFinalizing(false);
    }
  }, [activeLessonId]);

  // --- Persist window state ---
  useEffect(() => {
    saveState({ collapsed, x: position.x, y: position.y, w: size.w, h: size.h });
  }, [collapsed, position, size]);

  // --- Elapsed timer ---
  useEffect(() => {
    if (!activeLessonId) return;
    const id = setInterval(() => {
      setElapsed(Date.now() - startedAtRef.current);
    }, 1000);
    return () => clearInterval(id);
  }, [activeLessonId]);

  // --- Drag handlers ---
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
    const onMove = (e) => setPosition({
      x: Math.max(0, Math.min(window.innerWidth - 40, e.clientX - dragOffset.x)),
      y: Math.max(0, Math.min(window.innerHeight - 40, e.clientY - dragOffset.y)),
    });
    const onUp = () => setIsDragging(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [isDragging, dragOffset]);

  // --- Resize handlers ---
  const handleResizeMouseDown = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    setResizeStart({ x: e.clientX, y: e.clientY, w: size.w, h: size.h });
  }, [size]);

  useEffect(() => {
    if (!isResizing) return;
    const onMove = (e) => {
      setSize({
        w: Math.max(MIN_WIDTH, resizeStart.w + (e.clientX - resizeStart.x)),
        h: Math.max(MIN_HEIGHT, resizeStart.h + (e.clientY - resizeStart.y)),
      });
    };
    const onUp = () => setIsResizing(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [isResizing, resizeStart]);

  // --- Socket subscription ---
  useEffect(() => {
    if (!activeLessonId) return;

    const socket = io({ path: '/socket.io', transports: ['websocket', 'polling'] });
    socketRef.current = socket;

    socket.on('lesson:pearl_added', (data) => {
      if (!data || data.lesson_id !== lessonIdRef.current) return;
      addLessonPearl({
        id: data.pearl_id,
        task: data.task || '',
        created_at: data.created_at,
        ordinal: (data.ordinal != null) ? data.ordinal : null,
      });
    });

    socket.on('lesson:ended', (data) => {
      if (!data || data.lesson_id !== lessonIdRef.current) return;
      setFinalizing(true);
    });

    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, [activeLessonId, addLessonPearl]);

  if (!activeLessonId) return null;

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
        borderColor: finalizing ? 'warning.dark' : 'error.dark',
        userSelect: isDragging || isResizing ? 'none' : 'auto',
      }}
    >
      {/* Draggable header */}
      <Box
        onMouseDown={handleHeaderMouseDown}
        sx={{
          display: 'flex',
          alignItems: 'center',
          px: 1.5,
          py: 0.75,
          cursor: isDragging ? 'grabbing' : 'grab',
          bgcolor: 'background.paper',
          borderBottom: 1,
          borderColor: 'divider',
          userSelect: 'none',
          gap: 0.75,
        }}
      >
        <SchoolIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
        <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem', flex: 1 }}>
          Lesson — {pearls.length} {pearls.length === 1 ? 'pearl' : 'pearls'}
        </Typography>
        {finalizing ? (
          <HourglassTopIcon sx={{ fontSize: 14, color: 'warning.main' }} />
        ) : (
          <FiberManualRecordIcon
            sx={{
              fontSize: 12,
              color: 'error.main',
              animation: 'pearlPulse 1.5s ease-in-out infinite',
              '@keyframes pearlPulse': {
                '0%, 100%': { opacity: 1 },
                '50%': { opacity: 0.3 },
              },
            }}
          />
        )}
        <Typography variant="caption" sx={{ fontSize: '0.7rem', color: 'text.secondary', minWidth: 38, textAlign: 'right' }}>
          {fmtElapsed(elapsed)}
        </Typography>
        <IconButton
          className="header-btn"
          size="small"
          onClick={() => setCollapsed((c) => !c)}
          sx={{ p: 0.25 }}
        >
          {collapsed ? <ExpandMoreIcon sx={{ fontSize: 16 }} /> : <ExpandLessIcon sx={{ fontSize: 16 }} />}
        </IconButton>
        {onClose && (
          <Tooltip title="Hide (the lesson keeps running — use End Lesson in the chat header to stop)">
            <IconButton className="header-btn" size="small" onClick={onClose} sx={{ p: 0.25 }}>
              <CloseIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        )}
      </Box>

      {/* Body — hidden when collapsed */}
      {!collapsed && (
        <Box sx={{ p: 1.5, height: size.h - 45, overflowY: 'auto', position: 'relative' }}>
          {pearls.length === 0 ? (
            <Typography
              variant="body2"
              sx={{ color: 'text.secondary', textAlign: 'center', py: 3, px: 1, fontSize: '0.8rem' }}
            >
              Waiting for the first pearl. Thumbs-up any reply that got it right.
            </Typography>
          ) : (
            <Box>
              {pearls.map((p, i) => (
                <Box
                  key={p.id ?? `pearl-${i}`}
                  sx={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 1,
                    py: 0.5,
                    borderBottom: 1,
                    borderColor: 'divider',
                    '&:last-child': { borderBottom: 0 },
                  }}
                >
                  <Box
                    sx={{
                      minWidth: 22,
                      height: 22,
                      borderRadius: '50%',
                      bgcolor: 'success.dark',
                      color: 'success.contrastText',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '0.7rem',
                      fontWeight: 700,
                      flexShrink: 0,
                      mt: 0.25,
                    }}
                  >
                    {i + 1}
                  </Box>
                  <Typography
                    variant="body2"
                    sx={{ fontSize: '0.8rem', lineHeight: 1.4, wordBreak: 'break-word' }}
                  >
                    {p.task || '(no task text)'}
                  </Typography>
                </Box>
              ))}
            </Box>
          )}

          {finalizing && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mt: 1.5, pt: 1.5, borderTop: 1, borderColor: 'divider' }}>
              <HourglassTopIcon sx={{ fontSize: 16, color: 'warning.main' }} />
              <Typography variant="caption" sx={{ fontSize: '0.75rem', color: 'text.secondary' }}>
                Finalizing lesson — distilling pearls into a summary…
              </Typography>
            </Box>
          )}

          {/* Resize handle */}
          <Box
            onMouseDown={handleResizeMouseDown}
            sx={{
              position: 'absolute',
              right: 0,
              bottom: 0,
              width: 16,
              height: 16,
              cursor: 'nwse-resize',
              '&::after': {
                content: '""',
                position: 'absolute',
                right: 3,
                bottom: 3,
                width: 8,
                height: 8,
                borderRight: 2,
                borderBottom: 2,
                borderColor: 'divider',
              },
            }}
          />
        </Box>
      )}
    </Paper>
  );
}
