import React, { useState, useEffect } from "react";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  RadioGroup,
  FormControlLabel,
  Radio,
  Box,
  Typography,
  Grid,
  Card,
  CardContent,
  Chip,
} from "@mui/material";
import { themes } from "../../theme";
import { useAppStore } from "../../stores/useAppStore";

// Inline SVG cologne bottle icon for Elon's Musk theme
const CologneBottleIcon = ({ size = 40 }) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
    {/* Cap */}
    <rect x="24" y="4" width="16" height="8" rx="2" fill="rgba(255,255,255,0.9)" />
    {/* Neck */}
    <rect x="27" y="12" width="10" height="6" fill="rgba(255,255,255,0.7)" />
    {/* Shoulders */}
    <path d="M27 18 L20 26 L20 54 C20 56.2 21.8 58 24 58 L40 58 C42.2 58 44 56.2 44 54 L44 26 L37 18 Z" fill="rgba(0,229,255,0.35)" stroke="rgba(255,255,255,0.8)" strokeWidth="1.5" />
    {/* Liquid level */}
    <path d="M20 32 L44 32 L44 54 C44 56.2 42.2 58 40 58 L24 58 C21.8 58 20 56.2 20 54 Z" fill="rgba(0,229,255,0.5)" />
    {/* Sprayer nozzle */}
    <rect x="30" y="1" width="4" height="4" rx="1" fill="rgba(255,255,255,0.6)" />
    {/* Label */}
    <text x="32" y="47" textAnchor="middle" fill="rgba(255,255,255,0.85)" fontSize="7" fontWeight="bold" fontFamily="monospace">EM</text>
    {/* Shine */}
    <line x1="23" y1="28" x2="23" y2="50" stroke="rgba(255,255,255,0.3)" strokeWidth="1" strokeLinecap="round" />
  </svg>
);

// Radioactive trefoil symbol for Fallout theme
const RadioactiveIcon = ({ size = 40 }) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
    {/* Three fan blades */}
    <path d="M32 12 C32 12 22 22 24 32 C18 30 8 30 8 30 C8 20 18 10 32 12Z" fill="rgba(240,192,64,0.85)" />
    <path d="M52 42 C52 42 42 32 32 34 C34 28 32 18 32 18 C44 18 54 30 52 42Z" fill="rgba(240,192,64,0.85)" />
    <path d="M22 50 C22 50 32 40 32 34 C28 38 20 44 20 44 C14 52 22 58 32 58 C30 58 22 56 22 50Z" fill="rgba(240,192,64,0.85)" />
    {/* Simplified trefoil using circles and arcs */}
    <circle cx="32" cy="32" r="6" fill="rgba(10,14,10,0.95)" stroke="rgba(240,192,64,0.9)" strokeWidth="1.5" />
    <circle cx="32" cy="32" r="20" fill="none" stroke="rgba(24,255,109,0.4)" strokeWidth="1" strokeDasharray="4 3" />
    {/* Three radiation wedges */}
    <path d="M32 12 A20 20 0 0 1 49.3 42 L38 36 A8 8 0 0 0 34 24 Z" fill="rgba(24,255,109,0.6)" />
    <path d="M49.3 42 A20 20 0 0 1 14.7 42 L26 36 A8 8 0 0 0 38 36 Z" fill="rgba(24,255,109,0.6)" />
    <path d="M14.7 42 A20 20 0 0 1 32 12 L34 24 A8 8 0 0 0 26 36 Z" fill="rgba(24,255,109,0.6)" />
    {/* Center dot */}
    <circle cx="32" cy="32" r="4" fill="rgba(24,255,109,0.9)" />
  </svg>
);

// Sith emblem for Vader theme — actual PNG with transparent background
const SithIcon = ({ size = 40 }) => (
  <img
    src="/theme-icons/sith-emblem.png"
    alt="Sith"
    width={size}
    height={size}
    style={{ objectFit: "contain", filter: "drop-shadow(0 1px 3px rgba(0,0,0,0.5))" }}
  />
);

const ThemePreview = ({ themeKey, themeData, isSelected, onClick }) => {
  const { label, description, previewGradient, icon } = themeData;
  
  const getPreviewStyle = () => {
    if (previewGradient) {
      return {
        background: previewGradient,
        border: "2px solid transparent",
      };
    }
    
    // Fallback gradients for themes without explicit preview
    const fallbackGradients = {
      default: "linear-gradient(135deg, #008080, #006666)",
      light: "linear-gradient(135deg, #fafafa, #1976d2)",
      sunset: "linear-gradient(45deg, #ff7043, #ffb74d)",
      musk: "linear-gradient(45deg, #00e5ff, #ff1744)",
      hacker: "linear-gradient(135deg, #18ff6d, #0a0e0a, #f0c040)",
    };
    
    return {
      background: fallbackGradients[themeKey] || "linear-gradient(135deg, #333, #666)",
      border: "2px solid transparent",
    };
  };

  return (
    <Card
      onClick={onClick}
      sx={{
        cursor: "pointer",
        transition: "all 0.3s ease",
        transform: isSelected ? "scale(1.05)" : "scale(1)",
        boxShadow: isSelected ? 4 : 1,
        border: isSelected ? "2px solid" : "1px solid",
        borderColor: isSelected ? "primary.main" : "divider",
        backgroundColor: "background.paper",
        "&:hover": {
          transform: "scale(1.02)",
          boxShadow: 3,
          backgroundColor: "action.hover",
        },
      }}
    >
      <Box
        sx={{
          height: 80,
          ...getPreviewStyle(),
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          position: "relative",
          overflow: "hidden",
        }}
      >
        {/* Enlarged background emblem for Sith/Vader — rotated, translucent, color-dodge */}
        {icon === "sith" && (
          <img
            src="/theme-icons/sith-emblem.png"
            alt=""
            style={{
              position: "absolute",
              width: "500%",
              height: "500%",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%) rotate(20deg)",
              opacity: 0.5,
              mixBlendMode: "color-dodge",
              pointerEvents: "none",
              objectFit: "contain",
            }}
          />
        )}
        {isSelected && (
          <Chip
            label="Selected"
            color="primary"
            size="small"
            sx={{
              position: "absolute",
              top: 8,
              right: 8,
              backgroundColor: "rgba(0, 0, 0, 0.8)",
              color: "white",
              fontWeight: "bold",
            }}
          />
        )}
        <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 0.5 }}>
          {icon === "cologne" && <CologneBottleIcon size={36} />}
          {icon === "radioactive" && <RadioactiveIcon size={36} />}
          {icon === "sith" && <SithIcon size={40} />}
          <Typography
            variant="h6"
            sx={{
              color: "white",
              fontWeight: "bold",
              textShadow: "0 2px 4px rgba(0,0,0,0.5)",
              textAlign: "center",
              ...(icon && { fontSize: "0.9rem" }),
            }}
          >
            {label}
          </Typography>
        </Box>
      </Box>
      <CardContent sx={{ p: 2, bgcolor: "background.paper" }}>
        <Typography
          variant="body2"
          sx={{
            minHeight: description ? "auto" : 40,
            fontSize: "0.875rem",
            lineHeight: 1.4,
            color: "text.secondary",
          }}
        >
          {description || "Classic theme design"}
        </Typography>
      </CardContent>
    </Card>
  );
};

const ThemeSelectorModal = ({ open, onClose }) => {
  const themeName = useAppStore((state) => state.themeName);
  const setThemeName = useAppStore((state) => state.setThemeName);
  const [tempTheme, setTempTheme] = useState(themeName);

  useEffect(() => {
    if (open) setTempTheme(themeName);
  }, [open, themeName]);

  const handleThemeSelect = (themeKey) => {
    setTempTheme(themeKey);
  };

  const handleApply = () => {
    setThemeName(tempTheme);
    if (onClose) onClose();
  };

  const handleCancel = () => {
    setTempTheme(themeName); // Reset to current theme
    if (onClose) onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={handleCancel}
      fullWidth
      maxWidth="md"
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxHeight: "90vh",
          bgcolor: "background.paper",
        },
      }}
    >
      <DialogTitle sx={{ pb: 1, bgcolor: "background.paper" }}>
        <Typography variant="h5" component="div" sx={{ fontWeight: 600, color: "text.primary" }}>
          Choose Your Theme
        </Typography>
        <Typography variant="body2" sx={{ mt: 0.5, color: "text.secondary" }}>
          Select a visual theme to personalize your experience
        </Typography>
      </DialogTitle>

      <DialogContent dividers sx={{ p: 3, bgcolor: "background.default" }}>
        <Grid container spacing={2}>
          {Object.entries(themes).map(([key, themeData]) => (
            <Grid item xs={12} sm={6} key={key}>
              <ThemePreview
                themeKey={key}
                themeData={themeData}
                isSelected={tempTheme === key}
                onClick={() => handleThemeSelect(key)}
              />
            </Grid>
          ))}
        </Grid>
        
        {/* Current selection info */}
        <Box sx={{ mt: 3, p: 2, bgcolor: "background.paper", borderRadius: 1 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1, color: "text.primary" }}>
            Selected: {themes[tempTheme]?.label || "Unknown"}
          </Typography>
          <Typography variant="body2" sx={{ color: "text.secondary" }}>
            {themes[tempTheme]?.description || "No description available"}
          </Typography>
        </Box>
      </DialogContent>

      <DialogActions sx={{ p: 2, gap: 1, bgcolor: "background.paper" }}>
        <Button onClick={handleCancel} variant="outlined">
          Cancel
        </Button>
        <Button 
          onClick={handleApply} 
          variant="contained"
          disabled={tempTheme === themeName}
          sx={{ minWidth: 100 }}
        >
          {tempTheme === themeName ? "Applied" : "Apply Theme"}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ThemeSelectorModal;
