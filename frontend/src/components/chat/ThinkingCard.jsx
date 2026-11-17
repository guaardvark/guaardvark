/**
 * ThinkingCard - Collapsible model reasoning stream: a coloured title over
 * plain text. Same visual language as ToolCallCard so thinking and tool
 * calls read as one sequence inside the message bubble.
 */
import React, { useState, useEffect } from "react";
import PropTypes from "prop-types";
import { Box, Typography, Collapse, IconButton, CircularProgress } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import { useUiTranslation } from "../../i18n";
import useFollowTail from "../../hooks/useFollowTail";
import { STEP_COLORS, stepHeaderSx, stepTitleSx, stepBodySx, stepScrollSx } from "./stepText";

const formatElapsed = (ms) => `${(ms / 1000).toFixed(1)}s`;

const ThinkingCard = ({ text = "", streaming = false, elapsedMs = null, defaultExpanded = false }) => {
  const { t } = useUiTranslation();
  const [expanded, setExpanded] = useState(defaultExpanded);
  const follow = useFollowTail(streaming && expanded, text.length);

  // The parent flips defaultExpanded to collapse the card once the answer
  // starts; a manual toggle after that still wins until the next flip.
  useEffect(() => {
    setExpanded(defaultExpanded);
  }, [defaultExpanded]);

  return (
    <Box data-testid="thinking-card" sx={{ my: 0.5 }}>
      <Box
        role="button"
        aria-expanded={expanded}
        sx={{ ...stepHeaderSx, color: STEP_COLORS.thinking }}
        onClick={() => setExpanded((prev) => !prev)}
      >
        {streaming && (
          <CircularProgress size={12} color="inherit" data-testid="thinking-spinner" />
        )}

        <Typography component="span" sx={stepTitleSx}>
          {t("chat.thinking.tag")}
        </Typography>

        <Typography
          component="span"
          color="text.secondary"
          sx={{ fontSize: "0.75rem", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {streaming ? t("chat.thinking.streaming") : t("chat.thinking.done")}
        </Typography>

        {!streaming && elapsedMs != null && (
          <Typography component="span" color="text.secondary" sx={{ fontSize: "0.75rem", flexShrink: 0 }}>
            {formatElapsed(elapsedMs)}
          </Typography>
        )}

        <IconButton
          size="small"
          disableRipple
          sx={{ p: 0, color: "inherit", "&:hover": { bgcolor: "transparent" } }}
          aria-label={expanded ? t("chat.thinking.collapse") : t("chat.thinking.expand")}
        >
          {expanded ? (
            <ExpandLessIcon sx={{ fontSize: 16 }} />
          ) : (
            <ExpandMoreIcon sx={{ fontSize: 16 }} />
          )}
        </IconButton>
      </Box>

      <Collapse in={expanded}>
        <Box
          ref={follow.ref}
          onScroll={follow.onScroll}
          component="pre"
          data-testid="thinking-body"
          sx={{ ...stepBodySx, ...stepScrollSx }}
        >
          {text || (streaming ? "" : t("chat.thinkingTrail.noReasoning"))}
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
