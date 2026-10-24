// frontend/src/components/social/PostFields.jsx
// The inputs of one post, shared by the publish composer and the Approvals
// edit: title, the platforms' per-post fields, text, link, tags, visibility,
// the AI label and the time. Limits come from the targets' declared
// capabilities; the strictest one governs. Visibility is per target, since
// each platform has its own choices and default.
import React, { useEffect, useState } from "react";
import {
  Box,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";

import { fixedVisibilityNote, mergePostFields, strictestLimit } from "./platformRules";
import { translate, useUiTranslation } from "../../i18n";

/** Debounce so preflight doesn't fire on every keystroke. */
export const useDebounced = (value, delay = 400) => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
};

/** A Date as the value a datetime-local input takes, in local time. */
export const toLocalInput = (date) => {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
};

/** The next whole hour at least an hour away: a sensible first suggestion. */
export const suggestedTime = () => {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setMinutes(0, 0, 0);
  return toLocalInput(new Date(date.getTime() + 60 * 60 * 1000));
};

/** The datetime-local value as ISO with the browser's offset, or null. */
export const scheduledIsoOf = (scheduleOn, scheduledAt) => {
  if (!scheduleOn || !scheduledAt) return null;
  const date = new Date(scheduledAt);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
};

/**
 * @param {object[]} specs      provider specs of the targets
 * @param {object}   values     { title, body, linkUrl, tags, aiDisclosure,
 *                              postOptions, scheduleOn, scheduledAt }
 * @param {function} onChange   called with the fields that changed
 * @param {object[]} [visibilityTargets] one per target: { key, label, value,
 *                              choices: [{ value, disabled, reason }] }; value
 *                              is what the target is sent with, and a disabled
 *                              choice's reason shows under its select
 * @param {function} [onVisibilityChange] called with (key, value)
 * @param {object}   [preflight] a preflight result; its per_connection entries
 *                              give the AI label rules and who sends a timed post
 * @param {string}   [bodyLabel]
 * @param {boolean}  [showLink]  offer the link input
 * @param {boolean}  [disclosureLocked] the target's rule keeps the AI label on
 * @param {string}   [scheduleRefusal]  why this target takes no time; the time
 *                              switch is then offered only to turn a time off
 * @param {string}   [scheduleHelp]
 */
const PostFields = ({
  specs,
  values,
  onChange,
  visibilityTargets = [],
  onVisibilityChange,
  preflight = null,
  bodyLabel = translate("social.postFields.text"),
  showLink = false,
  disclosureLocked = false,
  scheduleRefusal = "",
  scheduleHelp = translate("social.postFields.scheduleHelp"),
}) => {
  const { t } = useUiTranslation();
  const charLimit = strictestLimit(specs, "max_text_chars");
  const titleLimit = strictestLimit(specs, "max_title_chars");
  const supportsTitle = specs.some((s) => s.capabilities?.supports_title);
  const supportsTags = specs.some((s) => s.capabilities?.supports_tags);
  const titleRequiredBy = specs
    .filter((s) => s.capabilities?.requires_title)
    .map((s) => s.label || s.provider);
  const postFields = mergePostFields(specs);
  // A target with one visibility has nothing to pick; it is named in a line instead.
  const visibilitySelects = visibilityTargets.filter((t) => t.choices.length > 1);
  const fixedVisibilities = visibilityTargets.filter((t) => t.choices.length === 1);
  const { title, body, linkUrl, tags, aiDisclosure, postOptions, scheduleOn, scheduledAt } = values;
  const overLimit = charLimit !== null && body.length > charLimit;

  // Destinations whose terms require the AI label for this post: the switch
  // cannot turn it off there, and each one says why.
  const requiredDisclosures = Object.values(preflight?.per_connection || {})
    .filter((entry) => entry.ai_disclosure?.required)
    .map((entry) => ({ label: entry.label, rule: entry.ai_disclosure.rule }));
  const scheduleNotes = Object.values(preflight?.per_connection || {})
    .filter((entry) => entry.schedule?.note)
    .map((entry) => ({ label: entry.label, note: entry.schedule.note }));

  return (
    <>
      {supportsTitle && (
        <TextField
          label={t("social.postFields.title")}
          value={title}
          onChange={(e) => onChange({ title: e.target.value })}
          size="small"
          error={titleLimit !== null && title.trim().length > titleLimit}
          helperText={
            [
              titleRequiredBy.length
                ? t("social.postFields.requiredFor", { targets: titleRequiredBy.join(", ") })
                : "",
              titleLimit !== null
                ? t("social.postFields.charactersOfLimit", { count: title.trim().length, limit: titleLimit })
                : "",
            ]
              .filter(Boolean)
              .join(" ") || undefined
          }
        />
      )}

      {postFields.map((field) => (
        <TextField
          key={field.name}
          label={field.label}
          value={postOptions[field.name] ?? ""}
          onChange={(e) => onChange({ postOptions: { ...postOptions, [field.name]: e.target.value } })}
          placeholder={field.placeholder || undefined}
          size="small"
          helperText={`${
            field.required
              ? t("social.postFields.requiredFor", { targets: field.for.join(", ") })
              : t("social.postFields.usedFor", { targets: field.for.join(", ") })
          } ${field.help || ""}`.trim()}
        />
      ))}

      <TextField
        label={bodyLabel}
        value={body}
        onChange={(e) => onChange({ body: e.target.value })}
        multiline
        minRows={3}
        size="small"
        error={overLimit}
        helperText={
          charLimit !== null
            ? t("social.postFields.charactersOfLimit", { count: body.length, limit: charLimit })
            : t("social.postFields.characters", { count: body.length })
        }
      />

      {showLink && (
        <TextField
          label={t("social.postFields.link")}
          value={linkUrl || ""}
          onChange={(e) => onChange({ linkUrl: e.target.value })}
          size="small"
          helperText={t("social.postFields.linkHelp")}
        />
      )}

      {supportsTags && (
        <TextField
          label={t("social.postFields.tags")}
          value={tags}
          onChange={(e) => onChange({ tags: e.target.value })}
          size="small"
          helperText={t("social.postFields.tagsHelp")}
        />
      )}

      {visibilitySelects.map((target) => (
        <TextField
          key={target.key}
          select
          label={
            visibilityTargets.length > 1
              ? t("social.postFields.visibilityOn", { target: target.label })
              : t("social.postFields.visibility")
          }
          value={target.value}
          onChange={(e) => onVisibilityChange?.(target.key, e.target.value)}
          size="small"
          helperText={target.choices.find((choice) => choice.disabled)?.reason || undefined}
        >
          {target.choices.map((choice) => (
            <MenuItem key={choice.value} value={choice.value} disabled={choice.disabled}>
              {choice.value}
              {choice.value !== "private" && (
                <WarningAmberIcon fontSize="small" color="warning" sx={{ ml: 1, verticalAlign: "middle" }} />
              )}
            </MenuItem>
          ))}
        </TextField>
      ))}
      {fixedVisibilities.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          {fixedVisibilityNote(fixedVisibilities)}
        </Typography>
      )}

      <Box>
        <FormControlLabel
          control={
            <Switch
              checked={disclosureLocked || aiDisclosure}
              disabled={disclosureLocked}
              onChange={(e) => onChange({ aiDisclosure: e.target.checked })}
              size="small"
            />
          }
          label={t("social.postFields.aiLabel")}
        />
        {requiredDisclosures.map((entry) => (
          <Typography key={entry.label} variant="caption" color="text.secondary" sx={{ display: "block" }}>
            {t("social.postFields.alwaysOn", { target: entry.label, rule: entry.rule })}
          </Typography>
        ))}
      </Box>

      <Box>
        <FormControlLabel
          control={
            <Switch
              checked={scheduleOn}
              disabled={Boolean(scheduleRefusal) && !scheduleOn}
              onChange={(e) => {
                const patch = { scheduleOn: e.target.checked };
                if (e.target.checked && !scheduledAt) patch.scheduledAt = suggestedTime();
                onChange(patch);
              }}
              size="small"
            />
          }
          label={t("social.postFields.schedule")}
        />
        {scheduleRefusal && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
            {scheduleRefusal}
          </Typography>
        )}
        {scheduleOn && (
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            <TextField
              type="datetime-local"
              label={t("social.postFields.goesOutAt")}
              value={scheduledAt}
              onChange={(e) => onChange({ scheduledAt: e.target.value })}
              size="small"
              InputLabelProps={{ shrink: true }}
              helperText={scheduleHelp}
            />
            {scheduleNotes.map((entry) => (
              <Typography key={entry.label} variant="caption" color="text.secondary">
                {entry.label}: {entry.note}
              </Typography>
            ))}
          </Stack>
        )}
      </Box>
    </>
  );
};

export default PostFields;
