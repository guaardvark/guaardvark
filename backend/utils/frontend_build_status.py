"""What frontend/dist holds, for /api/health/frontend.

start.sh and scripts/frontend_rebuild.py (a rebuild made while Guaardvark runs)
record every production build attempt in .start_cache/frontend_build.json and
write frontend/dist/.build_fp only after a build has finished, so a dist without
that stamp did not come from a finished build of theirs. start.sh serves dist
with `vite preview`; when it was started with the dev server, or its build did
not finish, the interface comes from frontend/src instead and dist is not read
(backend/utils/frontend_serve.py says which).

Statuses:
  ok           dist is a finished build of the code start.sh last checked
  stale        the latest build failed; dist is an earlier finished build
  building     a build started and has not recorded a result; dist is an earlier finished build
  incomplete   dist exists but the build that wrote it did not finish
  unverified   dist exists without a completion stamp and no failed attempt is
               recorded (built outside start.sh, or by a version that kept no record)
  unavailable  there is no dist
"""

import json
import os
from datetime import datetime, timezone

OOM_EXIT_CODE = 137


def _iso_mtime(path):
    dt = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_attempt(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _failure_reason(code, reason=None):
    if reason:
        return reason
    if code == OOM_EXIT_CODE:
        return "the last production build ran out of memory (exit code 137)"
    return f"the last production build failed (exit code {code})"


def frontend_build_status(repo_root):
    """Return (payload, http_status) describing frontend/dist under repo_root."""
    dist = os.path.join(repo_root, "frontend", "dist")
    index_path = os.path.join(dist, "index.html")
    stamp_path = os.path.join(dist, ".build_fp")
    attempt = _read_attempt(os.path.join(repo_root, ".start_cache", "frontend_build.json"))

    payload = {}
    if attempt:
        payload["last_attempt"] = {
            "state": attempt.get("state"),
            "exit_code": attempt.get("exit_code"),
            "at": attempt.get("at"),
        }
    state = (attempt or {}).get("state")
    code = (attempt or {}).get("exit_code")
    why = (attempt or {}).get("reason")

    if not os.path.exists(index_path):
        if state == "failed" and code == OOM_EXIT_CODE:
            reason = "the production build did not finish (out of memory); interface served by the dev server"
        elif state == "failed":
            reason = _failure_reason(code, why)
        elif state == "running":
            reason = "a production build started and has not finished"
        else:
            reason = "no frontend build found (frontend/dist/index.html missing)"
        payload.update(status="unavailable", error=reason)
        return payload, 503

    try:
        with open(stamp_path, encoding="utf-8") as fh:
            stamp = fh.read().strip()
    except OSError:
        stamp = None

    if not stamp:
        if state in ("failed", "running"):
            reason = "the build that wrote frontend/dist did not finish"
            if state == "failed":
                reason += f" (exit code {code})"
            payload.update(status="incomplete", error=reason)
            return payload, 503
        payload.update(
            status="unverified",
            error="frontend/dist has no completion stamp: it was built outside start.sh "
            "or by a build that did not finish",
        )
        return payload, 200

    # A finished build exists; its stamp was written when that build completed.
    payload["build_timestamp"] = _iso_mtime(stamp_path)
    if state == "failed":
        payload.update(status="stale", error=_failure_reason(code, why) + "; frontend/dist is an earlier build")
    elif state == "running":
        payload.update(status="building", error="a production build started and has not finished")
    elif attempt and attempt.get("fingerprint") != stamp:
        payload.update(
            status="unverified",
            error="the last recorded build does not match frontend/dist",
        )
    else:
        payload["status"] = "ok"
    return payload, 200
