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
  Button,
  ButtonGroup,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
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
import SecurityIcon from "@mui/icons-material/Security";
import Tooltip from "@mui/material/Tooltip";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { BASE_URL } from "../../api/apiClient";

// Tools that get thumbs up/down feedback — agent actions the user can judge
const FEEDBACK_TOOLS = new Set(["agent_task_execute", "agent_screen_capture"]);

const CSVTable = ({ csvString }) => {
  if (!csvString || !csvString.includes(",")) return null;
  const lines = csvString.trim().split("\n");
  if (lines.length < 1) return null;
  
  const headers = lines[0].split(",").map(h => h.trim());
  const rows = lines.slice(1).map(line => line.split(",").map(c => c.trim()));
  
  return (
    <TableContainer component={Paper} variant="outlined" sx={{ my: 0.5, maxHeight: 200, overflow: "auto" }}>
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow sx={{ bgcolor: "action.hover" }}>
            {headers.map((h, i) => (
              <TableCell key={i} sx={{ py: 0.25, px: 0.5, fontSize: "0.6rem", fontWeight: "bold" }}>{h}</TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {row.map((cell, j) => (
                <TableCell key={j} sx={{ py: 0.25, px: 0.5, fontSize: "0.6rem" }}>{cell}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
};

const ToolCallCard = ({
  toolName,
  params,
  result,
  durationMs,
  isPending,
  sessionId,
  outputChunks,
  requiresApproval,
  onApproval,
}) => {
  const [expanded, setExpanded] = useState(false);
  const [feedback, setFeedback] = useState(null); // null | "up" | "down"
  const [responded, setResponded] = useState(false);

  const showFeedback = FEEDBACK_TOOLS.has(toolName) && result && !isPending;

  const handleApproval = (approved) => {
    setResponded(true);
    if (onApproval) onApproval(approved);
  };

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
          tool_name: toolName,
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
  const borderColor = requiresApproval && !responded
    ? "error.main"
    : isPending
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
        bgcolor: requiresApproval && !responded ? "error.light" : "action.hover",
        overflow: "hidden",
        opacity: requiresApproval && !responded ? 1 : 0.9,
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
        {requiresApproval && !responded ? (
          <SecurityIcon sx={{ fontSize: 14, color: "error.main" }} />
        ) : isPending ? (
          <CircularProgress size={14} color="warning" />
        ) : (
          <BuildIcon sx={{ fontSize: 14, color: borderColor }} />
        )}

        <Typography
          variant="caption"
          sx={{ 
            fontWeight: 600, 
            fontFamily: "monospace",
            color: requiresApproval && !responded ? "error.main" : "text.primary"
          }}
        >
          {toolName} {requiresApproval && !responded && "(Needs Approval)"}
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

        {!isPending && !requiresApproval && (
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
      <Collapse in={expanded || (requiresApproval && !responded)}>
        <Box sx={{ px: 1.5, pb: 1, fontSize: "0.7rem" }}>
          {/* Approval UI */}
          {requiresApproval && !responded && (
            <Box sx={{ mb: 1, p: 1, bgcolor: "background.paper", borderRadius: 1, border: "1px solid", borderColor: "error.main" }}>
              <Typography variant="caption" sx={{ fontWeight: 600, color: "error.main", display: "block", mb: 1 }}>
                This action requires your approval. Do you want to proceed?
              </Typography>
              <ButtonGroup size="small" fullWidth variant="contained">
                <Button color="success" startIcon={<CheckCircleIcon />} onClick={() => handleApproval(true)}>
                  Approve
                </Button>
                <Button color="error" startIcon={<ErrorIcon />} onClick={() => handleApproval(false)}>
                  Reject
                </Button>
              </ButtonGroup>
            </Box>
          )}

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

          {/* Streaming/Final Result */}
          {(outputChunks || result) && (
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
              >
                {isPending ? "Output (streaming):" : "Result:"}
              </Typography>
              
              {/* Specialized Renderers */}
              {toolName.includes("execute_python") && (result?.output || outputChunks) ? (
                <SyntaxHighlighter
                  language="python"
                  style={a11yDark}
                  customStyle={{ fontSize: "0.6rem", margin: 0, borderRadius: 4 }}
                >
                  {result?.output || outputChunks}
                </SyntaxHighlighter>
              ) : toolName.includes("csv") && result?.output ? (
                <CSVTable csvString={result.output} />
              ) : (
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    p: 0.5,
                    bgcolor: isPending ? "action.disabledBackground" : isSuccess ? "success.main" : "error.main",
                    color: isPending ? "text.primary" : "white",
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
                  {isPending 
                    ? (outputChunks || "Waiting for output...")
                    : isSuccess
                    ? result.output || "Success (no output)"
                    : result.error || "Unknown error"}
                </Box>
              )}
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
  outputChunks: PropTypes.string,
  requiresApproval: PropTypes.bool,
  onApproval: PropTypes.func,
};

export default ToolCallCard;
