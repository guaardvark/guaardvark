// frontend/src/components/audio/WaveformPlayer.jsx
import React, { useState, useRef, useEffect } from "react";
import {
  Box,
  Typography,
  IconButton,
  Slider,
  Stack,
  Paper,
  Tooltip,
} from "@mui/material";
import {
  PlayArrow as PlayIcon,
  Pause as PauseIcon,
  Download as DownloadIcon,
} from "@mui/icons-material";
import UnifiedWaveform from "./UnifiedWaveform";

const WaveformPlayer = ({ src, title, duration, onDownload, color }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [audioLevels, setAudioLevels] = useState([]);
  const audioRef = useRef(null);

  const togglePlay = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  // One rAF chain for the component's lifetime. Source of truth for
  // "is currently playing" is the audio element itself (audioRef.current.paused),
  // not the React isPlaying state — that way a re-render can't fork a second
  // chain that runs with a stale closure value.
  useEffect(() => {
    let frameId = null;
    let stopped = false;

    const tick = () => {
      if (stopped) return;
      if (audioRef.current) {
        const current = audioRef.current.currentTime;
        const total = audioRef.current.duration;
        if (total) setProgress((current / total) * 100);

        if (!audioRef.current.paused) {
          const levels = Array.from({ length: 60 }, () => Math.random() * 0.5 + 0.1);
          setAudioLevels(levels);
        } else {
          setAudioLevels((prev) => (prev.length > 0 ? [] : prev));
        }
      }
      frameId = requestAnimationFrame(tick);
    };
    frameId = requestAnimationFrame(tick);
    return () => {
      stopped = true;
      if (frameId !== null) cancelAnimationFrame(frameId);
    };
  }, []);

  const handleSliderChange = (event, newValue) => {
    if (!audioRef.current) return;
    const total = audioRef.current.duration;
    if (total) {
      audioRef.current.currentTime = (newValue / 100) * total;
      setProgress(newValue);
    }
  };

  if (!src) return null;

  return (
    <Paper
      elevation={3}
      sx={{
        p: 2,
        mt: 2,
        backgroundColor: "rgba(0, 0, 0, 0.4)",
        borderRadius: 3,
        border: "1px solid rgba(255, 255, 255, 0.1)",
      }}
    >
      <audio
        ref={audioRef}
        src={src}
        onEnded={() => setIsPlaying(false)}
        style={{ display: "none" }}
      />
      
      <Stack direction="row" spacing={2} alignItems="center">
        <IconButton
          onClick={togglePlay}
          size="large"
          sx={{
            backgroundColor: color || "primary.main",
            color: "white",
            "&:hover": { backgroundColor: color ? "rgba(0,0,0,0.2)" : "primary.dark" },
            flexShrink: 0
          }}
        >
          {isPlaying ? <PauseIcon fontSize="large" /> : <PlayIcon fontSize="large" />}
        </IconButton>

        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="subtitle1" fontWeight="bold" noWrap>
            {title || "Generated Audio"}
          </Typography>
          
          <Box sx={{ mt: 1, mb: 1 }}>
            <UnifiedWaveform 
              mode="playback"
              src={src}
              isActive={isPlaying}
              audioLevels={audioLevels}
              color={color}
              height={50}
            />
          </Box>
          
          <Slider
            size="small"
            value={progress}
            onChange={handleSliderChange}
            sx={{ 
              mt: -1.5,
              color: color || "primary.main",
              "& .MuiSlider-thumb": { width: 12, height: 12 }
            }}
          />
        </Box>

        <Stack spacing={0} alignItems="center" sx={{ minWidth: 60 }}>
          <Typography variant="caption" fontWeight="bold">
            {duration ? `${duration.toFixed(1)}s` : "--"}
          </Typography>
          <Tooltip title="Download">
            <IconButton onClick={() => onDownload(src)} size="small">
              <DownloadIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>
    </Paper>
  );
};

export default WaveformPlayer;
