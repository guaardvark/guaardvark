import { createFullTheme } from "./createTheme";

// ─── Default: Clean dark with teal accent ────────────────────────────────────

const defaultTheme = createFullTheme({
  accent: "#008080",
  accentDark: "#006666",
  accentLight: "#26a6a6",
  secondary: "#ce93d8",
  secondaryDark: "#ab47bc",
  secondaryLight: "#e1bee7",
  bg: "#121212",
  bgPaper: "#1e1e1e",
  textPrimary: "#e0e0e0",
  textSecondary: "#a0a0a0",
  divider: "rgba(255, 255, 255, 0.08)",
});

// ─── Elon's Musk: Futuristic dark with neon cyan + red ──────────────────────

const muskTheme = createFullTheme({
  accent: "#00e5ff",
  accentDark: "#00b8d4",
  accentLight: "#18ffff",
  secondary: "#ff1744",
  secondaryDark: "#d50000",
  secondaryLight: "#ff5252",
  bg: "#0d0d0d",
  bgPaper: "#101010",
  textPrimary: "#e0e0e0",
  textSecondary: "#9e9e9e",
  divider: "rgba(0, 229, 255, 0.12)",
  fontFamily: '"Roboto Mono", Menlo, monospace',
  componentOverrides: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "#151515",
          border: "1px solid #333",
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          borderRadius: "20px",
          textTransform: "none",
          fontWeight: 500,
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        filledInfo: {
          backgroundImage: "linear-gradient(180deg, #1a1a1a, #0d0d0d)",
          color: "#e0e0e0",
          border: "1px solid #00e5ff",
        },
        filledSuccess: {
          backgroundImage: "linear-gradient(180deg, #1a1a1a, #0d0d0d)",
          color: "#e0e0e0",
          border: "1px solid #4caf50",
        },
        filledWarning: {
          backgroundImage: "linear-gradient(180deg, #1a1a1a, #0d0d0d)",
          color: "#e0e0e0",
          border: "1px solid #ff9800",
        },
        filledError: {
          backgroundImage: "linear-gradient(180deg, #1a1a1a, #0d0d0d)",
          color: "#e0e0e0",
          border: "1px solid #ff1744",
        },
      },
    },
  },
});

// ─── Fallout: Pip-Boy inspired green/amber on dark ──────────────────────────

const hackerTheme = createFullTheme({
  accent: "#18ff6d",
  accentDark: "#10c050",
  accentLight: "#55ff8a",
  secondary: "#f0c040",
  secondaryDark: "#c09a20",
  secondaryLight: "#f5d060",
  bg: "#0a0e0a",
  bgPaper: "rgba(10, 20, 10, 0.92)",
  textPrimary: "#18ff6d",
  textSecondary: "#90c0a0",
  divider: "rgba(24, 255, 109, 0.15)",
  fontFamily: '"Courier New", "Monaco", monospace',
  componentOverrides: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundColor: "rgba(10, 20, 10, 0.92)",
          backgroundImage: "none",
          border: "1px solid rgba(24, 255, 109, 0.3)",
          boxShadow: "0 0 12px rgba(24, 255, 109, 0.15)",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "rgba(10, 20, 10, 0.92)",
          border: "1px solid rgba(24, 255, 109, 0.3)",
          boxShadow: "0 0 8px rgba(24, 255, 109, 0.1)",
          borderRadius: "0",
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 500,
          borderRadius: "0",
        },
        contained: {
          backgroundColor: "#000000",
          color: "#00ff41",
          border: "1px solid #00ff41",
          textTransform: "uppercase",
          fontFamily: '"Courier New", monospace',
          "&:hover": {
            backgroundColor: "#001100",
            boxShadow: "0 0 15px rgba(0, 255, 65, 0.5)",
          },
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        filledInfo: {
          backgroundColor: "rgba(0, 20, 0, 0.9)",
          color: "#00ff41",
          border: "1px solid #00ff41",
          boxShadow: "0 0 15px rgba(0, 255, 65, 0.2)",
        },
        filledSuccess: {
          backgroundColor: "rgba(0, 20, 0, 0.9)",
          color: "#00ff41",
          border: "1px solid #00ff41",
          boxShadow: "0 0 15px rgba(0, 255, 65, 0.2)",
        },
        filledWarning: {
          backgroundColor: "rgba(0, 20, 0, 0.9)",
          color: "#ff9800",
          border: "1px solid #ff9800",
          boxShadow: "0 0 15px rgba(255, 152, 0, 0.2)",
        },
        filledError: {
          backgroundColor: "rgba(0, 20, 0, 0.9)",
          color: "#f44336",
          border: "1px solid #f44336",
          boxShadow: "0 0 15px rgba(244, 67, 54, 0.2)",
        },
      },
    },
  },
});

// ─── Vader: Dark imposing black with red accents ─────────────────────────────

const vaderTheme = createFullTheme({
  accent: "#d32f2f",
  accentDark: "#b71c1c",
  accentLight: "#f44336",
  secondary: "#424242",
  secondaryDark: "#212121",
  secondaryLight: "#616161",
  bg: "#000000",
  bgPaper: "rgba(211, 47, 47, 0.05)",
  textPrimary: "#ffffff",
  textSecondary: "#d32f2f",
  divider: "rgba(211, 47, 47, 0.2)",
  fontFamily: '"Orbitron", "Roboto Mono", monospace',
  componentOverrides: {
    MuiPaper: {
      styleOverrides: {
        root: {
          background: "linear-gradient(135deg, rgba(0, 0, 0, 0.9), rgba(211, 47, 47, 0.05))",
          backgroundImage: "none",
          backgroundColor: "rgba(10, 0, 0, 0.95)",
          border: "1px solid #d32f2f",
          boxShadow: "0 0 20px rgba(211, 47, 47, 0.3), inset 0 0 20px rgba(0, 0, 0, 0.5)",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "rgba(10, 0, 0, 0.9)",
          border: "1px solid #d32f2f",
          boxShadow: "0 0 15px rgba(211, 47, 47, 0.2)",
          borderRadius: "0",
          "&:hover": {
            boxShadow: "0 0 25px rgba(211, 47, 47, 0.4)",
            transform: "translateY(-2px)",
          },
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          textTransform: "none",
          fontWeight: 500,
          borderRadius: "0",
        },
        contained: {
          background: "linear-gradient(45deg, #000000, #d32f2f)",
          color: "#ffffff",
          border: "1px solid #d32f2f",
          textTransform: "uppercase",
          fontWeight: "bold",
          letterSpacing: "2px",
          fontFamily: '"Orbitron", monospace',
          boxShadow: "0 0 20px rgba(211, 47, 47, 0.4)",
          "&:hover": {
            background: "linear-gradient(45deg, #212121, #f44336)",
            boxShadow: "0 0 30px rgba(211, 47, 47, 0.6), 0 0 60px rgba(211, 47, 47, 0.3)",
            transform: "scale(1.05)",
          },
        },
        outlined: {
          color: "#d32f2f",
          border: "2px solid #d32f2f",
          textTransform: "uppercase",
          fontWeight: "bold",
          letterSpacing: "1px",
          fontFamily: '"Orbitron", monospace',
          "&:hover": {
            backgroundColor: "rgba(211, 47, 47, 0.1)",
            border: "2px solid #f44336",
            boxShadow: "0 0 15px rgba(211, 47, 47, 0.4)",
          },
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        filledInfo: {
          backgroundColor: "rgba(10, 0, 0, 0.9)",
          color: "#ffffff",
          border: "1px solid #d32f2f",
          boxShadow: "0 0 20px rgba(211, 47, 47, 0.3)",
        },
        filledSuccess: {
          backgroundColor: "rgba(10, 0, 0, 0.9)",
          color: "#4caf50",
          border: "1px solid #4caf50",
          boxShadow: "0 0 20px rgba(76, 175, 80, 0.3)",
        },
        filledWarning: {
          backgroundColor: "rgba(10, 0, 0, 0.9)",
          color: "#ff9800",
          border: "1px solid #ff9800",
          boxShadow: "0 0 20px rgba(255, 152, 0, 0.3)",
        },
        filledError: {
          backgroundColor: "rgba(10, 0, 0, 0.9)",
          color: "#d32f2f",
          border: "1px solid #d32f2f",
          boxShadow: "0 0 20px rgba(211, 47, 47, 0.3)",
        },
      },
    },
  },
});

// ─── Guaardvark: Ultra-minimal monochrome matching guaardvark.com ────────────

const guaardvarkTheme = createFullTheme({
  accent: "#8a9bae",
  accentDark: "#6b7d91",
  accentLight: "#a8b5c4",
  secondary: "#9e9e9e",
  secondaryDark: "#757575",
  secondaryLight: "#bdbdbd",
  bg: "#000000",
  bgPaper: "#080a0e",
  textPrimary: "rgba(255, 255, 255, 0.7)",
  textSecondary: "rgba(255, 255, 255, 0.45)",
  divider: "rgba(138, 155, 174, 0.15)",
  fontFamily: '"Lato", sans-serif',
  componentOverrides: {
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "rgba(255, 255, 255, 0.03)",
          border: "1px solid rgba(138, 155, 174, 0.15)",
          backdropFilter: "blur(12px)",
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          backgroundImage: "none",
          backgroundColor: "rgba(255, 255, 255, 0.03)",
          border: "1px solid rgba(138, 155, 174, 0.15)",
          backdropFilter: "blur(12px)",
          borderRadius: "8px",
          "&:hover": {
            borderColor: "rgba(138, 155, 174, 0.25)",
          },
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: {
          fontFamily: '"Raleway", sans-serif',
          textTransform: "uppercase",
          fontWeight: 400,
          letterSpacing: "2px",
          borderRadius: "4px",
        },
        contained: {
          backgroundColor: "rgba(255, 255, 255, 0.05)",
          color: "rgba(255, 255, 255, 0.7)",
          border: "1px solid rgba(138, 155, 174, 0.3)",
          "&:hover": {
            backgroundColor: "rgba(138, 155, 174, 0.15)",
            boxShadow: "0 0 12px rgba(138, 155, 174, 0.2)",
          },
        },
        outlined: {
          borderColor: "rgba(255, 255, 255, 0.1)",
          color: "rgba(255, 255, 255, 0.6)",
          "&:hover": {
            borderColor: "rgba(138, 155, 174, 0.4)",
            backgroundColor: "rgba(138, 155, 174, 0.08)",
          },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: "rgba(0, 0, 0, 0.8)",
          backgroundImage: "none",
          backdropFilter: "blur(12px)",
          borderBottom: "1px solid rgba(255, 255, 255, 0.06)",
        },
      },
    },
    MuiDrawer: {
      styleOverrides: {
        paper: {
          backgroundImage: "none",
          backgroundColor: "rgba(0, 0, 0, 0.9)",
          backdropFilter: "blur(12px)",
          borderRight: "1px solid rgba(255, 255, 255, 0.06)",
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          backgroundColor: "rgba(8, 10, 14, 0.92)",
          backdropFilter: "blur(20px)",
          border: "1px solid rgba(138, 155, 174, 0.2)",
          borderRadius: "12px",
          backgroundImage: "none",
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          fontFamily: '"Raleway", sans-serif',
          fontWeight: 400,
          letterSpacing: "1px",
          color: "rgba(255, 255, 255, 0.8)",
        },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: {
          color: "rgba(255, 255, 255, 0.65)",
        },
        dividers: {
          borderColor: "rgba(138, 155, 174, 0.15)",
        },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: {
          borderTop: "1px solid rgba(138, 155, 174, 0.1)",
          padding: "12px 24px",
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        root: {
          backgroundColor: "rgba(8, 10, 14, 0.85)",
          backdropFilter: "blur(12px)",
          border: "1px solid rgba(138, 155, 174, 0.25)",
          borderRadius: "8px",
          color: "rgba(255, 255, 255, 0.8)",
          fontFamily: '"Lato", sans-serif',
        },
        outlined: {
          backgroundColor: "rgba(8, 10, 14, 0.6)",
          backdropFilter: "blur(8px)",
        },
        outlinedInfo: {
          borderColor: "rgba(138, 155, 174, 0.3)",
        },
        outlinedSuccess: {
          borderColor: "rgba(76, 175, 80, 0.4)",
        },
        outlinedWarning: {
          borderColor: "rgba(255, 152, 0, 0.4)",
        },
        outlinedError: {
          borderColor: "rgba(244, 67, 54, 0.4)",
        },
        filled: {
          backgroundColor: "rgba(8, 10, 14, 0.85)",
          backdropFilter: "blur(12px)",
        },
        filledInfo: {
          backgroundColor: "rgba(8, 10, 14, 0.85)",
          color: "rgba(255, 255, 255, 0.8)",
          border: "1px solid rgba(138, 155, 174, 0.3)",
          backdropFilter: "blur(12px)",
        },
        filledSuccess: {
          backgroundColor: "rgba(8, 10, 14, 0.85)",
          color: "rgba(255, 255, 255, 0.8)",
          border: "1px solid rgba(76, 175, 80, 0.4)",
          backdropFilter: "blur(12px)",
        },
        filledWarning: {
          backgroundColor: "rgba(8, 10, 14, 0.85)",
          color: "rgba(255, 255, 255, 0.8)",
          border: "1px solid rgba(255, 152, 0, 0.4)",
          backdropFilter: "blur(12px)",
        },
        filledError: {
          backgroundColor: "rgba(8, 10, 14, 0.85)",
          color: "rgba(255, 255, 255, 0.8)",
          border: "1px solid rgba(244, 67, 54, 0.4)",
          backdropFilter: "blur(12px)",
        },
        standardInfo: {
          backgroundColor: "rgba(8, 10, 14, 0.6)",
          border: "1px solid rgba(138, 155, 174, 0.2)",
        },
        standardSuccess: {
          backgroundColor: "rgba(8, 10, 14, 0.6)",
          border: "1px solid rgba(76, 175, 80, 0.3)",
        },
        standardWarning: {
          backgroundColor: "rgba(8, 10, 14, 0.6)",
          border: "1px solid rgba(255, 152, 0, 0.3)",
        },
        standardError: {
          backgroundColor: "rgba(8, 10, 14, 0.6)",
          border: "1px solid rgba(244, 67, 54, 0.3)",
        },
      },
    },
    MuiSnackbarContent: {
      styleOverrides: {
        root: {
          backgroundColor: "rgba(8, 10, 14, 0.9)",
          backdropFilter: "blur(12px)",
          border: "1px solid rgba(138, 155, 174, 0.2)",
          borderRadius: "8px",
          color: "rgba(255, 255, 255, 0.8)",
        },
      },
    },
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          scrollbarColor: "rgba(255, 255, 255, 0.08) transparent",
          "&::-webkit-scrollbar": { width: 6, height: 6 },
          "&::-webkit-scrollbar-thumb": {
            backgroundColor: "rgba(255, 255, 255, 0.08)",
            borderRadius: 3,
          },
          "&::-webkit-scrollbar-track": {
            backgroundColor: "transparent",
          },
        },
      },
    },
  },
});

// Override typography for Raleway headings
["h1", "h2", "h3", "h4", "h5", "h6"].forEach((variant) => {
  guaardvarkTheme.typography[variant] = {
    ...guaardvarkTheme.typography[variant],
    fontFamily: '"Raleway", sans-serif',
    fontWeight: 300,
    letterSpacing: "3px",
    textTransform: "uppercase",
  };
});
guaardvarkTheme.typography.subtitle1 = {
  ...guaardvarkTheme.typography.subtitle1,
  fontFamily: '"Raleway", sans-serif',
  fontWeight: 400,
  letterSpacing: "1px",
};
guaardvarkTheme.typography.subtitle2 = {
  ...guaardvarkTheme.typography.subtitle2,
  fontFamily: '"Raleway", sans-serif',
  fontWeight: 400,
  letterSpacing: "1px",
};

// ─── Exported themes map ─────────────────────────────────────────────────────
// Shape: { [key]: { label, description, previewGradient, theme } }
// Used by App.jsx, ThemeSelectorModal, SettingsPage.

export const themes = {
  guaardvark: {
    label: "Guaardvark",
    description: "Ultra-minimal monochrome theme inspired by guaardvark.com",
    previewGradient: "linear-gradient(135deg, #8a9bae, #000000)",
    theme: guaardvarkTheme,
  },
  default: {
    label: "Dark Gray",
    description: "Clean dark theme with teal accents",
    previewGradient: "linear-gradient(135deg, #008080, #006666)",
    theme: defaultTheme,
  },
  musk: {
    label: "Elon's Musk",
    description: "Futuristic dark theme with neon cyan and red accents",
    previewGradient: "linear-gradient(45deg, #00e5ff, #ff1744)",
    icon: "cologne",
    theme: muskTheme,
  },
  hacker: {
    label: "Fallout",
    description: "Pip-Boy inspired wasteland terminal with radiation green and amber",
    previewGradient: "linear-gradient(135deg, #18ff6d, #0a0e0a, #f0c040)",
    icon: "radioactive",
    theme: hackerTheme,
  },
  vader: {
    label: "Vader",
    description: "Dark imposing theme with black and red accents inspired by Darth Vader",
    previewGradient: "linear-gradient(135deg, #d32f2f, #000000)",
    icon: "sith",
    theme: vaderTheme,
  },
};
