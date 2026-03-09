# Session Handoff — 2026-02-28

## Summary

This session focused on fresh install testing and fixing bugs discovered during end-to-end verification of a backup deployed to `/home/llamax1/LLAMAX7_TEST`. The chat context isolation plan (10 phases) was completed in a prior session and is NOT outstanding.

---

## 1. DONE: `duckduckgo-search` Silently Fails to Install (FIXED)

**Problem:** `pip install -r requirements.txt` installs `lxml-5.2.1` instead of the required `6.0.2`, and silently skips `duckduckgo-search` entirely. The web search tool then fails with `No module named 'duckduckgo_search'`, causing the LLM to fall back to filesystem commands for current-events questions.

**Evidence:** Fresh install setup log shows `lxml-5.2.1` in the `Successfully installed` line; `duckduckgo-search` is absent. Manual `pip install duckduckgo-search>=7.5.0` works fine afterward (upgrades lxml to 6.0.2 and installs `primp`).

**Root cause:** Pip dependency resolution conflict — likely `duckduckgo-search` needs `lxml>=6.0` but another package constrains it lower, and pip resolves in favor of the other package, dropping duckduckgo-search.

**Recommended fix in `start.sh`:** Add a post-install verification step after `pip install -r requirements.txt`:

```bash
# After the main pip install, verify critical packages are actually installed
CRITICAL_PACKAGES="duckduckgo-search flask celery redis llama-index-core"
for pkg in $CRITICAL_PACKAGES; do
    if ! pip show "$pkg" >/dev/null 2>&1; then
        vader_warn "Critical package $pkg missing after requirements install — installing individually..."
        pip install "$pkg" >> "$SETUP_LOG" 2>&1
    fi
done
```

Alternatively, split `duckduckgo-search` into a separate install step that runs after the main requirements, ensuring lxml gets upgraded.

**Files:** `start.sh` (~line 893), `backend/requirements.txt`

---

## 2. DONE: `BACKEND_URL` Hardcoded to Port 5000 (FIXED)

**Problem:** `frontend/src/api/apiClient.js` had `BACKEND_URL` defaulting to `http://localhost:5000`. When `FLASK_PORT` is set to anything else (e.g., 5002), Socket.IO and direct backend requests bypassed the Vite proxy, causing connection failures.

**Fix applied:** Changed default from `"http://localhost:5000"` to `window.location.origin`. The Vite proxy handles routing in dev; same-origin works in production.

**File:** `frontend/src/api/apiClient.js` (line 15)

---

## 3. DONE: Flask Trailing-Slash Redirects Cause CORS Errors (FIXED)

**Problem:** Flask routes like `/api/model` redirected to `/api/model/` with an absolute URL including the backend port (e.g., `http://localhost:5002/api/model/`). When accessed through the Vite proxy (origin `localhost:5175`), the browser followed the redirect to a different origin, triggering CORS blocks. This caused "Model: Error" in the chat header.

**Fix applied:** Added `app.url_map.strict_slashes = False` in `create_app()`.

**File:** `backend/app.py` (line 291)

---

## 4. DONE: StreamingMessage Socket.IO Race Condition (FIXED — prior session)

**Problem:** Chat responses required a page refresh to appear. The `onComplete` prop passed to `StreamingMessage.jsx` was an inline function, causing React's useEffect to tear down and re-register socket listeners on every parent re-render. Events emitted during the cleanup gap were silently dropped.

**Fix applied:** Rewrote `StreamingMessage.jsx` to use refs (`sessionIdRef`, `onCompleteRef`) instead of putting callbacks in the useEffect dependency array. Listeners now only depend on `chatService`.

**File:** `frontend/src/components/chat/StreamingMessage.jsx`

---

## 5. DONE: Web Search Tool Improvements (FIXED — prior session)

**Changes made:**
- **`backend/tools/system_tools.py`:** Updated `SystemCommandTool` description to clarify it's for local filesystem only, not general information lookup.
- **`backend/services/unified_chat_engine.py`:** Updated system prompt to prioritize `web_search` for current events questions. Removed "For simple questions, respond directly without tools."
- **`backend/requirements.txt`:** Changed `duckduckgo-search==7.5.0` to `duckduckgo-search>=7.5.0`.

---

## 6. Known Pre-existing Issues (MEDIUM)

These were noted but not addressed:

- **Chat history sometimes doesn't render on page load** — React strict mode race condition with duplicate provider initialization. Workaround already in place (`STRICT MODE PROTECTION: Reusing recent provider`).
- **Some unit tests have broken imports:** `test_full_backup.py`, `test_security_self_check.py`, `test_celery_ping.py`.
- **GPU VRAM is tight:** `llama3:latest` + `qwen3-embedding:8b` uses ~93.7% of the RTX 4070 Ti SUPER's 16GB.
- **"Some health checks failed" warning on startup** — Appears during fresh install because Whisper.cpp source isn't included in code-only backups (voice processing unavailable). Non-critical but the warning is confusing for new users. Could be improved to specify WHICH checks failed.
- **`ensure_pip_requirements()` in `start.sh`** (line 727) parses requirements.txt line-by-line and checks `pip show` for each package. This is a secondary safety net but relies on pip's package name normalization (dashes vs underscores). May miss packages with unusual naming.

---

## 7. Chat Context Isolation (COMPLETE — no action needed)

The 10-phase chat isolation fix was fully implemented and verified in a prior session. See:
- `/home/llamax1/.claude/projects/-home-llamax1-LLAMAX7/memory/chat-isolation-fix.md`
- Plan reference: `/home/llamax1/.claude/plans/harmonic-twirling-swan.md`

All RAG retrieval pathways now filter by `project_id`. Frontend uses per-project session IDs. EntityTracker is per-session. Backward compat maintained via fallback to global index.

---

## Test Environment Cleanup

`/home/llamax1/LLAMAX7_TEST` can be safely deleted. All fixes were made in `/home/llamax1/LLAMAX7` first and copied to the test env. The only test-only change was the manual `pip install duckduckgo-search` in the test venv (which gets recreated on fresh install anyway).
