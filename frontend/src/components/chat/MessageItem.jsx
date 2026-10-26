// frontend/src/components/chat/MessageItem.jsx
// Version 1.1: Renders a single message bubble with appropriate styling.
// Added support for agent loop messages with step-by-step visualization.
/* eslint-env browser */
import React, { useState, useCallback, useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Paper, Avatar, CardMedia, Chip, CircularProgress, Typography } from "@mui/material";
import ReactMarkdown from "react-markdown";
import PlanCard from "./PlanCard";
import { usePlanActions } from "./planActions";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { a11yDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { guardedMediaSrc } from "../../utils/assetGuard";
// No user avatar icon — user messages are clean right-aligned bubbles
import ImageIcon from "@mui/icons-material/Image";
import ThumbUpOutlinedIcon from "@mui/icons-material/ThumbUpOutlined";
import ThumbDownOutlinedIcon from "@mui/icons-material/ThumbDownOutlined";
import ThumbUpIcon from "@mui/icons-material/ThumbUp";
import ThumbDownIcon from "@mui/icons-material/ThumbDown";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import CheckIcon from "@mui/icons-material/Check";
import MemoryIcon from "@mui/icons-material/Memory";
import Tooltip from "@mui/material/Tooltip";
import IconButton from "@mui/material/IconButton";
import { BrandLogo } from "../branding";
import { useAppStore } from "../../stores/useAppStore";
import { BASE_URL } from "../../api/apiClient";
import AgentResultDisplay from "./AgentResultDisplay";
import { StatusChip } from "../../utils/familyColors";
import ToolCallCard from "./ToolCallCard";
import GeneratedMedia from "./GeneratedMedia";
import ThinkingCard from "./ThinkingCard";
import SynthesizedAnswerChip from "./SynthesizedAnswerChip";
import { isSynthesizedMessage, isSynthesizedStep } from "./synthesizedAnswer";
import WebSearchOfferChip from "./WebSearchOfferChip";
import { webSearchOfferOf } from "./webSearchOffer";
import AgentThinkingTrail from "./AgentThinkingTrail";
import OrchestratorPlanView from "../orchestrator/OrchestratorPlanView";
import ImageLightbox from "../images/ImageLightbox";
import NarrateButton from "../common/NarrateButton";
import EntityContextMenu from "../common/EntityContextMenu";
import useContextMenu from "../../hooks/useContextMenu";
import copyText from "../../utils/copyText";
import { agentNoteCaption } from "./agentNoteCaptions";
import { useUiTranslation, uiLocale } from "../../i18n";


const UPLOAD_BASE_URL = BASE_URL + "/uploads";

const formatTime = (timestamp) => {
  if (!timestamp) return "";
  try {
    const date = new Date(timestamp);
    if (isNaN(date.getTime())) return "";
    
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    
    const timeStr = date.toLocaleTimeString(uiLocale(), { hour: '2-digit', minute: '2-digit' });
    if (isToday) {
      return timeStr;
    } else {
      const dateStr = date.toLocaleDateString(uiLocale(), { month: 'short', day: 'numeric' });
      return `${dateStr}, ${timeStr}`;
    }
  } catch (e) {
    return "";
  }
};

const MessageItem = ({ message, sessionId: sessionIdProp, onOrchestratorUpdate, onSearchWeb }) => {
  const { t } = useUiTranslation();
  const isUser = message.role === "user";
  // Per-message sessionId takes precedence, fall through to the list-level
  // prop so feedback on assistant turns (which often lack message.sessionId)
  // still carries the active chat session.
  const effectiveSessionId = message.sessionId || sessionIdProp || null;
  const planActions = usePlanActions();

  // Read narrate visibility + selected voice from voice settings (localStorage).
  // The voice picked in Settings flows through to NarrateButton's Fast (Piper)
  // path; Expressive ignores it (audio_foundry's dispatcher picks Chatterbox/Kokoro).
  const readVoiceSettings = () => {
    try {
      const vs = localStorage.getItem('guaardvark_voiceSettings');
      if (vs) {
        const parsed = JSON.parse(vs);
        return {
          showNarrate: parsed.showNarrateButtons !== false,
          voice: parsed.voice || 'libritts',
        };
      }
    } catch {
      // invalid JSON in localStorage — fall through to defaults
    }
    return { showNarrate: true, voice: 'libritts' };
  };

  const [showNarrate, setShowNarrate] = useState(() => readVoiceSettings().showNarrate);
  const [selectedVoice, setSelectedVoice] = useState(() => readVoiceSettings().voice);

  useEffect(() => {
    const handler = () => {
      const next = readVoiceSettings();
      setShowNarrate(next.showNarrate);
      setSelectedVoice(next.voice);
    };
    window.addEventListener('voiceSettingsChanged', handler);
    window.addEventListener('storage', handler);
    return () => {
      window.removeEventListener('voiceSettingsChanged', handler);
      window.removeEventListener('storage', handler);
    };
  }, []);
  const isCommand = message.type === "command";
  const isProgress = message.type === "progress";
  // Plan mode: a proposed plan shows as a card; a step's instruction (saved as
  // a user row while the plan ran) shows as a step heading, not as a message
  // the person typed.
  const isPlanMessage = message.messageType === "plan" && Boolean(message.plan);
  const isPlanStep = message.messageType === "plan_step";
  // Every picture the user attached to this turn (several), by position: an
  // entry is null where the server could not save that picture, so the file
  // names stay aligned with the pictures. The single-picture case keeps
  // reading imageUrl below.
  const attachedImageUrls = Array.isArray(message.imageUrls) && message.imageUrls.length > 1
    ? message.imageUrls.map((u) => (typeof u === "string" && u ? u : null))
    : [];
  const showsSeveralImages = attachedImageUrls.some(Boolean);
  const isAgentLoop = message.isAgentLoop;
  const logo = useAppStore((s) => s.systemLogo);
  const [lightbox, setLightbox] = useState(null);
  // Hydrate thumb state from the message's persisted extra_data so the
  // icon survives a page refresh. Backend stamps {"feedback": "up"/"down"}
  // onto LLMMessage.extra_data when the user clicks the chips.
  const initialFeedback = (() => {
    const v = message?.extra_data?.feedback;
    return v === "up" || v === "down" ? v : null;
  })();
  const [feedback, setFeedback] = useState(initialFeedback); // null | "up" | "down"
  const [copied, setCopied] = useState(false);
  const bubbleMenu = useContextMenu();
  // Tag every thumb with the active lesson, if one is open. Pearls with a
  // lesson_id skip the per-👍 distill path and flow through the End-Lesson
  // summary distiller instead.
  const activeLessonId = useAppStore((s) => s.activeLessonId);

  const handleCopy = useCallback(async () => {
    const text = typeof message.content === "string" ? message.content : JSON.stringify(message.content, null, 2);
    // copyText falls back to execCommand for non-HTTPS contexts.
    if (await copyText(text)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  }, [message.content]);

  // What the last thumb taught, shown for a moment under the action bar.
  const [taughtNote, setTaughtNote] = useState("");
  const taughtTimerRef = useRef(null);
  useEffect(() => () => { if (taughtTimerRef.current) clearTimeout(taughtTimerRef.current); }, []);
  // Re-sync the icon if the persisted stamp changes under us (history reload).
  useEffect(() => {
    const v = message?.extra_data?.feedback;
    if (v === "up" || v === "down") setFeedback(v);
  }, [message?.extra_data?.feedback]);

  // The database row this reply became: from history (`id` is the row id) or
  // from chat:message_saved (`message_id`). A thumb names it; the backend
  // resolves by request_id when the row is not known yet.
  const dbMessageId = message.message_id ?? (typeof message.id === "number" ? message.id : null);

  const handleFeedback = useCallback(async (positive) => {
    const newVal = positive ? "up" : "down";
    // Clicking the lit thumb withdraws the verdict; the backend reverses
    // what it taught.
    const verdict = feedback === newVal ? "none" : newVal;
    const previous = feedback;
    setFeedback(verdict === "none" ? null : verdict);
    try {
      const content = typeof message.content === "string" ? message.content : "";
      const res = await fetch(`${BASE_URL}/agent-control/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          verdict,
          kind: "response",
          message_id: dbMessageId,
          request_id: message.request_id || null,
          task: content.slice(0, 200),
          session_id: effectiveSessionId,
          type: "response",
          lesson_id: activeLessonId || undefined,
          // The why channel: wired through the API and storage now; the
          // input that fills these arrives with the dedicated feature.
          why_text: null,
          why_tags: [],
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data?.success === false) throw new Error(data?.error || `HTTP ${res.status}`);
      const labels = (data?.taught || []).map((t) => t?.label).filter(Boolean);
      if (labels.length) {
        setTaughtNote(labels.join(" · "));
        if (taughtTimerRef.current) clearTimeout(taughtTimerRef.current);
        taughtTimerRef.current = setTimeout(() => setTaughtNote(""), 5000);
      }
    } catch (err) {
      console.error("Feedback failed:", err);
      setFeedback(previous);
    }
  }, [feedback, message.content, message.request_id, dbMessageId, effectiveSessionId, activeLessonId]);

  const openLightbox = useCallback((url, name, images, index) => {
    setLightbox({ url, name, images: images || [{ url, name }], index: index || 0 });
  }, []);

  const handleLightboxDownload = useCallback(() => {
    if (!lightbox) return;
    const img = lightbox.images[lightbox.index];
    const link = document.createElement("a");
    link.href = img.url;
    link.download = img.name || "image";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }, [lightbox]);

  // Construct the full logo URL if logo path exists
  const logoUrl = logo ? `${UPLOAD_BASE_URL}/${logo}` : undefined;

  if (isPlanStep) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, my: 1, px: 1 }} data-testid="plan-step-heading">
        <Box sx={{ flex: 1, borderTop: 1, borderColor: "divider" }} />
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          {t("chat.plan.stepHeading", { index: message.planStep, title: message.planStepTitle || "" })}
        </Typography>
        <Box sx={{ flex: 1, borderTop: 1, borderColor: "divider" }} />
      </Box>
    );
  }

  // Handle agent loop messages specially
  if (isAgentLoop) {
    const isThinking = message.agentLoopStatus === "thinking";
    const isComplete = message.agentLoopStatus === "complete";
    const hasError = message.agentLoopStatus === "error";
    const formattedTime = formatTime(message.timestamp);

    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          width: "100%",
        }}
      >
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-start",
            flexDirection: "row",
            alignItems: "flex-start",
            gap: 1,
            width: "100%",
          }}
        >
          <Avatar
            sx={{
              bgcolor: isThinking ? "warning.main" : isComplete ? "success.main" : hasError ? "error.main" : "grey.500",
              width: 32,
              height: 32,
              border: 1,
              borderColor: "divider",
            }}
          >
            <BrandLogo
              size={20}
              variant={isThinking ? "warning" : isComplete ? "success" : hasError ? "error" : "default"}
              animate={isThinking}
            />
          </Avatar>
          <Paper
            elevation={2}
            sx={{
              p: 1.5,
              maxWidth: "85%",
              bgcolor: "background.paper",
              borderTopLeftRadius: 4,
              borderTopRightRadius: 16,
              borderBottomLeftRadius: 16,
              borderBottomRightRadius: 16,
              border: 1,
              borderColor: isThinking ? "warning.main" : isComplete ? "success.main" : hasError ? "error.main" : "grey.500",
            }}
          >
            {isThinking ? (
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <CircularProgress size={16} color="warning" />
                <Typography variant="body2" color="text.secondary">
                  {message.content || t("chat.message.agentReasoning")}
                </Typography>
              </Box>
            ) : (
              <>
                <AgentResultDisplay
                  result={message.agentResult}
                  isLoading={false}
                />
                {message.content && !message.agentResult?.final_answer && (
                  <Typography variant="body2" sx={{ mt: 1 }}>
                    {message.content}
                  </Typography>
                )}
              </>
            )}
          </Paper>
        </Box>
        {formattedTime && (
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.65rem",
              color: "text.secondary",
              opacity: 0.65,
              mt: 0.25,
              ml: "40px",
            }}
          >
            {formattedTime}
          </Typography>
        )}
      </Box>
    );
  }

  // Progress messages (e.g., "Generating image...") — styled like active streaming messages
  if (isProgress) {
    const formattedTime = formatTime(message.timestamp);
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          width: "100%",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "flex-start", gap: 1, width: "100%" }}>
          <Avatar
            src={logoUrl}
            sx={{
              bgcolor: "warning.main",
              width: 32,
              height: 32,
              border: 1,
              borderColor: "divider",
            }}
          >
            {!logo && <BrandLogo size={20} variant="warning" animate />}
          </Avatar>
          <Paper
            elevation={2}
            sx={{
              p: 1.5,
              maxWidth: "85%",
              bgcolor: "background.paper",
              borderTopLeftRadius: 4,
              borderTopRightRadius: 16,
              borderBottomLeftRadius: 16,
              borderBottomRightRadius: 16,
              border: 1,
              borderColor: "warning.main",
              minWidth: 200,
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <CircularProgress size={14} color="warning" />
              <Typography variant="body2" color="text.secondary">
                {message.content || t("chat.message.processing")}
              </Typography>
            </Box>
          </Paper>
        </Box>
        {formattedTime && (
          <Typography
            variant="caption"
            sx={{
              fontSize: "0.65rem",
              color: "text.secondary",
              opacity: 0.65,
              mt: 0.25,
              ml: "40px",
            }}
          >
            {formattedTime}
          </Typography>
        )}
      </Box>
    );
  }

  const formattedTime = formatTime(message.timestamp);
  const webSearchOffer = webSearchOfferOf(message);

  // Same condition as the thumbs row under assistant replies.
  const canRate =
    message.role === "assistant" && !isCommand && !isProgress &&
    typeof message.content === "string" && message.content.length > 10;
  // Media keeps the browser menu (save or copy image).
  const handleBubbleContextMenu = (e) => {
    if (e.target?.closest?.("img, video, audio, canvas")) return;
    bubbleMenu.open(e);
  };

  return (
    <>
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        width: "100%",
      }}
    >
      <Box
        sx={{
          display: "flex",
          justifyContent: isUser ? "flex-end" : "flex-start",
          flexDirection: "row",
          alignItems: "flex-start",
          gap: 1,
          width: "100%",
          // Reveal the user-message copy affordance on hover (AI messages keep
          // their always-visible action row below).
          "&:hover .msg-user-copy": { opacity: 1 },
        }}
      >
      {/* User messages get a hover-revealed copy button beside the bubble.
          AI messages already carry copy in their action row. */}
      {isUser && (
        <Tooltip title={copied ? t("chat.message.copied") : t("chat.message.copy")}>
          <IconButton
            className="msg-user-copy"
            size="small"
            onClick={handleCopy}
            aria-label={t("chat.message.copyMessage")}
            sx={{ p: 0.25, alignSelf: "center", opacity: 0, transition: "opacity 0.15s" }}
          >
            {copied ? (
              <CheckIcon sx={{ fontSize: 14, color: "success.main" }} />
            ) : (
              <ContentCopyIcon sx={{ fontSize: 14, opacity: 0.6 }} />
            )}
          </IconButton>
        </Tooltip>
      )}
      {!isUser && (
        <Avatar
          src={logoUrl}
          sx={{
            bgcolor: "primary.main",
            width: 32,
            height: 32,
            border: 1,
            borderColor: "divider"
          }}
        >
          {!logo && <BrandLogo size={20} />}
        </Avatar>
      )}
      <Paper
        elevation={isCommand ? 0 : 2}
        onContextMenu={message.content ? handleBubbleContextMenu : undefined}
        sx={{
          p: 1.5,
          maxWidth: "80%",
          bgcolor: isCommand ? "action.hover" : isUser ? "primary.main" : "background.paper",
          color: isCommand ? "text.secondary" : isUser ? "primary.contrastText" : "text.primary",
          // Command messages get a colored left border and subtle styling
          ...(isCommand && {
            borderLeft: "3px solid",
            borderLeftColor: "info.main",
            borderRadius: 1,
            fontSize: "0.85rem",
            fontFamily: "monospace",
          }),
          // Prevent theme-level Paper gradients (e.g. Musk) from covering user bubble bgcolor
          ...(isUser && !isCommand && { backgroundImage: 'none' }),
          ...(!isCommand && {
            borderTopLeftRadius: isUser ? 16 : 4,
            borderTopRightRadius: isUser ? 4 : 16,
            borderBottomLeftRadius: 16,
            borderBottomRightRadius: 16,
          }),
          // Agent-mode marker: messages tagged with mode === "agent" get an
          // orange outline so the chat history visibly distinguishes between
          // chat-mode and agent-mode messages. The visual sits on the AI's
          // surface (the bubble) rather than the user's input field, since
          // agent mode is a property of how the AI interprets the message.
          ...(message.mode === "agent" && {
            border: "2px solid",
            borderColor: "warning.main",
          }),
        }}
      >
        {/* Command badge */}
        {isCommand && (
          <Chip label={t("chat.message.commandBadge")} size="small" variant="outlined" color="info"
            sx={{ height: 18, fontSize: "0.65rem", mb: 0.5, fontFamily: "monospace" }} />
        )}
        {/* Display the attached pictures if present: all of them when the
            user sent several, else the one (or the related image). */}
        {showsSeveralImages ? (
          <Box sx={{ mb: 1, display: "flex", flexWrap: "wrap", gap: 1 }}>
            {attachedImageUrls.map((url, idx) => url && (
              <Box key={`${idx}-${url}`}>
                <CardMedia
                  component="img"
                  sx={{
                    maxWidth: 300,
                    maxHeight: 200,
                    width: 'auto',
                    height: 'auto',
                    borderRadius: 1,
                    border: '1px solid',
                    borderColor: 'divider',
                    objectFit: 'contain'
                  }}
                  image={guardedMediaSrc(url)}
                  alt={message.imageFileNames?.[idx] || t("chat.message.uploadedImageNumbered", { number: idx + 1 })}
                />
                {message.imageFileNames?.[idx] && (
                  <Chip
                    icon={<ImageIcon />}
                    label={message.imageFileNames[idx]}
                    size="small"
                    variant="outlined"
                    sx={{ mt: 0.5 }}
                  />
                )}
              </Box>
            ))}
          </Box>
        ) : (message.imageUrl || message.relatedImageUrl) && (
          <Box sx={{ mb: 1 }}>
            <CardMedia
              component="img"
              sx={{
                maxWidth: 300,
                maxHeight: 200,
                width: 'auto',
                height: 'auto',
                borderRadius: 1,
                border: '1px solid',
                borderColor: 'divider',
                objectFit: 'contain'
              }}
              image={guardedMediaSrc(message.imageUrl || message.relatedImageUrl)}
              alt={message.imageFileName || t("chat.message.uploadedImage")}
            />
            {message.imageFileName && (
              <Chip
                icon={<ImageIcon />}
                label={message.imageFileName}
                size="small"
                variant="outlined"
                sx={{ mt: 0.5 }}
              />
            )}
          </Box>
        )}

        {/* Generated images and videos (from agent tool calls) */}
        <GeneratedMedia
          items={message.generatedImages}
          onOpenPicture={(pictures, index) =>
            openLightbox(pictures[index].url, pictures[index].name, pictures, index)
          }
        />

        {/* Thinking context — persisted from the streaming phase */}
        {message.thinkingText && message.toolCalls?.length > 0 && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1, mt: 0.5 }}>
            <CircularProgress size={14} color="warning" sx={{ opacity: 0.6 }} />
            <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
              {message.thinkingText}
            </Typography>
          </Box>
        )}

        {/* Model reasoning persisted from the streaming phase */}
        {typeof message.thinking === "string" && message.thinking.trim() && (
          <ThinkingCard text={message.thinking} streaming={false} defaultExpanded={false} />
        )}

        {/* Agent loop reasoning trail — one collapsible holding every step,
            so a long see-think-act run doesn't stack N accordions. */}
        {message.agentThinkingSteps && message.agentThinkingSteps.length > 0 && (
          <Box sx={{ mb: 1, mt: 0.5 }}>
            {import.meta.env.DEV && console.debug('[MessageItem] RENDERING PERSISTED agentThinkingSteps (from history/extra_data or post-stream append), length=', message.agentThinkingSteps.length, ' — this is post-complete persisted, not live StreamingMessage state')}
            <AgentThinkingTrail steps={message.agentThinkingSteps} />
          </Box>
        )}

        {message.orchestratorPlan && (
          <Box sx={{ mb: 1, mt: 0.5 }}>
            <OrchestratorPlanView
              embedded
              plan={message.orchestratorPlan}
              planId={message.orchestratorPlanId}
              onExecutionComplete={(payload) => onOrchestratorUpdate?.(message.id, payload)}
            />
          </Box>
        )}

        {/* Unified chat tool call cards (displayed inline before the response text) */}
        {message.isUnifiedChat && message.toolCalls && message.toolCalls.length > 0 && (
          <Box sx={{ mb: 1 }}>
            {message.toolCalls.map((step, stepIdx) => (
              isSynthesizedStep(step) ? (
                <Box
                  key={`step-${stepIdx}`}
                  data-testid="synthesized-step"
                  sx={{
                    display: "flex",
                    alignItems: "flex-start",
                    gap: 1,
                    mb: 0.75,
                    mt: stepIdx === 0 ? 0 : 0.5,
                    pl: 1,
                    borderLeft: 2,
                    borderColor: "divider",
                    opacity: 0.9,
                  }}
                >
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ fontStyle: "italic", whiteSpace: "pre-wrap", fontSize: "0.7rem" }}
                  >
                    {step.thoughts?.trim() || ""}
                  </Typography>
                </Box>
              ) : (
              <Box key={`step-${stepIdx}`}>
                {/* Per-iteration thinking: the agent's reasoning for this step */}
                {step.thoughts && step.thoughts.trim() && (
                  <Box
                    sx={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 1,
                      mb: 0.75,
                      mt: stepIdx === 0 ? 0 : 0.5,
                      pl: 1,
                      borderLeft: 2,
                      borderColor: "warning.main",
                      opacity: 0.85,
                    }}
                  >
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ fontStyle: "italic", whiteSpace: "pre-wrap", fontSize: "0.7rem" }}
                    >
                      <Box component="span" sx={{ color: "warning.main", fontWeight: 600, mr: 0.5 }}>
                        {t("chat.thinkingTrail.step", { step: step.iteration ?? stepIdx + 1 })}:
                      </Box>
                      {step.thoughts.trim()}
                    </Typography>
                  </Box>
                )}
                {(step.tool_calls || []).map((tc, tcIdx) => (
                  <ToolCallCard
                    key={`${stepIdx}-${tcIdx}`}
                    toolName={tc.tool_name}
                    params={tc.params || tc.arguments || tc.args || {}}
                    result={tc.success != null ? {
                      success: tc.success,
                      output: tc.output_preview,
                      error: tc.success ? null : tc.output_preview,
                      artifact: tc.artifact || null,
                    } : null}
                    durationMs={tc.duration_ms}
                    isPending={false}
                    sessionId={effectiveSessionId}
                    messageId={dbMessageId}
                    requestId={message.request_id || null}
                    cardKey={`${stepIdx}.${tcIdx}`}
                  />
                ))}
              </Box>
              )
            ))}
          </Box>
        )}

        {!isUser && isSynthesizedMessage(message) && (
          <Box sx={{ mb: 0.75 }}>
            <SynthesizedAnswerChip />
          </Box>
        )}

        {(typeof message.content === 'string' ? message.content.trim() : message.content) && (
        <Box
          sx={{
            userSelect: 'text',
            WebkitUserSelect: 'text',
            MozUserSelect: 'text',
            msUserSelect: 'text',
            cursor: 'text',
            fontSize: '0.75rem', // Match other cards' font size
            '& p': {
              fontSize: '0.75rem',
              margin: '0.25rem 0',
            },
            '& pre': {
              fontSize: '0.7rem',
            },
            '& code': {
              fontSize: '0.7rem',
            },
            '& ul, & ol': {
              fontSize: '0.75rem',
              paddingLeft: '1.25rem',
            },
            '& li': {
              fontSize: '0.75rem',
              margin: '0.125rem 0',
            },
            '& h1, & h2, & h3, & h4, & h5, & h6': {
              fontSize: '0.85rem',
              fontWeight: 'bold',
              margin: '0.5rem 0',
            },
            '& img': {
              maxWidth: '100%',
              borderRadius: '4px',
              border: '1px solid',
              borderColor: 'divider',
              marginTop: '0.5rem',
              display: 'block',
            },
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}
            components={{
              // Offline-first: never load a remote image URL from message markdown.
              img: (props) => <img {...props} src={guardedMediaSrc(props.src)} alt={props.alt || ""} />,
              code({ inline, className, children, ...props }) {
                const match = /language-(\w+)/.exec(className || "");
                return !inline && match ? (
                  <SyntaxHighlighter
                    style={a11yDark}
                    language={match[1]}
                    PreTag="div"
                    {...props}
                  >
                    {String(children).replace(/\n$/, "")}
                  </SyntaxHighlighter>
                ) : (
                  <code className={className} {...props}>
                    {children}
                  </code>
                );
              },
            }}
          >
            {isPlanMessage
              ? (message.findings || "")
              : typeof message.content === 'string' ? message.content : JSON.stringify(message.content, null, 2)}
          </ReactMarkdown>
        </Box>
        )}

        {/* The plan comes after the research that led to it. */}
        {isPlanMessage && (
          <PlanCard
            plan={message.plan}
            onRunStarted={planActions?.onRunStarted}
            onKeepPlanning={planActions?.onKeepPlanning}
            onStop={planActions?.onStop}
            onPlanChanged={planActions?.onPlanChanged}
          />
        )}
        {isUser && message.agentNote && (
          <Typography
            variant="caption"
            sx={{ display: "block", mt: 0.25, opacity: 0.85, fontStyle: "italic" }}
          >
            {agentNoteCaption(message.agentNote)}
          </Typography>
        )}
        {message.truncated === true && (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: "block", mt: 0.5, fontStyle: "italic", opacity: 0.8 }}
          >
            {t("chat.message.outputLimit")}
          </Typography>
        )}
        {/* An agent answer the facts check could not match to its tool
            results; true and unset (no tools ran) show nothing. */}
        {message.role === "assistant" && message.verified === false && (
          <Typography
            variant="caption"
            color="text.secondary"
            data-testid="unverified-note"
            sx={{ display: "block", mt: 0.5, fontStyle: "italic", opacity: 0.8 }}
          >
            {t("chat.message.unverified")}
          </Typography>
        )}
        {!isCommand && webSearchOffer && (
          <WebSearchOfferChip offer={webSearchOffer} onSearch={onSearchWeb} />
        )}
        {/* Feedback + narrate for assistant replies (not system/command rows) */}
        {message.role === "assistant" && !isCommand && !isProgress && message.content && typeof message.content === 'string' && message.content.length > 10 && (
          <>
          <Box sx={{ mt: 0.5, display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 0.25 }}>
            {message.toolCalls && message.toolCalls.some(tc => tc.tool_name === "search_memory") && (
              <Tooltip title={t("chat.message.recalledTooltip")}>
                <Box sx={{ 
                  display: "flex", 
                  alignItems: "center", 
                  gap: 0.5, 
                  mr: 1, 
                  px: 0.75, 
                  py: 0.25, 
                  borderRadius: 1, 
                  bgcolor: "background.paper",
                  border: "1px solid",
                  borderColor: "divider"
                }}>
                  <MemoryIcon sx={{ fontSize: 12, color: "primary.main" }} />
                  <Typography variant="caption" sx={{ fontSize: "0.65rem", color: "text.secondary" }}>
                    {t("chat.message.recalled")}
                  </Typography>
                </Box>
              </Tooltip>
            )}
            <Tooltip title={copied ? t("chat.message.copied") : t("chat.message.copy")}>
              <IconButton size="small" onClick={handleCopy} sx={{ p: 0.25 }}>
                {copied ? (
                  <CheckIcon sx={{ fontSize: 14, color: "success.main" }} />
                ) : (
                  <ContentCopyIcon sx={{ fontSize: 14, opacity: 0.4 }} />
                )}
              </IconButton>
            </Tooltip>
            <Tooltip title={t("chat.message.goodResponse")}>
              <IconButton size="small" onClick={() => handleFeedback(true)} sx={{ p: 0.25 }}>
                {feedback === "up" ? (
                  <ThumbUpIcon sx={{ fontSize: 14, color: "success.main" }} />
                ) : (
                  <ThumbUpOutlinedIcon sx={{ fontSize: 14, opacity: 0.4 }} />
                )}
              </IconButton>
            </Tooltip>
            <Tooltip title={t("chat.message.badResponse")}>
              <IconButton size="small" onClick={() => handleFeedback(false)} sx={{ p: 0.25 }}>
                {feedback === "down" ? (
                  <ThumbDownIcon sx={{ fontSize: 14, color: "error.main" }} />
                ) : (
                  <ThumbDownOutlinedIcon sx={{ fontSize: 14, opacity: 0.4 }} />
                )}
              </IconButton>
            </Tooltip>
            {showNarrate && <NarrateButton text={message.content} voice={selectedVoice} size="small" />}
          </Box>
          {taughtNote && (
            <Typography variant="caption" sx={{ display: "block", textAlign: "right", fontSize: "0.65rem", color: "text.secondary", mt: 0.25 }}>
              {taughtNote}
            </Typography>
          )}
          </>
        )}
        {/* Source badge for Uncle Claude / Family / Self-Improvement responses */}
        {message.badge && (
          <Box sx={{ mt: 1, display: "flex", justifyContent: "flex-end" }}>
            <StatusChip
              source={message.source || "nephew"}
              status="authored"
              label={message.badge}
              sx={{ height: 20, fontSize: "0.65rem" }}
            />
          </Box>
        )}
      </Paper>
      {/* No avatar for user messages — clean right-aligned bubbles */}
      </Box>
      {formattedTime && (
        <Typography
          variant="caption"
          sx={{
            fontSize: "0.65rem",
            color: "text.secondary",
            opacity: 0.65,
            mt: 0.25,
            mr: isUser ? "12px" : 0,
            ml: isUser ? 0 : "40px",
          }}
        >
          {formattedTime}
        </Typography>
      )}
    </Box>
    <EntityContextMenu
      anchorPosition={bubbleMenu.anchorPosition}
      onClose={bubbleMenu.close}
      actions={
        bubbleMenu.isOpen
          ? [
              { label: t("chat.message.copy"), onClick: handleCopy },
              canRate && {
                label: t("chat.message.goodResponse"),
                checked: feedback === "up",
                onClick: () => handleFeedback(true),
                dividerBefore: true,
              },
              canRate && { label: t("chat.message.badResponse"), checked: feedback === "down", onClick: () => handleFeedback(false) },
            ]
          : []
      }
    />
    {lightbox && (
      <ImageLightbox
        imageUrl={lightbox.images[lightbox.index]?.url || lightbox.url}
        imageName={lightbox.images[lightbox.index]?.name || lightbox.name}
        onClose={() => setLightbox(null)}
        onPrev={() => setLightbox(prev => ({ ...prev, index: Math.max(0, prev.index - 1) }))}
        onNext={() => setLightbox(prev => ({ ...prev, index: Math.min(prev.images.length - 1, prev.index + 1) }))}
        onDownload={handleLightboxDownload}
        hasPrev={lightbox.index > 0}
        hasNext={lightbox.index < lightbox.images.length - 1}
      />
    )}
    </>
  );
};

MessageItem.propTypes = {
  message: PropTypes.shape({
    role: PropTypes.string,
    content: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
    isAgentLoop: PropTypes.bool,
    agentLoopStatus: PropTypes.oneOf(["thinking", "complete", "error"]),
    agentResult: PropTypes.object,
    imageUrl: PropTypes.string,
    imageUrls: PropTypes.arrayOf(PropTypes.string),
    relatedImageUrl: PropTypes.string,
    imageFileName: PropTypes.string,
    imageFileNames: PropTypes.arrayOf(PropTypes.string),
    isUnifiedChat: PropTypes.bool,
    toolCalls: PropTypes.array,
    generatedImages: PropTypes.array,
    badge: PropTypes.string,
    source: PropTypes.string,
    orchestratorPlan: PropTypes.object,
    plan: PropTypes.object,
    findings: PropTypes.string,
    planStep: PropTypes.number,
    planStepTitle: PropTypes.string,
    orchestratorPlanId: PropTypes.string,
    messageType: PropTypes.string,
    thinking: PropTypes.string,
    truncated: PropTypes.bool,
    synthesized: PropTypes.bool,
    verified: PropTypes.bool,
    extra_data: PropTypes.object,
    message_id: PropTypes.number,
    request_id: PropTypes.string,
    web_search_offer: PropTypes.object,
  }).isRequired,
  sessionId: PropTypes.string,
  onOrchestratorUpdate: PropTypes.func,
  onSearchWeb: PropTypes.func,
};

export default React.memo(MessageItem);
