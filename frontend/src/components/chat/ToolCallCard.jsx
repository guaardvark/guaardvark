/**
 * ToolCallCard - Inline collapsible card for a single tool call + result.
 * Displayed within a message bubble during unified chat streaming.
 */
import React, { useEffect, useState } from "react";
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
import SecurityIcon from "@mui/icons-material/Security";
import Tooltip from "@mui/material/Tooltip";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { BASE_URL } from "../../api/apiClient";
import { ActionButton } from "../settings/ui";
import { guardedMediaSrc } from "../../utils/assetGuard";
import ArtifactCard, { CSVTable, artifactShape } from "./ArtifactCard";
import SelfEditNotice from "./SelfEditNotice";
import { consentImageSrc } from "./consentApproval";
import { translate, useUiTranslation } from "../../i18n";

// Tools that get thumbs up/down feedback — agent actions the user can judge
const FEEDBACK_TOOLS = new Set(["agent_task_execute", "agent_screen_capture"]);

const consentCopy = () => translate("chat.toolCall.consentCopy");

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
  consent,
  consentImage,
  consentPrompt,
  messageId,
  requestId,
  cardKey,
}) => {
  const { t } = useUiTranslation();
  const artifact = result?.artifact || null;
  // A card that produced a file opens by default so the file is visible in the thread.
  const [expanded, setExpanded] = useState(Boolean(artifact));
  const [feedback, setFeedback] = useState(null); // null | "up" | "down"
  const [taughtNote, setTaughtNote] = useState("");
  const [responded, setResponded] = useState(false);

  // Keyed on identity, not the object, so a parent that rebuilds `result` each
  // render cannot re-open a card the user has collapsed.
  const artifactKey = artifact ? `${artifact.filename}:${artifact.size_bytes}` : null;
  useEffect(() => {
    if (artifactKey) setExpanded(true);
  }, [artifactKey]);

  const showFeedback = FEEDBACK_TOOLS.has(toolName) && result && !isPending;

  // scope "always": run this tool without a card from now on (withdrawn on
  // the Approvals page). Plain Approve covers this call only.
  const handleApproval = (approved, scope) => {
    setResponded(true);
    if (onApproval) {
      if (scope) onApproval(approved, scope);
      else onApproval(approved);
    }
  };

  const handleFeedback = async (positive) => {
    const newFeedback = positive ? "up" : "down";
    // Clicking the lit thumb withdraws the verdict; the backend reverses
    // what it taught.
    const verdict = feedback === newFeedback ? "none" : newFeedback;
    const previous = feedback;
    setFeedback(verdict === "none" ? null : verdict);
    try {
      const res = await fetch(`${BASE_URL}/agent-control/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          verdict,
          kind: `tool:${toolName}@${cardKey ?? "0.0"}`,
          type: "tool_action",
          message_id: messageId ?? null,
          request_id: requestId ?? null,
          tool_name: toolName,
          task: params?.task || toolName,
          session_id: sessionId || null,
          steps: result?.metadata?.steps || null,
          time_seconds: result?.metadata?.time_seconds || (durationMs ? durationMs / 1000 : null),
          model: "",
          why_text: null,
          why_tags: [],
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.success === false) throw new Error(data?.error || `HTTP ${res.status}`);
      const labels = (data?.taught || []).map((t) => t?.label).filter(Boolean);
      setTaughtNote(labels.join(" · "));
      if (labels.length) setTimeout(() => setTaughtNote(""), 5000);
    } catch (err) {
      console.error("Feedback submit failed:", err);
      setFeedback(previous);
    }
  };

  const isSuccess = result?.success;
  const isError = result && !result.success;
  // Waiting states (approval, running) use the theme's primary colour; red and
  // green are kept for a tool that actually failed or finished.
  const awaitingApproval = requiresApproval && !responded;
  const borderColor = awaitingApproval || isPending
    ? "primary.main"
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
        bgcolor: awaitingApproval ? "action.selected" : "action.hover",
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
          <SecurityIcon sx={{ fontSize: 14, color: "primary.main" }} />
        ) : isPending ? (
          <CircularProgress size={14} color="primary" />
        ) : (
          <BuildIcon sx={{ fontSize: 14, color: borderColor }} />
        )}

        <Typography
          variant="caption"
          sx={{ 
            fontWeight: 600, 
            fontFamily: "monospace",
            color: "text.primary"
          }}
        >
          {toolName} {requiresApproval && !responded && t("chat.toolCall.needsApproval")}
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
            consent ? (
              <Box
                data-testid="consent-approval-card"
                sx={{ mb: 1, p: 1.25, bgcolor: "background.paper", borderRadius: 1, border: "1px solid", borderColor: "divider" }}
              >
                <Typography variant="body2" sx={{ display: "block", mb: 1, lineHeight: 1.45 }}>
                  {consentCopy()}
                </Typography>
                {(consentImage || consentPrompt) && (
                  <Box sx={{ display: "flex", gap: 1, alignItems: "flex-start", mb: 1 }}>
                    {consentImageSrc(consentImage) && (
                      <Box
                        component="img"
                        src={guardedMediaSrc(consentImageSrc(consentImage))}
                        alt={t("chat.toolCall.referenceAlt")}
                        sx={{
                          width: 72,
                          height: 72,
                          objectFit: "cover",
                          borderRadius: 1,
                          border: "1px solid",
                          borderColor: "divider",
                          flexShrink: 0,
                        }}
                      />
                    )}
                    <Box sx={{ minWidth: 0 }}>
                      {consentPrompt && (
                        <Typography variant="body2" sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                          {consentPrompt}
                        </Typography>
                      )}
                      {consentImage && !consentImageSrc(consentImage) && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.25 }}>
                          {t("chat.toolCall.reference", { image: consentImage })}
                        </Typography>
                      )}
                    </Box>
                  </Box>
                )}
                <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                  <ActionButton kind="primary" onClick={() => handleApproval(true)}>
                    {t("chat.toolCall.consentApprove")}
                  </ActionButton>
                  <ActionButton kind="link" onClick={() => handleApproval(false)}>
                    {t("chat.toolCall.decline")}
                  </ActionButton>
                </Box>
              </Box>
            ) : (
              <Box
                data-testid="tool-approval-card"
                sx={{ mb: 1, p: 1.25, bgcolor: "background.paper", borderRadius: 1, border: "1px solid", borderColor: "divider" }}
              >
                <Typography variant="body2" sx={{ display: "block", mb: 1 }}>
                  {t("chat.toolCall.approvalPrompt")}
                </Typography>
                <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                  <ActionButton kind="primary" onClick={() => handleApproval(true)}>
                    {t("chat.toolCall.approve")}
                  </ActionButton>
                  <ActionButton
                    kind="neutral"
                    tooltip={t("chat.toolCall.alwaysApproveTooltip", { tool: toolName })}
                    onClick={() => handleApproval(true, "always")}
                  >
                    {t("chat.toolCall.alwaysApprove")}
                  </ActionButton>
                  <ActionButton kind="link" onClick={() => handleApproval(false)}>
                    {t("chat.toolCall.reject")}
                  </ActionButton>
                </Box>
              </Box>
            )
          )}

          {/* Parameters */}
          {params && Object.keys(params).length > 0 && (
            <Box sx={{ mb: 0.5 }}>
              <Typography
                variant="caption"
                sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
              >
                {t("chat.toolCall.parameters")}:
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
          {(outputChunks || result) && !artifact && (
            <Box>
              <Typography
                variant="caption"
                sx={{ fontWeight: 600, display: "block", mb: 0.25 }}
              >
                {isPending ? t("chat.toolCall.outputStreaming") : t("chat.toolCall.result")}:
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
                    ? (outputChunks || t("chat.toolCall.waitingForOutput"))
                    : isSuccess
                    ? result.output || t("chat.toolCall.successNoOutput")
                    : result.error || t("chat.toolCall.unknownError")}
                </Box>
              )}
            </Box>
          )}

          {artifact && <ArtifactCard artifact={artifact} />}

          {result?.self_edit && <SelfEditNotice edit={result.self_edit} />}

          {/* Thumbs up/down feedback for agent tasks */}
          {showFeedback && (
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.5, justifyContent: "flex-end" }}>
              <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
                {t("chat.toolCall.feedbackPrompt")}
              </Typography>
              <Tooltip title={t("chat.toolCall.feedbackUp")}>
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
              <Tooltip title={t("chat.toolCall.feedbackDown")}>
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
          {showFeedback && taughtNote && (
            <Typography variant="caption" sx={{ display: "block", textAlign: "right", fontSize: "0.65rem", color: "text.secondary" }}>
              {taughtNote}
            </Typography>
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
    artifact: artifactShape,
  }),
  durationMs: PropTypes.number,
  isPending: PropTypes.bool,
  sessionId: PropTypes.string,
  // The reply this card belongs to, so a thumb names the row.
  messageId: PropTypes.number,
  requestId: PropTypes.string,
  cardKey: PropTypes.string,
  outputChunks: PropTypes.string,
  requiresApproval: PropTypes.bool,
  onApproval: PropTypes.func,
  consent: PropTypes.bool,
  consentImage: PropTypes.string,
  consentPrompt: PropTypes.string,
};

export default ToolCallCard;
