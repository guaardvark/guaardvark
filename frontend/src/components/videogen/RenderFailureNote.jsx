// frontend/src/components/videogen/RenderFailureNote.jsx
// Why a Video Gen clip or request failed: the failure record the backend
// attaches (job_types.describe_failure), shown as its label, message and next
// step. Results from before the record existed fall back to the raw error.

import React from "react";
import { Typography } from "@mui/material";
import { formatUiError } from "../../utils/uiError";
import { failureText } from "../../utils/renderFailure";

/**
 * @param {object} failure  a result's failure record
 * @param {*} error         the raw error, for results without a record
 */
const RenderFailureNote = ({ failure, error }) => {
  const text = failureText(failure) || formatUiError(error);
  if (!text) return null;
  return (
    <Typography
      variant="caption"
      color={failure?.kind === "cancelled" ? "text.secondary" : "error"}
      display="block"
      sx={{ mt: 0.5 }}
      data-failure-kind={failure?.kind || undefined}
    >
      {text}
    </Typography>
  );
};

export default RenderFailureNote;
