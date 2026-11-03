import React, { useEffect, useState } from "react";
import { Typography } from "@mui/material";
import { uiLocale, useUiTranslation } from "../../i18n";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const DEBOUNCE_MS = 400;

const fmt = (n) => Number(n).toLocaleString(uiLocale());

/**
 * Tokens a song's text takes of what ACE-Step 1.5 reads, counted by the backend
 * with the model's own tokenizer. Fields follow the caption, so they count too.
 */
export default function Ace15TextCounter({ caption, lyrics, bpm, keyscale, timesignature, durationS, vocalLanguage }) {
  const { t } = useUiTranslation();
  const [usage, setUsage] = useState(null);

  useEffect(() => {
    if (!(caption || "").trim()) {
      setUsage(null);
      return undefined;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const resp = await fetch(`${API_BASE}/audio-foundry/text-usage`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            caption, lyrics, bpm, keyscale, timesignature,
            duration_s: durationS, vocal_language: vocalLanguage,
          }),
          signal: ctrl.signal,
        });
        const body = await resp.json();
        setUsage(resp.ok ? body : null);
      } catch (err) {
        if (err.name !== "AbortError") setUsage(null);
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [caption, lyrics, bpm, keyscale, timesignature, durationS, vocalLanguage]);

  if (!usage?.caption) return null;

  const { tokens, limit } = usage.caption;
  const over = tokens > limit;
  const near = !over && tokens > limit * 0.9;
  const label = t("audio.music.captionTokens", { tokens: fmt(tokens), limit: fmt(limit) });
  const lyricsOver = usage.lyrics?.tokens > usage.lyrics?.limit;
  return (
    <>
      <Typography
        variant="caption"
        data-testid="ace15-text-counter"
        sx={{ display: "block", color: over ? "error.main" : near ? "warning.main" : "text.secondary" }}
      >
        {over ? t("audio.music.captionOver", { label }) : label}
      </Typography>
      {lyricsOver && (
        <Typography variant="caption" sx={{ display: "block", color: "error.main" }}>
          {t("audio.music.lyricsOver", { tokens: fmt(usage.lyrics.tokens), limit: fmt(usage.lyrics.limit) })}
        </Typography>
      )}
    </>
  );
}
