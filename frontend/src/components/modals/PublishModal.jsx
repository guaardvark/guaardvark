import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";

import {
  fetchConnections,
  fetchProviders,
  preflightPublish,
  queuePublish,
} from "../../api/connectionsService";
import { defaultVisibility, strictestLimit, visibilityChoices } from "../social/platformRules";
import PostFields, { scheduledIsoOf, useDebounced } from "../social/PostFields";
import { translate, useUiTranslation } from "../../i18n";

/**
 * Compose a post and publish selected assets, or text alone when there are
 * none, to one or more connections. Validation limits come from each
 * provider's capabilities, never hardcoded; the AI disclosure rule and who
 * sends a scheduled post come back per destination from preflight. Fields a
 * platform needs per post (its post_fields) appear while it is selected.
 *
 * Each destination has its own visibility, starting at its platform's
 * declared default; what the select shows is what is sent, explicitly.
 */
// One empty list, so a text-only post does not reset the form on every render.
const NO_DOCUMENTS = [];

const PublishModal = ({ open, onClose, documents = NO_DOCUMENTS, onFeedback }) => {
  const { t } = useUiTranslation();
  const [connections, setConnections] = useState([]);
  const [specs, setSpecs] = useState({});
  const [selected, setSelected] = useState([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [tags, setTags] = useState("");
  // Visibilities the person picked, by connection id; the rest go out with
  // their platform's default (visibilityTargets).
  const [visibilities, setVisibilities] = useState({});
  const [aiDisclosure, setAiDisclosure] = useState(false);
  const [postOptions, setPostOptions] = useState({});
  const [scheduleOn, setScheduleOn] = useState(false);
  const [scheduledAt, setScheduledAt] = useState("");
  const [preflight, setPreflight] = useState(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const documentIds = useMemo(
    () => documents.map((d) => d.id).filter(Boolean),
    [documents],
  );

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchConnections("social"), fetchProviders("social")])
      .then(([conns, providerSpecs]) => {
        if (cancelled) return;
        setConnections(conns);
        setSpecs(Object.fromEntries(providerSpecs.map((s) => [s.provider, s])));
      })
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const first = documents[0];
    setTitle(first?.filename || "");
    setBody(first?.metadata?.original_prompt || first?.summary || "");
    setTags(Array.isArray(first?.tags) ? first.tags.join(", ") : "");
    setSelected([]);
    setVisibilities({});
    setAiDisclosure(false);
    setPostOptions({});
    setScheduleOn(false);
    setScheduledAt("");
    setPreflight(null);
    setError(null);
  }, [open, documents]);

  // ISO with the browser's offset, so the server reads the same moment.
  const scheduledIso = useMemo(() => scheduledIsoOf(scheduleOn, scheduledAt), [scheduleOn, scheduledAt]);

  const selectedConnections = useMemo(
    () =>
      selected
        .map((id) => connections.find((c) => c.id === id))
        .filter((c) => c && specs[c.provider]),
    [selected, connections, specs],
  );
  const selectedSpecs = useMemo(
    () => selectedConnections.map((c) => specs[c.provider]),
    [selectedConnections, specs],
  );

  const visibilityTargets = useMemo(
    () =>
      selectedConnections.map((conn) => {
        const spec = specs[conn.provider];
        return {
          key: String(conn.id),
          label: conn.display_name || conn.handle || spec.label || conn.provider,
          value: visibilities[conn.id] ?? defaultVisibility(spec),
          choices: visibilityChoices(spec, conn),
        };
      }),
    [selectedConnections, specs, visibilities],
  );
  // Sent for every target, so none falls back to a default the select did not show.
  const sentVisibilities = useMemo(
    () => Object.fromEntries(visibilityTargets.map((t) => [t.key, t.value])),
    [visibilityTargets],
  );

  // The strictest limit across the selected targets governs the counter.
  const charLimit = useMemo(() => strictestLimit(selectedSpecs, "max_text_chars"), [selectedSpecs]);
  const supportsTitle = selectedSpecs.some((s) => s.capabilities?.supports_title);

  const debouncedBody = useDebounced(body);
  const debouncedTitle = useDebounced(title);
  const debouncedOptions = useDebounced(postOptions);

  useEffect(() => {
    if (!open || !selected.length) {
      setPreflight(null);
      return;
    }
    let cancelled = false;
    preflightPublish({
      connection_ids: selected,
      document_ids: documentIds,
      body: debouncedBody,
      title: supportsTitle ? debouncedTitle : null,
      visibilities: sentVisibilities,
      scheduled_at: scheduledIso,
      ai_disclosure: aiDisclosure,
      post_options: debouncedOptions,
    })
      .then((result) => !cancelled && setPreflight(result))
      .catch(() => !cancelled && setPreflight(null));
    return () => {
      cancelled = true;
    };
  }, [
    open, selected, documentIds, debouncedBody, debouncedTitle,
    sentVisibilities, supportsTitle, scheduledIso, aiDisclosure, debouncedOptions,
  ]);

  const toggle = useCallback((id) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  }, []);

  const violationsFor = useCallback(
    (id) => preflight?.per_connection?.[String(id)]?.violations || [],
    [preflight],
  );

  // Top-level violations apply to every destination, e.g. public posting stopped.
  const allViolations = useMemo(() => {
    if (!preflight) return [];
    const general = preflight.violations || [];
    const perConnection = Object.entries(preflight.per_connection || {}).flatMap(([id, entry]) =>
      (entry.violations || []).map((v) => `${entry.label || id}: ${v}`),
    );
    return [...general, ...perConnection];
  }, [preflight]);

  const fieldValues = {
    title, body, tags, aiDisclosure, postOptions, scheduleOn, scheduledAt,
  };
  const setters = {
    title: setTitle,
    body: setBody,
    tags: setTags,
    aiDisclosure: setAiDisclosure,
    postOptions: setPostOptions,
    scheduleOn: setScheduleOn,
    scheduledAt: setScheduledAt,
  };
  const handleFieldChange = (patch) =>
    Object.entries(patch).forEach(([key, value]) => setters[key]?.(value));
  const handleVisibilityChange = (key, value) =>
    setVisibilities((prev) => ({ ...prev, [key]: value }));

  const handlePublish = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      const result = await queuePublish({
        connection_ids: selected,
        document_ids: documentIds,
        body,
        title: supportsTitle ? title : null,
        tags: tags
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        visibilities: sentVisibilities,
        requested_by: "ui",
        scheduled_at: scheduledIso,
        ai_disclosure: aiDisclosure,
        post_options: postOptions,
      });
      const count = result.count;
      onFeedback?.({
        open: true,
        message: result.requires_approval
          ? scheduledIso
            ? translate("dialogs.publish.scheduledForApproval", { count })
            : translate("dialogs.publish.queuedForApproval", { count })
          : scheduledIso
            ? translate("dialogs.publish.scheduled", { count })
            : translate("dialogs.publish.publishing", { count }),
        severity: "success",
      });
      onClose?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }, [
    selected, documentIds, body, title, tags, sentVisibilities, supportsTitle,
    scheduledIso, aiDisclosure, postOptions, onClose, onFeedback,
  ]);

  const overLimit = charLimit !== null && body.length > charLimit;
  const canPublish =
    selected.length > 0 &&
    !overLimit &&
    !allViolations.length &&
    !submitting &&
    (!scheduleOn || Boolean(scheduledIso));

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {documents.length
          ? documents.length > 1
            ? t("dialogs.publish.publishItems", { count: documents.length })
            : t("dialogs.publish.publishAsset")
          : t("dialogs.publish.newPost")}
      </DialogTitle>
      <DialogContent dividers>
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        ) : (
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}

            {!connections.length && (
              <Alert severity="info">
                {t("dialogs.publish.noAccounts")}
              </Alert>
            )}

            <Box>
              <Typography variant="subtitle2" gutterBottom>
                {t("dialogs.publish.destinations")}
              </Typography>
              <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
                {connections.map((conn) => {
                  const problems = violationsFor(conn.id);
                  const isSelected = selected.includes(conn.id);
                  const chip = (
                    <Chip
                      key={conn.id}
                      label={conn.display_name || conn.handle || conn.provider}
                      color={
                        isSelected && problems.length
                          ? "error"
                          : isSelected
                            ? "primary"
                            : "default"
                      }
                      variant={isSelected ? "filled" : "outlined"}
                      onClick={() => toggle(conn.id)}
                      disabled={!conn.enabled || !conn.has_credentials}
                    />
                  );
                  const reason = !conn.has_credentials
                    ? t("dialogs.publish.noCredentials")
                    : !conn.enabled
                      ? t("dialogs.publish.disabled")
                      : problems[0];
                  return reason ? (
                    <Tooltip key={conn.id} title={reason}>
                      <span>{chip}</span>
                    </Tooltip>
                  ) : (
                    chip
                  );
                })}
              </Stack>
            </Box>

            <Divider />

            <PostFields
              specs={selectedSpecs}
              values={fieldValues}
              onChange={handleFieldChange}
              visibilityTargets={visibilityTargets}
              onVisibilityChange={handleVisibilityChange}
              preflight={preflight}
              bodyLabel={documents.length ? t("dialogs.publish.caption") : t("dialogs.publish.text")}
            />

            {allViolations.length > 0 && (
              <Alert severity="warning">
                <Stack spacing={0.5}>
                  {allViolations.map((v) => (
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
        <Button onClick={onClose}>{t("dialogs.shared.cancel")}</Button>
        <Button variant="contained" onClick={handlePublish} disabled={!canPublish}>
          {submitting ? (scheduleOn ? t("dialogs.publish.schedulingButton") : t("dialogs.publish.publishingButton")) : scheduleOn ? t("dialogs.publish.schedule") : t("dialogs.publish.publish")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default PublishModal;
