/**
 * ThinkingStepCard - Inline collapsible card for one agent see-think-act step.
 * Mirrors ToolCallCard's visual idiom (red accent + chevron) but renders
 * iteration / label / reasoning instead of tool params + result.
 */
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Collapse, IconButton } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import PsychologyIcon from "@mui/icons-material/Psychology";

const ThinkingStepCard = ({ iteration, label, reasoning }) => {
  const [expanded, setExpanded] = useState(false);

  const trimmedReasoning = (reasoning || "").trim();
  const previewReasoning = trimmedReasoning.length > 80
    ? trimmedReasoning.slice(0, 80) + "..."
    : trimmedReasoning;

  return (
    <Box
      sx={{
        my: 0.5,
        borderLeft: 3,
        borderColor: "error.main",
        borderRadius: 1,
        bgcolor: "action.hover",
        overflow: "hidden",
        opacity: 0.95,
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
        <PsychologyIcon sx={{ fontSize: 14, color: "error.main" }} />

        <Typography
          variant="caption"
          sx={{
            fontWeight: 600,
            fontFamily: "monospace",
            color: "text.primary",
          }}
        >
          Step {iteration}{label ? ` — ${label}` : ""}
        </Typography>

        {previewReasoning && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{
              flex: 1,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontStyle: "italic",
              fontSize: "0.65rem",
            }}
          >
            {previewReasoning}
          </Typography>
        )}

        <IconButton size="small" sx={{ p: 0 }}>
          {expanded ? (
            <ExpandLessIcon sx={{ fontSize: 16, color: "common.white" }} />
          ) : (
            <ExpandMoreIcon sx={{ fontSize: 16, color: "common.white" }} />
          )}
        </IconButton>
      </Box>

      {/* Expanded reasoning */}
      <Collapse in={expanded}>
        <Box sx={{ px: 1.5, pb: 1, fontSize: "0.7rem" }}>
          <Typography
            variant="caption"
            sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
          >
            Reasoning:
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
              maxHeight: 200,
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {trimmedReasoning || "(no reasoning recorded)"}
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
};

ThinkingStepCard.propTypes = {
  iteration: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  label: PropTypes.string,
  reasoning: PropTypes.string,
};

export default ThinkingStepCard;
