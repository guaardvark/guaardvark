/**
 * ThinkingCard - Collapsible card showing a model's reasoning stream.
 * Same visual language as ToolCallCard so thinking and tool calls read as
 * one sequence inside the message bubble.
 */
import React, { useState, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Collapse, IconButton, Chip, CircularProgress } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import PsychologyIcon from "@mui/icons-material/Psychology";

// Taller than ToolCallCard's 200px result block: reasoning runs longer than
// tool output and the extra rows keep the tail readable while it streams.
const BODY_MAX_HEIGHT = 240;

const formatElapsed = (ms) => `${(ms / 1000).toFixed(1)}s`;

const ThinkingCard = ({ text = "", streaming = false, elapsedMs = null, defaultExpanded = false }) => {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const bodyRef = useRef(null);

  // The parent flips defaultExpanded to collapse the card once the answer
  // starts; a manual toggle after that still wins until the next flip.
  useEffect(() => {
    setExpanded(defaultExpanded);
  }, [defaultExpanded]);

  useEffect(() => {
    if (streaming && expanded && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [text, streaming, expanded]);

  const borderColor = streaming ? "warning.main" : "grey.500";

  return (
    <Box
      data-testid="thinking-card"
      sx={{
        my: 0.5,
        borderLeft: 3,
        borderColor,
        borderRadius: 1,
        bgcolor: "action.hover",
        overflow: "hidden",
        opacity: 0.9,
      }}
    >
      <Box
        role="button"
        aria-expanded={expanded}
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
        {streaming ? (
          <CircularProgress size={14} color="warning" data-testid="thinking-spinner" />
        ) : (
          <PsychologyIcon sx={{ fontSize: 14, color: borderColor }} />
        )}

        <Typography
          variant="caption"
          sx={{ fontWeight: 600, fontFamily: "monospace", color: "text.primary" }}
        >
          thinking
        </Typography>

        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ flex: 1, fontSize: "0.65rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {streaming ? "Thinking…" : "Reasoning"}
        </Typography>

        {!streaming && elapsedMs != null && (
          <Chip
            label={formatElapsed(elapsedMs)}
            size="small"
            variant="outlined"
            sx={{ height: 18, fontSize: "0.6rem" }}
          />
        )}

        <IconButton size="small" sx={{ p: 0 }} aria-label={expanded ? "Collapse thinking" : "Expand thinking"}>
          {expanded ? (
            <ExpandLessIcon sx={{ fontSize: 16 }} />
          ) : (
            <ExpandMoreIcon sx={{ fontSize: 16 }} />
          )}
        </IconButton>
      </Box>

      <Collapse in={expanded}>
        <Box sx={{ px: 1.5, pb: 1 }}>
          <Box
            ref={bodyRef}
            component="pre"
            data-testid="thinking-body"
            sx={{
              m: 0,
              p: 0.5,
              bgcolor: "background.default",
              color: "text.secondary",
              borderRadius: 0.5,
              fontSize: "0.65rem",
              fontFamily: "monospace",
              overflow: "auto",
              maxHeight: BODY_MAX_HEIGHT,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {text || (streaming ? "" : "(no reasoning recorded)")}
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
};

ThinkingCard.propTypes = {
  text: PropTypes.string,
  streaming: PropTypes.bool,
  elapsedMs: PropTypes.number,
  defaultExpanded: PropTypes.bool,
};

export default ThinkingCard;
