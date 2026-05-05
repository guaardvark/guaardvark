import React, { useEffect, useState, useRef, useCallback } from 'react';
import {
  Box,
  Typography,
  IconButton,
  Paper,
  Tooltip,
  Button,
  Chip,
  LinearProgress,
  TextField,
  CircularProgress,
} from '@mui/material';
import SchoolIcon from '@mui/icons-material/School';
import CloseIcon from '@mui/icons-material/Close';
import StopIcon from '@mui/icons-material/Stop';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import CheckIcon from '@mui/icons-material/Check';
import EditIcon from '@mui/icons-material/Edit';
import SkipNextIcon from '@mui/icons-material/SkipNext';
// No trash icons — system uses X/close for all remove actions
import BookmarkBorderIcon from '@mui/icons-material/BookmarkBorder';
import KeyboardIcon from '@mui/icons-material/Keyboard';
import KeyboardOutlinedIcon from '@mui/icons-material/KeyboardOutlined';
import axios from 'axios';
import { io } from 'socket.io-client';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '../../stores/useAppStore';

const API_BASE = '/api/agent-control/learn';
const STORAGE_KEY = 'guaardvark_training_floater_state';
const DEFAULT_WIDTH = 320;
const DEFAULT_HEIGHT = 400;
const MIN_WIDTH = 280;
const MIN_HEIGHT = 200;
const DOUBLE_CLICK_MS = 400;
const POLL_INTERVAL = 2000;

const STATE_COLORS = {
  idle: 'text.disabled',
  recording: 'error.main',
  questioning: 'warning.main',
  attempting: 'info.main',
};

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
    // localStorage may be blocked in private mode — drop the save quietly
  }
}

export default function TrainingFloater({ open, onClose, _onNavigateAway }) {
  const navigate = useNavigate();
  const kbdForwarding = useAppStore((s) => s.keyboardForwardingEnabled);
  const toggleKbdForwarding = useAppStore((s) => s.toggleKeyboardForwarding);
  // --- Persisted window state ---
  const saved = loadState();
  const [collapsed, setCollapsed] = useState(saved.collapsed ?? false);
  const [position, setPosition] = useState({
    x: saved.x ?? window.innerWidth - DEFAULT_WIDTH - 20,
    y: saved.y ?? 100,
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

  // --- Learning state ---
  const [mode, setMode] = useState('idle');
  const [demonstrations, setDemonstrations] = useState([]);
  const [stepsRecorded, setStepsRecorded] = useState(0);
  const [currentQuestion, setCurrentQuestion] = useState(null);
  const [attemptInfo, setAttemptInfo] = useState(null);
  const [demoName, setDemoName] = useState('');
  const [showNameInput, setShowNameInput] = useState(false);
  const [loading, setLoading] = useState(false);

  const pollRef = useRef(null);
  const socketRef = useRef(null);

  // --- Persist window state ---
  useEffect(() => {
    saveState({ collapsed, x: position.x, y: position.y, w: size.w, h: size.h });
  }, [collapsed, position, size]);

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
    const onMove = (e) => setPosition({ x: e.clientX - dragOffset.x, y: e.clientY - dragOffset.y });
    const onUp = () => setIsDragging(false);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
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
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); };
  }, [isResizing, resizeStart]);

  // --- Polling + SocketIO ---
  useEffect(() => {
    if (!open) return;

    const poll = async () => {
      try {
        const res = await axios.get(`${API_BASE}/status`);
        const data = res.data;
        if (data.learning && mode !== 'recording' && mode !== 'questioning') {
          setMode('recording');
        } else if (!data.learning && mode === 'recording') {
          setMode('idle');
        }
        if (data.steps_count !== undefined) {
          setStepsRecorded(data.steps_count);
        }
      } catch {
        // poll error tolerated; next tick will retry
      }
    };
    poll();
    pollRef.current = setInterval(poll, POLL_INTERVAL);

    if (mode === 'idle') {
      loadDemonstrations();
    }

    return () => clearInterval(pollRef.current);
  }, [open, mode]);

  useEffect(() => {
    if (!open) return;

    const socket = io({ path: '/socket.io', transports: ['websocket', 'polling'] });
    socketRef.current = socket;

    socket.on('agent:learning_mode_started', () => {
      setMode('recording');
      setStepsRecorded(0);
    });

    socket.on('agent:learning_mode_stopped', (data) => {
      setStepsRecorded(data?.step_count || 0);
      setMode('idle');
      loadDemonstrations();
    });

    socket.on('agent:learning_question', (data) => {
      setCurrentQuestion(data);
      setMode('questioning');
    });

    socket.on('agent:step_preview', (data) => {
      setAttemptInfo((prev) => ({
        ...prev,
        stepIndex: data.step_index,
        stepDescription: `${data.action_type}: ${data.target_description}`,
        confidence: data.confidence,
        awaitingConfirm: true,
      }));
    });

    socket.on('agent:step_executed', (data) => {
      setAttemptInfo((prev) => ({
        ...prev,
        stepIndex: data.step_index + 1,
        awaitingConfirm: false,
      }));
    });

    socket.on('agent:attempt_complete', (data) => {
      setAttemptInfo((prev) => ({
        ...prev,
        complete: true,
        success: data.success,
        stepsCompleted: data.steps_completed,
      }));
      setTimeout(() => {
        setMode('idle');
        setAttemptInfo(null);
        loadDemonstrations();
      }, 3000);
    });

    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, [open]);

  // --- API actions ---
  const loadDemonstrations = async () => {
    try {
      const res = await axios.get(`${API_BASE}/demonstrations`);
      setDemonstrations(res.data.demonstrations || []);
    } catch {
      // demonstrations endpoint optional — empty list is fine
    }
  };

  const handleStartRecording = async () => {
    setLoading(true);
    try {
      await axios.post(`${API_BASE}/start`, {
        name: demoName || undefined,
      });
      setMode('recording');
      setStepsRecorded(0);
      setDemoName('');
      setShowNameInput(false);
    } catch (err) {
      console.error('Failed to start recording:', err);
    }
    setLoading(false);
  };

  const handleStopRecording = async () => {
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/stop`);
      setStepsRecorded(res.data.steps_recorded || 0);
      setMode('idle');
      loadDemonstrations();
    } catch (err) {
      console.error('Failed to stop recording:', err);
    }
    setLoading(false);
  };

  const handleAttempt = async (demo) => {
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/demonstrations/${demo.id}/attempt`);
      if (res.data.success) {
        setMode('attempting');
        setAttemptInfo({
          demoName: demo.name || demo.description,
          level: res.data.autonomy_level,
          stepIndex: 0,
          totalSteps: demo.steps?.length || 0,
          stepDescription: '',
          complete: false,
          success: false,
          awaitingConfirm: false,
        });
      }
    } catch (err) {
      console.error('Failed to start attempt:', err);
    }
    setLoading(false);
  };

  const handleAnswer = async (answer) => {
    if (!currentQuestion) return;
    try {
      if (socketRef.current) {
        socketRef.current.emit('agent:learning_answer', {
          question_id: currentQuestion.question_id,
          answer,
        });
      } else {
        await axios.post(`${API_BASE}/answer`, {
          question_id: currentQuestion.question_id,
          answer,
        });
      }
    } catch (err) {
      console.error('Failed to send answer:', err);
    }
    setCurrentQuestion(null);
    setMode(attemptInfo ? 'attempting' : 'idle');
  };

  const handleConfirmStep = () => {
    if (socketRef.current) {
      socketRef.current.emit('agent:step_confirm', {
        step_index: attemptInfo?.stepIndex,
      });
    }
    setAttemptInfo((prev) => ({ ...prev, awaitingConfirm: false }));
  };

  const handleCorrectStep = () => {
    if (socketRef.current) {
      socketRef.current.emit('agent:step_correct', {
        step_index: attemptInfo?.stepIndex,
        correction: {},
      });
    }
    setAttemptInfo((prev) => ({ ...prev, awaitingConfirm: false }));
  };

  const handleDeleteDemo = async (demo, e) => {
    e.stopPropagation();
    try {
      await axios.delete(`${API_BASE}/demonstrations/${demo.id}`);
      setDemonstrations((prev) => prev.filter((d) => d.id !== demo.id));
    } catch (err) {
      console.error('Failed to delete demonstration:', err);
    }
  };

  const handleSaveDemo = (demo, e) => {
    e.stopPropagation();
    navigate(`/training?tab=demonstrations&demo=${demo.id}`);
    if (onClose) onClose();
  };

  // --- Render helpers ---
  const levelColor = (level) => {
    switch (level) {
      case 'guided': return 'warning';
      case 'supervised': return 'info';
      case 'autonomous': return 'success';
      default: return 'default';
    }
  };

  const renderIdleView = () => (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {demonstrations.length === 0 ? (
        <Typography variant="body2" sx={{ color: 'text.secondary', textAlign: 'center', py: 3, px: 1 }}>
          No demonstrations yet. Connect to VNC and click Start Recording.
        </Typography>
      ) : (
        <Box sx={{ flex: 1, overflowY: 'auto', maxHeight: 220 }}>
          {demonstrations.slice(0, 10).map((demo) => (
            <Box
              key={demo.id}
              sx={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                py: 0.75,
                px: 0.5,
                borderBottom: 1,
                borderColor: 'divider',
                '&:last-child': { borderBottom: 0 },
              }}
            >
              <Box sx={{ flex: 1, minWidth: 0, mr: 1 }}>
                <Typography variant="body2" noWrap sx={{ fontSize: '0.8rem' }}>
                  {demo.name || demo.description?.slice(0, 30) || 'Untitled'}
                </Typography>
                <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center', mt: 0.25 }}>
                  <Chip
                    label={demo.autonomy_level}
                    size="small"
                    color={levelColor(demo.autonomy_level)}
                    sx={{ height: 18, fontSize: '0.65rem' }}
                  />
                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem' }}>
                    {demo.attempt_count} attempts
                  </Typography>
                </Box>
              </Box>
              <Box sx={{ display: 'flex', gap: 0.25, alignItems: 'center' }}>
                <Tooltip title="Delete demo">
                  <IconButton
                    size="small"
                    onClick={(e) => handleDeleteDemo(demo, e)}
                    disabled={loading}
                    sx={{ p: 0.25 }}
                  >
                    <CloseIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Save & edit in Training page">
                  <IconButton
                    size="small"
                    onClick={(e) => handleSaveDemo(demo, e)}
                    sx={{ p: 0.25 }}
                  >
                    <BookmarkBorderIcon sx={{ fontSize: 16, color: 'info.main' }} />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Agent attempts this demo">
                  <IconButton
                    size="small"
                    onClick={() => handleAttempt(demo)}
                    disabled={loading || mode !== 'idle'}
                    sx={{ p: 0.25 }}
                  >
                    <PlayArrowIcon sx={{ fontSize: 18 }} />
                  </IconButton>
                </Tooltip>
              </Box>
            </Box>
          ))}
        </Box>
      )}

      <Box sx={{ mt: 'auto', pt: 1.5, borderTop: 1, borderColor: 'divider' }}>
        {showNameInput && (
          <Box sx={{ display: 'flex', gap: 0.5, mb: 1 }}>
            <TextField
              size="small"
              placeholder="Demo name (optional)"
              value={demoName}
              onChange={(e) => setDemoName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleStartRecording()}
              sx={{ flex: 1, '& input': { fontSize: '0.8rem', py: 0.5 } }}
              autoFocus
            />
          </Box>
        )}
        <Box sx={{ display: 'flex', gap: 1 }}>
          {!showNameInput && (
            <Tooltip title="Name this demo">
              <IconButton size="small" onClick={() => setShowNameInput(true)} sx={{ p: 0.5 }}>
                <EditIcon sx={{ fontSize: 16 }} />
              </IconButton>
            </Tooltip>
          )}
          <Button
            variant="contained"
            color="error"
            size="small"
            fullWidth
            startIcon={<FiberManualRecordIcon sx={{ fontSize: 12 }} />}
            onClick={handleStartRecording}
            disabled={loading}
            sx={{ fontSize: '0.75rem', py: 0.5 }}
          >
            Start Recording
          </Button>
        </Box>
      </Box>
    </Box>
  );

  const renderRecordingView = () => (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', py: 4 }}>
      <FiberManualRecordIcon
        sx={{
          fontSize: 32,
          color: 'error.main',
          animation: 'pulse 1.5s ease-in-out infinite',
          '@keyframes pulse': {
            '0%, 100%': { opacity: 1 },
            '50%': { opacity: 0.3 },
          },
        }}
      />
      <Typography variant="body1" sx={{ mt: 1, fontWeight: 600 }}>
        Recording...
      </Typography>
      <Typography variant="caption" sx={{ color: 'text.secondary', mt: 0.5 }}>
        {stepsRecorded} steps captured
      </Typography>
      <Button
        variant="outlined"
        color="error"
        size="small"
        startIcon={<StopIcon />}
        onClick={handleStopRecording}
        disabled={loading}
        sx={{ mt: 3, fontSize: '0.75rem' }}
      >
        Stop Recording
      </Button>
    </Box>
  );

  const renderQuestioningView = () => {
    if (!currentQuestion) {
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', py: 4 }}>
          <CircularProgress size={20} sx={{ mr: 1 }} />
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            Waiting for questions...
          </Typography>
        </Box>
      );
    }

    const typeLabel = { A: 'Clarification', B: 'Generalization', C: 'Confirmation' };

    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, py: 1 }}>
        <Chip
          label={typeLabel[currentQuestion.question_type] || 'Question'}
          size="small"
          color="warning"
          sx={{ alignSelf: 'flex-start', height: 20, fontSize: '0.65rem' }}
        />
        <Typography variant="body2" sx={{ fontSize: '0.8rem', lineHeight: 1.4 }}>
          {currentQuestion.text}
        </Typography>
        {currentQuestion.options ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.75 }}>
            {currentQuestion.options.map((opt, i) => (
              <Button
                key={i}
                variant="outlined"
                size="small"
                onClick={() => handleAnswer(opt)}
                sx={{ fontSize: '0.7rem', textTransform: 'none', justifyContent: 'flex-start' }}
              >
                {opt}
              </Button>
            ))}
          </Box>
        ) : (
          <Box sx={{ display: 'flex', gap: 0.5 }}>
            <TextField
              size="small"
              placeholder="Type your answer..."
              fullWidth
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleAnswer(e.target.value);
                  e.target.value = '';
                }
              }}
              sx={{ '& input': { fontSize: '0.8rem', py: 0.5 } }}
            />
          </Box>
        )}
        <Button
          size="small"
          startIcon={<SkipNextIcon sx={{ fontSize: 14 }} />}
          onClick={() => handleAnswer('')}
          sx={{ alignSelf: 'flex-end', fontSize: '0.7rem', textTransform: 'none', color: 'text.secondary' }}
        >
          Skip
        </Button>
      </Box>
    );
  };

  const renderAttemptingView = () => {
    if (!attemptInfo) return null;
    const progress = attemptInfo.totalSteps > 0
      ? ((attemptInfo.stepIndex) / attemptInfo.totalSteps) * 100
      : 0;

    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, py: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Typography variant="body2" noWrap sx={{ fontSize: '0.8rem', fontWeight: 600, flex: 1 }}>
            {attemptInfo.demoName}
          </Typography>
          <Chip
            label={attemptInfo.level}
            size="small"
            color={levelColor(attemptInfo.level)}
            sx={{ height: 20, fontSize: '0.65rem', ml: 1 }}
          />
        </Box>

        {attemptInfo.complete ? (
          <Box sx={{ textAlign: 'center', py: 2 }}>
            <Typography
              variant="h6"
              sx={{ color: attemptInfo.success ? 'success.main' : 'error.main', fontSize: '1rem' }}
            >
              {attemptInfo.success ? 'Success!' : 'Failed'}
            </Typography>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {attemptInfo.stepsCompleted} of {attemptInfo.totalSteps} steps completed
            </Typography>
          </Box>
        ) : (
          <>
            <Box>
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Step {attemptInfo.stepIndex + 1} of {attemptInfo.totalSteps}
                </Typography>
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  {Math.round(progress)}%
                </Typography>
              </Box>
              <LinearProgress variant="determinate" value={progress} sx={{ height: 6, borderRadius: 3 }} />
            </Box>

            {attemptInfo.stepDescription && (
              <Typography variant="body2" sx={{ fontSize: '0.75rem', color: 'text.secondary', fontStyle: 'italic' }}>
                {attemptInfo.stepDescription}
              </Typography>
            )}

            {attemptInfo.awaitingConfirm && attemptInfo.level === 'guided' && (
              <Box sx={{ display: 'flex', gap: 1, justifyContent: 'center', mt: 1 }}>
                <Button
                  variant="contained"
                  color="success"
                  size="small"
                  startIcon={<CheckIcon sx={{ fontSize: 14 }} />}
                  onClick={handleConfirmStep}
                  sx={{ fontSize: '0.7rem' }}
                >
                  Confirm
                </Button>
                <Button
                  variant="outlined"
                  color="warning"
                  size="small"
                  startIcon={<EditIcon sx={{ fontSize: 14 }} />}
                  onClick={handleCorrectStep}
                  sx={{ fontSize: '0.7rem' }}
                >
                  Correct
                </Button>
              </Box>
            )}
          </>
        )}

        {!attemptInfo.complete && attemptInfo.level && (
          <Box sx={{ borderTop: 1, borderColor: 'divider', pt: 1, mt: 'auto' }}>
            <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem' }}>
              {attemptInfo.level.toUpperCase()}
            </Typography>
          </Box>
        )}
      </Box>
    );
  };

  if (!open) return null;

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
        borderColor: mode === 'recording' ? 'error.dark' : mode === 'attempting' ? 'info.dark' : 'divider',
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
        }}
      >
        <SchoolIcon sx={{ fontSize: 16, mr: 0.75, color: 'text.secondary' }} />
        <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.8rem', flex: 1 }}>
          Trainer
        </Typography>
        <Box
          sx={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            bgcolor: STATE_COLORS[mode],
            mr: 1,
            ...(mode === 'recording' && {
              animation: 'pulse 1.5s ease-in-out infinite',
              '@keyframes pulse': { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.3 } },
            }),
          }}
        />
        {/* Stop button always visible in header when recording — even when collapsed */}
        {mode === 'recording' && (
          <Tooltip title="Stop Recording">
            <IconButton
              className="header-btn"
              size="small"
              onClick={handleStopRecording}
              disabled={loading}
              sx={{ p: 0.25, mr: 0.25 }}
            >
              <StopIcon sx={{ fontSize: 16, color: 'error.main' }} />
            </IconButton>
          </Tooltip>
        )}
        <Tooltip title={kbdForwarding ? 'Keyboard → Agent: ON (click to stop)' : 'Send keyboard to Agent Screen'}>
          <IconButton
            className="header-btn"
            size="small"
            onClick={toggleKbdForwarding}
            sx={{ p: 0.25, mr: 0.25, color: kbdForwarding ? 'success.main' : 'text.secondary' }}
          >
            {kbdForwarding ? <KeyboardIcon sx={{ fontSize: 16 }} /> : <KeyboardOutlinedIcon sx={{ fontSize: 16 }} />}
          </IconButton>
        </Tooltip>
        <IconButton
          className="header-btn"
          size="small"
          onClick={() => setCollapsed((c) => !c)}
          sx={{ p: 0.25 }}
        >
          {collapsed ? <ExpandMoreIcon sx={{ fontSize: 16 }} /> : <ExpandLessIcon sx={{ fontSize: 16 }} />}
        </IconButton>
        <IconButton className="header-btn" size="small" onClick={onClose} sx={{ p: 0.25, ml: 0.25 }}>
          <CloseIcon sx={{ fontSize: 16 }} />
        </IconButton>
      </Box>

      {/* Body — hidden when collapsed */}
      {!collapsed && (
        <Box sx={{ p: 1.5, height: size.h - 45, overflowY: 'auto', position: 'relative' }}>
          {mode === 'idle' && renderIdleView()}
          {mode === 'recording' && renderRecordingView()}
          {mode === 'questioning' && renderQuestioningView()}
          {mode === 'attempting' && renderAttemptingView()}

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
