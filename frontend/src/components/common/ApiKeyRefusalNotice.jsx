// frontend/src/components/common/ApiKeyRefusalNotice.jsx
// What a browser sees when a protected route refuses it (backend/utils/
// auth_guard.py): the advice from describeAuthRefusal and a link to
// Settings → Access.
//
// ApiKeyRefusalNotice is mounted once in App and answers AUTH_REFUSED_EVENT from
// any request, so pages that only log their errors still tell the person what
// to do. Each kind of refusal is shown once until this browser signs in or out,
// so a page that polls a protected route does not repeat it. It stays quiet on
// the Settings page, which shows the state itself.
// ApiKeyRefusalAlert is the same advice inline, for pages that show the error
// where the action was.
/* eslint-env browser */

import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, IconButton, Snackbar } from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import { useNavigate } from "react-router-dom";

import {
  API_KEY_SETTINGS_PATH,
  AUTH_REFUSED_EVENT,
  describeAuthRefusal,
  onSessionChanged,
} from "../../api/apiAuth";

const LINK_LABEL = "Settings → Access";
// How many ApiKeyRefusalAlerts are on screen. The notice waits a moment and
// stays quiet when a page already shows the advice where the action was.
let inlineAlerts = 0;
const INLINE_GRACE_MS = 400;

export function ApiKeyRefusalAlert({ message, severity = "info", sx, onFollow }) {
  const navigate = useNavigate();
  useEffect(() => {
    inlineAlerts += 1;
    return () => {
      inlineAlerts -= 1;
    };
  }, []);
  return (
    <Alert
      severity={severity}
      sx={sx}
      action={
        <Button
          color="inherit"
          size="small"
          onClick={() => {
            onFollow?.();
            navigate(API_KEY_SETTINGS_PATH);
          }}
          sx={{ whiteSpace: "nowrap" }}
        >
          {LINK_LABEL}
        </Button>
      }
    >
      {message}
    </Alert>
  );
}

ApiKeyRefusalAlert.propTypes = {
  message: PropTypes.node.isRequired,
  severity: PropTypes.string,
  sx: PropTypes.object,
  // Called before the link opens Settings → Access (a dialog closes itself).
  onFollow: PropTypes.func,
};

export default function ApiKeyRefusalNotice() {
  const navigate = useNavigate();
  const [notice, setNotice] = useState(null);
  const shown = useRef(new Set());

  useEffect(() => {
    const timers = new Set();
    const onRefused = (event) => {
      const code = event.detail?.code;
      if (!code || window.location.pathname.startsWith("/settings")) return;
      const rejected = Boolean(event.detail?.rejected);
      const seen = `${code}:${rejected}`;
      if (shown.current.has(seen)) return;
      shown.current.add(seen);
      const timer = window.setTimeout(() => {
        timers.delete(timer);
        if (inlineAlerts === 0) setNotice(describeAuthRefusal(code, rejected));
      }, INLINE_GRACE_MS);
      timers.add(timer);
    };
    window.addEventListener(AUTH_REFUSED_EVENT, onRefused);
    const stopWatching = onSessionChanged(() => {
      shown.current.clear();
      setNotice(null);
    });
    return () => {
      timers.forEach((t) => window.clearTimeout(t));
      window.removeEventListener(AUTH_REFUSED_EVENT, onRefused);
      stopWatching();
    };
  }, []);

  const close = (_event, reason) => {
    if (reason !== "clickaway") setNotice(null);
  };

  return (
    <Snackbar
      open={Boolean(notice)}
      onClose={close}
      autoHideDuration={15000}
      anchorOrigin={{ vertical: "top", horizontal: "center" }}
    >
      <Alert
        severity="warning"
        variant="filled"
        sx={{ maxWidth: 640, alignItems: "center" }}
        action={
          <>
            <Button
              color="inherit"
              size="small"
              sx={{ whiteSpace: "nowrap" }}
              onClick={() => {
                setNotice(null);
                navigate(API_KEY_SETTINGS_PATH);
              }}
            >
              {LINK_LABEL}
            </Button>
            <IconButton color="inherit" size="small" aria-label="Close" onClick={() => setNotice(null)}>
              <CloseIcon fontSize="small" />
            </IconButton>
          </>
        }
      >
        {notice}
      </Alert>
    </Snackbar>
  );
}
