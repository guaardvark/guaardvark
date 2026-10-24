import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from "@mui/material";

import {
  completeOAuth,
  createConnection,
  startOAuth,
  updateConnection,
} from "../../api/connectionsService";
import { codeFromPaste } from "../social/oauthCode";
import { translate, uiLocale, useUiTranslation, Trans } from "../../i18n";

const MASK_PLACEHOLDER = "••••••••••••••••";
const REDIRECT_PLACEHOLDER = "{redirect_uri}";

/** A setup step with the redirect URL this install uses written in. */
const SetupStep = ({ text, redirectUri }) => {
  const parts = text.split(REDIRECT_PLACEHOLDER);
  return parts.map((part, i) => (
    <React.Fragment key={i}>
      {part}
      {i < parts.length - 1 && (
        <Box component="code" sx={{ wordBreak: "break-all", userSelect: "all" }}>
          {redirectUri}
        </Box>
      )}
    </React.Fragment>
  ));
};

/**
 * Create or edit a connection. Fields are rendered from the provider spec, so
 * a provider added on the backend appears here without a frontend change.
 */
const ConnectionModal = ({ open, onClose, connection, providerSpec, onFeedback, onSaved }) => {
  const { t } = useUiTranslation();
  const spec = providerSpec;
  // A connection created in this dialog: an OAuth one stays open on it so
  // the person can authorize straight away.
  const [created, setCreated] = useState(null);
  const current = created || connection;
  const isEdit = Boolean(current?.id);

  const [accountSlug, setAccountSlug] = useState("default");
  const [displayName, setDisplayName] = useState("");
  const [config, setConfig] = useState({});
  const [secrets, setSecrets] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [oauth, setOauth] = useState(null);
  const [oauthCode, setOauthCode] = useState("");

  const isOAuth = spec?.auth_kinds?.includes("oauth2");
  const redirectUri =
    typeof window !== "undefined" ? `${window.location.origin}/connections` : "";

  useEffect(() => {
    if (!open) return;
    setCreated(null);
    setAccountSlug(connection?.account_slug || "default");
    setDisplayName(connection?.display_name || "");
    setConfig(connection?.config || {});
    setSecrets({});
    setError(null);
    setOauth(null);
    setOauthCode("");
  }, [open, connection]);

  const configuredFields = current?.credential_fields || [];
  const isEnvManaged = current?.credential_source === "env";
  const tokenExpiry = current?.token_expires_at ? new Date(current.token_expires_at) : null;

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const payload = {
        family: spec.family,
        provider: spec.provider,
        account_slug: accountSlug.trim() || "default",
        display_name: displayName,
        auth_kind: spec.auth_kinds[0],
        config,
        ...secrets,
      };
      const saved = isEdit
        ? await updateConnection(current.id, payload)
        : await createConnection(payload);
      if (isOAuth) setCreated(saved);
      onFeedback?.({
        open: true,
        message: isEdit ? translate("dialogs.connection.updated") : translate("dialogs.connection.created"),
        severity: "success",
      });
      onSaved?.(saved);
      if (!isOAuth) onClose?.();
      else setSecrets({});
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }, [
    spec, accountSlug, displayName, config, secrets, isEdit, current,
    isOAuth, onClose, onFeedback, onSaved,
  ]);

  const handleStartOAuth = useCallback(async () => {
    setError(null);
    try {
      const result = await startOAuth(current.id, redirectUri);
      setOauth(result);
    } catch (e) {
      setError(e.message);
    }
  }, [current, redirectUri]);

  const handleCompleteOAuth = useCallback(async () => {
    const pasted = codeFromPaste(oauthCode, oauth?.state);
    if (pasted.error) {
      setError(pasted.error);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await completeOAuth(current.id, pasted.code, redirectUri);
      onFeedback?.({
        open: true,
        message: result.message || translate("dialogs.connection.authorized"),
        severity: result.ok ? "success" : "warning",
      });
      onSaved?.(result.connection);
      onClose?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }, [current, oauth, oauthCode, redirectUri, onClose, onFeedback, onSaved]);

  if (!spec) return null;

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {isEdit ? t("dialogs.connection.editTitle", { provider: spec.label }) : t("dialogs.connection.connectTitle", { provider: spec.label })}
      </DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {spec.setup_help && (
            <Typography variant="body2" color="text.secondary">
              {spec.setup_help}
              {spec.docs_url && (
                <>
                  {" "}
                  <Link href={spec.docs_url} target="_blank" rel="noopener">
                    {t("dialogs.connection.documentation")}
                  </Link>
                </>
              )}
            </Typography>
          )}

          {spec.setup_steps?.length > 0 && (
            <Box component="ol" sx={{ m: 0, pl: 2.5 }}>
              {spec.setup_steps.map((step) => (
                <Typography component="li" variant="body2" key={step} sx={{ mb: 0.5 }}>
                  <SetupStep text={step} redirectUri={redirectUri} />
                </Typography>
              ))}
            </Box>
          )}

          {spec.notice && <Alert severity="info">{spec.notice}</Alert>}

          {spec.review_required && (
            <Alert severity="warning">
              {t("dialogs.connection.reviewRequired")}
            </Alert>
          )}

          {isEnvManaged && (
            <Alert severity="info">
              <Trans
                i18nKey="dialogs.connection.envManaged"
                values={{ envKey: current.credential_env_key }}
                components={{ code: <code /> }}
              />
            </Alert>
          )}

          {error && <Alert severity="error">{error}</Alert>}

          <TextField
            label={t("dialogs.connection.accountName")}
            value={accountSlug}
            onChange={(e) => setAccountSlug(e.target.value)}
            disabled={isEdit}
            size="small"
            helperText={t("dialogs.connection.accountNameHelp")}
          />
          <TextField
            label={t("dialogs.connection.displayName")}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            size="small"
          />

          {spec.config_fields.map((field) => (
            <TextField
              key={field.name}
              label={field.label}
              required={field.required}
              size="small"
              select={field.choices?.length > 0}
              value={config[field.name] ?? field.default ?? ""}
              onChange={(e) =>
                setConfig((prev) => ({ ...prev, [field.name]: e.target.value }))
              }
              helperText={field.help}
            >
              {field.choices?.map((choice) => (
                <MenuItem key={choice} value={choice}>
                  {choice}
                </MenuItem>
              ))}
            </TextField>
          ))}

          {!isEnvManaged &&
            spec.credential_fields.map((field) => {
              const alreadySet = configuredFields.includes(field.name);
              return (
                <TextField
                  key={field.name}
                  label={field.label}
                  type="password"
                  size="small"
                  autoComplete="new-password"
                  placeholder={alreadySet ? MASK_PLACEHOLDER : field.placeholder}
                  value={secrets[field.name] ?? ""}
                  onChange={(e) =>
                    setSecrets((prev) => ({ ...prev, [field.name]: e.target.value }))
                  }
                  helperText={
                    alreadySet
                      ? t("dialogs.connection.configured", { hint: current.credential_hint })
                      : field.help
                  }
                />
              );
            })}

          {isOAuth && !isEdit && (
            <Typography variant="body2" color="text.secondary">
              {t("dialogs.connection.saveFirst")}
            </Typography>
          )}

          {isOAuth && isEdit && (
            <Box>
              {tokenExpiry && (
                <Typography
                  variant="body2"
                  color={tokenExpiry < new Date() ? "error" : "text.secondary"}
                  sx={{ mb: 1 }}
                >
                  {tokenExpiry < new Date()
                    ? t("dialogs.connection.expired", { date: tokenExpiry.toLocaleDateString(uiLocale()) })
                    : t("dialogs.connection.lastsUntil", { date: tokenExpiry.toLocaleDateString(uiLocale()) })}
                </Typography>
              )}
              <Button variant="outlined" onClick={handleStartOAuth} size="small">
                {t("dialogs.connection.authorizeWith", { provider: spec.label })}
              </Button>
              {oauth && (
                <Stack spacing={1} sx={{ mt: 2 }}>
                  <Typography variant="body2">
                    {t("dialogs.connection.oauthInstructions")}
                  </Typography>
                  <TextField
                    value={oauth.authorize_url}
                    size="small"
                    InputProps={{ readOnly: true }}
                    onFocus={(e) => e.target.select()}
                  />
                  <TextField
                    label={t("dialogs.connection.addressOrCode")}
                    value={oauthCode}
                    onChange={(e) => setOauthCode(e.target.value)}
                    size="small"
                  />
                  <Button
                    variant="contained"
                    size="small"
                    disabled={!oauthCode.trim() || saving}
                    onClick={handleCompleteOAuth}
                  >
                    {t("dialogs.connection.complete")}
                  </Button>
                </Stack>
              )}
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{t("dialogs.shared.cancel")}</Button>
        <Button variant="contained" onClick={handleSave} disabled={saving}>
          {saving ? t("dialogs.connection.saving") : t("dialogs.shared.save")}
        </Button>
      </DialogActions>
    </Dialog>
  );
};

export default ConnectionModal;
