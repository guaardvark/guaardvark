// frontend/src/pages/SocialAccountsPage.jsx
//
// The person's social accounts, one panel per platform, built on Connections
// the way WordPress Sites is built on its sites. Add and Edit reuse
// ConnectionModal. Each platform's rules (rate limits, AI disclosure, how an
// account declares automation, scheduling, delete) come from its provider
// spec; nothing here knows a platform by name. Social Posts lists what went out.
// No per-account switch lives here: the one exception to per-post approval (an
// owned-channel connection) and a provider's public opt-in are set on the
// Connections page and only shown here.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Collapse,
  IconButton,
  Link,
  Menu,
  MenuItem,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useNavigate } from "react-router-dom";

import PageLayout from "../components/layout/PageLayout";
import ConnectionModal from "../components/modals/ConnectionModal";
import { useSnackbar } from "../components/common/SnackbarProvider";
import {
  ActionButton,
  Cluster,
  ConfirmActionDialog,
  Hint,
  Line,
  SettingChip,
  SettingsPanel,
  StatusPill,
} from "../components/settings/ui";
import {
  deleteConnection,
  fetchConnectionUsage,
  fetchConnections,
  fetchEnvironment,
  fetchProviders,
  fetchPublishSettings,
  fetchStoreHealth,
  testConnection,
  updatePublishSettings,
} from "../api/connectionsService";
import {
  describeDelete,
  describeDisclosure,
  describeRateLimit,
  describeScheduling,
} from "../components/social/platformRules";
import { translate, uiLocale, useUiTranslation } from "../i18n";

const STATUS_TONE = {
  connected: "ok",
  error: "error",
  expired: "warn",
  disabled: "neutral",
  unconfigured: "neutral",
};

const statusLabels = () => ({
  connected: translate("social.accounts.status.connected"),
  error: translate("social.accounts.status.error"),
  expired: translate("social.accounts.status.expired"),
  disabled: translate("social.accounts.status.disabled"),
  unconfigured: translate("social.accounts.status.unconfigured"),
});

const formatWhen = (iso) =>
  iso ? new Date(iso).toLocaleString(uiLocale()) : translate("social.accounts.never");

const usageTone = (used, posts) => (used >= posts ? "error" : used >= posts * 0.8 ? "warn" : "ok");

/** How the account declares automation: its state where the provider reads it. */
const BotLabelLine = ({ label, connection }) => {
  const { t } = useUiTranslation();
  if (!label || label.kind === "none") return null;
  if (label.kind === "automatic") return <Hint>{label.how}</Hint>;
  const state = connection.config?.bot_label_set;
  return (
    <Line>
      {label.readable && (
        <StatusPill
          label={
            state === true
              ? t("social.accounts.botLabelSet")
              : state === false
                ? t("social.accounts.botLabelNotSet")
                : t("social.accounts.botLabelUnread")
          }
          tone={state === true ? "ok" : state === false ? "warn" : "neutral"}
          tooltip={state === undefined ? t("social.accounts.botLabelTooltip") : ""}
        />
      )}
      <Hint>{label.how}</Hint>
    </Line>
  );
};

const RulesBlock = ({ capabilities }) => {
  const { t } = useUiTranslation();
  const rule = capabilities.ai_disclosure;
  const label = capabilities.bot_label;
  const optIn = capabilities.public_opt_in;
  const source = (url) =>
    url ? (
      <>
        {" "}
        <Link href={url} target="_blank" rel="noopener noreferrer">
          {t("social.accounts.source")}
        </Link>
      </>
    ) : null;
  return (
    <Stack spacing={0.75} sx={{ pt: 0.5 }}>
      {(capabilities.rate_limits || []).map((limit) => (
        <Hint key={`${limit.posts}-${limit.window_seconds}`}>
          {t("social.accounts.rules.rateLimit", { rule: describeRateLimit(limit) })}
          {source(limit.source)}
        </Hint>
      ))}
      {rule && (
        <Hint>
          {t("social.accounts.rules.aiDisclosure", { rule: describeDisclosure(rule) })}
          {source(rule.source)}
        </Hint>
      )}
      {label && label.kind !== "none" && (
        <Hint>
          {t("social.accounts.rules.automationLabel", { how: label.how })}
          {source(label.source)}
        </Hint>
      )}
      {label && label.kind === "none" && (
        <Hint>
          {t("social.accounts.rules.automation", { how: label.how })}
          {source(label.source)}
        </Hint>
      )}
      <Hint>
        {t("social.accounts.rules.scheduling", { rule: describeScheduling(capabilities) })}
        {source(capabilities.native_scheduling?.source)}
      </Hint>
      {optIn && (
        <Hint>
          {t("social.accounts.rules.publicOptIn", { label: optIn.label, explanation: optIn.explanation })}
          {(optIn.sources || []).map((s) => (
            <React.Fragment key={s.url}>
              {" "}
              <Link href={s.url} target="_blank" rel="noopener noreferrer">
                {s.title}
              </Link>
            </React.Fragment>
          ))}
        </Hint>
      )}
      <Hint>{t("social.accounts.rules.delete", { rule: describeDelete(capabilities) })}</Hint>
    </Stack>
  );
};

const SocialAccountsPage = () => {
  const { t } = useUiTranslation();
  const navigate = useNavigate();
  const { showMessage } = useSnackbar();

  const [connections, setConnections] = useState([]);
  const [providers, setProviders] = useState([]);
  const [usage, setUsage] = useState({});
  const [settings, setSettings] = useState(null);
  const [environment, setEnvironment] = useState([]);
  const [storeHealth, setStoreHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [addAnchor, setAddAnchor] = useState(null);
  const [modal, setModal] = useState({ open: false, connection: null, spec: null });
  const [testingId, setTestingId] = useState(null);
  const [removing, setRemoving] = useState(null);
  const [busy, setBusy] = useState(false);
  const [rulesOpen, setRulesOpen] = useState({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [conns, specs, use, publishSettings, env, health] = await Promise.all([
        fetchConnections("social"),
        fetchProviders("social"),
        fetchConnectionUsage("social"),
        fetchPublishSettings(),
        fetchEnvironment(),
        fetchStoreHealth(),
      ]);
      setConnections(conns);
      setProviders(specs);
      setUsage(use);
      setSettings(publishSettings);
      setEnvironment(env);
      setStoreHealth(health);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const byProvider = useMemo(() => {
    const groups = {};
    for (const conn of connections) {
      (groups[conn.provider] = groups[conn.provider] || []).push(conn);
    }
    return groups;
  }, [connections]);

  const handleTest = async (connection) => {
    setTestingId(connection.id);
    try {
      const result = await testConnection(connection.id);
      showMessage(result.message, result.ok ? "success" : "error");
      setConnections((prev) => prev.map((c) => (c.id === connection.id ? result.connection : c)));
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setTestingId(null);
    }
  };

  const handleRemove = async () => {
    if (!removing) return;
    setBusy(true);
    try {
      await deleteConnection(removing.id);
      showMessage(t("social.accounts.removed"), "success");
      setRemoving(null);
      await load();
    } catch (e) {
      showMessage(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const handleSetting = async (key, value) => {
    try {
      setSettings(await updatePublishSettings({ [key]: value }));
    } catch (e) {
      showMessage(e.message, "error");
    }
  };

  const openPosts = (connection) =>
    navigate(connection ? `/social/posts?connection_id=${connection.id}` : "/social/posts");

  const renderAccount = (conn, spec) => {
    const stats = usage[String(conn.id)] || {};
    return (
      <Box
        key={conn.id}
        sx={{ py: 1.25, borderTop: 1, borderColor: "divider", "&:first-of-type": { borderTop: 0, pt: 0 } }}
      >
        <Stack spacing={0.75}>
          <Line>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {conn.display_name || conn.handle || conn.account_slug}
            </Typography>
            {conn.handle && conn.handle !== conn.display_name && (
              <Typography variant="caption" color="text.secondary">
                {conn.handle}
              </Typography>
            )}
            <StatusPill
              label={statusLabels()[conn.status] || conn.status}
              tone={STATUS_TONE[conn.status] || "neutral"}
            />
            <Typography variant="caption" color="text.secondary">
              {conn.has_credentials
                ? `${conn.auth_kind.replace("_", " ")} · ${conn.credential_hint}`
                : t("social.accounts.noCredentials")}
            </Typography>
          </Line>
          <Line>
            <Hint>
              {t("social.accounts.usage", { count: stats.posts || 0, when: formatWhen(stats.last_posted_at) })}
              {stats.scheduled ? ` · ${t("social.accounts.scheduledCount", { count: stats.scheduled })}` : ""}
            </Hint>
            {(stats.rate_limits || []).map((limit) => (
              <StatusPill
                key={limit.window_seconds}
                label={t("social.accounts.rateUsage", { used: limit.used, posts: limit.posts, window: limit.window })}
                tone={usageTone(limit.used, limit.posts)}
                tooltip={describeRateLimit(limit)}
              />
            ))}
          </Line>
          {conn.token_expires_at && (
            <Hint>
              {new Date(conn.token_expires_at) < new Date()
                ? t("social.accounts.authExpired", { when: formatWhen(conn.token_expires_at) })
                : t("social.accounts.authLasts", { when: formatWhen(conn.token_expires_at) })}
            </Hint>
          )}
          <BotLabelLine label={spec?.capabilities?.bot_label} connection={conn} />
          {spec?.capabilities?.public_opt_in && conn.public_opt_in?.on === true && (
            <Line>
              <StatusPill
                label={t("social.accounts.publicOn", { label: spec.capabilities.public_opt_in.label })}
                tone="warn"
                tooltip={
                  conn.public_opt_in.source === "setting"
                    ? t("social.accounts.publicOnSetting", {
                        setting: spec.capabilities.public_opt_in.legacy_setting,
                      })
                    : t("social.accounts.publicOnAccount")
                }
              />
            </Line>
          )}
          {spec?.posts_to_owned_channel && conn.config?.post_without_approval === true && (
            <Line>
              <StatusPill
                label={t("social.accounts.withoutApproval")}
                tone="warn"
                tooltip={t("social.accounts.withoutApprovalTooltip")}
              />
            </Line>
          )}
          {conn.error_message && (
            <Alert severity="error" sx={{ py: 0 }}>
              {conn.error_message}
            </Alert>
          )}
          <Line>
            <ActionButton loading={testingId === conn.id} onClick={() => handleTest(conn)}>
              {t("social.accounts.test")}
            </ActionButton>
            <ActionButton onClick={() => setModal({ open: true, connection: conn, spec })} disabled={!spec}>
              {t("social.accounts.edit")}
            </ActionButton>
            <ActionButton onClick={() => openPosts(conn)}>{t("social.accounts.posts")}</ActionButton>
            <ActionButton kind="destructive" onClick={() => setRemoving(conn)}>
              {t("social.accounts.remove")}
            </ActionButton>
          </Line>
        </Stack>
      </Box>
    );
  };

  return (
    <PageLayout
      title={t("social.accounts.title")}
      actions={
        <Stack direction="row" spacing={1} alignItems="center">
          <ActionButton kind="link" onClick={() => openPosts(null)}>
            {t("social.accounts.socialPosts")}
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/approvals")}>
            {t("social.accounts.approvals")}
          </ActionButton>
          <ActionButton kind="link" onClick={() => navigate("/connections")}>
            {t("social.accounts.connections")}
          </ActionButton>
          <ActionButton startIcon={<AddIcon />} onClick={(e) => setAddAnchor(e.currentTarget)} disabled={!providers.length}>
            {t("social.accounts.addAccount")}
          </ActionButton>
          <Tooltip title={t("social.accounts.refresh")}>
            <IconButton onClick={load} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      }
    >
      <Menu anchorEl={addAnchor} open={Boolean(addAnchor)} onClose={() => setAddAnchor(null)}>
        {providers.map((spec) => (
          <MenuItem
            key={spec.provider}
            onClick={() => {
              setModal({ open: true, connection: null, spec });
              setAddAnchor(null);
            }}
          >
            {spec.label}
          </MenuItem>
        ))}
      </Menu>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {settings?.posting_stopped && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {settings.posting_stop_reason || t("social.accounts.postingStopped")}{" "}
          {t("social.accounts.queuedHeld")}
        </Alert>
      )}

      <Stack spacing={2}>
        {settings && (
          <SettingsPanel
            title={t("social.accounts.publishingTitle")}
            description={t("social.accounts.publishingDescription")}
            help={t("social.accounts.publishingHelp")}
          >
            <Cluster label={t("social.accounts.switches")}>
              <Line>
                <SettingChip
                  label={t("social.accounts.publishingSwitch")}
                  on={settings.publish_enabled}
                  onToggle={(value) => handleSetting("publish_enabled", value)}
                />
              </Line>
              <Hint>
                {t("social.accounts.approvalNote")}
              </Hint>
            </Cluster>
          </SettingsPanel>
        )}

        {loading && !providers.length ? (
          <Hint>{t("social.accounts.loading")}</Hint>
        ) : (
          providers.map((spec) => {
            const accounts = byProvider[spec.provider] || [];
            const open = Boolean(rulesOpen[spec.provider]);
            return (
              <SettingsPanel
                key={spec.provider}
                title={spec.label}
                description={
                  accounts.length
                    ? t("social.accounts.accountCount", { count: accounts.length })
                    : t("social.accounts.notConnected")
                }
                actions={
                  <>
                    <ActionButton
                      kind="link"
                      onClick={() => setRulesOpen((prev) => ({ ...prev, [spec.provider]: !open }))}
                    >
                      {open ? t("social.accounts.hideRules") : t("social.accounts.showRules")}
                    </ActionButton>
                    <ActionButton onClick={() => setModal({ open: true, connection: null, spec })}>
                      {accounts.length ? t("social.accounts.addAnother") : t("social.accounts.connect")}
                    </ActionButton>
                  </>
                }
              >
                {accounts.map((conn) => renderAccount(conn, spec))}
                {!accounts.length && <Hint>{spec.setup_help}</Hint>}
                <Collapse in={open} unmountOnExit>
                  <RulesBlock capabilities={spec.capabilities || {}} />
                </Collapse>
              </SettingsPanel>
            );
          })
        )}

        {connections
          .filter((conn) => !providers.some((spec) => spec.provider === conn.provider))
          .map((conn) => (
            <Alert key={conn.id} severity="warning">
              {t("social.accounts.providerMissing", {
                name: conn.display_name || conn.provider,
                provider: conn.provider,
              })}
            </Alert>
          ))}
      </Stack>

      {environment.length > 0 && (
        <Box sx={{ mt: 3 }}>
          <Typography variant="subtitle2">{t("social.accounts.environment")}</Typography>
          <Hint>{t("social.accounts.environmentNote")}</Hint>
          <Line sx={{ mt: 1 }}>
            {environment.map((entry) => (
              <StatusPill key={entry.env_key} label={`${entry.env_key} · ${entry.hint}`} />
            ))}
          </Line>
        </Box>
      )}

      {storeHealth && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 3 }}>
          {t("social.accounts.storedOutside", {
            state: storeHealth.encrypted ? t("social.accounts.encrypted") : t("social.accounts.unencrypted"),
          })}{" "}
          ·{" "}
          {t("social.accounts.credentialCount", { count: storeHealth.record_count })} · {storeHealth.path}
          {storeHealth.mode ? ` (${storeHealth.mode})` : ""}
        </Typography>
      )}

      <ConnectionModal
        open={modal.open}
        connection={modal.connection}
        providerSpec={modal.spec}
        onClose={() => setModal({ open: false, connection: null, spec: null })}
        onFeedback={(feedback) => showMessage(feedback.message, feedback.severity)}
        onSaved={load}
      />

      <ConfirmActionDialog
        open={Boolean(removing)}
        title={t("social.accounts.removeTitle")}
        description={t("social.accounts.removeDescription")}
        facts={
          removing
            ? [
                {
                  label: t("social.accounts.factAccount"),
                  value: removing.display_name || removing.handle || removing.account_slug,
                },
                { label: t("social.accounts.factPlatform"), value: removing.provider },
              ]
            : []
        }
        keeps={t("social.accounts.removeKeeps")}
        confirmLabel={t("social.accounts.removeConfirm")}
        busy={busy}
        onConfirm={handleRemove}
        onClose={() => setRemoving(null)}
      />
    </PageLayout>
  );
};

export default SocialAccountsPage;
