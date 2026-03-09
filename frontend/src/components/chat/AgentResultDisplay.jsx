// frontend/src/components/chat/AgentResultDisplay.jsx
// Displays agent reasoning loop results with step-by-step visualization
// Version 1.0
/* eslint-env browser */

import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Typography,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Chip,
  Paper,
  Divider,
  LinearProgress,
} from "@mui/material";
import {
  ExpandMore,
  Psychology,
  Build,
  Visibility,
  CheckCircle,
  Error as ErrorIcon,
} from "@mui/icons-material";

/**
 * Displays the results of an agent reasoning loop execution
 * Shows each step with thoughts, tool calls, and observations
 */
const AgentResultDisplay = ({ result, isLoading = false }) => {
  const [expandedStep, setExpandedStep] = useState(null);

  if (isLoading) {
    return (
      <Paper sx={{ p: 2, my: 1 }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
          <Psychology color="primary" />
          <Typography variant="subtitle2">Agent is thinking...</Typography>
        </Box>
        <LinearProgress />
      </Paper>
    );
  }

  if (!result) {
    return null;
  }

  const { final_answer, steps = [], iterations = 0, success = true } = result;

  return (
    <Box sx={{ my: 1 }}>
      {/* Summary Header */}
      <Paper sx={{ p: 2, mb: 1, bgcolor: success ? "success.dark" : "error.dark", backgroundImage: 'none', color: '#fff' }}>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
          {success ? (
            <CheckCircle color="success" />
          ) : (
            <ErrorIcon color="error" />
          )}
          <Typography variant="subtitle1" fontWeight="bold">
            Agent Result ({iterations} iteration{iterations !== 1 ? "s" : ""})
          </Typography>
        </Box>
      </Paper>

      {/* Steps Accordion */}
      {steps.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
            Reasoning Steps:
          </Typography>
          {steps.map((step, index) => (
            <Accordion
              key={index}
              expanded={expandedStep === index}
              onChange={() => setExpandedStep(expandedStep === index ? null : index)}
              sx={{ mb: 0.5 }}
            >
              <AccordionSummary expandIcon={<ExpandMore />}>
                <Box sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%" }}>
                  <Chip
                    label={`Step ${step.iteration || index + 1}`}
                    size="small"
                    color="primary"
                    variant="outlined"
                  />
                  {step.tool_calls?.length > 0 && (
                    <Chip
                      icon={<Build fontSize="small" />}
                      label={`${step.tool_calls.length} tool${step.tool_calls.length > 1 ? "s" : ""}`}
                      size="small"
                      variant="outlined"
                    />
                  )}
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{
                      flexGrow: 1,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {step.thoughts?.substring(0, 60) || "Processing..."}
                    {step.thoughts?.length > 60 ? "..." : ""}
                  </Typography>
                </Box>
              </AccordionSummary>
              <AccordionDetails>
                {/* Thoughts */}
                {step.thoughts && (
                  <Box sx={{ mb: 2 }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.5 }}>
                      <Psychology fontSize="small" color="primary" />
                      <Typography variant="caption" fontWeight="bold">
                        Reasoning
                      </Typography>
                    </Box>
                    <Typography variant="body2" sx={{ pl: 2.5, whiteSpace: "pre-wrap" }}>
                      {step.thoughts}
                    </Typography>
                  </Box>
                )}

                {/* Tool Calls */}
                {step.tool_calls?.length > 0 && (
                  <Box sx={{ mb: 2 }}>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.5 }}>
                      <Build fontSize="small" color="secondary" />
                      <Typography variant="caption" fontWeight="bold">
                        Tool Calls
                      </Typography>
                    </Box>
                    {step.tool_calls.map((tc, tcIndex) => (
                      <Paper key={tcIndex} sx={{ p: 1, mb: 1, ml: 2.5, bgcolor: "action.hover", backgroundImage: 'none' }}>
                        <Typography variant="body2" fontWeight="medium">
                          {tc.tool_name || tc.tool || "Unknown tool"}
                        </Typography>
                        {tc.parameters && (
                          <Typography
                            variant="caption"
                            component="pre"
                            sx={{ mt: 0.5, fontFamily: "monospace", overflow: "auto" }}
                          >
                            {JSON.stringify(tc.parameters, null, 2)}
                          </Typography>
                        )}
                      </Paper>
                    ))}
                  </Box>
                )}

                {/* Observations */}
                {step.observations?.length > 0 && (
                  <Box>
                    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, mb: 0.5 }}>
                      <Visibility fontSize="small" color="info" />
                      <Typography variant="caption" fontWeight="bold">
                        Observations
                      </Typography>
                    </Box>
                    {step.observations.map((obs, obsIndex) => (
                      <Paper key={obsIndex} sx={{ p: 1, mb: 1, ml: 2.5, bgcolor: "info.dark", backgroundImage: 'none', color: '#fff' }}>
                        <Typography variant="body2" fontWeight="medium">
                          {obs.tool}: {obs.result?.success ? "Success" : "Failed"}
                        </Typography>
                        {obs.result?.output && (
                          <Typography
                            variant="caption"
                            component="pre"
                            sx={{
                              mt: 0.5,
                              fontFamily: "monospace",
                              overflow: "auto",
                              maxHeight: 150,
                            }}
                          >
                            {typeof obs.result.output === "string"
                              ? obs.result.output.substring(0, 500)
                              : JSON.stringify(obs.result.output, null, 2).substring(0, 500)}
                            {(obs.result.output?.length || JSON.stringify(obs.result.output)?.length) > 500
                              ? "..."
                              : ""}
                          </Typography>
                        )}
                        {obs.result?.metadata?.image_base64 && (
                          <Box sx={{ mt: 1 }}>
                            <img
                              src={`data:image/${obs.result.metadata.format || "png"};base64,${obs.result.metadata.image_base64}`}
                              alt={`Screenshot from ${obs.tool}`}
                              style={{
                                maxWidth: "100%",
                                borderRadius: 4,
                                border: "1px solid #ccc",
                              }}
                            />
                          </Box>
                        )}
                        {obs.result?.error && (
                          <Typography variant="caption" color="error">
                            Error: {obs.result.error}
                          </Typography>
                        )}
                      </Paper>
                    ))}
                  </Box>
                )}
              </AccordionDetails>
            </Accordion>
          ))}
        </Box>
      )}

      {/* Final Answer */}
      {final_answer && (
        <Paper sx={{ p: 2, bgcolor: "background.paper", border: 1, borderColor: "divider" }}>
          <Typography variant="subtitle2" color="primary" gutterBottom>
            Final Answer
          </Typography>
          <Divider sx={{ mb: 1 }} />
          <Typography variant="body1" sx={{ whiteSpace: "pre-wrap" }}>
            {final_answer}
          </Typography>
        </Paper>
      )}
    </Box>
  );
};

AgentResultDisplay.propTypes = {
  result: PropTypes.shape({
    final_answer: PropTypes.string,
    steps: PropTypes.arrayOf(
      PropTypes.shape({
        iteration: PropTypes.number,
        thoughts: PropTypes.string,
        tool_calls: PropTypes.array,
        observations: PropTypes.array,
      })
    ),
    iterations: PropTypes.number,
    success: PropTypes.bool,
  }),
  isLoading: PropTypes.bool,
};

AgentResultDisplay.defaultProps = {
  result: null,
  isLoading: false,
};

export default AgentResultDisplay;
