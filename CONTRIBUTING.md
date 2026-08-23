# Contributing to Guaardvark

Thanks for your interest in contributing to Guaardvark! Whether it's a bug report, feature idea, documentation fix, or code contribution — every bit helps.

## Quick Links

- [Open Issues](https://github.com/guaardvark/guaardvark/issues)
- [Good First Issues](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Project Board](https://github.com/guaardvark/guaardvark/projects)
- [README — Get Involved](README.md#get-involved)

### I want to help — 4 steps

1. **Join Discord** (community + live Guaardvark bot: chat, `/imagine`, status) — invite link in the README when the server bot is live.
2. **Run the project:** `git clone … && ./start.sh` (see Development Setup below).
3. **Pick a** [`good first issue`](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) — especially recipes, docs, CLI, hardware tiers (#46–#49).
4. **Open a focused PR** and expect feedback within 24–48 hours when possible.

### Safe vs high-risk areas

| Prefer for first PRs | High risk — open an issue before large changes |
|----------------------|--------------------------------------------------|
| `data/agent/recipes.json` and recipe docs | Agent control loop, servo, vision targeting |
| `docs/`, README, INSTALL, CAPABILITIES accuracy | Self-improvement apply / codebase mutation paths |
| `cli/` offline commands (list/validate/doctor polish) | MCP default-deny policy and tool exposure |
| Frontend copy, empty states, accessibility | Plugin CUDA/fork runner and GPU orchestrator core |
| Unit tests for pure helpers | Auth, credentials, Interconnector credential sync |

If your change touches high-risk paths, describe the approach in an issue first. Smaller, reviewable PRs merge faster.

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
6. **Push** and open a Pull Request against `main`

### Improve Documentation

Documentation lives in the README, `docs/ARCHITECTURE.md`, and inline code comments. If something confused you, it'll confuse others — fixes welcome.

---

## Development Setup

### Prerequisites

- Python 3.12+
- Node.js 20+
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
  api/             ~90+ API modules (auto-discovered via blueprint_discovery)
  services/        Core business logic + guarded_code_service, plugin runner, etc.
  tools/           ~70 tool classes (registered; policy-gated for MCP)
  tests/           Test suite
frontend/          React/Vite app
  src/pages/       ~38 page routes
  src/components/  Many UI components (chat, agent, videoeditor, documents, swarm, etc.)
  src/stores/      Zustand + contexts
cli/               Guaardvark CLI (`llx` / PyPI `guaardvark`); 24 command modules
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

If you modify `backend/models.py`:

```bash
cd backend && source venv/bin/activate
flask db migrate -m "description of change"
flask db upgrade
python3 ../scripts/check_migrations.py
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
- Use `backend.config` for all path resolution — never hardcode paths

### JavaScript/React

- Functional components with hooks
- Material-UI v5 for all UI elements
- Zustand for global state, React Context for layout/status
- Apollo Client for GraphQL, Axios for REST

### General

- Don't over-engineer. Keep changes focused on what was asked.
- Match the style of surrounding code.
- If you're unsure about an approach, open an issue or draft PR to discuss first.

---

## Pull Request Guidelines

- **One concern per PR.** Bug fix? One PR. New feature? One PR. Don't mix.
- **Describe what and why** in the PR description. Link related issues.
- **Include screenshots** for UI changes.
- **Keep PRs reviewable** — under 500 lines of diff when possible.
- **Don't break the build.** Run `npm run lint` and `python3 run_tests.py` before pushing.
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
3. Tag it: `git tag v$(cat VERSION) && git push origin v$(cat VERSION)`.
   The `release` workflow builds the package and publishes it to PyPI.
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
