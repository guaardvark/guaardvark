// A prompt TextField that keeps its text to itself while someone types and
// hands it to the page after a short pause, on blur, and when it unmounts. A
// long page then re-renders once per pause instead of on every keystroke. A
// value the page sets itself (a retry, the examples, Director, Clear) replaces
// what the field shows. ``flushRef.current()`` hands over anything still
// waiting and returns the text, for an action that must not miss the last
// keystrokes (Start).

import React, { useEffect, useRef, useState } from "react";
import { TextField } from "@mui/material";

const COMMIT_DELAY_MS = 250;

const DeferredPromptField = ({ value, onCommit, onBlur, flushRef, ...props }) => {
  const [text, setText] = useState(value);
  const committedRef = useRef(value);
  const pendingRef = useRef(null);
  const timerRef = useRef(null);
  const onCommitRef = useRef(onCommit);
  onCommitRef.current = onCommit;

  const commit = (next) => {
    clearTimeout(timerRef.current);
    timerRef.current = null;
    pendingRef.current = null;
    if (next !== committedRef.current) {
      committedRef.current = next;
      onCommitRef.current(next);
    }
  };

  if (flushRef) {
    flushRef.current = () => {
      if (pendingRef.current !== null) commit(pendingRef.current);
      return committedRef.current;
    };
  }

  useEffect(() => {
    if (value !== committedRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
      pendingRef.current = null;
      committedRef.current = value;
      setText(value);
    }
  }, [value]);

  // Text typed just before the field goes away (switching single and bulk
  // mode) still reaches the page.
  useEffect(() => () => {
    if (pendingRef.current !== null) commit(pendingRef.current);
    if (flushRef) flushRef.current = null;
  }, []);

  return (
    <TextField
      {...props}
      value={text}
      onChange={(event) => {
        const next = event.target.value;
        setText(next);
        pendingRef.current = next;
        clearTimeout(timerRef.current);
        timerRef.current = setTimeout(() => commit(next), COMMIT_DELAY_MS);
      }}
      onBlur={(event) => {
        commit(event.target.value);
        if (onBlur) onBlur(event);
      }}
    />
  );
};

export default DeferredPromptField;
