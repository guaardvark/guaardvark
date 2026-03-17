# Contributing to Guaardvark

Thanks for your interest in contributing to Guaardvark! Whether it's a bug report, feature idea, documentation fix, or code contribution — every bit helps.

## Quick Links

- [Open Issues](https://github.com/guaardvark/guaardvark/issues)
- [Good First Issues](https://github.com/guaardvark/guaardvark/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Project Board](https://github.com/guaardvark/guaardvark/projects)

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

Documentation lives in the README, CLAUDE.md, and inline code comments. If something confused you, it'll confuse others — fixes welcome.

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
  api/             68 REST blueprint modules
  services/        48 business logic modules
  tools/           Agent-callable tools
  tests/           Test suite
frontend/          React/Vite app
  src/pages/       31 page components
  src/components/  129 UI components
  src/stores/      Zustand state management
cli/               Guaardvark CLI
plugins/           GPU service plugins (Ollama, ComfyUI)
scripts/           Utility and system scripts
```

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

---

## Code of Conduct

Be respectful, constructive, and kind. We're building something together. Harassment, trolling, and bad faith participation won't be tolerated.

---

## Questions?

Open a [Question issue](https://github.com/guaardvark/guaardvark/issues/new?template=question.yml) or start a [Discussion](https://github.com/guaardvark/guaardvark/discussions) if enabled.

Thanks for helping make Guaardvark better!
