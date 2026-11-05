// AgentScreenPage.jsx — the Agent Screen in a window of its own.
//
// Opened by the floating viewer's pop-out button, or directly at
// /agent-screen. The display arrives as one continuous MJPEG stream; in
// Control mode the person's mouse (clicks, hover, drag, wheel) and keyboard
// go to it. The window tells the other Guaardvark windows it is up, so their
// floating viewer hides, and closes when one of them takes the screen back.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Box, Chip, IconButton, Slider, Tooltip, Typography } from '@mui/material';
import DesktopWindowsIcon from '@mui/icons-material/DesktopWindows';
import MouseIcon from '@mui/icons-material/Mouse';
import MouseOutlinedIcon from '@mui/icons-material/MouseOutlined';
import PauseIcon from '@mui/icons-material/Pause';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import CloseFullscreenIcon from '@mui/icons-material/CloseFullscreen';
import CircleIcon from '@mui/icons-material/Circle';
import axios from 'axios';
import { BASE_URL } from '../api/apiClient';
import { startDisplay, cancelDisplayIdle } from '../api/agentDisplayService';
import { useUiTranslation } from '../i18n';
import { attachMouseForwarding } from '../components/agent/screenInput';
import { attachKeyForwarding } from '../hooks/useKeyboardForwarding';
import { listenAgentScreen, postAgentScreen } from '../utils/agentScreenChannel';

const STORAGE_KEY = 'guaardvark_agent_screen_window';
const DEFAULT_FPS = 8;
const MAX_FPS = 15; // the stream route's own ceiling
const STREAM_QUALITY = 70;
const RETRY_MS = 2000;
const IDLE_SHUTDOWN_SECONDS = 300;

function loadFps() {
  try {
    const fps = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}').fps;
    return fps >= 1 && fps <= MAX_FPS ? fps : DEFAULT_FPS;
  } catch {
    return DEFAULT_FPS;
  }
}

function saveFps(fps) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ fps }));
  } catch {
    // storage blocked; the speed just is not remembered
  }
}

// Arm the display's idle shutdown as the window goes away. A beacon still
// goes out while the page unloads, where a fetch may be cancelled.
function armIdleOnExit() {
  const body = new Blob([JSON.stringify({ seconds: IDLE_SHUTDOWN_SECONDS })], { type: 'application/json' });
  navigator.sendBeacon?.(`${BASE_URL}/agent-control/display-idle-arm`, body);
}

export default function AgentScreenPage() {
  const { t } = useUiTranslation();
  const [control, setControl] = useState(false);
  const [training, setTraining] = useState(false);
  const [fps, setFps] = useState(loadFps);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);
  const [still, setStill] = useState(null); // last frame, shown while paused
  const [streamKey, setStreamKey] = useState(0);
  const imgRef = useRef(null);
  const screenRef = useRef(null);
  const controlRef = useRef(false);
  const trainingRef = useRef(false);
  const leavingRef = useRef(false); // set when the screen goes back into a page

  useEffect(() => { controlRef.current = control; }, [control]);
  useEffect(() => { trainingRef.current = training; }, [training]);

  useEffect(() => {
    document.title = t('agents.screen.popupTitle');
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, [t]);

  useEffect(() => {
    let cancelled = false;
    cancelDisplayIdle().catch(() => {});
    startDisplay()
      .then((result) => {
        if (cancelled) return;
        if (result?.success || result?.display_running) setReady(true);
        else setError(result?.error || t('agents.screen.startFailed'));
      })
      .catch(() => {
        if (!cancelled) setError(t('agents.screen.startFailed'));
      });
    return () => { cancelled = true; };
  }, []);

  // Tell the other windows this one is up; close when one takes the screen back.
  useEffect(() => {
    const stop = listenAgentScreen((msg) => {
      if (msg.type === 'ping') {
        postAgentScreen('popout-open');
      } else if (msg.type === 'dock') {
        leavingRef.current = true;
        window.close();
      }
    });
    postAgentScreen('popout-open');
    const onPageHide = () => {
      if (leavingRef.current) return;
      postAgentScreen('popout-closed');
      armIdleOnExit();
    };
    window.addEventListener('pagehide', onPageHide);
    return () => {
      stop();
      window.removeEventListener('pagehide', onPageHide);
    };
  }, []);

  // While a demonstration is recorded, clicks go through as whole clicks so
  // the recorder can keep them as steps.
  useEffect(() => {
    const check = async () => {
      try {
        const res = await axios.get(`${BASE_URL}/agent-control/learn/status`);
        setTraining(res.data?.learning === true);
      } catch {
        setTraining(false);
      }
    };
    check();
    const id = setInterval(check, 2000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => attachKeyForwarding(window, { isActive: () => controlRef.current }), []);

  useEffect(() => {
    const el = screenRef.current;
    if (!el) return undefined;
    return attachMouseForwarding(el, () => imgRef.current, {
      getMode: () => (trainingRef.current ? 'click' : controlRef.current ? 'full' : 'off'),
      onError: (err) => console.error('Screen input failed:', err?.message || err),
    });
  }, []);

  const onStreamError = useCallback(() => {
    setError(t('agents.screen.reconnecting'));
    setTimeout(() => {
      setError(null);
      setStreamKey((k) => k + 1);
    }, RETRY_MS);
  }, [t]);

  const togglePause = useCallback(() => {
    if (still) {
      setStill(null);
      return;
    }
    const img = imgRef.current;
    if (!img?.naturalWidth) return;
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext('2d').drawImage(img, 0, 0);
    setStill(canvas.toDataURL('image/jpeg', 0.9));
  }, [still]);

  const changeFps = useCallback((_, value) => {
    setFps(value);
    saveFps(value);
  }, []);

  const backToPage = useCallback(() => {
    leavingRef.current = true;
    postAgentScreen('docked');
    window.close();
  }, []);

  const streamUrl = `${BASE_URL}/agent-control/stream?fps=${fps}&quality=${STREAM_QUALITY}&auto_start=1&k=${streamKey}`;
  const cursor = control || training ? 'crosshair' : 'default';

  return (
    <Box sx={{ height: '100vh', display: 'flex', flexDirection: 'column', bgcolor: '#000' }}>
      <Box
        sx={{
          flex: 'none',
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          px: 1,
          py: 0.25,
          bgcolor: 'background.default',
          borderBottom: 2,
          borderColor: training ? 'error.dark' : control ? 'warning.main' : 'divider',
        }}
      >
        <DesktopWindowsIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
        <Typography variant="caption" sx={{ fontWeight: 500, color: 'text.secondary' }}>
          {t('agents.screen.title')}
        </Typography>
        {training && (
          <Chip
            icon={<CircleIcon sx={{ fontSize: '6px !important' }} />}
            label={t('agents.screen.training')}
            size="small"
            color="error"
            sx={{ height: 18, fontSize: '0.6rem' }}
          />
        )}
        {control && !training && (
          <Chip label={t('agents.screen.control')} size="small" color="warning" sx={{ height: 18, fontSize: '0.6rem' }} />
        )}
        <Box sx={{ flex: 1 }} />
        <Tooltip title={control ? t('agents.screen.controlOn') : t('agents.screen.controlOff')}>
          <IconButton
            size="small"
            onClick={(e) => { setControl((c) => !c); e.currentTarget.blur(); }}
            sx={{ color: control ? 'warning.main' : 'text.secondary' }}
          >
            {control ? <MouseIcon fontSize="small" /> : <MouseOutlinedIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 34, textAlign: 'right' }}>
          {t('agents.screen.fpsValue', { fps })}
        </Typography>
        <Slider
          value={fps}
          onChange={changeFps}
          min={1}
          max={MAX_FPS}
          step={1}
          size="small"
          aria-label={t('agents.screen.fps')}
          sx={{ width: 80 }}
        />
        <Tooltip title={still ? t('agents.screen.resume') : t('agents.screen.pause')}>
          <IconButton size="small" onClick={(e) => { togglePause(); e.currentTarget.blur(); }}>
            {still ? <PlayArrowIcon fontSize="small" /> : <PauseIcon fontSize="small" />}
          </IconButton>
        </Tooltip>
        <Tooltip title={t('agents.screen.dock')}>
          <IconButton size="small" onClick={backToPage}>
            <CloseFullscreenIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Box>

      <Box
        ref={screenRef}
        sx={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      >
        {ready && !error ? (
          <img
            key={still ? 'still' : streamKey}
            ref={imgRef}
            src={still || streamUrl}
            alt={t('agents.screen.imageAlt')}
            draggable={false}
            onError={still ? undefined : onStreamError}
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', cursor }}
          />
        ) : (
          <Typography variant="caption" color={error ? 'error.main' : 'grey.600'}>
            {error || t('agents.screen.starting')}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
