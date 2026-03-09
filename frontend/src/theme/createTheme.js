import { createTheme as muiCreateTheme } from "@mui/material/styles";
import { borderRadius } from "./tokens";

/**
 * Creates a fully-specified MUI theme from a config object.
 * All themes share consistent component overrides; only colors/fonts differ.
 */
export function createFullTheme(config) {
  const {
    accent,
    accentDark,
    accentLight,
    secondary,
    secondaryDark,
    secondaryLight,
    bg,
    bgPaper,
    textPrimary = "#e0e0e0",
    textSecondary = "#a0a0a0",
    divider,
    fontFamily = '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    componentOverrides = {},
  } = config;

  const dividerColor = divider || "rgba(255, 255, 255, 0.08)";

  const base = muiCreateTheme({
    palette: {
      mode: "dark",
      primary: {
        main: accent,
        dark: accentDark || accent,
        light: accentLight || accent,
      },
      secondary: {
        main: secondary || "#9e9e9e",
        dark: secondaryDark || secondary || "#757575",
        light: secondaryLight || secondary || "#bdbdbd",
      },
      background: {
        default: bg,
        paper: bgPaper,
      },
      text: {
        primary: textPrimary,
        secondary: textSecondary,
      },
      divider: dividerColor,
    },
    typography: {
      fontFamily,
      fontSize: 14,
      h1: { fontSize: "2rem", fontWeight: 700 },
      h2: { fontSize: "1.6rem", fontWeight: 600 },
      h3: { fontSize: "1.35rem", fontWeight: 600 },
      h4: { fontSize: "1.2rem", fontWeight: 600 },
      h5: { fontSize: "1.1rem", fontWeight: 600 },
      h6: { fontSize: "1rem", fontWeight: 600 },
      subtitle1: { fontSize: "0.95rem", fontWeight: 500 },
      subtitle2: { fontSize: "0.85rem", fontWeight: 500 },
      body1: { fontSize: "0.875rem" },
      body2: { fontSize: "0.825rem" },
      caption: { fontSize: "0.75rem" },
      button: { fontSize: "0.825rem", fontWeight: 500 },
    },
    shape: {
      borderRadius: 8,
    },
    components: {
      MuiCssBaseline: {
        styleOverrides: {
          body: {
            scrollbarColor: `${dividerColor} transparent`,
            "&::-webkit-scrollbar": { width: 8, height: 8 },
            "&::-webkit-scrollbar-thumb": {
              backgroundColor: dividerColor,
              borderRadius: 4,
            },
            "&::-webkit-scrollbar-track": {
              backgroundColor: "transparent",
            },
          },
        },
      },
      MuiButton: {
        defaultProps: {
          disableElevation: true,
        },
        styleOverrides: {
          root: {
            textTransform: "none",
            borderRadius: borderRadius.button,
            fontWeight: 500,
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
          },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
            border: `1px solid ${dividerColor}`,
            borderRadius: borderRadius.card,
          },
        },
      },
      MuiAlert: {
        styleOverrides: {
          filledInfo: {
            backgroundColor: bgPaper,
            color: textPrimary,
            border: `1px solid ${accent}`,
          },
          filledSuccess: {
            backgroundColor: bgPaper,
            color: textPrimary,
            border: "1px solid #4caf50",
          },
          filledWarning: {
            backgroundColor: bgPaper,
            color: textPrimary,
            border: "1px solid #ff9800",
          },
          filledError: {
            backgroundColor: bgPaper,
            color: textPrimary,
            border: "1px solid #f44336",
          },
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: {
            backgroundColor: bgPaper,
            backgroundImage: "none",
          },
        },
      },
      MuiDrawer: {
        styleOverrides: {
          paper: {
            backgroundImage: "none",
          },
        },
      },
      MuiTextField: {
        styleOverrides: {
          root: {
            "& .MuiOutlinedInput-root": {
              borderRadius: borderRadius.input,
            },
          },
        },
      },
      MuiChip: {
        styleOverrides: {
          root: {
            borderRadius: borderRadius.chip,
          },
        },
      },
      MuiTableCell: {
        styleOverrides: {
          root: {
            borderColor: dividerColor,
          },
        },
      },
      MuiAccordion: {
        styleOverrides: {
          root: {
            backgroundImage: "none",
            "&:before": { display: "none" },
          },
        },
      },
      MuiIconButton: {
        styleOverrides: {
          root: {
            borderRadius: borderRadius.button,
          },
        },
      },
      MuiTooltip: {
        defaultProps: {
          arrow: true,
        },
      },
      // Merge any per-theme overrides
      ...componentOverrides,
    },
  });

  return base;
}
