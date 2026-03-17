# Good First Issues — Ready to Post

Post these on GitHub after `gh auth login`. Each is scoped, actionable, and introduces a new contributor to one part of the codebase.

---

## Issue 1: Add dark/light mode toggle to the Settings page

**Labels:** `good first issue`, `enhancement`, `frontend`

**Description:**

Guaardvark ships with four dark themes (Default, Musk, Hacker, Vader) but no light theme option. Add a dark/light mode toggle or a "Light" theme variant to the Settings page.

**Where to look:**
- `frontend/src/theme.js` — current theme definitions
- `frontend/src/pages/SettingsPage.jsx` — settings UI
- `frontend/src/contexts/` — theme context if it exists

**Acceptance criteria:**
- [ ] New light theme option appears in Settings
- [ ] Theme persists across page refreshes
- [ ] All major pages render readably in light mode

---

## Issue 2: Add keyboard shortcuts overlay (? key)

**Labels:** `good first issue`, `enhancement`, `frontend`

**Description:**

Power users expect a keyboard shortcut reference. Add a `?` shortcut that opens a modal listing all available keyboard shortcuts across the app.

**Where to look:**
- `frontend/src/components/` — existing modal patterns
- `frontend/src/pages/` — see which pages have keyboard handlers

**Acceptance criteria:**
- [ ] Pressing `?` (when not in a text input) opens a shortcuts modal
- [ ] Modal lists shortcuts organized by page/feature
- [ ] Modal closes on Escape or click-outside

---

## Issue 3: Add health check endpoint for frontend build info

**Labels:** `good first issue`, `enhancement`, `backend`

**Description:**

The backend has `/api/health` but it doesn't report frontend build info (version, build timestamp). Add a `/api/health/frontend` endpoint that reads from the Vite build manifest.

**Where to look:**
- `backend/api/` — existing health check endpoints
- `frontend/dist/` — Vite build output and manifest
- `backend/config.py` — path configuration

**Acceptance criteria:**
- [ ] New endpoint returns frontend version and build timestamp
- [ ] Returns appropriate error if no build exists
- [ ] Dashboard can optionally display this info

---

## Issue 4: Add copy-to-clipboard button for chat messages

**Labels:** `good first issue`, `enhancement`, `frontend`

**Description:**

Chat messages with code blocks or long responses should have a "copy to clipboard" button on hover.

**Where to look:**
- `frontend/src/components/` — chat message rendering components
- Material-UI `IconButton` + `ContentCopy` icon pattern used elsewhere in the app

**Acceptance criteria:**
- [ ] Copy button appears on hover over chat messages
- [ ] Clicking copies the message text (or code block content) to clipboard
- [ ] Brief "Copied!" tooltip feedback
- [ ] Works for both user and AI messages

---

## Issue 5: Add `--version` flag to the CLI

**Labels:** `good first issue`, `enhancement`, `cli`

**Description:**

`llx --version` should print the current Guaardvark version. Currently the CLI doesn't expose this.

**Where to look:**
- `cli/llx/` — CLI entry point and command definitions
- `backend/config.py` — where the version string lives

**Acceptance criteria:**
- [ ] `llx --version` prints version number
- [ ] Version matches what's in the backend config

---

## Issue 6: Improve error messages during `start.sh` PostgreSQL setup

**Labels:** `good first issue`, `enhancement`, `devops`

**Description:**

When PostgreSQL setup fails (e.g., wrong permissions, port conflict), `start.sh` shows generic errors. Improve error messages to be specific and suggest fixes.

**Where to look:**
- `start.sh` — main startup script
- `start_postgres.sh` — PostgreSQL provisioning

**Acceptance criteria:**
- [ ] Common failure modes have clear, actionable error messages
- [ ] Messages suggest specific fix commands where possible
- [ ] Non-PostgreSQL users see a clear "skip" path

---

## How to post these

```bash
gh auth login

# Then for each issue:
gh issue create \
  --title "Add dark/light mode toggle to Settings" \
  --body "$(cat <<'EOF'
Guaardvark ships with four dark themes (Default, Musk, Hacker, Vader) but no light theme option.

**Task:** Add a light theme variant to the Settings page theme selector.

**Where to look:**
- `frontend/src/theme.js` — current theme definitions
- `frontend/src/pages/SettingsPage.jsx` — settings UI

**Acceptance criteria:**
- [ ] New light theme option appears in Settings
- [ ] Theme persists across page refreshes
- [ ] All major pages render readably in light mode
EOF
)" \
  --label "good first issue" --label "enhancement" --label "frontend"
```

Repeat for each issue, adjusting title/body/labels.
