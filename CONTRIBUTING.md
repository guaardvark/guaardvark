# Contributing to Guaardvark

Thanks for your interest in contributing to Guaardvark! Whether it's a bug report, feature idea, documentation fix, or code contribution — every bit helps.

## Quick Links

- [Open Issues](https://github.com/guaardvark/guaardvark/issues)
- [Good First Issues](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Project Board](https://github.com/guaardvark/guaardvark/projects)
- [README — Get Involved](README.md#get-involved)
- Questions that do not fit an issue: info@guaardvark.com

### I want to help — 4 steps

1. **Join Discord** (community + live Guaardvark bot: chat, `/imagine`, status) — invite link in the README when the server bot is live.
2. **Run the project:** `git clone … && ./start.sh` (see Development Setup below).
3. **Pick a** [`good first issue`](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) — especially docs, CLI, hardware tiers (#46–#49).
4. **Open a focused PR** and expect feedback within 24–48 hours when possible.

### Safe vs high-risk areas

| Prefer for first PRs | High risk — open an issue before large changes |
|----------------------|--------------------------------------------------|
| Bug reports with steps and logs | Agent control loop, servo, vision targeting |
| `docs/`, README, INSTALL, CAPABILITIES accuracy | Self-improvement apply / codebase mutation paths |
| `cli/` offline commands (list/validate/doctor polish) | MCP default-deny policy and tool exposure |
| Frontend copy, empty states, accessibility | Plugin CUDA/fork runner and GPU orchestrator core |
| Unit tests for pure helpers | Auth, credentials, Interconnector credential sync |

If your change touches high-risk paths, describe the approach in an issue first. Smaller, reviewable PRs merge faster.

### Agent behaviour is maintainer-written

Some files tell the agents what to do rather than describing code: the recipes and
knowledge in `data/agent/`, rule and lesson bundles, seed rules, skills, and agent
instruction files (`AGENTS.md`, `CLAUDE.md`, `.agents/`, `.claude/`). A recipe runs before
any model reads a request: when a request matches its trigger, its keys and text go
straight to the agent's browser, which may be signed in to the user's accounts.

Maintainers write these files. A pull request from a fork that changes one fails the
inbound check and is closed. To get a new behaviour, open an issue with the phrase, what
should happen, and on which site, and a maintainer will write it.

Every recipe, wherever it comes from (including the ones the agent learns), must also stay
inside these bounds, or the agent does not load it:

- Its triggers start with `^` and do not claim everyday requests ("hello", "check my email").
- What it types comes from the request itself, apart from an address on an allowed site.
- It presses no keys that open a terminal, a run dialog, a console or developer tools, and
  never the system key.
- It clicks nothing that spends, deletes or grants (pay, delete, install, allow).

The checks are in `backend/services/agent_knowledge_validator.py`.

---

## Ways to Contribute

### Report a Bug

Open an issue using the **Bug Report** template. Include:
- Steps to reproduce
- Expected vs actual behavior
- Browser, OS, GPU info if relevant
- Logs from `logs/` directory if applicable

### Suggest a Feature

Open an issue using the **Feature Request** template. Describe the use case — not just the solution.

### Submit Code

1. **Fork** the repo and clone your fork
2. **Create a branch** from `main`: `git checkout -b feature/my-feature`
3. **Make your changes** (see Development Setup below)
4. **Test** your changes: `python3 run_tests.py`
5. **Commit** with a clear message following the project style (see below)
6. **Push** and open a Pull Request against `main`. If your change is larger or
   still settling, target the `dev` integration branch instead; CI and CodeQL run
   on pull requests into either, and `dev` is merged forward into `main`.

### Improve Documentation

Documentation lives in the README, `docs/ARCHITECTURE.md`, and inline code comments. If something confused you, it'll confuse others — fixes welcome.

### Translate the Interface

The interface can run in any language. [docs/LOCALIZATION.md](docs/LOCALIZATION.md) covers writing translatable text, adding a language with a local model, and correcting a translation by hand.

---

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 20.19+ or 22.12+
- PostgreSQL 14+ (auto-installed by `start.sh`)
- Redis 5.0+ (auto-installed by `start.sh`)
- NVIDIA GPU recommended (not required for non-generation features)

### Getting Running

```bash
git clone https://github.com/guaardvark/guaardvark.git
cd guaardvark
./start.sh
```

The startup script handles everything on first run — PostgreSQL, Redis, Python venv, Node modules, database migrations, frontend build, and all services.

### Project Structure

```
backend/           Flask app — API endpoints, services, models
  api/             about 96 API modules (auto-discovered via blueprint_discovery)
  services/        Core business logic + guarded_code_service, plugin runner, etc.
  tools/           about 110 tool classes (100+ registered; policy-gated for MCP)
  tests/           Test suite
frontend/          React/Vite app
  src/pages/       about 50 page components
  src/components/  Many UI components (chat, agent, videoeditor, documents, swarm, etc.)
  src/stores/      Zustand + contexts
cli/               Guaardvark CLI (`llx` / PyPI `guaardvark`); 34 command modules
plugins/           10+ GPU service plugins (each with plugin.json manifest)
scripts/           Operator scripts, system-manager, dep_reconciler, etc.
```

See also [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for project orientation, the architecture overview, and setup/build/test guidance.

### Running Tests

```bash
# All tests
python3 run_tests.py

# Specific test file
python3 -m pytest backend/tests/test_rules.py -vv

# Frontend lint
cd frontend && npm run lint
```

### Frontend Development

```bash
cd frontend
npm install
npm run dev -- --host --port=5173
```

Hot module replacement is enabled — changes appear instantly in the browser.

### Backend Development

```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt

# Run Flask with debug mode
export FLASK_APP=backend.app
export GUAARDVARK_ROOT=$(pwd)/..
flask run --debug --host=0.0.0.0 --port=5000
```

### Database Migrations

`backend/models.py` is the source of truth; a native install stamps Alembic revisions to head rather than replaying them. If you modify it:

- A new table is created at startup by `db.create_all()`.
- A new column on an existing table also needs an idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the schema reconcile block of `backend/app.py`, because `db.create_all()` never alters an existing table.

The backend applies both at every start, and `./start.sh` runs `scripts/schema_sync.py` before it. To apply or check by hand:

```bash
source backend/venv/bin/activate
python3 scripts/schema_sync.py          # create missing tables, stamp Alembic to head
python3 scripts/schema_sync.py --check  # report missing tables, change nothing
```

---

## Code Style

### Commit Messages

Follow the conventional format used in the project:

```
type(scope): short description

# Examples:
feat(chat): add voice input toggle to chat toolbar
fix(documents): prevent folder rename from losing children
refactor(api): extract indexing logic into dedicated service
```

**Types:** `feat`, `fix`, `refactor`, `style`, `docs`, `test`, `chore`

### Python

- Follow existing patterns in the codebase
- Use type hints where the surrounding code does
- Keep imports organized: stdlib, third-party, local
- Use `backend.config` for all path resolution — never hardcode paths (see
  [Portability and Secrets](#portability-and-secrets))

### JavaScript/React

- Functional components with hooks
- Material-UI v5 for all UI elements
- Zustand for global state, React Context for layout/status
- Axios or fetch for REST, Socket.IO for streamed replies

### Comments and Docstrings

This repo is public. Comments are written for the next contributor — and for the
coding agents many of us run — never as a record of the session that produced the
change.

- **Follow the standard convention for the language:** PEP 257 docstrings for Python
  modules, classes and functions; JSDoc for exported JS/JSX functions and components;
  a short usage header for shell scripts. API documentation belongs in a docstring,
  not a `#` block above it.
- **An inline comment earns its place by stating a constraint or intent the code
  cannot express.** If the code already says it, delete the comment.
- **Don't narrate history.** No "used to", "previously", "fixed by", "this was broken
  because". That belongs in the commit message, where it is attached to the diff it
  explains.
- **Don't argue with the reviewer in the code.** "This ensures…", "this is correct
  because…" — once merged, that is noise.
- **One or two lines.** If it needs a paragraph, it belongs in `docs/` or the commit
  message, with at most a one-line pointer in the code.
- **Don't retrofit these rules across files you aren't otherwise touching.** Apply
  them to comments you write or edit.

### General

- Don't over-engineer. Keep changes focused on what was asked.
- Match the style of surrounding code.
- If you're unsure about an approach, open an issue or draft PR to discuss first.

---

## Portability and Secrets

This repo is public, it gets forked into other people's projects, and **git history is
permanent**. By the time anyone notices a bad commit it has been cloned, forked and
cached; rewriting history to remove it breaks every fork downstream. Treat every commit
as unrecallable — checking costs seconds.

**Four things never enter a commit:**

1. **Machine paths.** Derive them from the repo root
   (`Path(__file__).resolve().parents[N]`) or read an environment variable with a
   portable default. Never an absolute path in a literal — Linux, macOS or Windows
   alike. `os.path.expanduser("~/Downloads")` is fine; it resolves per user.
2. **Identity.** No personal names, private hostnames or tailnet addresses, in code or
   comments. Describe the role — "the operator", "the production box" — not the person
   or the machine. A machine nickname is identity too: keep the technical detail that
   helps a stranger debug (*"an ASRock Z390 Pro4, I219-V"*), drop the name.
3. **Secrets.** No keys, tokens or credentials in any file, ever — test fixtures and
   example configs included. Credentials belong outside the repo tree so a release
   archive cannot carry them.
4. **Machine-generated state.** Browser profiles, virtualenvs, caches, session and
   permission files. These arrive innocuous and fill up later — a browser profile is
   empty today and holds cookies and saved logins after the next run. Ignore the whole
   class rather than the one instance you happened to see.

The one deliberate exception is the project's own public identity: the `LICENSE`
copyright holder, `.github/FUNDING.yml`, and the sponsorship links in `README.md` and
`frontend/src/config/brand.jsx`. Those name the owner on purpose and are allowlisted.
Nothing else is.

### How this is enforced

`scripts/check_portable.sh` scans every tracked file and runs in CI and
`scripts/lint.sh`. It is a *detector* — it can only tell you something is already
committed.

`scripts/check_portable.sh --staged` scans the lines a commit would add, and is the
gate that actually prevents a leak. **Install the hooks once per clone:**

```bash
scripts/install_hooks.sh
```

It works from a worktree too: there `.git` is a file and every worktree shares the main
clone's single hooks directory, which the installer resolves for you. Linking into
`.git/hooks` by hand from a worktree rewrites the main clone's hook and breaks when that
worktree is removed.

Identifiers specific to your own machine belong in the untracked
`scripts/.portable-local-patterns`, one `pattern<TAB>explanation` per line. The guard
picks them up automatically, and your machine names stay out of the public script.
They apply to every file, the allowlisted ones included: a machine name has no
legitimate place in a test or the README.

Whole files that must never be committed — private notes, local planning documents,
anything that is yours rather than the project's — go in the untracked
`scripts/.portable-local-paths`, one `path-regex<TAB>explanation` per line. The guard
refuses any of them that reaches the index. Pair it with `.git/info/exclude` rather
than `.gitignore`: an ignore rule is itself published, so naming a private file there
tells everyone it exists. `.git/info/exclude` keeps it out of `git add -A` without
announcing it, and the guard covers the case that file cannot — an explicit
`git add <path>` in a clone whose exclude list was never set up.

The riskiest moment is adding a **new** file, not editing a tracked one. Run
`git status` before `git add`, and never `git add -A` without reading what it staged.
If a check fires, fix the content — don't widen the allowlist and don't reach for
`--no-verify`. An allowlist entry is permanent permission for an entire path.

### Code coming in

The portability guard watches what leaves a clone. `scripts/check_inbound.py` reads what
arrives: every pull request is checked by the base branch's copy of it, which annotates
the lines a maintainer should read before merging. It reads only the lines you add, so
existing code never trips it. The check reports, and fails a pull request only when the
policy blocks it (see [Agent behaviour is maintainer-written](#agent-behaviour-is-maintainer-written)).

What it points out, so nothing in a review is a surprise:

- **Guards and policy:** changes to either guard, to CI workflows (triggers, permissions,
  secrets, actions), to auth and MCP policy, and anything that shortens a protected list.
- **Agent instructions:** `AGENTS.md`, skills, rule and lesson bundles, recipes. These steer
  agents, so they are read as instructions, not prose. From a fork they are blocked.
- **Network:** hosted AI or telemetry clients, new outside hosts, turning off TLS checks.
  Guaardvark never contacts an outside host except behind a visible Install.
- **Running code:** `shell=True`, `eval`/`exec`, unpickling, `torch.load` without
  `weights_only=True`, `trust_remote_code=True`, `curl | sh`.
- **Hidden text:** bidirectional and invisible characters, look-alike identifiers, long
  encoded strings.
- **Dependencies:** new packages, installs from a repository or URL, other package
  indexes, npm install scripts.
- **File shape:** symlinks out of the repository, pickle files, binaries among source.

Run it on your branch before opening a pull request:

```bash
python3 scripts/check_inbound.py scan --range main...HEAD
```

A test that needs trigger text keeps it in a data file (see
`backend/tests/inbound_guard/cases.json`), so the test itself reads clean.

In your own clone the same guard can watch merges and fetches. `scripts/install_hooks.sh`
installs it switched off; `git config inboundguard.mode observe` records what each fetch,
merge or cherry-pick brought to `main`, and `enforce` also refuses a merge it would hold
until you approve that exact change. `python3 scripts/check_inbound.py log` shows what
it has seen.

---

## Pull Request Guidelines

- **One concern per PR.** Bug fix? One PR. New feature? One PR. Don't mix.
- **Describe what and why** in the PR description. Link related issues.
- **Include screenshots** for UI changes.
- **Keep PRs reviewable** — under 500 lines of diff when possible.
- **Don't break the build.** Run `npm run lint` and `python3 run_tests.py` before pushing.
- **Install the hooks** once per clone with `scripts/install_hooks.sh` — see
  [Portability and Secrets](#portability-and-secrets). The pre-commit hook catches
  machine paths and secrets before they reach a commit, which is the only point at
  which they are still cheap to remove.
- **Sign the CLA.** On your first PR a bot will ask you to read the
  [Contributor License Agreement](CLA.md) and post one line as a comment. It takes a minute
  and you only do it once. You keep the copyright to your work — the CLA grants the project a
  licence to use it and to relicense the project later, which is impossible to do retroactively
  once contributions accumulate. Say `recheck` in a comment if the bot needs another look.

---

## Releasing (maintainers)

`pip install guaardvark` serves a real package, not a placeholder page. It is built
from `cli/setup.py`, which reads the repo-root `VERSION` file as the single source
of truth for the version number.

**Never announce a release without publishing it.** Bumping `VERSION`, tagging or
posting a version while PyPI still serves the previous one means every `pip install`
gets stale software while the README badge advertises the new number. The two drift
apart silently, because nothing about a tag updates PyPI on its own.

1. Bump `VERSION` — the package version follows automatically.
2. Land everything on `main` and confirm CI is green.
3. Tag it, signed: `git tag -s -m "v$(cat VERSION)" v$(cat VERSION) && git push origin v$(cat VERSION)`.
   The `release` workflow builds the package and publishes it to PyPI. The tag is
   signed with the maintainer's SSH signing key, so GitHub shows it as Verified;
   the GitHub release is then created from that tag (`gh release create --verify-tag`)
   rather than letting GitHub make an unsigned one.
4. Confirm what is actually live:
   `curl -s https://pypi.org/pypi/guaardvark/json | jq -r .info.version`
5. Only then announce.

To publish by hand instead — or if the workflow is unavailable:

```bash
python -m build cli/
twine upload cli/dist/*
```

A bad upload cannot be deleted and reused; the fix is `yank`, which leaves the
version visible but stops `pip` installing it by default. Version numbers are never
recycled, so a botched publish costs you the number.

---

## Code of Conduct

Be respectful, constructive, and kind. We're building something together. Harassment, trolling, and bad faith participation won't be tolerated.

---

## Questions?

Open a [Question issue](https://github.com/guaardvark/guaardvark/issues/new?template=question.yml) or start a [Discussion](https://github.com/guaardvark/guaardvark/discussions) if enabled.

Thanks for helping make Guaardvark better!
