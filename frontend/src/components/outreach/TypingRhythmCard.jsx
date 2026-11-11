// Record how the person types, so watched posts are typed at their rhythm
// rather than one constant delay per key. Only the gap between keys and each
// key's class (letter, space, punctuation, line break) leave this box; the
// words typed are not sent or kept.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Paper, Stack, TextField, Typography } from "@mui/material";
import { clearTypingRhythm, fetchTypingRhythm, saveTypingRhythm } from "../../api/outreachService";
import { useUiTranslation } from "../../i18n";

const classOf = (key) => {
  if (key === "Enter") return "newline";
  if (key === " ") return "space";
  if (key.length !== 1) return "other";
  return /[\p{L}\p{N}]/u.test(key) ? "letter" : "punct";
};

const TypingRhythmCard = () => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const [text, setText] = useState("");
  const [count, setCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const eventsRef = useRef([]);

  const refresh = useCallback(async () => {
    try {
      setStatus(await fetchTypingRhythm());
    } catch (err) {
      setError(err.message);
    }
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const onKeyDown = (e) => {
    // A held key repeats at the keyboard's rate, not the person's.
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const cls = classOf(e.key);
    // Shift and the like are not keystrokes of the text.
    if (cls === "other" && !["Backspace", "Delete"].includes(e.key)) return;
    eventsRef.current.push({ c: cls, t: performance.now() });
    if (cls !== "other") setCount((n) => n + 1);
  };

  const reset = () => {
    eventsRef.current = [];
    setText("");
    setCount(0);
  };

  const run = async (action) => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await action());
      reset();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const needed = status?.min_keys ?? 200;
  return (
    <Paper sx={{ p: 2, mt: 2 }}>
      <Typography variant="subtitle2">{t("outreach.typing.title")}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {t("outreach.typing.explain")}
      </Typography>
      <Typography variant="body2" sx={{ mb: 1 }}>
        {status?.recorded
          ? t("outreach.typing.recorded", { keys: status.keys, ms: status.median_letter_ms })
          : t("outreach.typing.notRecorded", { ms: status?.median_letter_ms ?? "…" })}
      </Typography>
      <TextField
        label={t("outreach.typing.boxLabel")}
        placeholder={t("outreach.typing.placeholder")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={(e) => e.preventDefault()}
        multiline
        minRows={3}
        fullWidth
        size="small"
        inputProps={{ spellCheck: false, autoComplete: "off" }}
      />
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
        <Button
          variant="contained"
          size="small"
          disabled={busy || count < needed}
          onClick={() => run(() => saveTypingRhythm(eventsRef.current))}
        >
          {t("outreach.typing.save")}
        </Button>
        {status?.recorded && (
          <Button size="small" disabled={busy} onClick={() => run(clearTypingRhythm)}>
            {t("outreach.typing.clear")}
          </Button>
        )}
        <Box sx={{ flex: 1 }} />
        <Typography variant="caption" color="text.secondary">
          {t("outreach.typing.progress", { count, needed })}
        </Typography>
      </Stack>
      {error && (
        <Alert severity="error" sx={{ mt: 1 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
    </Paper>
  );
};

export default TypingRhythmCard;
