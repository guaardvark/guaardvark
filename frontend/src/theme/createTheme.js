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
    mode = "dark",
    fontFamily = '"Inter", "Roboto", "Helvetica", "Arial", sans-serif',
    componentOverrides = {},
    // Semantic hues. A theme that leaves these unset keeps MUI's stock
    // green/amber/red, which reads as generic against a tuned background.
    success,
    warning,
    error,
    info,
    // Route prefix -> colour, so each area of the app can carry its own hue.
    // Themes that omit it simply get no accent rule.
    moduleAccents = {},
    // Px of page left showing round each card on an edge-to-edge (TILED)
    // board, so the cards read as separate; 0 tiles them flush.
    boardCardInset = 0,
  } = config;

  const dividerColor = divider || (mode === "light" ? "rgba(0, 0, 0, 0.08)" : "rgba(255, 255, 255, 0.08)");
  const successColor = success || "#4caf50";
  const warningColor = warning || "#ff9800";
  const errorColor = error || "#f44336";
  const infoColor = info || accent;

  const base = muiCreateTheme({
    board: { cardInset: boardCardInset },
    palette: {
      mode,
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
      success: { main: successColor },
      warning: { main: warningColor },
      error: { main: errorColor },
      info: { main: infoColor },
      divider: dividerColor,
      moduleAccents,
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
            // Firefox only: in Chromium a scrollbar-color replaces the
            // ::-webkit-scrollbar rules and its hover thumb turns near-black.
            "@supports not selector(::-webkit-scrollbar)": {
              scrollbarColor: `${dividerColor} transparent`,
            },
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
          root: {
            variants: [
              {
                props: { variant: "filled", colorSeverity: "info" },
                style: {
                  backgroundColor: bgPaper,
                  color: textPrimary,
                  border: `1px solid ${infoColor}`,
                },
              },
              {
                props: { variant: "filled", colorSeverity: "success" },
                style: {
                  backgroundColor: bgPaper,
                  color: textPrimary,
                  border: `1px solid ${successColor}`,
                },
              },
              {
                props: { variant: "filled", colorSeverity: "warning" },
                style: {
                  backgroundColor: bgPaper,
                  color: textPrimary,
                  border: `1px solid ${warningColor}`,
                },
              },
              {
                props: { variant: "filled", colorSeverity: "error" },
                style: {
                  backgroundColor: bgPaper,
                  color: textPrimary,
                  border: `1px solid ${errorColor}`,
                },
              },
            ],
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
      // The summary row became a <button> in MUI 6, and a button does not
      // inherit its font from the page the way the old <div> did.
      MuiAccordionSummary: {
        styleOverrides: {
          root: {
            font: "inherit",
            letterSpacing: "inherit",
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
      // MUI 9 narrowed the icon column of a list row to 36px; lists here were
      // laid out for 56px. Menus set their own 36px and are unaffected.
      MuiListItemIcon: {
        styleOverrides: {
          root: {
            minWidth: 56,
          },
        },
      },
      // Merge any per-theme overrides
      ...componentOverrides,
    },
  });

  return base;
}
