/**
 * AgentThinkingTrail — single collapsible holding the full agent see-think-act
 * reasoning trail for a message. Replaces the previous per-step card fanout so
 * the chat thread isn't dominated by N stacked accordions for long agent runs.
 */
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Collapse, IconButton } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import PsychologyIcon from "@mui/icons-material/Psychology";

const AgentThinkingTrail = ({ steps }) => {
  const [expanded, setExpanded] = useState(false);

  if (!steps || steps.length === 0) return null;

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
            flex: 1,
          }}
        >
          Agent thinking — {steps.length} step{steps.length === 1 ? "" : "s"}
        </Typography>
        <IconButton size="small" sx={{ p: 0 }}>
          {expanded ? (
            <ExpandLessIcon sx={{ fontSize: 16, color: "common.white" }} />
          ) : (
            <ExpandMoreIcon sx={{ fontSize: 16, color: "common.white" }} />
          )}
        </IconButton>
      </Box>

      <Collapse in={expanded}>
        <Box sx={{ px: 1.5, pb: 1, pt: 0.25 }}>
          {steps.map((step, idx) => {
            const reasoning = (step.reasoning || "").trim();
            return (
              <Box
                key={`thinking-step-${idx}`}
                sx={{
                  mb: idx === steps.length - 1 ? 0 : 1,
                  pt: idx === 0 ? 0 : 0.5,
                  borderTop: idx === 0 ? 0 : 1,
                  borderColor: "divider",
                }}
              >
                <Typography
                  variant="caption"
                  sx={{
                    fontWeight: 600,
                    fontFamily: "monospace",
                    fontSize: "0.65rem",
                    color: "text.primary",
                    display: "block",
                    mb: 0.25,
                  }}
                >
                  Step {step.iteration ?? idx + 1}
                  {step.label ? ` — ${step.label}` : ""}
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
                  {reasoning || "(no reasoning recorded)"}
                </Box>
              </Box>
            );
          })}
        </Box>
      </Collapse>
    </Box>
  );
};

AgentThinkingTrail.propTypes = {
  steps: PropTypes.arrayOf(
    PropTypes.shape({
      iteration: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
      label: PropTypes.string,
      reasoning: PropTypes.string,
    })
  ),
};

export default AgentThinkingTrail;
