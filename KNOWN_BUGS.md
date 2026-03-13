# Known Bugs — Guaardvark v2.4.1

Last updated: 2026-03-13

## Active Bugs

### #51 — Voice chat not streaming text response
- **Severity:** Medium
- **Page:** ChatPage
- **Description:** Voice transcription works (Whisper captures and transcribes speech correctly), but `aiResponse` is missing from the voice message flow. Messages fall through to the normal chat pipeline instead of streaming back as voice.
- **Console output:** `VOICE WARNING: Voice message detected but missing aiResponse - will process through normal chat flow`
- **File:** `frontend/src/pages/ChatPage.jsx` (lines ~1170-1173)

### #52 — ALPACA: White screen after idle refresh
- **Severity:** Medium
- **Machine:** ALPACA only
- **Description:** After ~10 minutes idle, refreshing the browser shows a white screen. Backend process stops running after reboot/idle, causing all API calls to fail (status 0). Vite proxy on port 5175 can't reach Flask backend.
- **Related:** WebSocket failures (`ws://localhost:5175/socket.io/`), `fetchActiveJobs` abort errors in UnifiedProgressContext
- **Workaround:** Restart the backend on ALPACA (`./start.sh`)
- **UPDATE:** This issue comes and goes. We'll need to look into a 'refresher' for when the system is rebooted via the 'reboot' button on SettingsPage, to refresh any stale items which may lead to stale session data. 

### Chat history sometimes blank on page load
- **Severity:** Low
- **Page:** ChatPage
- **Description:** Chat message history occasionally doesn't render on initial page load. Suspected React strict mode race condition with double-mounting. Also, sometimes the Model Selector is empty on the SettingsPage, reboot usually fixes this but we will need to consider 'refresher' aspect mentioned in Update above. 

### MUI Tooltip on disabled buttons
- **Severity:** Low (cosmetic)
- **Description:** Console warning: "You are providing a disabled `button` child to the Tooltip component. A disabled element does not fire events."
- **Fix needed:** Wrap disabled IconButtons in a `<span>` element inside Tooltip components.
- **File:** Multiple pages (seen in ChatPage toolbar)

### EMU (Pi): psycopg2 reinstall loop on ARM
- **Severity:** Low
- **Machine:** EMU (Raspberry Pi) only
- **Description:** Interconnector sync triggers psycopg2 reinstall on ARM architecture, which fails and loops. ARM needs `psycopg2-binary` instead of building from source.
- **Fix needed:** Add ARM architecture detection, just like we did with the PyTorch / Nvidia GPU Detection. 

## Open Feature Issues

### #46 — Redesign System Config Backup/Restore modal
- **Type:** UI improvement
- **Description:** Current backup/restore modal needs a redesign for better UX.

### #62 — Interconnector sync using wrong port on remote machine
- **Type:** Bug
- **Component:** CLI
- **Description:** Interconnector sync doesn't respect the remote machine's configured port, defaults to 5000 instead of reading FLASK_PORT.

### #63 — Beef up CLI functionality
- **Type:** Enhancement
- **Component:** CLI
- **Description:** CLI needs expanded commands and capabilities (dedicated phase planned).

## Resolved Recently (2026-03-12)

These were fixed in the pre-launch bug bash and are listed for reference:

- **DB connection leak** — `after_request` handler skipped `db.session.remove()` when `in_transaction()=True`, causing pool exhaustion. Fixed: always call `db.session.remove()`.
- **Progress cleanup race condition** — Stale cleanup timer from failed task killed active retry's progress. Fixed with cancel-on-recreate + active-state guard.
- **Celery double-retry** — Restarting a task created parallel retry loops. Fixed: revoke old Celery task before queueing new one.
- **Retry status contradiction** — Task set to 'failed' then retried (DB says failed but Celery running). Fixed: set 'queued' on retry, 'failed' only when retries exhausted.
- **TaskPage TDZ crash** — `useEffect` referenced `fetchTasks` before `const` declaration. Fixed: moved effects after all `useCallback` declarations.

## Environment Notes

- **GPU VRAM:** 16GB card is tight. Model switch now unloads old model before loading new. Best code gen model: `qwen2.5-coder:14b`.
- **TMOUT:** `/etc/profile.d/timeout.sh` sets `TMOUT=900` on both LLAMAX1 and ALPACA. Mitigated by `unset TMOUT` in `~/.bashrc` and `start.sh`.
- **PostgreSQL:** Must run `sudo systemctl enable postgresql` once per machine for auto-start on boot.
- **Broken test imports:** `test_full_backup.py`, `test_security_self_check.py`, `test_celery_ping.py` have stale imports.
