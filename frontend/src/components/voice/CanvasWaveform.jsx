import React, { useRef, useEffect } from 'react';
import { Box } from '@mui/material';
import voiceService from '../../api/voiceService';

const CanvasWaveform = ({ isListening, speechDetected, waveformActive, height = 32, compact = false }) => {
  const canvasRef = useRef(null);
  const animationRef = useRef(null);

  useEffect(() => {
    if (!isListening) {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
      return;
    }

    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    const numBars = compact ? 7 : 20;
    
    // Handle high-DPI displays
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const draw = () => {
      ctx.clearRect(0, 0, rect.width, rect.height);
      
      const levels = voiceService.getAudioLevels() || new Array(numBars).fill(0);
      const barWidth = compact ? 3 : 2;
      const gap = compact ? 2.5 : 4;
      const totalWidth = numBars * barWidth + (numBars - 1) * gap;
      const startX = (rect.width - totalWidth) / 2;
      
      for (let i = 0; i < numBars; i++) {
        const levelIndex = Math.floor((i / numBars) * (levels.length || 1));
        const level = levels[levelIndex] || 0;
        
        let barHeight;
        if (compact) {
          const idleHeights = [0.22, 0.38, 0.28, 0.45, 0.28, 0.38, 0.22];
          barHeight = waveformActive ? Math.max(0.15, Math.min(1, level * 2.0)) * rect.height * 0.55 : idleHeights[i] * rect.height * 0.55;
        } else {
          barHeight = Math.max(4, Math.pow(level, 0.6) * rect.height * 0.9);
        }
        
        const x = startX + i * (barWidth + gap);
        const y = (rect.height - barHeight) / 2;
        
        ctx.fillStyle = speechDetected ? '#d32f2f' : (waveformActive ? '#1976d2' : '#bdbdbd');
        if (compact) {
          ctx.fillStyle = 'currentColor'; // Inherit from parent
        }
        
        ctx.beginPath();
        ctx.roundRect(x, y, barWidth, barHeight, 2);
        ctx.fill();
      }
      
      animationRef.current = requestAnimationFrame(draw);
    };
    
    draw();
    
    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [isListening, speechDetected, waveformActive, compact]);

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: compact ? '100%' : height,
        width: '100%',
        minWidth: compact ? 'auto' : 80,
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          width: '100%',
          height: '100%',
          display: 'block'
        }}
      />
    </Box>
  );
};

export default CanvasWaveform;
