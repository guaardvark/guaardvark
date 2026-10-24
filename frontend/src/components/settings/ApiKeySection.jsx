// frontend/src/components/settings/ApiKeySection.jsx
// Settings → Access: network access, this browser's sign-in, and this
// install's key.
//
// Protected actions (running tools, automation, backups, file edits, ...)
// answer the Guaardvark machine itself while the install has no key. Once it
// has one, every device, that machine included, needs it. Network access on
// opens them to every device on the local network, key or not. A browser signs in
// by sending the key once to /api/auth/session; it then holds an HttpOnly
// cookie, never the key (api/apiAuth.js). The install's key is created,
// replaced and removed here, from the Guaardvark machine while there is no
// key, or from a browser signed in with the current key.
/* eslint-env browser */

import React, { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  IconButton,
  InputAdornment,
  TextField,
  Typography,
} from "@mui/material";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";

import { describeAuthRefusal, notifySessionChanged, onSessionChanged } from "../../api/apiAuth";
import {
  createApiKey,
  getAuthStatus,
  removeApiKey,
  replaceApiKey,
  setNetworkAccess,
  signIn,
  signOut,
} from "../../api/authService";
import { ActionButton, Cluster, ConfirmActionDialog, Hint, Line, SettingChip, SettingsPanel, StatusPill } from "./ui";
import { translate, useUiTranslation } from "../../i18n";

export const API_KEY_SECTION_ID = "settings-api-key";

const machineOf = (status) => status?.machine || translate("settingsAccess.apiKey.theMachine");

const noKeyElsewhere = (docker, machine = translate("settingsAccess.apiKey.theMachine")) =>
  docker
    ? translate("settingsAccess.apiKey.noKeyDocker")
    : translate("settingsAccess.apiKey.noKeyElsewhere", { machine });

/** Pill and sentence for what a status answer means for this browser. */
export function describeStatus(status) {
  if (!status) return { tone: "neutral", label: translate("settingsAccess.apiKey.status.checking"), text: "", ok: false };
  if (status.network_access && status.on_local_network) {
    return {
      tone: "ok",
      label: translate("settingsAccess.apiKey.status.networkAccess"),
      text: translate("settingsAccess.apiKey.status.networkAccessText", { machine: machineOf(status) }),
      ok: true,
    };
  }
  if (status.session_ok) {
    return {
      tone: "ok",
      label: translate("settingsAccess.apiKey.status.signedIn"),
      text: translate("settingsAccess.apiKey.status.signedInText"),
      ok: true,
    };
  }
  if (!status.key_required && status.this_machine) {
    return {
      tone: "ok",
      label: translate("settingsAccess.apiKey.status.thisMachine"),
      text: translate("settingsAccess.apiKey.status.thisMachineText"),
      ok: true,
    };
  }
  if (status.key_required && status.session_rejected) {
    return { tone: "error", label: translate("settingsAccess.apiKey.status.outOfDate"), text: describeAuthRefusal("api_key_required", true), ok: false };
  }
  if (status.key_required) {
    return {
      tone: "warn",
      label: translate("settingsAccess.apiKey.status.notSignedIn"),
      text: translate("settingsAccess.apiKey.status.notSignedInText"),
      ok: false,
    };
  }
  return { tone: "warn", label: translate("settingsAccess.apiKey.status.machineOnly", { machine: machineOf(status) }), text: noKeyElsewhere(status.docker, machineOf(status)), ok: false };
}

function manageHint(status) {
  if (!status || status.can_manage_key) return null;
  if (status.manage_note) return status.manage_note;
  if (!status.key_required) {
    return status.docker
      ? translate("settingsAccess.apiKey.manageDocker")
      : translate("settingsAccess.apiKey.manageOnMachine", { machine: machineOf(status) });
  }
  return translate("settingsAccess.apiKey.manageSignIn");
}

// navigator.clipboard exists only in secure contexts, and a LAN address over
// http is not one; selecting the field and copying works there.
async function copyText(text, input) {
  try {
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the selection copy.
  }
  try {
    input?.focus();
    input?.select();
    return document.execCommand("copy");
  } catch {
    return false;
  }
}

function NewKeyDialog({ apiKey, onClose }) {
  const { t } = useUiTranslation();
  const inputRef = useRef(null);
  const [copied, setCopied] = useState(null);
  const copy = async () => setCopied(await copyText(apiKey, inputRef.current));
  return (
    <Dialog open={Boolean(apiKey)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: "1rem" }}>{t("settingsAccess.apiKey.newKey.title")}</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          {t("settingsAccess.apiKey.newKey.body")}
        </Typography>
        <Line nowrap>
          <TextField
            className="grow"
            size="small"
            value={apiKey || ""}
            inputRef={inputRef}
            onFocus={(e) => e.target.select()}
            InputProps={{ readOnly: true, sx: { fontFamily: "monospace", fontSize: "0.85rem" } }}
          />
          <ActionButton onClick={copy}>{t("settingsAccess.apiKey.newKey.copy")}</ActionButton>
        </Line>
        {copied === true && <Hint>{t("settingsAccess.apiKey.newKey.copied")}</Hint>}
        {copied === false && <Hint>{t("settingsAccess.apiKey.newKey.copyFailed")}</Hint>}
        <Hint>
          {t("settingsAccess.apiKey.newKey.lostIt")}
        </Hint>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose}>{t("settingsAccess.apiKey.newKey.done")}</ActionButton>
      </DialogActions>
    </Dialog>
  );
}

NewKeyDialog.propTypes = {
  apiKey: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};

export default function ApiKeySection() {
  const { t } = useUiTranslation();
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [draft, setDraft] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(null);
  const [confirm, setConfirm] = useState(null);
  // A new key, held only while the dialog that shows it once is open.
  const [newKey, setNewKey] = useState(null);
  const [actionError, setActionError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getAuthStatus();
      setStatus(next);
      setStatusError(null);
      return next;
    } catch (e) {
      setStatusError(e.message || t("settingsAccess.apiKey.statusFailed"));
      return null;
    }
  }, []);

  useEffect(() => {
    refresh();
    return onSessionChanged(refresh);
  }, [refresh]);

  const run = async (name, fn) => {
    setBusy(name);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e.message || t("settingsAccess.apiKey.actionFailed"));
    } finally {
      setBusy(null);
    }
  };

  const test = () =>
    run("test", async () => {
      const typed = draft.trim();
      if (typed) {
        // Checks the typed key without signing in with it.
        const answer = await getAuthStatus({ key: typed });
        setResult(
          answer.key_ok
            ? { severity: "success", text: t("settingsAccess.apiKey.testOk") }
            : answer.key_required
              ? { severity: "warning", text: t("settingsAccess.apiKey.testWrong") }
              : { severity: "warning", text: noKeyElsewhere(answer.docker, machineOf(answer)) },
        );
        return;
      }
      const said = describeStatus(await refresh());
      setResult({ severity: said.ok ? "success" : "warning", text: said.text });
    });

  const save = () =>
    run("save", async () => {
      try {
        await signIn(draft.trim());
      } catch (e) {
        setResult({ severity: "warning", text: e.message || t("settingsAccess.apiKey.signInFailed") });
        return;
      }
      setDraft("");
      setShowKey(false);
      notifySessionChanged();
      setResult({ severity: "success", text: t("settingsAccess.apiKey.signedInResult") });
    });

  const leave = () =>
    run("signout", async () => {
      await signOut();
      notifySessionChanged();
      setResult({ severity: "info", text: t("settingsAccess.apiKey.signedOut") });
    });

  const toggleNetwork = (enabled) =>
    run("network", async () => {
      await setNetworkAccess(enabled);
      notifySessionChanged();
      await refresh();
    });

  const showNewKey = (key) => {
    setNewKey(key);
    setResult(null);
    notifySessionChanged();
  };

  const create = () =>
    run("create", async () => {
      const { key } = await createApiKey();
      showNewKey(key);
    });

  const replace = () =>
    run("replace", async () => {
      setConfirm(null);
      const { key } = await replaceApiKey();
      showNewKey(key);
    });

  const remove = () =>
    run("remove", async () => {
      setConfirm(null);
      await removeApiKey();
      notifySessionChanged();
      setResult({ severity: "info", text: t("settingsAccess.apiKey.removedResult") });
    });

  const said = describeStatus(status);
  const typed = draft.trim();
  const hint = manageHint(status);
  const signedIn = Boolean(status?.session_ok || status?.session_rejected);

  return (
    <SettingsPanel
      id={API_KEY_SECTION_ID}
      title={t("settingsAccess.apiKey.title")}
      help={
        <>
          {t("settingsAccess.apiKey.help")}
          {status?.protected?.length > 0 && (
            <Box component="span" sx={{ display: "block", mt: 0.75 }}>
              {t("settingsAccess.apiKey.protected", { actions: status.protected.join(" · ") })}
            </Box>
          )}
        </>
      }
    >
      <Cluster
        label={t("settingsAccess.apiKey.network")}
        help={t("settingsAccess.apiKey.networkHelp")}
        note={status ? (status.network_access ? t("settingsAccess.apiKey.networkNoteOpen") : t("settingsAccess.apiKey.networkNoteClosed")) : undefined}
      >
        <Line>
          <SettingChip
            label={t("settingsAccess.apiKey.networkAccess")}
            on={Boolean(status?.network_access)}
            onToggle={toggleNetwork}
            disabled={!status?.can_manage_network_access || busy === "network"}
            tooltip={
              status?.can_manage_network_access
                ? t("settingsAccess.apiKey.networkTooltip")
                : status?.network_access_note ||
                  t("settingsAccess.apiKey.networkChangeElsewhere", { machine: machineOf(status) })
            }
          />
          {status?.network_access_note && <Hint>{status.network_access_note}</Hint>}
        </Line>
      </Cluster>

      <Cluster
        label={t("settingsAccess.apiKey.thisBrowser")}
        help={t("settingsAccess.apiKey.thisBrowserHelp")}
      >
        <Line>
          <TextField
            className="grow"
            size="small"
            label={t("settingsAccess.apiKey.keyLabel")}
            placeholder={t("settingsAccess.apiKey.keyPlaceholder")}
            type={showKey ? "text" : "password"}
            value={draft}
            autoComplete="off"
            onChange={(e) => {
              setDraft(e.target.value);
              setResult(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && typed) {
                e.preventDefault();
                save();
              }
            }}
            inputProps={{ spellCheck: false, "aria-label": t("settingsAccess.apiKey.keyLabel") }}
            InputProps={{
              sx: { fontFamily: showKey ? "monospace" : undefined },
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    aria-label={showKey ? t("settingsAccess.apiKey.hideKey") : t("settingsAccess.apiKey.showKey")}
                    onClick={() => setShowKey((v) => !v)}
                    edge="end"
                  >
                    {showKey ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                  </IconButton>
                </InputAdornment>
              ),
            }}
          />
          {typed && (
            <ActionButton
              kind="primary"
              onClick={save}
              loading={busy === "save"}
              tooltip={t("settingsAccess.apiKey.saveTooltip")}
            >
              {t("settingsAccess.apiKey.save")}
            </ActionButton>
          )}
          <ActionButton
            onClick={test}
            loading={busy === "test"}
            tooltip={t("settingsAccess.apiKey.testTooltip")}
          >
            {t("settingsAccess.apiKey.test")}
          </ActionButton>
          {signedIn && (
            <ActionButton onClick={leave} loading={busy === "signout"} tooltip={t("settingsAccess.apiKey.signOutTooltip")}>
              {t("settingsAccess.apiKey.signOut")}
            </ActionButton>
          )}
        </Line>
        <Line>
          <StatusPill tone={said.tone} label={said.label} />
          {said.text && <Hint>{said.text}</Hint>}
        </Line>
        {statusError && <Alert severity="error" sx={{ py: 0.25 }}>{statusError}</Alert>}
        {result && (
          <Alert severity={result.severity} sx={{ py: 0.25 }} onClose={() => setResult(null)}>
            {result.text}
          </Alert>
        )}
      </Cluster>

      <Cluster
        label={t("settingsAccess.apiKey.thisInstall")}
        help={t("settingsAccess.apiKey.thisInstallHelp")}
        note={status ? (status.key_required ? t("settingsAccess.apiKey.hasKey") : t("settingsAccess.apiKey.noKeyYet")) : undefined}
      >
        {status?.can_manage_key && (
          <Line>
            {!status.key_required ? (
              <ActionButton
                onClick={create}
                loading={busy === "create"}
                tooltip={t("settingsAccess.apiKey.createTooltip")}
              >
                {t("settingsAccess.apiKey.create")}
              </ActionButton>
            ) : (
              <>
                <ActionButton onClick={() => setConfirm("replace")} loading={busy === "replace"}>
                  {t("settingsAccess.apiKey.replace")}
                </ActionButton>
                <ActionButton onClick={() => setConfirm("remove")} loading={busy === "remove"}>
                  {t("settingsAccess.apiKey.remove")}
                </ActionButton>
              </>
            )}
          </Line>
        )}
        {hint && <Hint>{hint}</Hint>}
        {actionError && (
          <Alert severity="error" sx={{ py: 0.25 }} onClose={() => setActionError(null)}>
            {actionError}
          </Alert>
        )}
      </Cluster>

      <ConfirmActionDialog
        open={confirm === "replace"}
        title={t("settingsAccess.apiKey.replaceTitle")}
        description={t("settingsAccess.apiKey.replaceDescription")}
        keeps={t("settingsAccess.apiKey.keeps")}
        confirmLabel={t("settingsAccess.apiKey.replace")}
        busy={busy === "replace"}
        onConfirm={replace}
        onClose={() => setConfirm(null)}
      />
      <ConfirmActionDialog
        open={confirm === "remove"}
        title={t("settingsAccess.apiKey.removeTitle")}
        description={t("settingsAccess.apiKey.removeDescription")}
        keeps={t("settingsAccess.apiKey.keeps")}
        confirmLabel={t("settingsAccess.apiKey.remove")}
        busy={busy === "remove"}
        onConfirm={remove}
        onClose={() => setConfirm(null)}
      />
      <NewKeyDialog apiKey={newKey} onClose={() => setNewKey(null)} />
    </SettingsPanel>
  );
}
