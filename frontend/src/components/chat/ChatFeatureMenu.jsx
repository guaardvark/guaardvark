// The grid icon beside the chat box, and the grid of features it opens
// (chatFeatures.js). The chat page and the floating chat both use it; each
// says what prefilling, attaching and switching a mode mean for its own box.
import AppsIcon from "@mui/icons-material/Apps";
import { Box, ButtonBase, IconButton, Popover, Tooltip, Typography } from "@mui/material";
import React, { useCallback, useEffect, useRef, useState } from "react";

import { BASE_URL } from "../../api/apiClient";
import { useUiTranslation } from "../../i18n";
import { CHAT_FEATURES, CHAT_FEATURE_COLUMNS } from "./chatFeatures";

// What this install can do: its chat tools and the web access setting. Read
// once, and again when the grid opens a minute or more later. A failed read
// leaves every tile shown rather than hiding ones that may work.
const FRESH_MS = 60_000;
let cached = { at: 0, tools: null, webAccess: null };
let inflight = null;

function readAvailability() {
  if (Date.now() - cached.at < FRESH_MS) return Promise.resolve(cached);
  if (!inflight) {
    inflight = Promise.allSettled([
      fetch(`${BASE_URL}/tools`).then((r) => (r.ok ? r.json() : null)),
      fetch(`${BASE_URL}/settings/web_access`).then((r) => (r.ok ? r.json() : null)),
    ]).then(([tools, web]) => {
      const list = tools.status === "fulfilled" ? tools.value?.tools : null;
      const allow = web.status === "fulfilled" ? web.value?.data?.allow_web_search : undefined;
      cached = {
        at: Date.now(),
        tools: Array.isArray(list) ? new Set(list.map((tool) => tool.name)) : null,
        webAccess: typeof allow === "boolean" ? allow : null,
      };
      inflight = null;
      return cached;
    });
  }
  return inflight;
}

export default function ChatFeatureMenu({
  onPrefill,
  onAttach,
  onMode,
  modes = {},
  disabled = false,
  size = "medium",
}) {
  const { t } = useUiTranslation();
  const [anchor, setAnchor] = useState(null);
  const [avail, setAvail] = useState(cached);
  const triggerRef = useRef(null);
  const tileRefs = useRef([]);
  const returnFocus = useRef(false);

  useEffect(() => {
    let live = true;
    readAvailability().then((a) => live && setAvail(a)).catch(() => {});
    return () => { live = false; };
  }, []);

  const features = CHAT_FEATURES.filter(
    (f) => !avail.tools || (f.needs || []).every((name) => avail.tools.has(name)),
  );
  const isOff = (f) => Boolean(f.webAccess && avail.webAccess === false);

  const openGrid = (event) => {
    setAnchor(event.currentTarget);
    readAvailability().then(setAvail).catch(() => {});
  };

  // Escape or a click outside: back to the grid icon, once the grid has
  // closed (its focus trap holds focus until then).
  const dismiss = () => {
    returnFocus.current = true;
    setAnchor(null);
  };

  // A chosen tile hands focus to the chat box (or the file picker), so the
  // grid icon does not take it back.
  const choose = (f) => {
    returnFocus.current = false;
    setAnchor(null);
    if (f.mode) onMode?.(f.mode);
    else if (f.attach) onAttach?.(f.id);
    else onPrefill?.(f.prefill || t(`chat.features.${f.id}.starter`));
  };

  const focusFrom = useCallback((start, step) => {
    for (let i = start + step; i >= 0 && i < tileRefs.current.length; i += step) {
      const el = tileRefs.current[i];
      if (el && !el.disabled) {
        el.focus();
        return;
      }
    }
  }, []);

  const onGridKeyDown = (event) => {
    const step = {
      ArrowRight: 1, ArrowLeft: -1, ArrowDown: CHAT_FEATURE_COLUMNS, ArrowUp: -CHAT_FEATURE_COLUMNS,
    }[event.key];
    const at = tileRefs.current.indexOf(document.activeElement);
    if (!step || at < 0) return;
    event.preventDefault();
    focusFrom(at, step);
  };

  const small = size === "small";
  tileRefs.current = [];

  return (
    <>
      <Tooltip title={t("chat.features.open")}>
        <span style={{ alignSelf: "center" }}>
          <IconButton
            ref={triggerRef}
            onClick={openGrid}
            disabled={disabled}
            size={small ? "small" : "medium"}
            aria-haspopup="dialog"
            aria-expanded={Boolean(anchor)}
            aria-label={t("chat.features.open")}
            data-testid="chat-features-open"
            className={small ? "floating-chat-btn" : undefined}
            sx={{ color: "text.secondary", ...(small && { p: 0.25 }) }}
          >
            <AppsIcon sx={small ? { fontSize: 16 } : undefined} />
          </IconButton>
        </span>
      </Tooltip>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={dismiss}
        disableRestoreFocus
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        TransitionProps={{
          onEntered: () => focusFrom(-1, 1),
          onExited: () => { if (returnFocus.current) triggerRef.current?.focus(); },
        }}
        // Above the floating chat card (zIndex 1400), below tooltips.
        sx={{ zIndex: (theme) => theme.zIndex.snackbar + 1 }}
        // The slash-command popup's surface, so the two read as one family.
        slotProps={{ paper: { elevation: 8, sx: { p: 1, mb: 1, border: "1px solid", borderColor: "divider" } } }}
      >
        <Box
          role="group"
          aria-label={t("chat.features.open")}
          onKeyDown={onGridKeyDown}
          data-testid="chat-features-grid"
          sx={{
            display: "grid",
            gridTemplateColumns: `repeat(${CHAT_FEATURE_COLUMNS}, 1fr)`,
            gap: 0.5,
          }}
        >
          {features.map((f, i) => {
            const on = Boolean(f.mode && modes[f.mode]);
            const off = isOff(f);
            const label = t(`chat.features.${f.id}.label`);
            const hint = off ? t(`chat.features.${f.id}.off`) : t(`chat.features.${f.id}.hint`);
            const command = f.prefill ? f.prefill.trim() : f.mode === "plan" ? "/plan" : f.mode === "agent" ? "/agent" : "";
            return (
              <Tooltip key={f.id} title={command && !off ? `${hint} (${command})` : hint} placement="top">
                <span>
                  <ButtonBase
                    ref={(el) => { tileRefs.current[i] = el; }}
                    onClick={() => choose(f)}
                    disabled={off}
                    aria-pressed={f.mode ? on : undefined}
                    data-testid={`chat-feature-${f.id}`}
                    sx={{
                      width: 88,
                      height: 72,
                      flexDirection: "column",
                      // Icons in one line; a two-line label grows downward.
                      justifyContent: "flex-start",
                      pt: 1.25,
                      gap: 0.5,
                      borderRadius: 1.5,
                      color: on ? "primary.main" : "text.primary",
                      bgcolor: on ? "action.selected" : "transparent",
                      "&:hover, &.Mui-focusVisible": { bgcolor: "action.hover" },
                      "&.Mui-disabled": { opacity: 0.45 },
                    }}
                  >
                    <f.Icon fontSize="small" />
                    <Typography variant="caption" sx={{ lineHeight: 1.15, textAlign: "center", px: 0.5 }}>
                      {label}
                    </Typography>
                  </ButtonBase>
                </span>
              </Tooltip>
            );
          })}
        </Box>
      </Popover>
    </>
  );
}
