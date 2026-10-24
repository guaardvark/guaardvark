import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  Divider,
  Grid,
  Link,
  Menu,
  MenuItem,
  Snackbar,
  Stack,
  Switch,
  Tab,
  Tabs,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import LockIcon from "@mui/icons-material/Lock";
import LockOpenIcon from "@mui/icons-material/LockOpen";

import ConnectionModal from "../components/modals/ConnectionModal";
import {
  deleteConnection,
  fetchConnections,
  fetchEnvironment,
  fetchProviders,
  fetchPublishSettings,
  fetchStoreHealth,
  testConnection,
  updateConnection,
  updatePublishSettings,
} from "../api/connectionsService";
import { translate, useUiTranslation } from "../i18n";

const families = () => [
  { key: "social", label: translate("connections.page.families.social") },
  { key: "ai_provider", label: translate("connections.page.families.aiProviders") },
  { key: "mcp_server", label: translate("connections.page.families.mcp") },
];

const STATUS_COLOR = {
  connected: "success",
  error: "error",
  expired: "warning",
  disabled: "default",
  unconfigured: "default",
};

const statusLabels = () => ({
  connected: translate("connections.page.status.connected"),
  error: translate("connections.page.status.error"),
  expired: translate("connections.page.status.expired"),
  disabled: translate("connections.page.status.disabled"),
  unconfigured: translate("connections.page.status.unconfigured"),
});

const ConnectionsPage = () => {
  const { t } = useUiTranslation();
  const [family, setFamily] = useState("social");
  const [connections, setConnections] = useState([]);
  const [providers, setProviders] = useState([]);
  const [environment, setEnvironment] = useState([]);
  const [storeHealth, setStoreHealth] = useState(null);
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({ open: false, message: "", severity: "info" });
  const [addAnchor, setAddAnchor] = useState(null);
  const [modal, setModal] = useState({ open: false, connection: null, spec: null });
  const [testingId, setTestingId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, specs, env, health, publishSettings] = await Promise.all([
        fetchConnections(family),
        fetchProviders(family),
        fetchEnvironment(),
        fetchStoreHealth(),
        fetchPublishSettings(),
      ]);
      setConnections(conns);
      setProviders(specs);
      setEnvironment(env);
      setStoreHealth(health);
      setSettings(publishSettings);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [family]);

  useEffect(() => {
    load();
  }, [load]);

  const specByProvider = useMemo(
    () => Object.fromEntries(providers.map((s) => [s.provider, s])),
    [providers],
  );

  const handleTest = useCallback(
    async (connection) => {
      setTestingId(connection.id);
      try {
        const result = await testConnection(connection.id);
        setFeedback({
          open: true,
          message: result.message,
          severity: result.ok ? "success" : "error",
        });
        setConnections((prev) =>
          prev.map((c) => (c.id === connection.id ? result.connection : c)),
        );
      } catch (e) {
        setFeedback({ open: true, message: e.message, severity: "error" });
      } finally {
        setTestingId(null);
      }
    },
    [],
  );

  const handleDelete = useCallback(async (connection) => {
    try {
      await deleteConnection(connection.id);
      setConnections((prev) => prev.filter((c) => c.id !== connection.id));
      setFeedback({ open: true, message: t("connections.page.removed"), severity: "success" });
    } catch (e) {
      setFeedback({ open: true, message: e.message, severity: "error" });
    }
  }, []);

  const handleSettingChange = useCallback(async (key, value) => {
    try {
      const updated = await updatePublishSettings({ [key]: value });
      setSettings(updated);
    } catch (e) {
      setFeedback({ open: true, message: e.message, severity: "error" });
    }
  }, []);

  // Only a provider that declares capabilities.public_opt_in (YouTube) offers
  // this; the backend refuses it for any other.
  const handleAllowPublic = useCallback(async (connection, optIn, value) => {
    try {
      const saved = await updateConnection(connection.id, { allow_public: value });
      setConnections((prev) => prev.map((c) => (c.id === connection.id ? saved : c)));
      setFeedback({
        open: true,
        message: value
          ? t("connections.page.publicOn", { label: optIn.label })
          : t("connections.page.publicOff", { label: optIn.label }),
        severity: "success",
      });
    } catch (e) {
      setFeedback({ open: true, message: e.message, severity: "error" });
    }
  }, []);

  // Only a provider whose spec says posts_to_owned_channel (a Discord webhook)
  // offers this; the backend refuses it for any other.
  const handlePostWithoutApproval = useCallback(async (connection, value) => {
    try {
      const saved = await updateConnection(connection.id, { post_without_approval: value });
      setConnections((prev) => prev.map((c) => (c.id === connection.id ? saved : c)));
      setFeedback({
        open: true,
        message: value
          ? t("connections.page.withoutApprovalOn")
          : t("connections.page.withoutApprovalOff"),
        severity: "success",
      });
    } catch (e) {
      setFeedback({ open: true, message: e.message, severity: "error" });
    }
  }, []);

  return (
    <Container maxWidth="lg" sx={{ py: 3 }}>
      <Typography variant="h4" gutterBottom>
        {t("connections.page.title")}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t("connections.page.intro")}
      </Typography>

      <Tabs value={family} onChange={(_e, v) => setFamily(v)} sx={{ mb: 2 }}>
        {families().map((f) => (
          <Tab key={f.key} value={f.key} label={f.label} />
        ))}
      </Tabs>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {family === "social" && settings && (
        <Card variant="outlined" sx={{ mb: 2 }}>
          <CardContent>
            {settings.posting_stopped && (
              <Alert severity="error" sx={{ mb: 2 }}>
                {settings.posting_stop_reason ||
                  t("connections.page.postingStopped")}{" "}
                {t("connections.page.queuedHeld")}
              </Alert>
            )}
            <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap">
              <Stack direction="row" alignItems="center" spacing={1}>
                <Switch
                  checked={settings.publish_enabled}
                  onChange={(e) => handleSettingChange("publish_enabled", e.target.checked)}
                />
                <Typography variant="body2">{t("connections.page.publishingEnabled")}</Typography>
              </Stack>
              <Typography variant="body2" color="text.secondary">
                {t("connections.page.approvalNote")}
              </Typography>
            </Stack>
          </CardContent>
        </Card>
      )}

      <Box sx={{ mb: 2 }}>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={(e) => setAddAnchor(e.currentTarget)}
          disabled={!providers.length}
        >
          {t("connections.page.addConnection")}
        </Button>
        <Menu
          anchorEl={addAnchor}
          open={Boolean(addAnchor)}
          onClose={() => setAddAnchor(null)}
        >
          {providers.map((spec) => (
            <MenuItem
              key={spec.provider}
              onClick={() => {
                setModal({ open: true, connection: null, spec });
                setAddAnchor(null);
              }}
            >
              {spec.label}
              {spec.review_required && (
                <Chip label={t("connections.page.appReview")} size="small" sx={{ ml: 1 }} />
              )}
            </MenuItem>
          ))}
        </Menu>
      </Box>

      {loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Grid container spacing={2}>
          {connections.map((conn) => {
            const spec = specByProvider[conn.provider];
            const optIn = spec?.capabilities?.public_opt_in;
            return (
              <Grid item xs={12} sm={6} md={4} key={conn.id}>
                <Card variant="outlined" sx={{ height: "100%" }}>
                  <CardContent>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="flex-start"
                    >
                      <Typography variant="h6">
                        {spec?.label || conn.provider}
                      </Typography>
                      <Chip
                        size="small"
                        label={statusLabels()[conn.status] || conn.status}
                        color={STATUS_COLOR[conn.status] || "default"}
                      />
                    </Stack>
                    <Typography variant="body2" color="text.secondary">
                      {conn.handle || conn.display_name || conn.account_slug}
                    </Typography>
                    <Stack direction="row" spacing={0.5} alignItems="center" sx={{ mt: 1 }}>
                      {conn.credential_encrypted ? (
                        <LockIcon fontSize="small" color="success" />
                      ) : (
                        <LockOpenIcon fontSize="small" color="disabled" />
                      )}
                      <Typography variant="caption" color="text.secondary">
                        {conn.has_credentials
                          ? `${conn.credential_hint} · ${conn.credential_source}`
                          : t("connections.page.noCredentials")}
                      </Typography>
                    </Stack>
                    {conn.error_message && (
                      <Alert severity="error" sx={{ mt: 1 }}>
                        {conn.error_message}
                      </Alert>
                    )}
                    {spec?.posts_to_owned_channel && (
                      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
                        <Switch
                          size="small"
                          checked={conn.config?.post_without_approval === true}
                          onChange={(e) => handlePostWithoutApproval(conn, e.target.checked)}
                        />
                        <Tooltip title={t("connections.page.withoutApprovalTooltip")}>
                          <Typography variant="body2">{t("connections.page.postWithoutApproval")}</Typography>
                        </Tooltip>
                      </Stack>
                    )}
                    {optIn && (
                      <Box sx={{ mt: 1 }}>
                        <Stack direction="row" alignItems="center" spacing={1}>
                          <Switch
                            size="small"
                            checked={conn.public_opt_in?.on === true}
                            onChange={(e) => handleAllowPublic(conn, optIn, e.target.checked)}
                            inputProps={{ "aria-label": optIn.label }}
                          />
                          <Typography variant="body2">{optIn.label}</Typography>
                        </Stack>
                        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
                          {optIn.explanation}{" "}
                          {(optIn.sources || []).map((source, index) => (
                            <React.Fragment key={source.url}>
                              {index > 0 && " · "}
                              <Link href={source.url} target="_blank" rel="noopener noreferrer">
                                {source.title}
                              </Link>
                            </React.Fragment>
                          ))}
                        </Typography>
                        {conn.public_opt_in?.source === "setting" && (
                          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5 }}>
                            {t("connections.page.onThroughSetting", { setting: optIn.legacy_setting })}
                          </Typography>
                        )}
                      </Box>
                    )}
                  </CardContent>
                  <CardActions>
                    <Button
                      size="small"
                      onClick={() => handleTest(conn)}
                      disabled={testingId === conn.id}
                    >
                      {testingId === conn.id ? t("connections.page.testing") : t("connections.page.test")}
                    </Button>
                    <Button
                      size="small"
                      onClick={() =>
                        setModal({ open: true, connection: conn, spec })
                      }
                    >
                      {t("connections.page.edit")}
                    </Button>
                    <Button size="small" color="error" onClick={() => handleDelete(conn)}>
                      {t("connections.page.remove")}
                    </Button>
                  </CardActions>
                </Card>
              </Grid>
            );
          })}
          {!connections.length && (
            <Grid item xs={12}>
              <Alert severity="info">
                {t("connections.page.empty")}
              </Alert>
            </Grid>
          )}
        </Grid>
      )}

      {environment.length > 0 && (
        <Box sx={{ mt: 4 }}>
          <Divider sx={{ mb: 2 }} />
          <Typography variant="h6" gutterBottom>
            {t("connections.page.environment")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t("connections.page.environmentNote")}
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
            {environment.map((entry) => (
              <Chip
                key={entry.env_key}
                label={`${entry.env_key} · ${entry.hint}`}
                variant="outlined"
              />
            ))}
          </Stack>
        </Box>
      )}

      {storeHealth && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 3 }}>
          {storeHealth.encrypted ? t("connections.page.encrypted") : t("connections.page.unencrypted")} ·{" "}
          {t("connections.page.credentialCount", { count: storeHealth.record_count })} · {storeHealth.path}
          {storeHealth.mode ? ` (${storeHealth.mode})` : ""}
        </Typography>
      )}

      <ConnectionModal
        open={modal.open}
        connection={modal.connection}
        providerSpec={modal.spec}
        onClose={() => setModal({ open: false, connection: null, spec: null })}
        onFeedback={setFeedback}
        onSaved={load}
      />

      <Snackbar
        open={feedback.open}
        autoHideDuration={5000}
        onClose={() => setFeedback((f) => ({ ...f, open: false }))}
      >
        <Alert
          severity={feedback.severity}
          onClose={() => setFeedback((f) => ({ ...f, open: false }))}
        >
          {feedback.message}
        </Alert>
      </Snackbar>
    </Container>
  );
};

export default ConnectionsPage;
