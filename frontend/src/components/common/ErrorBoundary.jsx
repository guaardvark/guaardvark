import React from "react";
import {
  Box,
  Paper,
  Typography,
  Button,
  Stack,
  Alert,
  Divider
} from "@mui/material";
import { RefreshOutlined } from "@mui/icons-material";
import { BrandLogo } from "../branding";
import { isStaleModuleError, reloadOnceForStaleModule } from "../../utils/lazyWithReload";
import { translate, useUiTranslation } from "../../i18n";

// The outermost boundary sits outside the MUI theme, whose defaults are light.
// These are the variables AppContainer publishes from the active theme; the
// fallbacks are index.css's dark defaults.
const PAGE_COLOURS = {
  page: "var(--bg-default, #121212)",
  card: "var(--bg-paper, #1e1e1e)",
  text: "var(--text-primary, #e0e0e0)",
  muted: "var(--text-secondary, #a0a0a0)",
};

/** Default resetKey for every boundary below its provider (AppLayout provides the route). */
export const ErrorResetContext = React.createContext(undefined);

class ErrorBoundaryCore extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null, resetKey: props.resetKey };
  }

  static getDerivedStateFromProps(props, state) {
    if (props.resetKey !== state.resetKey) {
      return { hasError: false, error: null, errorInfo: null, resetKey: props.resetKey };
    }
    return null;
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    // Log error details
    console.error("Error Boundary caught an error:", error, errorInfo);

    // Code replaced on disk under this tab: one reload loads the new version.
    reloadOnceForStaleModule(error);

    this.setState({ errorInfo });

    // Optional: Send error to monitoring service
    // reportError(error, errorInfo);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    // Scroll to top after reset
    window.scrollTo(0, 0);
  };

  render() {
    if (this.state.hasError) {
      const { fullPage } = this.props;
      // React.lazy keeps a failed import's rejection, so "Try Again" cannot
      // recover it; only a reload fetches the new code.
      const stale = isStaleModuleError(this.state.error);
      return (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            flexGrow: 1,
            minHeight: fullPage ? "100vh" : "100%",
            overflow: "auto",
            p: 4,
            backgroundColor: fullPage ? PAGE_COLOURS.page : "background.default",
          }}
        >
          <Paper
            elevation={3}
            sx={{
              p: 4,
              maxWidth: 600,
              width: "100%",
              textAlign: "center",
              ...(fullPage && { backgroundColor: PAGE_COLOURS.card, color: PAGE_COLOURS.text }),
            }}
          >
            <Box sx={{ mb: 2 }}>
              <BrandLogo size={64} variant="error" />
            </Box>

            <Typography variant="h4" gutterBottom color="error">
              {stale ? translate("layoutCommon.errorBoundary.updatedTitle") : translate("layoutCommon.errorBoundary.errorTitle")}
            </Typography>

            <Typography
              variant="body1"
              paragraph
              sx={{ color: fullPage ? PAGE_COLOURS.muted : "text.secondary" }}
            >
              {stale
                ? translate("layoutCommon.errorBoundary.updatedBody")
                : translate("layoutCommon.errorBoundary.errorBody")}
            </Typography>

            <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
              <Button
                variant="contained"
                startIcon={<RefreshOutlined />}
                onClick={this.handleReload}
              >
                {translate("layoutCommon.errorBoundary.reloadPage")}
              </Button>
              {!stale && (
                <Button
                  variant="outlined"
                  onClick={this.handleReset}
                >
                  {translate("layoutCommon.errorBoundary.tryAgain")}
                </Button>
              )}
            </Stack>

            {process.env.NODE_ENV === "development" && this.state.error && (
              <>
                <Divider sx={{ my: 2 }} />
                <Alert severity="error" sx={{ textAlign: "left" }}>
                  <Typography variant="subtitle2" gutterBottom>
                    {translate("layoutCommon.errorBoundary.detailsHeading")}:
                  </Typography>
                  <Typography variant="body2" component="pre" sx={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontSize: "0.75rem"
                  }}>
                    {this.state.error.toString()}
                    {this.state.errorInfo?.componentStack}
                  </Typography>
                </Alert>
              </>
            )}
          </Paper>
        </Box>
      );
    }

    return this.props.children;
  }
}

/**
 * Catches a render error below it and shows the recovery card.
 *
 * @param {object}    props
 * @param {ReactNode} props.children
 * @param {unknown}   [props.resetKey]  a shown error clears when this changes; defaults to ErrorResetContext
 * @param {boolean}   [props.fullPage]  for the boundary outside the theme provider
 */
const ErrorBoundary = ({ resetKey, ...props }) => {
  // The class below reads translate(); this re-renders it on a language change.
  useUiTranslation();
  const inheritedKey = React.useContext(ErrorResetContext);
  return <ErrorBoundaryCore {...props} resetKey={resetKey ?? inheritedKey} />;
};

export default ErrorBoundary;
