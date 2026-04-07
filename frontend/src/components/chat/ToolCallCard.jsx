/**
 * ToolCallCard - Inline collapsible card for a single tool call + result.
 * Displayed within a message bubble during unified chat streaming.
 */
import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  Collapse,
  IconButton,
  Chip,
  CircularProgress,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import BuildIcon from "@mui/icons-material/Build";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import HourglassEmptyIcon from "@mui/icons-material/HourglassEmpty";
import ThumbUpIcon from "@mui/icons-material/ThumbUp";
import ThumbDownIcon from "@mui/icons-material/ThumbDown";
import ThumbUpOutlinedIcon from "@mui/icons-material/ThumbUpOutlined";
import ThumbDownOutlinedIcon from "@mui/icons-material/ThumbDownOutlined";
import Tooltip from "@mui/material/Tooltip";
import { BASE_URL } from "../../api/apiClient";

// Tools that get thumbs up/down feedback — agent actions the user can judge
const FEEDBACK_TOOLS = new Set(["agent_task_execute", "agent_screen_capture"]);

const ToolCallCard = ({
  toolName,
  params,
  result,
  durationMs,
  isPending,
  sessionId,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState(null); // null | "up" | "down"

  const showFeedback = FEEDBACK_TOOLS.has(toolName) && result && !isPending;

  const handleFeedback = async (positive) => {
    const newFeedback = positive ? "up" : "down";
    // Toggle off if same button clicked again
    if (feedback === newFeedback) {
      setFeedback(null);
      return;
    }
    setFeedback(newFeedback);
    try {
      await fetch(`${BASE_URL}/agent-control/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          positive,
          task: params?.task || toolName,
          session_id: sessionId || null,
          steps: result?.metadata?.steps || null,
          time_seconds: result?.metadata?.time_seconds || (durationMs ? durationMs / 1000 : null),
          model: "",
        }),
      });
    } catch (err) {
      console.error("Feedback submit failed:", err);
    }
  };

  const isSuccess = result?.success;
  const isError = result && !result.success;
  const borderColor = isPending
    ? "warning.main"
    : isSuccess
    ? "success.main"
    : isError
    ? "error.main"
    : "grey.500";

  // Summarize params for collapsed view
  const paramSummary = params
    ? Object.entries(params)
        .map(([k, v]) => {
          const val = typeof v === "string" ? v : JSON.stringify(v);
          return `${k}=${val.length > 30 ? val.slice(0, 30) + "..." : val}`;
        })
        .join(", ")
    : "";

  return (
    <Box
      sx={{
        my: 0.5,
        borderLeft: 3,
        borderColor,
        borderRadius: 1,
        bgcolor: "action.hover",
        overflow: "hidden",
      }}
    >
      {/* Collapsed header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 0.5,
          px: 1,
          py: 0.5,
          cursor: "pointer",
          "&:hover": { bgcolor: "action.selected" },
        }}
        onClick={() => setExpanded((prev) => !prev)}
      >
        {isPending ? (
          <CircularProgress size={14} color="warning" />
        ) : (
          <BuildIcon sx={{ fontSize: 14, color: borderColor }} />
        )}

        <Typography
          variant="caption"
          sx={{ fontWeight: 600, fontFamily: "monospace" }}
        >
          {toolName}
        </Typography>

        {paramSummary && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              flex: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontFamily: "monospace",
              fontSize: "0.65rem",
            }}
          >
            ({paramSummary})
          </Typography>
        )}

        {durationMs != null && (
          <Chip
            label={`${durationMs}ms`}
            size="small"
            variant="outlined"
            sx={{ height: 18, fontSize: "0.6rem" }}
          />
        )}

        {!isPending && (
          isSuccess ? (
            <CheckCircleIcon sx={{ fontSize: 14, color: "success.main" }} />
          ) : isError ? (
            <ErrorIcon sx={{ fontSize: 14, color: "error.main" }} />
          ) : (
            <HourglassEmptyIcon sx={{ fontSize: 14, color: "grey.500" }} />
          )
        )}

        <IconButton size="small" sx={{ p: 0 }}>
          {expanded ? (
            <ExpandLessIcon sx={{ fontSize: 16 }} />
          ) : (
            <ExpandMoreIcon sx={{ fontSize: 16 }} />
          )}
        </IconButton>
      </Box>

      {/* Expanded details */}
      <Collapse in={expanded}>
        <Box sx={{ px: 1.5, pb: 1, fontSize: "0.7rem" }}>
          {/* Parameters */}
          {params && Object.keys(params).length > 0 && (
            <Box sx={{ mb: 0.5 }}>
              <Typography
                variant="caption"
                sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
              >
                Parameters:
              </Typography>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 0.5,
                  bgcolor: "background.default",
                  borderRadius: 0.5,
                  fontSize: "0.65rem",
                  fontFamily: "monospace",
                  overflow: "auto",
                  maxHeight: 120,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {JSON.stringify(params, null, 2)}
              </Box>
            </Box>
          )}

          {/* Result */}
          {result && (
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
              >
                Result:
              </Typography>
              <Box
                component="pre"
                sx={{
                  m: 0,
                  p: 0.5,
                  bgcolor: isSuccess
                    ? "success.main"
                    : "error.main",
                  color: "white",
                  borderRadius: 0.5,
                  fontSize: "0.65rem",
                  fontFamily: "monospace",
                  overflow: "auto",
                  maxHeight: 200,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  opacity: 0.9,
                }}
              >
                {isSuccess
                  ? result.output || "Success (no output)"
                  : result.error || "Unknown error"}
              </Box>
            </Box>
          )}

          {/* Thumbs up/down feedback for agent tasks */}
          {showFeedback && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.5, justifyContent: "flex-end" }}>
              <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
                Did this work?
              </Typography>
              <Tooltip title="Yes, it worked">
                <IconButton
                  size="small"
                  onClick={() => handleFeedback(true)}
                  sx={{ p: 0.25 }}
                >
                  {feedback === "up" ? (
                    <ThumbUpIcon sx={{ fontSize: 16, color: "success.main" }} />
                  ) : (
                    <ThumbUpOutlinedIcon sx={{ fontSize: 16, opacity: 0.5 }} />
                  )}
                </IconButton>
              </Tooltip>
              <Tooltip title="No, it missed">
                <IconButton
                  size="small"
                  onClick={() => handleFeedback(false)}
                  sx={{ p: 0.25 }}
                >
                  {feedback === "down" ? (
                    <ThumbDownIcon sx={{ fontSize: 16, color: "error.main" }} />
                  ) : (
                    <ThumbDownOutlinedIcon sx={{ fontSize: 16, opacity: 0.5 }} />
                  )}
                </IconButton>
              </Tooltip>
            </Box>
          )}
        </Box>
      </Collapse>
    </Box>
  );
};

ToolCallCard.propTypes = {
  toolName: PropTypes.string.isRequired,
  params: PropTypes.object,
  result: PropTypes.shape({
    success: PropTypes.bool,
    output: PropTypes.string,
    error: PropTypes.string,
  }),
  durationMs: PropTypes.number,
  isPending: PropTypes.bool,
  sessionId: PropTypes.string,
};

export default ToolCallCard;
