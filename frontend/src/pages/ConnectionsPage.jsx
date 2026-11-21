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
  Link,
  Menu,
  MenuItem,
  Snackbar,
  Stack,
  Switch,
  FormControlLabel,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import LockIcon from "@mui/icons-material/Lock";
import LockOpenIcon from "@mui/icons-material/LockOpen";

import ConnectionModal from "../components/modals/ConnectionModal";
import PageLayout from "../components/layout/PageLayout";
import { CardWorkspace } from "../components/board";
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

// Where each family's card sits by default on the board.
const FAMILY_CARD_LAYOUT = {
  social: { x: 0, y: 0, w: 110, h: 62 },
  ai_provider: { x: 110, y: 0, w: 65, h: 31 },
  mcp_server: { x: 110, y: 31, w: 65, h: 31 },
};
const ENVIRONMENT_CARD_LAYOUT = { x: 0, y: 62, w: 175, h: 22 };

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
  const [connections, setConnections] = useState([]);
  const [providers, setProviders] = useState([]);
  const [environment, setEnvironment] = useState([]);
  const [storeHealth, setStoreHealth] = useState(null);
  const [settings, setSettings] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState({ open: false, message: "", severity: "info" });
  // The Add connection menu that is open: { family, el }.
  const [addAnchor, setAddAnchor] = useState(null);
  const [modal, setModal] = useState({ open: false, connection: null, spec: null });
  const [testingId, setTestingId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, specs, env, health, publishSettings] = await Promise.all([
        fetchConnections(),
        fetchProviders(),
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
  }, []);

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

  // One card per family: its connections, and Add connection for its providers.
  const familyCard = (family) => {
    const specs = providers.filter((spec) => spec.family === family);
    const own = connections.filter((conn) => conn.family === family);
    return (
      <Box sx={{ p: 1.5 }}>
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
              <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                  <Switch
                    checked={settings.publish_enabled}
                    onChange={(e) => handleSettingChange("publish_enabled", e.target.checked)}
                  />
                  <Typography variant="body2">{t("connections.page.publishingEnabled")}</Typography>
                </Stack>
                <Typography variant="body2" sx={{ color: "text.secondary" }}>
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
            onClick={(e) => setAddAnchor({ family, el: e.currentTarget })}
            disabled={!specs.length}
          >
            {t("connections.page.addConnection")}
          </Button>
          <Menu
            anchorEl={addAnchor?.family === family ? addAnchor.el : null}
            open={addAnchor?.family === family}
            onClose={() => setAddAnchor(null)}
          >
            {specs.map((spec) => (
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
        ) : own.length ? (
          <Box sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 2 }}>
            {own.map((conn) => {
              const spec = specByProvider[conn.provider];
              const optIn = spec?.capabilities?.public_opt_in;
              return (
                <Card variant="outlined" key={conn.id}>
                  <CardContent>
                    <Stack
                      direction="row"
                      sx={{ justifyContent: "space-between", alignItems: "flex-start" }}
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
                    <Typography variant="body2" sx={{ color: "text.secondary" }}>
                      {conn.handle || conn.display_name || conn.account_slug}
                    </Typography>
                    <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", mt: 1 }}>
                      {conn.credential_encrypted ? (
                        <LockIcon fontSize="small" color="success" />
                      ) : (
                        <LockOpenIcon fontSize="small" color="disabled" />
                      )}
                      <Typography variant="caption" sx={{ color: "text.secondary" }}>
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
                      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mt: 1 }}>
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
                        <FormControlLabel
                          control={
                            <Switch
                              size="small"
                              checked={conn.public_opt_in?.on === true}
                              onChange={(e) => handleAllowPublic(conn, optIn, e.target.checked)}
                            />
                          }
                          label={optIn.label}
                        />
                        <Typography variant="caption" sx={{ color: "text.secondary", display: "block" }}>
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
                          <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.5 }}>
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

              );
            })}
          </Box>
        ) : (
          <Alert severity="info">{t("connections.page.empty")}</Alert>
        )}
      </Box>
    );
  };

  const cards = families().map((f) => ({
    id: f.key,
    title: f.label,
    layout: FAMILY_CARD_LAYOUT[f.key],
    body: familyCard(f.key),
  }));
  if (environment.length > 0 || storeHealth) {
    cards.push({
      id: "environment",
      title: t("connections.page.environment"),
      layout: ENVIRONMENT_CARD_LAYOUT,
      body: (
        <Box sx={{ p: 1.5 }}>
          {environment.length > 0 && (
            <>
              <Typography variant="body2" sx={{ color: "text.secondary", mb: 1 }}>
                {t("connections.page.environmentNote")}
              </Typography>
              <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: "wrap" }}>
                {environment.map((entry) => (
                  <Chip
                    key={entry.env_key}
                    label={`${entry.env_key} · ${entry.hint}`}
                    variant="outlined"
                  />
                ))}
              </Stack>

            </>
          )}
          {storeHealth && (
            <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: environment.length > 0 ? 2 : 0 }}>
              {storeHealth.encrypted ? t("connections.page.encrypted") : t("connections.page.unencrypted")} ·{" "}
              {t("connections.page.credentialCount", { count: storeHealth.record_count })} · {storeHealth.path}
              {storeHealth.mode ? ` (${storeHealth.mode})` : ""}
            </Typography>

          )}
        </Box>
      ),
    });
  }

  return (
    <PageLayout title={t("connections.page.title")} variant="standard">
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 1.5 }}>
        {t("connections.page.intro")}
      </Typography>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <CardWorkspace boardKey="connections" cards={cards} />

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
    </PageLayout>
  );
};

export default ConnectionsPage;
