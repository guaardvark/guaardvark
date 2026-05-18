// frontend/src/components/common/EnhancedMicroInteractions.jsx
// Phase 2B: Advanced UI Polish & Micro-interactions
// Provides subtle animations, enhanced feedback, and polished interactions

import React from "react";
import { 
  styled, 
  keyframes, 
  alpha,
  useTheme 
} from "@mui/material/styles";
import { 
  IconButton, 
  Button, 
  Chip, 
  LinearProgress,
  CircularProgress,
  Fade,
  Grow,
  Slide,
  Zoom,
  Box
} from "@mui/material";

// Subtle Animation Keyframes
const pulseGlow = keyframes`
  0% {
    box-shadow: 0 0 0 0 rgba(25, 118, 210, 0.4);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(25, 118, 210, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(25, 118, 210, 0);
  }
`;

const gentleHover = keyframes`
  0% {
    transform: translateY(0px);
  }
  50% {
    transform: translateY(-1px);
  }
  100% {
    transform: translateY(0px);
  }
`;

const smoothScale = keyframes`
  0% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.02);
  }
  100% {
    transform: scale(1);
  }
`;

// Enhanced IconButton with subtle micro-interactions
export const PolishedIconButton = styled(IconButton)(({ theme, variant = "default" }) => ({
  transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
  borderRadius: theme.spacing(1),
  position: 'relative',
  overflow: 'hidden',
  
  '&:hover': {
    transform: 'translateY(-1px)',
    boxShadow: `0 4px 8px ${alpha(theme.palette.primary.main, 0.15)}`,
    backgroundColor: variant === 'primary' 
      ? alpha(theme.palette.primary.main, 0.08)
      : alpha(theme.palette.action.hover, 0.8),
    
    '&::before': {
      content: '""',
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: `linear-gradient(45deg, ${alpha(theme.palette.primary.main, 0.05)}, ${alpha(theme.palette.primary.main, 0.1)})`,
      borderRadius: 'inherit',
      zIndex: -1,
    }
  },
  
  '&:active': {
    transform: 'translateY(0px) scale(0.98)',
    transition: 'all 0.1s cubic-bezier(0.4, 0, 0.2, 1)',
  },
  
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: '2px',
    animation: `${pulseGlow} 1.5s infinite`,
  },
  
  '&.loading': {
    pointerEvents: 'none',
    opacity: 0.7,
    animation: `${smoothScale} 1.5s ease-in-out infinite`,
  }
}));

// Enhanced Button with polished interactions
export const PolishedButton = styled(Button)(({ theme, _size = 'medium' }) => ({
  transition: 'all 0.25s cubic-bezier(0.4, 0, 0.2, 1)',
  borderRadius: theme.spacing(1.5),
  textTransform: 'none',
  fontWeight: 500,
  position: 'relative',
  overflow: 'hidden',
  
  '&:hover': {
    transform: 'translateY(-1px)',
    boxShadow: theme.shadows[4],
    
    '&::before': {
      content: '""',
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: `linear-gradient(45deg, ${alpha(theme.palette.primary.light, 0.1)}, ${alpha(theme.palette.primary.main, 0.1)})`,
      borderRadius: 'inherit',
      zIndex: 0,
    }
  },
  
  '&:active': {
    transform: 'translateY(0px) scale(0.98)',
    transition: 'all 0.15s cubic-bezier(0.4, 0, 0.2, 1)',
  },
  
  '&:focus-visible': {
    outline: `2px solid ${theme.palette.primary.main}`,
    outlineOffset: '2px',
  },
  
  '&.Mui-disabled': {
    transform: 'none',
    boxShadow: 'none',
  }
}));

// Enhanced Chip with subtle animations
export const PolishedChip = styled(Chip)(({ theme, status }) => {
  const getStatusColors = () => {
    switch (status) {
      case 'success':
        return {
          bg: alpha(theme.palette.success.main, 0.1),
          border: theme.palette.success.main,
          glow: theme.palette.success.main
        };
      case 'error':
        return {
          bg: alpha(theme.palette.error.main, 0.1),
          border: theme.palette.error.main,
          glow: theme.palette.error.main
        };
      case 'warning':
        return {
          bg: alpha(theme.palette.warning.main, 0.1),
          border: theme.palette.warning.main,
          glow: theme.palette.warning.main
        };
      case 'info':
        return {
          bg: alpha(theme.palette.info.main, 0.1),
          border: theme.palette.info.main,
          glow: theme.palette.info.main
        };
      default:
        return {
          bg: alpha(theme.palette.primary.main, 0.1),
          border: theme.palette.primary.main,
          glow: theme.palette.primary.main
        };
    }
  };
  
  const colors = getStatusColors();
  
  return {
    transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
    borderRadius: theme.spacing(2),
    backgroundColor: colors.bg,
    borderColor: colors.border,
    fontWeight: 500,
    
    '&:hover': {
      transform: 'scale(1.02)',
      boxShadow: `0 0 0 2px ${alpha(colors.glow, 0.2)}`,
              backgroundColor: alpha(colors.bg, 1.0),
    },
    
    '&.pulsing': {
      animation: `${pulseGlow} 2s infinite`,
      '--pulse-color': colors.glow,
    }
  };
});

// Enhanced Progress Indicator
export const PolishedProgressBar = styled(LinearProgress)(({ theme }) => ({
  borderRadius: theme.spacing(1),
  height: 6,
  backgroundColor: alpha(theme.palette.primary.main, 0.1),
  
  '& .MuiLinearProgress-bar': {
    borderRadius: theme.spacing(1),
    background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${theme.palette.primary.light})`,
    transition: 'transform 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
  },
  
  '&.animated .MuiLinearProgress-bar': {
    animation: `${gentleHover} 2s ease-in-out infinite`,
  }
}));

// Enhanced Loading Spinner
export const PolishedSpinner = styled(CircularProgress)(({ theme, _size = 20 }) => ({
  color: theme.palette.primary.main,
  animation: `spin 1s linear infinite, ${pulseGlow} 2s ease-in-out infinite`,
  
  '@keyframes spin': {
    '0%': {
      transform: 'rotate(0deg)',
    },
    '100%': {
      transform: 'rotate(360deg)',
    },
  }
}));

// Enhanced Table Row with micro-interactions
export const PolishedTableRow = styled('tr')(({ theme }) => ({
  transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
  
  '&:hover': {
    backgroundColor: alpha(theme.palette.primary.main, 0.04),
    transform: 'translateX(2px)',
    boxShadow: `inset 3px 0 0 ${theme.palette.primary.main}`,
    
    '& .action-icons': {
      opacity: 1,
      transform: 'translateX(0)',
    }
  },
  
  '& .action-icons': {
    opacity: 0.7,
    transform: 'translateX(4px)',
    transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
  }
}));

// Status Indicator with breathing animation
export const StatusIndicator = ({ status, children, animated = false }) => {
  const theme = useTheme();
  
  const getStatusColor = (status) => {
    switch (status) {
      case 'active':
      case 'success':
        return theme.palette.success.main;
      case 'error':
      case 'failed':
        return theme.palette.error.main;
      case 'warning':
      case 'pending':
        return theme.palette.warning.main;
      case 'info':
      case 'processing':
        return theme.palette.info.main;
      default:
        return theme.palette.grey[400];
    }
  };
  
  const breathingAnimation = keyframes`
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.7; transform: scale(1.1); }
  `;
  
  return (
    <Box
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 1,
        '&::before': {
          content: '""',
          width: 8,
          height: 8,
          borderRadius: '50%',
          backgroundColor: getStatusColor(status),
          animation: animated ? `${breathingAnimation} 2s ease-in-out infinite` : 'none',
        }
      }}
    >
      {children}
    </Box>
  );
};

// Smooth Transition Wrapper
export const SmoothTransition = ({ 
  children, 
  type = 'fade', 
  duration = 300,
  delay = 0,
  ...props 
}) => {
  const TransitionComponent = {
    fade: Fade,
    grow: Grow,
    slide: Slide,
    zoom: Zoom,
  }[type] || Fade;
  
  return (
    <TransitionComponent
      timeout={{
        enter: duration,
        exit: duration / 2,
      }}
      style={{ transitionDelay: `${delay}ms` }}
      {...props}
    >
      {children}
    </TransitionComponent>
  );
};

// Polished Card with hover effects
export const PolishedCard = styled(Box)(({ theme }) => ({
  backgroundColor: theme.palette.background.paper,
  borderRadius: theme.spacing(2),
  padding: theme.spacing(3),
  border: `1px solid ${alpha(theme.palette.divider, 0.1)}`,
  transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
  position: 'relative',
  overflow: 'hidden',
  
  '&:hover': {
    transform: 'translateY(-2px)',
    boxShadow: `
      0 8px 25px ${alpha(theme.palette.common.black, 0.1)},
      0 0 0 1px ${alpha(theme.palette.primary.main, 0.05)}
    `,
    
    '&::before': {
      content: '""',
      position: 'absolute',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      background: `linear-gradient(135deg, ${alpha(theme.palette.primary.main, 0.02)}, transparent)`,
      pointerEvents: 'none',
    }
  },
  
  '&:focus-within': {
    outline: `2px solid ${alpha(theme.palette.primary.main, 0.5)}`,
    outlineOffset: '2px',
  }
}));

export default {
  PolishedIconButton,
  PolishedButton,
  PolishedChip,
  PolishedProgressBar,
  PolishedSpinner,
  PolishedTableRow,
  StatusIndicator,
  SmoothTransition,
  PolishedCard,
}; 