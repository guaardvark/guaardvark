// frontend/src/components/common/TextInputDialog.jsx
// The themed stand-in for window.prompt: one line of text, Enter submits, Esc or
// the backdrop cancels. The draft starts as `initialValue` (selected, as the
// browser prompt did) each time the dialog opens, and onSubmit only ever gets a
// non-empty trimmed value; cancelling never calls it.

import React, { useEffect, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  TextField,
} from "@mui/material";
import { translate, useUiTranslation } from "../../i18n";

/**
 * @param {boolean}  open
 * @param {string}   title
 * @param {ReactNode} [description]  one line under the title
 * @param {string}   [label]         TextField label
 * @param {string}   [placeholder]
 * @param {string}   [initialValue]  prefilled and selected on open
 * @param {string}   [submitLabel]
 * @param {string}   [cancelLabel]
 * @param {string}   [inputType]     "text" | "url" | ...
 * @param {function} onSubmit        (trimmedValue) => void
 * @param {function} onClose         cancel (Esc, backdrop, Cancel button)
 */
const TextInputDialog = ({
  open,
  title,
  description,
  label,
  placeholder,
  initialValue = "",
  submitLabel = translate("common.ok"),
  cancelLabel = translate("common.cancel"),
  inputType = "text",
  onSubmit,
  onClose,
}) => {
  useUiTranslation();
  const [value, setValue] = useState(initialValue);
  const inputRef = useRef(null);

  // A fresh draft every time the dialog opens; a stale one from the last use
  // must not leak into the next.
  useEffect(() => {
    if (open) setValue(initialValue ?? "");
  }, [open, initialValue]);

  const trimmed = value.trim();

  const submit = () => {
    if (!trimmed) return;
    onSubmit?.(trimmed);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  };

  return (
    // autoFocus alone loses to a caller that keeps focus in its own editor
    // (the Notes link button holds the text selection on mouse-down), so the
    // field takes focus again once the dialog has finished opening. The draft
    // arrives after autoFocus has already selected an empty field, so it is
    // selected here too, or typing would add to the old value instead of
    // replacing it.
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      TransitionProps={{
        onEntered: () => {
          inputRef.current?.focus();
          inputRef.current?.select();
        },
      }}
    >
      <DialogTitle sx={{ fontSize: "0.9rem" }}>{title}</DialogTitle>
      <DialogContent>
        {description && (
          <DialogContentText variant="body2" sx={{ mb: 1 }}>
            {description}
          </DialogContentText>
        )}
        <TextField
          autoFocus
          inputRef={inputRef}
          fullWidth
          size="small"
          type={inputType}
          label={label}
          placeholder={placeholder}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={(e) => e.target.select()}
          inputProps={{ "aria-label": label || title }}
          sx={{ mt: 1 }}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} size="small">
          {cancelLabel}
        </Button>
        <Button onClick={submit} variant="contained" size="small" disabled={!trimmed}>
          {submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default TextInputDialog;
