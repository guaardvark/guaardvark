#!/usr/bin/env python3
"""Regression tests for the Settings → Agent Display installer.

The installer failed on two stock Ubuntu machines because it shelled out to
`sudo -n apt-get install`. A Flask request has no controlling terminal, so `sudo -n`
exits non-zero ("interactive authentication is required") on any host that has not
been given passwordless sudo — which is the default.

The button now escalates on its own: passwordless sudo when the host allows it,
otherwise pkexec, which raises a password dialog on the user's desktop. Only when
there is no desktop to prompt on (SSH, remote browser) does it fall back to handing
over a command. These tests pin that ordering and the safety gate on package names.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ["GUAARDVARK_MODE"] = "test"

from flask import Flask  # noqa: E402

import backend.api.agent_control_api as api  # noqa: E402


ABSENT = {"Xvfb", "x11vnc"}


def _client():
    app = Flask(__name__)
    app.register_blueprint(api.agent_control_bp, url_prefix="/api/agent-control")
    app.config["TESTING"] = True
    return app.test_client()


def _which_missing_some(cmd):
    """shutil.which stand-in: Xvfb and x11vnc absent, everything else present."""
    if cmd in ABSENT:
        return None
    return f"/usr/bin/{cmd}"


def _which_all_present(cmd):
    return f"/usr/bin/{cmd}"


class _Result:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


class TestSudoDetection(unittest.TestCase):

    def test_passwordless_sudo_false_when_sudo_needs_a_password(self):
        with patch("subprocess.run", return_value=_Result(returncode=1)):
            self.assertFalse(api._passwordless_sudo_available())

    def test_passwordless_sudo_true_when_sudo_is_open(self):
        with patch("subprocess.run", return_value=_Result(returncode=0)):
            self.assertTrue(api._passwordless_sudo_available())

    def test_passwordless_sudo_false_when_sudo_is_absent(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(api._passwordless_sudo_available())


class TestDesktopSessionDetection(unittest.TestCase):

    def test_needs_pkexec_on_path(self):
        with patch("shutil.which", return_value=None), \
             patch.dict(os.environ, {"DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertFalse(api._desktop_session_available())

    def test_needs_a_display(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with patch("shutil.which", return_value="/usr/bin/pkexec"), \
             patch.dict(os.environ, env, clear=True):
            self.assertFalse(api._desktop_session_available())

    def test_true_with_pkexec_display_and_a_session_bus(self):
        with patch("shutil.which", return_value="/usr/bin/pkexec"), \
             patch.dict(os.environ, {"DISPLAY": ":0", "XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertTrue(api._desktop_session_available())

    def test_wayland_counts_as_a_display(self):
        env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
        env.update({"WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000"})
        with patch("shutil.which", return_value="/usr/bin/pkexec"), \
             patch.dict(os.environ, env, clear=True):
            self.assertTrue(api._desktop_session_available())


class TestInstallScriptSafety(unittest.TestCase):

    def test_refreshes_the_index_and_installs_in_one_invocation(self):
        # One shell line on purpose: pkexec authenticates per invocation, so
        # splitting these would ask the user for a password twice.
        script = api._apt_install_script(["xvfb", "x11vnc"])
        self.assertIn("apt-get update -qq", script)
        self.assertIn("apt-get install -y xvfb x11vnc", script)
        self.assertLess(script.index("update"), script.index("install"))

    def test_index_refresh_failure_cannot_abort_the_install(self):
        self.assertIn("|| true", api._apt_install_script(["xvfb"]))

    def test_rejects_packages_outside_the_allowlist(self):
        # This string is interpolated into a shell line run as root.
        with self.assertRaises(ValueError):
            api._apt_install_script(["xvfb", "; rm -rf /"])
        with self.assertRaises(ValueError):
            api._apt_install_script(["curl"])

    def test_allowlist_covers_exactly_what_the_probe_looks_for(self):
        probed = {pkg for pkg, _ in api._DISPLAY_SYSTEM_DEPS}
        probed |= {pkg for pkg, _ in api._DISPLAY_BROWSER_CHOICES}
        self.assertEqual(probed, set(api._ALLOWED_APT_PACKAGES))

    def test_probes_what_the_start_script_runs(self):
        # start_agent_display.sh starts the desktop with `dbus-run-session -- startxfce4`
        # and uses lsof to free the VNC port; openbox and tint2 are no longer used.
        probed = {cmd for _, cmd in api._DISPLAY_SYSTEM_DEPS}
        self.assertTrue({"startxfce4", "dbus-run-session", "lsof"} <= probed)
        self.assertFalse({"openbox", "tint2"} & probed)

    def test_manual_command_is_runnable(self):
        self.assertEqual(
            api._manual_apt_command(["xvfb", "x11vnc"]),
            "sudo apt-get update && sudo apt-get install -y xvfb x11vnc",
        )


class TestVersionProbe(unittest.TestCase):

    def test_never_runs_programs_that_ignore_the_version_flag(self):
        # startxfce4 starts an X server when given --version.
        for cmd in ("startxfce4", "lsof"):
            with patch("shutil.which", return_value=f"/usr/bin/{cmd}"), \
                 patch("subprocess.run") as run:
                self.assertEqual(api._probe_command_version(cmd), "installed")
                run.assert_not_called()


class TestPrivilegeEscalationOrder(unittest.TestCase):

    def test_prefers_passwordless_sudo_when_available(self):
        seen = []
        with patch.object(api, "_passwordless_sudo_available", return_value=True), \
             patch.object(api, "_desktop_session_available", return_value=True), \
             patch("subprocess.run", side_effect=lambda cmd, *a, **k: seen.append(cmd) or _Result()):
            out = api._run_privileged_apt(["xvfb"])

        self.assertEqual(out["method"], "sudo")
        self.assertTrue(out["ok"])
        self.assertEqual(seen[0][:2], ["sudo", "-n"])

    def test_falls_back_to_pkexec_when_sudo_needs_a_password(self):
        seen = []
        with patch.object(api, "_passwordless_sudo_available", return_value=False), \
             patch.object(api, "_desktop_session_available", return_value=True), \
             patch("subprocess.run", side_effect=lambda cmd, *a, **k: seen.append(cmd) or _Result()):
            out = api._run_privileged_apt(["xvfb"])

        self.assertEqual(out["method"], "pkexec")
        self.assertTrue(out["ok"])
        self.assertEqual(seen[0][0], "pkexec")

    def test_reports_none_when_there_is_nothing_to_prompt_on(self):
        with patch.object(api, "_passwordless_sudo_available", return_value=False), \
             patch.object(api, "_desktop_session_available", return_value=False), \
             patch("subprocess.run") as run:
            out = api._run_privileged_apt(["xvfb"])

        run.assert_not_called()
        self.assertEqual(out["method"], "none")

    def test_timeout_is_reported_rather_than_raised(self):
        import subprocess
        with patch.object(api, "_passwordless_sudo_available", return_value=False), \
             patch.object(api, "_desktop_session_available", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pkexec", 1)):
            out = api._run_privileged_apt(["xvfb"])

        self.assertEqual(out["returncode"], "timeout")
        self.assertFalse(out["ok"])


class TestDisplayStatus(unittest.TestCase):

    def test_advertises_pkexec_when_sudo_needs_a_password(self):
        with patch("shutil.which", side_effect=_which_missing_some), \
             patch.object(api, "_passwordless_sudo_available", return_value=False), \
             patch.object(api, "_desktop_session_available", return_value=True):
            body = _client().get("/api/agent-control/display-status").get_json()

        self.assertEqual(body["missing_apt_packages"], ["xvfb", "x11vnc"])
        self.assertTrue(body["can_auto_install"])  # the button still works
        self.assertEqual(body["install_method"], "pkexec")
        self.assertIsNone(body["manual_command"])

    def test_falls_back_to_a_manual_command_with_no_desktop(self):
        with patch("shutil.which", side_effect=_which_missing_some), \
             patch.object(api, "_passwordless_sudo_available", return_value=False), \
             patch.object(api, "_desktop_session_available", return_value=False):
            body = _client().get("/api/agent-control/display-status").get_json()

        self.assertFalse(body["can_auto_install"])
        self.assertEqual(body["install_method"], "none")
        self.assertIn("apt-get install -y xvfb x11vnc", body["manual_command"])

    def test_does_not_probe_escalation_when_nothing_is_missing(self):
        with patch("shutil.which", side_effect=_which_all_present), \
             patch.object(api, "_passwordless_sudo_available") as sudo_probe, \
             patch.object(api, "_desktop_session_available") as desktop_probe:
            body = _client().get("/api/agent-control/display-status").get_json()

        sudo_probe.assert_not_called()
        desktop_probe.assert_not_called()
        self.assertIsNone(body["install_method"])
        self.assertEqual(body["missing_apt_packages"], [])


class TestInstallDisplay(unittest.TestCase):

    def _install_with(self, apt_result, which=_which_missing_some):
        """POST install-display with _run_privileged_apt stubbed."""
        client = _client()
        with patch("shutil.which", side_effect=which), \
             patch.object(api, "_run_privileged_apt", return_value=apt_result), \
             patch("os.path.exists", return_value=True):
            return client.post("/api/agent-control/install-display")

    def test_installs_via_pkexec_without_asking_the_user_to_do_anything(self):
        """The whole point of the fix: the button completes the job itself."""
        state = {"done": False}

        def which(cmd):
            if cmd in ABSENT and not state["done"]:
                return None
            return f"/usr/bin/{cmd}"

        def escalate(packages, **kw):
            state["done"] = True
            return {"ok": True, "method": "pkexec", "returncode": 0, "stderr": ""}

        client = _client()
        with patch("shutil.which", side_effect=which), \
             patch.object(api, "_run_privileged_apt", side_effect=escalate), \
             patch("os.path.exists", return_value=True):
            response = client.post("/api/agent-control/install-display")

        self.assertEqual(response.status_code, 200, response.get_json())
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertNotIn("needs_manual_install", body)

    def test_dismissed_password_prompt_says_so(self):
        response = self._install_with(
            {"ok": False, "method": "pkexec", "returncode": 126, "stderr": ""}
        )
        self.assertEqual(response.status_code, 403)
        body = response.get_json()
        self.assertIn("dismissed", body["error"].lower())
        # Not a manual-install case: retrying the button is the right next step.
        self.assertNotIn("manual_command", body)

    def test_no_desktop_hands_back_a_command(self):
        response = self._install_with(
            {"ok": False, "method": "none", "returncode": None, "stderr": ""}
        )
        self.assertEqual(response.status_code, 409)
        body = response.get_json()
        self.assertTrue(body["needs_manual_install"])
        self.assertIn("apt-get install -y xvfb x11vnc", body["manual_command"])

    def test_apt_failure_hands_back_a_command(self):
        response = self._install_with({
            "ok": False, "method": "pkexec", "returncode": 100,
            "stderr": "E: Unable to locate package xvfb",
        })
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertTrue(body["needs_manual_install"])
        self.assertIn("Unable to locate package", body["stderr_tail"])

    def test_timeout_is_surfaced_as_a_gateway_timeout(self):
        response = self._install_with(
            {"ok": False, "method": "pkexec", "returncode": "timeout", "stderr": ""}
        )
        self.assertEqual(response.status_code, 504)

    def test_pip_only_gap_never_escalates(self):
        client = _client()
        seen = {"n": 0}

        def find_spec(name, *a, **kw):
            seen["n"] += 1
            return None if seen["n"] == 1 else object()  # missing, then present

        commands = []

        with patch("shutil.which", side_effect=_which_all_present), \
             patch.object(api, "_run_privileged_apt") as escalate, \
             patch("importlib.util.find_spec", side_effect=find_spec), \
             patch("subprocess.run",
                   side_effect=lambda cmd, *a, **k: commands.append(cmd) or _Result()), \
             patch("os.path.exists", return_value=True):
            response = client.post("/api/agent-control/install-display")

        self.assertEqual(response.status_code, 200, response.get_json())
        escalate.assert_not_called()  # pip needs no root
        self.assertTrue(any("pip" in c for c in commands))


if __name__ == "__main__":
    unittest.main(verbosity=2)
