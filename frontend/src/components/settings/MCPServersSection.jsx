// frontend/src/components/settings/MCPServersSection.jsx
// MCP server management for the MCP Servers page: status, connect/disconnect,
// add/edit/remove local servers, per-tool policy, and the MCP audit log.
/* eslint-env browser, es2021 */

import React, { useCallback, useEffect, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  CircularProgress,
  Collapse,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";

import {
  connectMcpServer,
  deleteMcpServer,
  disconnectMcpServer,
  getMcpAuditLog,
  getMcpServer,
  getMcpStatus,
  listMcpServers,
  reloadMcpConfig,
  saveMcpServer,
} from "../../api/mcpService";
import { onSessionChanged } from "../../api/apiAuth";
import { ApiKeyRefusalAlert } from "../common/ApiKeyRefusalNotice";
import { ActionButton, ConfirmActionDialog, SettingChip, StatusPill } from "./ui";
import { translate, uiLocale, useUiTranslation } from "../../i18n";

const STATUS_TONE = {
  connected: "ok",
  connecting: "info",
  error: "error",
  disconnected: "neutral",
};

const policyPill = () => ({
  allow: { label: translate("settingsAccess.mcp.policy.allow"), tone: "ok" },
  confirm: { label: translate("settingsAccess.mcp.policy.confirm"), tone: "warn" },
  deny: { label: translate("settingsAccess.mcp.policy.deny"), tone: "error" },
});

const auditPill = (a) => {
  if (a.success) return { label: translate("settingsAccess.mcp.audit.ok"), tone: "ok" };
  if (a.decision === "needs_approval") return { label: translate("settingsAccess.mcp.audit.needsApproval"), tone: "warn" };
  if (a.decision === "deny") return { label: translate("settingsAccess.mcp.audit.blocked"), tone: "error" };
  return { label: translate("settingsAccess.mcp.audit.failed"), tone: "error" };
};

const splitList = (text) =>
  (text || "")
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);

// "KEY=value" lines <-> object. Existing secrets are shown as KEY=*** and sent
// back unchanged as "***", which the backend treats as "keep stored value".
const parsePairs = (text) => {
  const out = {};
  (text || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .forEach((line) => {
      const idx = line.indexOf("=");
      if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1);
    });
  return out;
};

const EMPTY_FORM = {
  name: "",
  command: "",
  args: "",
  env: "",
  fixedArgs: "",
  autoConnect: false,
  description: "",
  keywords: "",
  allowTools: "",
  denyTools: "",
  confirmTools: "",
  autoApproveTools: "",
  timeout: "",
};

const ServerDialog = ({ open, initial, isEdit, onClose, onSaved }) => {
  const { t } = useUiTranslation();
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (open) {
      setForm(initial || EMPTY_FORM);
      setError(null);
    }
  }, [open, initial]);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    const def = {
      command: form.command,
      args: form.args.split("\n").map((a) => a.trim()).filter(Boolean),
      env: parsePairs(form.env),
      fixedArgs: parsePairs(form.fixedArgs),
      autoConnect: form.autoConnect,
      description: form.description,
      keywords: splitList(form.keywords),
      allowTools: splitList(form.allowTools),
      denyTools: splitList(form.denyTools),
      confirmTools: splitList(form.confirmTools),
      autoApproveTools: splitList(form.autoApproveTools),
    };
    if (form.timeout) def.timeout = Number(form.timeout);
    try {
      await saveMcpServer(form.name.trim(), def);
      onSaved(form.name.trim());
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? t("settingsAccess.mcp.dialog.editTitle", { name: form.name }) : t("settingsAccess.mcp.dialog.addTitle")}</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label={t("settingsAccess.mcp.dialog.name")} size="small" value={form.name} onChange={set("name")} disabled={isEdit}
            helperText={t("settingsAccess.mcp.dialog.nameHelp")} />
          <TextField label={t("settingsAccess.mcp.dialog.command")} size="small" value={form.command} onChange={set("command")}
            placeholder="npx" helperText={t("settingsAccess.mcp.dialog.commandHelp")} />
          <TextField label={t("settingsAccess.mcp.dialog.args")} size="small" multiline minRows={2}
            value={form.args} onChange={set("args")}
            placeholder={"-y\n@modelcontextprotocol/server-filesystem\n/path/to/shared"} />
          <TextField label={t("settingsAccess.mcp.dialog.env")} size="small" multiline minRows={2}
            value={form.env} onChange={set("env")}
            helperText={t("settingsAccess.mcp.dialog.envHelp")} />
          <TextField label={t("settingsAccess.mcp.dialog.fixedArgs")} size="small" multiline minRows={2}
            value={form.fixedArgs} onChange={set("fixedArgs")} placeholder="root=${GUAARDVARK_ROOT}"
            helperText={t("settingsAccess.mcp.dialog.fixedArgsHelp")} />
          <TextField label={t("settingsAccess.mcp.dialog.description")} size="small" value={form.description} onChange={set("description")} />
          <TextField label={t("settingsAccess.mcp.dialog.keywords")} size="small" value={form.keywords}
            onChange={set("keywords")} helperText={t("settingsAccess.mcp.dialog.keywordsHelp")} />
          <TextField label={t("settingsAccess.mcp.dialog.denyTools")} size="small" value={form.denyTools} onChange={set("denyTools")} />
          <TextField label={t("settingsAccess.mcp.dialog.allowTools")} size="small" value={form.allowTools}
            onChange={set("allowTools")} />
          <TextField label={t("settingsAccess.mcp.dialog.confirmTools")} size="small" value={form.confirmTools}
            onChange={set("confirmTools")} />
          <TextField label={t("settingsAccess.mcp.dialog.autoApproveTools")} size="small" value={form.autoApproveTools}
            onChange={set("autoApproveTools")}
            helperText={t("settingsAccess.mcp.dialog.autoApproveHelp")} />
          <TextField label={t("settingsAccess.mcp.dialog.timeout")} size="small" type="number" value={form.timeout}
            onChange={set("timeout")} />
          <Box>
            <SettingChip
              label={t("settingsAccess.mcp.dialog.autoConnect")}
              on={form.autoConnect}
              onToggle={(next) => setForm((f) => ({ ...f, autoConnect: next }))}
            />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose}>{t("settingsAccess.mcp.dialog.cancel")}</ActionButton>
        <ActionButton kind="primary" loading={saving} onClick={handleSave}
          disabled={!form.name.trim() || !form.command.trim()}>
          {t("settingsAccess.mcp.dialog.save")}
        </ActionButton>
      </DialogActions>
    </Dialog>
  );
};

ServerDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  initial: PropTypes.object,
  isEdit: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onSaved: PropTypes.func.isRequired,
};

const ServerDetail = ({ name }) => {
  const { t } = useUiTranslation();
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getMcpServer(name)
      .then((res) => setDetail(res.server))
      .catch((e) => setError(e.message));
  }, [name]);

  if (error) return <Alert severity="error">{error}</Alert>;
  if (!detail) return <CircularProgress size={18} />;

  return (
    <Box sx={{ py: 1 }}>
      {detail.last_error && (
        <Alert severity="error" sx={{ mb: 1 }}>{detail.last_error}</Alert>
      )}
      {detail.tools?.length > 0 ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t("settingsAccess.mcp.detail.tool")}</TableCell>
              <TableCell>{t("settingsAccess.mcp.detail.policy")}</TableCell>
              <TableCell>{t("settingsAccess.mcp.detail.description")}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {detail.tools.map((tool) => {
              const pills = policyPill();
              const pill = pills[tool.policy] || pills.allow;
              return (
                <TableRow key={tool.name}>
                  <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>{tool.name}</TableCell>
                  <TableCell>
                    <StatusPill label={pill.label} tone={pill.tone} tooltip={tool.policyReason || ""} />
                  </TableCell>
                  <TableCell sx={{ fontSize: "0.75rem" }}>{tool.description}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : (
        <Typography variant="body2" color="text.secondary">
          {detail.connected ? t("settingsAccess.mcp.detail.noTools") : t("settingsAccess.mcp.detail.connectToSee")}
        </Typography>
      )}
      {detail.stderr_tail && (
        <Box component="pre" sx={{ mt: 1, p: 1, maxHeight: 160, overflow: "auto", fontSize: "0.7rem",
          bgcolor: "action.hover", borderRadius: 1, whiteSpace: "pre-wrap" }}>
          {detail.stderr_tail.slice(-2000)}
        </Box>
      )}
    </Box>
  );
};

ServerDetail.propTypes = { name: PropTypes.string.isRequired };

// Editable definition (from GET /servers/<name>; secret values arrive as "***").
const toForm = (name, d) => ({
  ...EMPTY_FORM,
  name,
  command: d.command || "",
  args: (d.args || []).join("\n"),
  env: Object.entries(d.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"),
  fixedArgs: Object.entries(d.fixedArgs || {}).map(([k, v]) => `${k}=${v}`).join("\n"),
  autoConnect: !!d.autoConnect,
  description: d.description || "",
  keywords: (d.keywords || []).join(", "),
  allowTools: (d.allowTools || []).join(", "),
  denyTools: (d.denyTools || []).join(", "),
  confirmTools: (d.confirmTools || []).join(", "),
  autoApproveTools: (d.autoApproveTools || []).join(", "),
  timeout: d.timeout || "",
});

const MCPServersSection = () => {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const [servers, setServers] = useState([]);
  const [configErrors, setConfigErrors] = useState([]);
  const [audit, setAudit] = useState([]);
  const [busy, setBusy] = useState({});
  const [expanded, setExpanded] = useState(null);
  const [showAudit, setShowAudit] = useState(false);
  const [dialog, setDialog] = useState({ open: false, initial: null, isEdit: false });
  const [removing, setRemoving] = useState(null);
  const [message, setMessage] = useState(null);
  // Set when the backend refuses this browser (these routes answer the
  // Guaardvark machine, or this install's API key): the advice to show in
  // place of the page. Polling stops until this browser signs in or out.
  const [refused, setRefused] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [st, list] = await Promise.all([getMcpStatus(), listMcpServers()]);
      setStatus(st);
      setServers(list.servers || []);
      setConfigErrors(list.config_errors || []);
      setRefused(null);
    } catch (e) {
      if (e.authRefused) {
        setRefused(e.message);
      } else {
        setMessage({ severity: "error", text: t("settingsAccess.mcp.loadFailed", { error: e.message }) });
      }
    }
  }, []);

  useEffect(() => {
    return onSessionChanged(() => setRefused(null));
  }, []);

  const refreshAudit = useCallback(async () => {
    try {
      const res = await getMcpAuditLog(25);
      setAudit(res.entries || []);
    } catch (e) {
      setAudit([]);
    }
  }, []);

  useEffect(() => {
    if (refused) return undefined;
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, [refresh, refused]);

  useEffect(() => {
    if (showAudit) refreshAudit();
  }, [showAudit, refreshAudit]);

  const withBusy = async (name, fn, okText) => {
    setBusy((b) => ({ ...b, [name]: true }));
    setMessage(null);
    try {
      await fn();
      if (okText) setMessage({ severity: "success", text: okText });
    } catch (e) {
      setMessage({ severity: "error", text: e.message });
    } finally {
      setBusy((b) => ({ ...b, [name]: false }));
      refresh();
    }
  };

  const openEdit = async (name) => {
    try {
      const res = await getMcpServer(name);
      setDialog({ open: true, initial: toForm(name, res.server.definition || {}), isEdit: true });
    } catch (e) {
      setMessage({ severity: "error", text: e.message });
    }
  };

  const confirmRemove = async () => {
    const name = removing?.name;
    setRemoving(null);
    if (name) await withBusy(name, () => deleteMcpServer(name), t("settingsAccess.mcp.removed", { name }));
  };

  if (refused) {
    return <ApiKeyRefusalAlert message={refused} />;
  }
  if (status && !status.mcp_enabled) {
    return <Alert severity="info">{t("settingsAccess.mcp.disabled")}</Alert>;
  }

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        <Box sx={{ flexGrow: 1, display: "flex", gap: 1, flexWrap: "wrap" }}>
          {status ? (
            <>
              <StatusPill
                tone={status.servers_connected > 0 ? "ok" : "neutral"}
                label={t("settingsAccess.mcp.connectedCount", { connected: status.servers_connected, configured: status.servers_configured })}
              />
              <StatusPill label={t("settingsAccess.mcp.tools", { count: status.total_tools_available })} />
              <StatusPill label={t("settingsAccess.mcp.calls", { count: status.total_calls })} />
              {!status.sdk_available && <StatusPill tone="error" label={t("settingsAccess.mcp.sdkMissing")} />}
            </>
          ) : (
            <Typography variant="body2" color="text.secondary">{t("settingsAccess.mcp.loading")}</Typography>
          )}
        </Box>
        <ActionButton onClick={refresh}>{t("settingsAccess.mcp.refresh")}</ActionButton>
        <ActionButton loading={!!busy._reload}
          onClick={() => withBusy("_reload", reloadMcpConfig, t("settingsAccess.mcp.reloaded"))}>
          {t("settingsAccess.mcp.reloadConfig")}
        </ActionButton>
        <ActionButton onClick={() => setDialog({ open: true, initial: null, isEdit: false })}>
          {t("settingsAccess.mcp.addServer")}
        </ActionButton>
      </Stack>

      {message && (
        <Alert severity={message.severity} onClose={() => setMessage(null)} sx={{ mb: 1 }}>{message.text}</Alert>
      )}
      {configErrors.map((err) => (
        <Alert key={err} severity="warning" sx={{ mb: 1 }}>{err}</Alert>
      ))}

      {servers.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("settingsAccess.mcp.noServers")}
        </Typography>
      ) : (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>{t("settingsAccess.mcp.columns.server")}</TableCell>
              <TableCell>{t("settingsAccess.mcp.columns.status")}</TableCell>
              <TableCell align="right">{t("settingsAccess.mcp.columns.tools")}</TableCell>
              <TableCell align="right">{t("settingsAccess.mcp.columns.actions")}</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {servers.map((s) => (
              <React.Fragment key={s.name}>
                <TableRow hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.name}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {s.command}
                      {s.auto_connect ? ` · ${t("settingsAccess.mcp.connectsAtStart")}` : ""}
                      {s.disabled ? ` · ${t("settingsAccess.mcp.serverDisabled")}` : ""}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <StatusPill label={s.status} tone={STATUS_TONE[s.status] || "neutral"}
                      tooltip={s.last_error || ""} />
                  </TableCell>
                  <TableCell align="right">{s.connected ? s.tool_count : "–"}</TableCell>
                  <TableCell align="right">
                    <Box sx={{ display: "inline-flex", gap: 0.75 }}>
                      <ActionButton onClick={() => setExpanded(expanded === s.name ? null : s.name)}>
                        {expanded === s.name ? t("settingsAccess.mcp.hideTools") : t("settingsAccess.mcp.showTools")}
                      </ActionButton>
                      {s.connected ? (
                        <ActionButton loading={!!busy[s.name]}
                          onClick={() => withBusy(s.name, () => disconnectMcpServer(s.name))}>
                          {t("settingsAccess.mcp.disconnect")}
                        </ActionButton>
                      ) : (
                        <ActionButton loading={!!busy[s.name]} disabled={s.disabled}
                          onClick={() => withBusy(s.name, () => connectMcpServer(s.name),
                            t("settingsAccess.mcp.connectedTo", { name: s.name }))}>
                          {t("settingsAccess.mcp.connect")}
                        </ActionButton>
                      )}
                      <ActionButton onClick={() => openEdit(s.name)}>{t("settingsAccess.mcp.edit")}</ActionButton>
                      <ActionButton kind="destructive" onClick={() => setRemoving(s)}>{t("settingsAccess.mcp.remove")}</ActionButton>
                    </Box>
                  </TableCell>
                </TableRow>
                <TableRow>
                  <TableCell colSpan={4} sx={{ py: 0, borderBottom: expanded === s.name ? undefined : "none" }}>
                    <Collapse in={expanded === s.name} unmountOnExit>
                      <ServerDetail name={s.name} key={`${s.name}-${s.status}`} />
                    </Collapse>
                  </TableCell>
                </TableRow>
              </React.Fragment>
            ))}
          </TableBody>
        </Table>
      )}

      <Box sx={{ mt: 1.5 }}>
        <ActionButton onClick={() => setShowAudit((v) => !v)}>
          {showAudit ? t("settingsAccess.mcp.hideActivity") : t("settingsAccess.mcp.showActivity")}
        </ActionButton>
        <Collapse in={showAudit} unmountOnExit>
          {audit.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>{t("settingsAccess.mcp.noCalls")}</Typography>
          ) : (
            <Table size="small" sx={{ mt: 1 }}>
              <TableHead>
                <TableRow>
                  <TableCell>{t("settingsAccess.mcp.auditColumns.time")}</TableCell>
                  <TableCell>{t("settingsAccess.mcp.auditColumns.call")}</TableCell>
                  <TableCell>{t("settingsAccess.mcp.auditColumns.by")}</TableCell>
                  <TableCell>{t("settingsAccess.mcp.auditColumns.result")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {audit.map((a, i) => {
                  const pill = auditPill(a);
                  return (
                    <TableRow key={`${a.ts}-${i}`}>
                      <TableCell sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}>
                        {new Date(a.ts).toLocaleTimeString(uiLocale())}
                      </TableCell>
                      <TableCell sx={{ fontSize: "0.7rem", fontFamily: "monospace" }}>
                        {a.server}/{a.tool}
                      </TableCell>
                      <TableCell sx={{ fontSize: "0.7rem" }}>{a.caller}</TableCell>
                      <TableCell>
                        <StatusPill label={pill.label} tone={pill.tone}
                          tooltip={a.error || (a.args ? JSON.stringify(a.args) : "")} />
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </Collapse>
      </Box>

      <ServerDialog
        open={dialog.open}
        initial={dialog.initial}
        isEdit={dialog.isEdit}
        onClose={() => setDialog({ open: false, initial: null, isEdit: false })}
        onSaved={(name) => {
          setDialog({ open: false, initial: null, isEdit: false });
          setMessage({ severity: "success", text: t("settingsAccess.mcp.saved", { name }) });
          refresh();
        }}
      />

      <ConfirmActionDialog
        open={!!removing}
        title={t("settingsAccess.mcp.removeTitle", { name: removing?.name || "" })}
        description={t("settingsAccess.mcp.removeDescription")}
        facts={removing ? [
          { label: t("settingsAccess.mcp.factCommand"), value: removing.command || "–" },
          { label: t("settingsAccess.mcp.factTools"), value: removing.connected ? removing.tool_count : t("settingsAccess.mcp.notConnected") },
        ] : []}
        keeps={t("settingsAccess.mcp.removeKeeps")}
        confirmLabel={t("settingsAccess.mcp.remove")}
        onConfirm={confirmRemove}
        onClose={() => setRemoving(null)}
      />
    </Box>
  );
};

export default MCPServersSection;
