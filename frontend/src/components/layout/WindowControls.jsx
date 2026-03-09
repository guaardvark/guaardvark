// WindowControls component for app mode - provides close and minimize buttons
import React, { useState, useEffect } from 'react';
import { Box, IconButton, Tooltip } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import RemoveIcon from '@mui/icons-material/Remove';
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit';

const WindowControls = () => {
  const [isMinimized, setIsMinimized] = useState(false);
  const [isKioskMode, setIsKioskMode] = useState(false);

  const handleClose = () => {
    // Try to close the window
    // Note: window.close() only works if the window was opened by a script
    // In kiosk mode, this may not work, so we'll try and show a message if it fails
    if (window.close) {
      window.close();
    } else {
      // Fallback: try to exit fullscreen/kiosk mode
      if (document.exitFullscreen) {
        document.exitFullscreen();
      } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      } else if (document.mozCancelFullScreen) {
        document.mozCancelFullScreen();
      } else if (document.msExitFullscreen) {
        document.msExitFullscreen();
      }
      // If still can't close, show alert
      setTimeout(() => {
        alert('To close the application, press Alt+F4 or use your system window controls.');
      }, 100);
    }
  };

  const handleMinimize = () => {
    // JavaScript cannot minimize browser windows due to security restrictions
    // We'll provide a workaround by hiding the main content and showing a small restore button
    const newMinimizedState = !isMinimized;
    setIsMinimized(newMinimizedState);
    
    // Store state in sessionStorage so it persists
    sessionStorage.setItem('windowMinimized', String(newMinimizedState));
    
    // If minimizing, hide the main content but keep window controls visible
    const mainContent = document.querySelector('[data-main-content]');
    if (mainContent) {
      if (newMinimizedState) {
        mainContent.style.display = 'none';
      } else {
        mainContent.style.display = 'block';
      }
    } else {
      // Fallback: hide the root content but keep controls
      const rootElement = document.getElementById('root');
      if (rootElement) {
        const mainBox = rootElement.querySelector('main, [role="main"]');
        if (mainBox) {
          if (newMinimizedState) {
            mainBox.style.display = 'none';
          } else {
            mainBox.style.display = 'block';
          }
        }
      }
    }
    
    // Show a restore indicator when minimized
    if (newMinimizedState) {
      // Create a small floating restore button
      let indicator = document.getElementById('minimize-indicator');
      if (!indicator) {
        indicator = document.createElement('div');
        indicator.id = 'minimize-indicator';
        indicator.style.cssText = `
          position: fixed;
          bottom: 20px;
          right: 20px;
          background: rgba(25, 118, 210, 0.9);
          color: white;
          padding: 12px 24px;
          border-radius: 8px;
          z-index: 10001;
          cursor: pointer;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          font-size: 14px;
          font-weight: 500;
          box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
          transition: all 0.2s ease;
        `;
        indicator.textContent = 'Click to restore window';
        indicator.onmouseenter = () => {
          indicator.style.background = 'rgba(25, 118, 210, 1)';
          indicator.style.transform = 'scale(1.05)';
        };
        indicator.onmouseleave = () => {
          indicator.style.background = 'rgba(25, 118, 210, 0.9)';
          indicator.style.transform = 'scale(1)';
        };
        indicator.onclick = () => {
          setIsMinimized(false);
          handleMinimize();
        };
        document.body.appendChild(indicator);
      }
    } else {
      const indicator = document.getElementById('minimize-indicator');
      if (indicator) {
        indicator.remove();
      }
    }
  };

  const handleExitKiosk = () => {
    // Exit kiosk/fullscreen mode
    if (document.exitFullscreen) {
      document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen();
    } else if (document.mozCancelFullScreen) {
      document.mozCancelFullScreen();
    } else if (document.msExitFullscreen) {
      document.msExitFullscreen();
    }
  };

  // Check if we're in kiosk/fullscreen mode and set up keyboard shortcuts
  useEffect(() => {
    const checkKioskMode = () => {
      setIsKioskMode(
        !!(
          document.fullscreenElement ||
          document.webkitFullscreenElement ||
          document.mozFullScreenElement ||
          document.msFullscreenElement
        )
      );
    };

    checkKioskMode();
    
    // Listen for fullscreen changes
    document.addEventListener('fullscreenchange', checkKioskMode);
    document.addEventListener('webkitfullscreenchange', checkKioskMode);
    document.addEventListener('mozfullscreenchange', checkKioskMode);
    document.addEventListener('MSFullscreenChange', checkKioskMode);

    // Restore minimized state if it was set
    const wasMinimized = sessionStorage.getItem('windowMinimized') === 'true';
    if (wasMinimized) {
      setIsMinimized(true);
      const mainContent = document.querySelector('[data-main-content]');
      if (mainContent) {
        mainContent.style.display = 'none';
      }
    }

    // Keyboard shortcuts
    const handleKeyDown = (e) => {
      // F11 to exit kiosk mode
      if (e.key === 'F11') {
        e.preventDefault();
        handleExitKiosk();
      }
      // Alt+F4 equivalent (Ctrl+Q on Linux)
      if ((e.ctrlKey && e.key === 'q') || (e.altKey && e.key === 'F4')) {
        e.preventDefault();
        handleClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('fullscreenchange', checkKioskMode);
      document.removeEventListener('webkitfullscreenchange', checkKioskMode);
      document.removeEventListener('mozfullscreenchange', checkKioskMode);
      document.removeEventListener('MSFullscreenChange', checkKioskMode);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []); // Empty deps - functions are stable

  return (
    <Box
      sx={{
        position: 'fixed',
        top: 0,
        right: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        backgroundColor: 'transparent',
        padding: '4px',
        '& .MuiIconButton-root': {
          width: '32px',
          height: '32px',
          padding: '4px',
          color: 'text.secondary',
          '&:hover': {
            backgroundColor: 'action.hover',
            color: 'text.primary',
          },
        },
        '& .MuiIconButton-root:last-child:hover': {
          backgroundColor: 'error.main',
          color: 'error.contrastText',
        },
      }}
    >
      {isKioskMode && (
        <Tooltip title="Exit Kiosk Mode (F11)">
          <IconButton
            size="small"
            onClick={handleExitKiosk}
            sx={{
              '&:hover': {
                backgroundColor: 'warning.main',
                color: 'warning.contrastText',
              },
            }}
          >
            <FullscreenExitIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}
      <Tooltip title="Minimize Window (Hide Content)">
        <IconButton
          size="small"
          onClick={handleMinimize}
        >
          <RemoveIcon fontSize="small" />
        </IconButton>
      </Tooltip>
      <Tooltip title="Close Application (Ctrl+Q or Alt+F4)">
        <IconButton
          size="small"
          onClick={handleClose}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Tooltip>
    </Box>
  );
};

export default WindowControls;
