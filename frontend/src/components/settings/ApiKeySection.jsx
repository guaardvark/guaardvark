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

export const API_KEY_SECTION_ID = "settings-api-key";

const machineOf = (status) => status?.machine || "the Guaardvark machine";

const NO_KEY_ELSEWHERE = (docker, machine = "the Guaardvark machine") =>
  docker
    ? "This install has no API key, so protected actions work only on the Guaardvark machine, and a browser never counts as that machine under Docker. Run ./start-docker.sh on the Docker host: it creates a key, prints it, and restarts with it."
    : `Network access is off and this install has no API key, so protected actions work only on ${machine} itself. Turn on Network access there, or create a key there and enter it here.`;

/** Pill and sentence for what a status answer means for this browser. */
export function describeStatus(status) {
  if (!status) return { tone: "neutral", label: "checking", text: "", ok: false };
  if (status.network_access && status.on_local_network) {
    return {
      tone: "ok",
      label: "network access",
      text: `Network access is on, so this device can run protected actions on ${machineOf(status)} without the key.`,
      ok: true,
    };
  }
  if (status.session_ok) {
    return {
      tone: "ok",
      label: "signed in",
      text: "This browser is signed in with the key and can run protected actions. It keeps a sign-in, not the key.",
      ok: true,
    };
  }
  if (!status.key_required && status.this_machine) {
    return {
      tone: "ok",
      label: "Guaardvark machine",
      text: "This install has no API key, so protected actions work here on the Guaardvark machine without one. Other devices need a key: create one below.",
      ok: true,
    };
  }
  if (status.key_required && status.session_rejected) {
    return { tone: "error", label: "sign-in out of date", text: describeAuthRefusal("api_key_required", true), ok: false };
  }
  if (status.key_required) {
    return {
      tone: "warn",
      label: "not signed in",
      text: "This install has an API key. Enter it above and press Save to sign this browser in.",
      ok: false,
    };
  }
  return { tone: "warn", label: `${machineOf(status)} only`, text: NO_KEY_ELSEWHERE(status.docker, machineOf(status)), ok: false };
}

function manageHint(status) {
  if (!status || status.can_manage_key) return null;
  if (status.manage_note) return status.manage_note;
  if (!status.key_required) {
    return status.docker
      ? "Under Docker the key is made by ./start-docker.sh on the Docker host."
      : `A key can be created only on ${machineOf(status)} itself (or from any local device while Network access is on): open Settings → Access in a browser there.`;
  }
  return "Sign in with the current key above to replace or remove it. The Guaardvark machine keeps it in its .env file as GUAARDVARK_API_KEY.";
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
  const inputRef = useRef(null);
  const [copied, setCopied] = useState(null);
  const copy = async () => setCopied(await copyText(apiKey, inputRef.current));
  return (
    <Dialog open={Boolean(apiKey)} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: "1rem" }}>This install&apos;s API key</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          It is shown once. This browser is already signed in with it and keeps the sign-in, not the key.
          To use Guaardvark from another device, open Settings → Access there, paste it and press Save.
          Command-line and API clients send it in the X-API-Key header.
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
          <ActionButton onClick={copy}>Copy</ActionButton>
        </Line>
        {copied === true && <Hint>Copied.</Hint>}
        {copied === false && <Hint>Copy did not work here; select the key and copy it by hand.</Hint>}
        <Hint>
          Lost it later? The Guaardvark machine keeps it in the .env file in its folder, as
          GUAARDVARK_API_KEY.
        </Hint>
      </DialogContent>
      <DialogActions>
        <ActionButton onClick={onClose}>Done</ActionButton>
      </DialogActions>
    </Dialog>
  );
}

NewKeyDialog.propTypes = {
  apiKey: PropTypes.string,
  onClose: PropTypes.func.isRequired,
};

export default function ApiKeySection() {
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
      setStatusError(e.message || "Could not read the API key status.");
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
      setActionError(e.message || "That did not work.");
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
            ? { severity: "success", text: "That is this install's key. Press Save to sign this browser in with it." }
            : answer.key_required
              ? { severity: "warning", text: "That is not this install's API key." }
              : { severity: "warning", text: NO_KEY_ELSEWHERE(answer.docker, machineOf(answer)) },
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
        setResult({ severity: "warning", text: e.message || "Could not sign in." });
        return;
      }
      setDraft("");
      setShowKey(false);
      notifySessionChanged();
      setResult({ severity: "success", text: "Signed in. This browser keeps the sign-in, not the key." });
    });

  const leave = () =>
    run("signout", async () => {
      await signOut();
      notifySessionChanged();
      setResult({ severity: "info", text: "Signed out in this browser. The install's key is unchanged." });
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
      setResult({ severity: "info", text: "The install has no API key now. Protected actions work only on the Guaardvark machine itself." });
    });

  const said = describeStatus(status);
  const typed = draft.trim();
  const hint = manageHint(status);
  const signedIn = Boolean(status?.session_ok || status?.session_rejected);

  return (
    <SettingsPanel
      id={API_KEY_SECTION_ID}
      title="Access"
      help={
        <>
          What other devices may do here. Everyone on the network can chat and generate. Protected actions need
          this machine, the API key, or Network access on.
          {status?.protected?.length > 0 && (
            <Box component="span" sx={{ display: "block", mt: 0.75 }}>
              Protected: {status.protected.join(" · ")}.
            </Box>
          )}
        </>
      }
    >
      <Cluster
        label="Network"
        help="Network access on: every device on this machine's local network can run protected actions too, with no key. Devices outside the local network still need the key. For a machine on a network you trust."
        note={status ? (status.network_access ? "open to the local network" : "this machine and the key") : undefined}
      >
        <Line>
          <SettingChip
            label="Network access"
            on={Boolean(status?.network_access)}
            onToggle={toggleNetwork}
            disabled={!status?.can_manage_network_access || busy === "network"}
            tooltip={
              status?.can_manage_network_access
                ? "On: everything works from any device on the local network. Off: protected actions need this machine or the API key."
                : status?.network_access_note ||
                  `Change it on ${machineOf(status)} itself, or from a browser signed in with the key.`
            }
          />
          {status?.network_access_note && <Hint>{status.network_access_note}</Hint>}
        </Line>
      </Cluster>

      <Cluster
        label="This browser"
        help="Sign this browser in with the install's key. The browser keeps a sign-in, not the key."
      >
        <Line>
          <TextField
            className="grow"
            size="small"
            label="API key"
            placeholder="Paste this install's key"
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
            inputProps={{ spellCheck: false, "aria-label": "API key" }}
            InputProps={{
              sx: { fontFamily: showKey ? "monospace" : undefined },
              endAdornment: (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    aria-label={showKey ? "Hide the key" : "Show the key"}
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
              tooltip="Signs this browser in; it keeps a sign-in, not the key"
            >
              Save
            </ActionButton>
          )}
          <ActionButton
            onClick={test}
            loading={busy === "test"}
            tooltip="With a key in the field: checks it without signing in. Empty: checks this browser's sign-in."
          >
            Test
          </ActionButton>
          {signedIn && (
            <ActionButton onClick={leave} loading={busy === "signout"} tooltip="Ends the sign-in in this browser only">
              Sign out
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
        label="This install"
        help="Once this install has a key, every device needs it, this machine included: browsers sign in here once, and scripts send it in the X-API-Key header. Replacing or removing the key signs every browser out."
        note={status ? (status.key_required ? "has a key" : "no key yet") : undefined}
      >
        {status?.can_manage_key && (
          <Line>
            {!status.key_required ? (
              <ActionButton
                onClick={create}
                loading={busy === "create"}
                tooltip="Makes a new random key, saves it in .env, signs this browser in and shows the key once"
              >
                Create API key
              </ActionButton>
            ) : (
              <>
                <ActionButton onClick={() => setConfirm("replace")} loading={busy === "replace"}>
                  Replace key
                </ActionButton>
                <ActionButton onClick={() => setConfirm("remove")} loading={busy === "remove"}>
                  Remove key
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
        title="Replace the API key"
        description="A new key takes the place of the current one at once. Every other browser is signed out, and every script and command-line client that uses the current key is refused until it is given the new one. This browser is signed in with the new key."
        keeps="Not touched: your data, chats, settings and generated files."
        confirmLabel="Replace key"
        busy={busy === "replace"}
        onConfirm={replace}
        onClose={() => setConfirm(null)}
      />
      <ConfirmActionDialog
        open={confirm === "remove"}
        title="Remove the API key"
        description="Protected actions go back to working only on the Guaardvark machine itself. Every browser is signed out, and other devices and scripts can no longer run them."
        keeps="Not touched: your data, chats, settings and generated files."
        confirmLabel="Remove key"
        busy={busy === "remove"}
        onConfirm={remove}
        onClose={() => setConfirm(null)}
      />
      <NewKeyDialog apiKey={newKey} onClose={() => setNewKey(null)} />
    </SettingsPanel>
  );
}
