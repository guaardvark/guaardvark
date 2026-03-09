// frontend/src/components/FileGenPopup.jsx
// Version 2.0: Enhanced styling to match system theme with modern gradients and animations

import CancelIcon from "@mui/icons-material/Cancel";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import DescriptionIcon from "@mui/icons-material/Description";
import {
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Fade,
  Slide,
  Typography,
  useTheme,
} from "@mui/material";
import React from "react";

const FileGenPopup = ({ open, fileData, onConfirm, onDismiss, useRAG = false }) => {
  const theme = useTheme();

  if (!open || !fileData) return null;

  const { filename, description, isBulkRequest, quantity } = fileData;

  return (
    <Slide direction="up" in={open} timeout={400}>
      <Box
        sx={{
          position: "fixed",
          bottom: 24,
          right: 24,
          zIndex: 1500,
        }}
      >
        <Fade in={open} timeout={600}>
          <Card
            elevation={12}
            sx={{
              width: 380,
              background:
                theme.palette.mode === "dark"
                  ? `linear-gradient(145deg, ${theme.palette.background.paper}, ${theme.palette.background.default})`
                  : `linear-gradient(145deg, ${theme.palette.background.paper}, ${theme.palette.grey[50]})`,
              border: `1px solid ${theme.palette.primary.main}33`,
              borderRadius: 3,
              boxShadow:
                theme.palette.mode === "dark"
                  ? `0 16px 40px rgba(0, 0, 0, 0.4), 0 0 0 1px ${theme.palette.primary.main}22`
                  : `0 16px 40px rgba(0, 0, 0, 0.15), 0 0 0 1px ${theme.palette.primary.main}22`,
              backdropFilter: "blur(10px)",
              position: "relative",
              overflow: "hidden",
              "&::before": {
                content: '""',
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                height: "4px",
                background: `linear-gradient(90deg, ${theme.palette.primary.main}, ${theme.palette.secondary.main})`,
                zIndex: 1,
              },
              "&:hover": {
                transform: "translateY(-2px)",
                boxShadow:
                  theme.palette.mode === "dark"
                    ? `0 20px 50px rgba(0, 0, 0, 0.5), 0 0 0 1px ${theme.palette.primary.main}44`
                    : `0 20px 50px rgba(0, 0, 0, 0.2), 0 0 0 1px ${theme.palette.primary.main}44`,
              },
              transition: "all 0.3s cubic-bezier(0.4, 0, 0.2, 1)",
            }}
          >
            <CardContent sx={{ pt: 3, pb: 2 }}>
              <Box sx={{ display: "flex", alignItems: "center", mb: 2 }}>
                <Box
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: 48,
                    height: 48,
                    borderRadius: "50%",
                    background: `linear-gradient(135deg, ${theme.palette.primary.main}20, ${theme.palette.secondary.main}20)`,
                    border: `2px solid ${theme.palette.primary.main}40`,
                    mr: 2,
                  }}
                >
                  <DescriptionIcon
                    sx={{
                      fontSize: 24,
                      color: theme.palette.primary.main,
                      filter: `drop-shadow(0 0 8px ${theme.palette.primary.main}40)`,
                    }}
                  />
                </Box>
                <Box>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 600,
                      background: `linear-gradient(45deg, ${theme.palette.text.primary}, ${theme.palette.primary.main})`,
                      WebkitBackgroundClip: "text",
                      WebkitTextFillColor: "transparent",
                      backgroundClip: "text",
                      mb: 0.5,
                    }}
                  >
                    {isBulkRequest
                      ? "Bulk CSV Generation Request"
                      : "File Generation Request"}
                  </Typography>
                  <Typography
                    variant="caption"
                    sx={{
                      color: theme.palette.text.secondary,
                      opacity: 0.8,
                    }}
                  >
                    {isBulkRequest
                      ? `Ready to generate ${
                          quantity || "multiple"
                        } CSV entries`
                      : "Ready to generate your file"}
                  </Typography>
                </Box>
              </Box>

              <Box
                sx={{
                  p: 2,
                  borderRadius: 2,
                  background:
                    theme.palette.mode === "dark"
                      ? `linear-gradient(135deg, ${theme.palette.background.default}80, ${theme.palette.background.paper}40)`
                      : `linear-gradient(135deg, ${theme.palette.grey[50]}80, ${theme.palette.background.paper}40)`,
                  border: `1px solid ${theme.palette.divider}`,
                  mb: 2,
                }}
              >
                <Typography variant="body2" sx={{ mb: 1, fontWeight: 500 }}>
                  Proposed Filename:
                </Typography>
                <Typography
                  variant="body1"
                  sx={{
                    fontWeight: 700,
                    fontFamily: '"Roboto Mono", monospace',
                    color: theme.palette.primary.main,
                    background:
                      theme.palette.mode === "dark"
                        ? `${theme.palette.background.paper}60`
                        : `${theme.palette.grey[100]}60`,
                    padding: "8px 12px",
                    borderRadius: 1,
                    border: `1px solid ${theme.palette.primary.main}20`,
                    mb: 2,
                  }}
                >
                  {filename}
                </Typography>
                {isBulkRequest && quantity && (
                  <Typography
                    variant="body2"
                    sx={{
                      color: theme.palette.warning.main,
                      fontWeight: 600,
                      mb: 1,
                    }}
                  >
                    Quantity: {quantity} entries
                  </Typography>
                )}
                <Typography
                  variant="body2"
                  sx={{
                    color: theme.palette.text.secondary,
                    lineHeight: 1.5,
                    fontStyle: "italic",
                  }}
                >
                  {description || "No detailed description provided."}
                </Typography>
                {useRAG && (
                  <Typography
                    variant="caption"
                    sx={{
                      color: theme.palette.success.main,
                      fontWeight: 600,
                      mt: 1,
                      display: "block",
                      background: `${theme.palette.success.main}10`,
                      padding: "4px 8px",
                      borderRadius: 1,
                      border: `1px solid ${theme.palette.success.main}30`,
                    }}
                  >
                    🧠 RAG Context: Will use uploaded code files for reference
                  </Typography>
                )}
                {isBulkRequest && (
                  <Typography
                    variant="caption"
                    sx={{
                      color: theme.palette.info.main,
                      fontStyle: "italic",
                      mt: 1,
                      display: "block",
                    }}
                  >
                    This may take several minutes to complete
                  </Typography>
                )}
              </Box>
            </CardContent>

            <CardActions sx={{ px: 3, pb: 3, justifyContent: "space-between" }}>
              <Button
                size="medium"
                variant="outlined"
                startIcon={<CancelIcon />}
                onClick={onDismiss}
                sx={{
                  borderColor: theme.palette.error.main,
                  color: theme.palette.error.main,
                  borderRadius: 2,
                  px: 3,
                  "&:hover": {
                    borderColor: theme.palette.error.main,
                    backgroundColor: `${theme.palette.error.main}10`,
                    transform: "translateY(-1px)",
                  },
                  transition: "all 0.2s ease",
                }}
              >
                Dismiss
              </Button>
              <Button
                size="medium"
                variant="contained"
                startIcon={<CheckCircleIcon />}
                onClick={onConfirm}
                sx={{
                  background: `linear-gradient(45deg, ${theme.palette.primary.main}, ${theme.palette.secondary.main})`,
                  borderRadius: 2,
                  px: 3,
                  boxShadow: `0 4px 15px ${theme.palette.primary.main}40`,
                  "&:hover": {
                    background: `linear-gradient(45deg, ${theme.palette.primary.dark}, ${theme.palette.secondary.dark})`,
                    transform: "translateY(-1px)",
                    boxShadow: `0 6px 20px ${theme.palette.primary.main}60`,
                  },
                  transition: "all 0.2s ease",
                }}
              >
                Confirm
              </Button>
            </CardActions>
          </Card>
        </Fade>
      </Box>
    </Slide>
  );
};

export default FileGenPopup;
