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
import { GuaardvarkLogo } from "../branding";

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(_error) {
    // Update state so the next render will show the fallback UI
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    // Log error details
    console.error("Error Boundary caught an error:", error, errorInfo);
    
    // Update state with error details
    this.setState({
      error: error,
      errorInfo: errorInfo
    });

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
      // Custom fallback UI
      return (
        <Box
          sx={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "100vh",
            p: 4,
            backgroundColor: "background.default"
          }}
        >
          <Paper
            elevation={3}
            sx={{
              p: 4,
              maxWidth: 600,
              width: "100%",
              textAlign: "center"
            }}
          >
            <Box sx={{ mb: 2 }}>
              <GuaardvarkLogo size={64} variant="error" />
            </Box>
            
            <Typography variant="h4" gutterBottom color="error">
              Something went wrong
            </Typography>
            
            <Typography variant="body1" color="text.secondary" paragraph>
              The application encountered an unexpected error. This has been logged for investigation.
            </Typography>

            <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
              <Button
                variant="contained"
                startIcon={<RefreshOutlined />}
                onClick={this.handleReload}
              >
                Reload Page
              </Button>
              <Button
                variant="outlined"
                onClick={this.handleReset}
              >
                Try Again
              </Button>
            </Stack>

            {process.env.NODE_ENV === "development" && this.state.error && (
              <>
                <Divider sx={{ my: 2 }} />
                <Alert severity="error" sx={{ textAlign: "left" }}>
                  <Typography variant="subtitle2" gutterBottom>
                    Error Details (Development Mode Only):
                  </Typography>
                  <Typography variant="body2" component="pre" sx={{ 
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontSize: "0.75rem"
                  }}>
                    {this.state.error.toString()}
                    {this.state.errorInfo.componentStack}
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

export default ErrorBoundary; 