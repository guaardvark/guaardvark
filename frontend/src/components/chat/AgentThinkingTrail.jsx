/**
 * AgentThinkingTrail — single collapsible holding the full agent see-think-act
 * reasoning trail for a message: one scrolling list of coloured step titles
 * and plain text, so a long agent run doesn't dominate the chat thread.
 */
import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Collapse } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import { useUiTranslation } from "../../i18n";
import useFollowTail from "../../hooks/useFollowTail";
import { STEP_COLORS, stepHeaderSx, stepTitleSx, stepBodySx, stepScrollSx } from "./stepText";

const AgentThinkingTrail = ({ steps, streaming = false }) => {
  const { t } = useUiTranslation();
  const [expanded, setExpanded] = useState(false);
  const count = steps ? steps.length : 0;
  const lastReasoning = count > 0 ? steps[count - 1].reasoning || "" : "";
  const follow = useFollowTail(streaming && expanded, `${count}:${lastReasoning.length}`);
  if (import.meta.env.DEV) {
    console.debug('[AgentThinkingTrail] RENDER steps.length=', count, ' (live from Streaming or persisted from MessageItem)');
  }

  if (count === 0) return null;
  const stepCount = steps.filter((s) => s.kind !== "note").length;
  const noteCount = steps.length - stepCount;
  const Chevron = expanded ? ExpandLessIcon : ExpandMoreIcon;

  return (
    <Box data-testid="agent-thinking-trail" sx={{ my: 0.5 }}>
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        sx={{ ...stepHeaderSx, color: STEP_COLORS.step }}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded((prev) => !prev);
          }
        }}
      >
        <Typography component="span" sx={stepTitleSx}>
          {noteCount > 0
            ? t("chat.thinkingTrail.summaryWithNotes", {
                steps: t("chat.thinkingTrail.steps", { count: stepCount }),
                notes: t("chat.thinkingTrail.notes", { count: noteCount }),
              })
            : t("chat.thinkingTrail.summary", {
                steps: t("chat.thinkingTrail.steps", { count: stepCount }),
              })}
        </Typography>
        <Chevron sx={{ fontSize: 16 }} />
      </Box>

      <Collapse in={expanded}>
        <Box
          ref={follow.ref}
          onScroll={follow.onScroll}
          data-testid="agent-thinking-trail-body"
          sx={stepScrollSx}
        >
          {steps.map((step, idx) => {
            const reasoning = (step.reasoning || "").trim();
            // A note the user sent mid-run: shown as theirs, where the agent read it.
            const isNote = step.kind === "note";
            return (
              <Box key={`thinking-step-${idx}`} sx={{ mb: idx === steps.length - 1 ? 0 : 1 }}>
                <Typography
                  component="div"
                  sx={{ ...stepTitleSx, color: isNote ? STEP_COLORS.note : STEP_COLORS.step }}
                >
                  {isNote
                    ? t("chat.thinkingTrail.noteBeforeStep", { step: step.iteration ?? idx + 1 })
                    : t("chat.thinkingTrail.step", { step: step.iteration ?? idx + 1 })}
                  {!isNote && step.label ? ` — ${step.label}` : ""}
                </Typography>
                <Box component="pre" sx={stepBodySx}>
                  {reasoning || t("chat.thinkingTrail.noReasoning")}
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
      kind: PropTypes.string,
    })
  ),
  // True while the run is still adding steps; an open trail then follows its tail.
  streaming: PropTypes.bool,
};

export default AgentThinkingTrail;
