import React, { useEffect, useRef } from 'react';
import PropTypes from 'prop-types';
import { useTheme } from '@mui/material/styles';

/**
 * AudioVisualizer Component
 * Displays real-time audio waveform and volume levels
 */
const AudioVisualizer = ({
  audioLevels = [],
  volume = 0,
  isRecording = false,
  width = 300,
  height = 100,
  style = 'waveform', // 'waveform', 'bars', 'circle'
  color: colorProp,
  backgroundColor: backgroundColorProp,
  className = ''
}) => {
  const theme = useTheme();
  const color = colorProp || theme.palette.primary.main;
  const backgroundColor = backgroundColorProp || theme.palette.background.default;
  const canvasRef = useRef(null);
  const animationRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const devicePixelRatio = window.devicePixelRatio || 1;
    
    // Set canvas size for high DPI displays
    canvas.width = width * devicePixelRatio;
    canvas.height = height * devicePixelRatio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.scale(devicePixelRatio, devicePixelRatio);

    const draw = () => {
      // Clear canvas
      ctx.fillStyle = backgroundColor;
      ctx.fillRect(0, 0, width, height);

      if (!isRecording || audioLevels.length === 0) {
        // Draw idle state
        drawIdleState(ctx);
      } else {
        // Draw active visualization based on style
        switch (style) {
          case 'bars':
            drawBars(ctx);
            break;
          case 'circle':
            drawCircle(ctx);
            break;
          case 'waveform':
          default:
            drawWaveform(ctx);
            break;
        }
      }

      if (isRecording) {
        animationRef.current = requestAnimationFrame(draw);
      }
    };

    const drawIdleState = (ctx) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      
      ctx.beginPath();
      ctx.moveTo(0, height / 2);
      ctx.lineTo(width, height / 2);
      ctx.stroke();
      
      ctx.setLineDash([]);
    };

    const drawWaveform = (ctx) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();

      const sliceWidth = width / audioLevels.length;
      let x = 0;

      for (let i = 0; i < audioLevels.length; i++) {
        const level = audioLevels[i];
        const y = height / 2 + (level * height / 2) * (Math.sin(Date.now() * 0.01 + i * 0.1));
        
        if (i === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
        
        x += sliceWidth;
      }
      
      ctx.stroke();

      // Draw volume indicator
      drawVolumeIndicator(ctx);
    };

    const drawBars = (ctx) => {
      const barWidth = width / audioLevels.length;
      const maxBarHeight = height * 0.8;
      
      ctx.fillStyle = color;
      
      for (let i = 0; i < audioLevels.length; i++) {
        const level = audioLevels[i];
        const barHeight = level * maxBarHeight;
        const x = i * barWidth;
        const y = height - barHeight;
        
        ctx.fillRect(x, y, barWidth - 1, barHeight);
      }

      // Draw volume indicator
      drawVolumeIndicator(ctx);
    };

    const drawCircle = (ctx) => {
      const centerX = width / 2;
      const centerY = height / 2;
      const maxRadius = Math.min(width, height) / 2 - 10;
      
      // Draw outer circle
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(centerX, centerY, maxRadius, 0, 2 * Math.PI);
      ctx.stroke();
      
      // Draw volume-based inner circle
      const volumeRadius = volume * maxRadius;
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      ctx.arc(centerX, centerY, volumeRadius, 0, 2 * Math.PI);
      ctx.fill();
      ctx.globalAlpha = 1;
      
      // Draw frequency bars around the circle
      const numBars = Math.min(audioLevels.length, 32);
      const angleStep = (2 * Math.PI) / numBars;
      
      for (let i = 0; i < numBars; i++) {
        const angle = i * angleStep;
        const level = audioLevels[i] || 0;
        const barLength = level * (maxRadius * 0.3);
        
        const startX = centerX + Math.cos(angle) * maxRadius;
        const startY = centerY + Math.sin(angle) * maxRadius;
        const endX = centerX + Math.cos(angle) * (maxRadius + barLength);
        const endY = centerY + Math.sin(angle) * (maxRadius + barLength);
        
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(startX, startY);
        ctx.lineTo(endX, endY);
        ctx.stroke();
      }
    };

    const drawVolumeIndicator = (ctx) => {
      // Draw volume level indicator
      const indicatorWidth = 4;
      const indicatorHeight = height * 0.1;
      const indicatorX = width - indicatorWidth - 5;
      const indicatorY = height - indicatorHeight - 5;
      
      // Background
      ctx.fillStyle = backgroundColor;
      ctx.fillRect(indicatorX, indicatorY, indicatorWidth, indicatorHeight);
      
      // Volume level
      const volumeHeight = volume * indicatorHeight;
      const volumeY = indicatorY + indicatorHeight - volumeHeight;
      
      ctx.fillStyle = volume > 0.7 ? theme.palette.error.main : volume > 0.4 ? theme.palette.warning.main : theme.palette.success.main;
      ctx.fillRect(indicatorX, volumeY, indicatorWidth, volumeHeight);
    };

    draw();

    return () => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, [audioLevels, volume, isRecording, width, height, style, color, backgroundColor, theme]);

  return (
    <div className={`audio-visualizer ${className}`}>
      <canvas
        ref={canvasRef}
        className="rounded-lg border border-gray-200"
        style={{ display: 'block' }}
      />
    </div>
  );
};

AudioVisualizer.propTypes = {
  audioLevels: PropTypes.arrayOf(PropTypes.number),
  volume: PropTypes.number,
  isRecording: PropTypes.bool,
  width: PropTypes.number,
  height: PropTypes.number,
  style: PropTypes.oneOf(['waveform', 'bars', 'circle']),
  color: PropTypes.string,
  backgroundColor: PropTypes.string,
  className: PropTypes.string,
};

export default AudioVisualizer; 