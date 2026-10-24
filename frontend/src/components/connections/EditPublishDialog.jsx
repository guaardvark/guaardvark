// frontend/src/components/connections/EditPublishDialog.jsx
// Edit a publish that waits for approval, with the composer's fields and the
// target platform's limits. Saving keeps it waiting; approving stays a
// separate click, and an approval only applies to the revision it was shown.
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Paper,
  Stack,
  Typography,
} from "@mui/material";

import { editPublish, fetchProviders, preflightPublishEdit } from "../../api/connectionsService";
import { ActionButton } from "../settings/ui";
import PostFields, { useDebounced } from "../social/PostFields";
import { defaultVisibility, describeRateLimit, visibilityChoices } from "../social/platformRules";
import { draftedBy, editChanges, editableValues, withDisclosureLine } from "./publishEdit";
import { useUiTranslation } from "../../i18n";

/**
 * @param {boolean}  open
 * @param {object}   record      the publish record (GET /publishes/<id>)
 * @param {object}   [spec]      its provider spec; fetched when not given
 * @param {object}   [connection] its connection (GET /connections), for the
 *                               visibilities it has turned on
 * @param {string}   [accountLabel]
 * @param {function} onClose
 * @param {function} onSaved     called with the saved record and the changed fields
 */
const EditPublishDialog = ({
  open,
  record: givenRecord,
  spec: givenSpec = null,
  connection = null,
  accountLabel = "",
  onClose,
  onSaved,
}) => {
  const { t } = useUiTranslation();
  // Parents clear the record as they close the dialog; the closing fade
  // keeps showing the last one.
  const lastRecord = useRef(givenRecord);
  if (givenRecord) lastRecord.current = givenRecord;
  const record = givenRecord || lastRecord.current;
  const [spec, setSpec] = useState(givenSpec);
  const [initial, setInitial] = useState(null);
  const [values, setValues] = useState(null);
  const [revision, setRevision] = useState(0);
  const [preflight, setPreflight] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  // Start from the record as it was when the dialog opened; a later poll of
  // the same record must not overwrite what the person is typing.
  useEffect(() => {
    if (!open || !record) return;
    const start = editableValues(record);
    setInitial(start);
    setValues(start);
    setRevision(record.revision || 0);
    setPreflight(null);
    setError(null);
  }, [open, record?.id]);

  const platform = record?.platform;
  useEffect(() => {
    if (!open || !platform) return;
    if (givenSpec) {
      setSpec(givenSpec);
      return;
    }
    let cancelled = false;
    setSpec(null);
    fetchProviders("social")
      .then((specs) => {
        if (cancelled) return;
        const found = specs.find((s) => s.provider === platform);
        if (found) setSpec(found);
        else setError(t("connections.editDialog.unavailable", { platform }));
      })
      .catch((e) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [open, platform, givenSpec]);

  const changes = useMemo(
    () => (initial && values ? editChanges(initial, values) : {}),
    [initial, values],
  );
  const debouncedChanges = useDebounced(changes);

  const recordId = record?.id;
  useEffect(() => {
    if (!open || !recordId || !initial) return;
    let cancelled = false;
    preflightPublishEdit(recordId, debouncedChanges)
      .then((result) => !cancelled && setPreflight(result))
      .catch(() => !cancelled && setPreflight(null));
    return () => {
      cancelled = true;
    };
  }, [open, recordId, initial, debouncedChanges]);

  const entry = Object.values(preflight?.per_connection || {})[0] || null;
  const violations = [...(preflight?.violations || []), ...(entry?.violations || [])];
  const disclosureRule = spec?.capabilities?.ai_disclosure;
  const disclosureLocked = Boolean(entry?.ai_disclosure?.required);
  const changed = Object.keys(changes).length > 0;
  const platformLabel = spec?.label || record?.platform || "";
  // The preflight answered for the debounced edit, so compare with that text.
  const checkedBody = "body" in debouncedChanges ? debouncedChanges.body : initial?.body;
  const goesOutAs = entry?.body !== undefined && entry.body !== checkedBody ? entry.body : null;
  // A record without a stored visibility goes out with the platform's
  // default, so that is what the select shows.
  const visibilityTargets =
    spec && values
      ? [
          {
            key: String(record?.connection_id ?? ""),
            label: accountLabel || platformLabel,
            value: values.visibility || defaultVisibility(spec),
            choices: visibilityChoices(spec, connection),
          },
        ]
      : [];

  const handleChange = (patch) => {
    setValues((prev) => {
      const next = { ...prev, ...patch };
      // Where the AI label is a line in the post, the switch adds or removes
      // it in the text box, so the box shows what goes out.
      if ("aiDisclosure" in patch) {
        next.body = withDisclosureLine(next.body, patch.aiDisclosure, disclosureRule);
      }
      return next;
    });
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const result = await editPublish(record.id, { ...changes, revision, edited_by: "ui" });
      onSaved?.(result.publish, result.changed || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const drafted = draftedBy(record);
  const ready = Boolean(spec && values && record);

  return (
    <Dialog open={open} onClose={saving ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("connections.editDialog.title")}</DialogTitle>
      <DialogContent dividers>
        {!ready ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            {error ? <Alert severity="error">{error}</Alert> : <CircularProgress size={28} />}
          </Box>
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            <Typography variant="body2" color="text.secondary">
              {accountLabel ? `${accountLabel} · ` : ""}
              {platformLabel}
            </Typography>
            {drafted && (
              <Alert severity="info">
                {t("connections.editDialog.draftedNote", { drafted })}
              </Alert>
            )}
            {error && <Alert severity="error">{error}</Alert>}

            <PostFields
              specs={[spec]}
              values={values}
              onChange={handleChange}
              visibilityTargets={visibilityTargets}
              onVisibilityChange={(_key, value) => handleChange({ visibility: value })}
              preflight={preflight}
              bodyLabel={
                record?.media_refs?.length
                  ? t("connections.editDialog.caption")
                  : t("connections.editDialog.text")
              }
              showLink={Boolean(spec.capabilities?.supports_link || record?.link_url)}
              disclosureLocked={disclosureLocked}
              scheduleRefusal={spec.capabilities?.schedule_refusal || ""}
              scheduleHelp={t("connections.editDialog.scheduleHelp")}
            />

            {goesOutAs !== null && (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  {t("connections.editDialog.goesOutAs", { platform: platformLabel })}
                </Typography>
                <Paper variant="outlined" sx={{ p: 1.5, mt: 0.5, whiteSpace: "pre-wrap" }}>
                  <Typography variant="body2">{goesOutAs}</Typography>
                </Paper>
              </Box>
            )}

            {(spec.capabilities?.rate_limits || []).map((limit) => (
              <Typography key={limit.window_seconds} variant="caption" color="text.secondary">
                {describeRateLimit(limit)}
              </Typography>
            ))}

            {violations.length > 0 && (
              <Alert severity="warning">
                <Stack spacing={0.5}>
                  {violations.map((v) => (
                    <Typography key={v} variant="body2">
                      {v}
                    </Typography>
                  ))}
                </Stack>
              </Alert>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Typography variant="caption" color="text.secondary" sx={{ flex: 1, pl: 1 }}>
          {t("connections.editDialog.savingNote")}
        </Typography>
        <ActionButton onClick={onClose} disabled={saving}>
          {t("connections.editDialog.cancel")}
        </ActionButton>
        <ActionButton
          kind="primary"
          loading={saving}
          disabled={!ready || !changed || violations.length > 0}
          onClick={handleSave}
        >
          {t("connections.editDialog.save")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

export default EditPublishDialog;
