# Guaardvark Rebrand Design

Date: 2026-02-25

## Summary

Full rebrand of the codebase from LlamaX1/LLAMAX to Guaardvark. All name references, environment variables, comments, and file names are updated. The result is a clean, professional codebase ready for public GitHub release.

## Name & Version

- Project name: Guaardvark
- Version: 2.4.1
- Repo: guaardvark
- Site: guaardvark.com

## Case Mappings

| From | To |
|------|----|
| `LLAMAX_*` (env vars/constants) | `GUAARDVARK_*` |
| `LlamaX1` / `LlamaX` / `llamaX1` | `Guaardvark` |
| `llamax` (lowercase) | `guaardvark` |
| `LLAMAX` (uppercase) | `GUAARDVARK` |
| `llamax-frontend` (package name) | `guaardvark` |
| `LlamaX1 v5.2` / version references | `Guaardvark 2.4.1` |
| Any URL placeholder | `guaardvark.com` |

## Comments Policy

Remove all comments from all source files. Zero inline comments, zero docstrings, zero block comments, zero dividers.

## File Renames

| From | To |
|------|-----|
| `LLAMAX7.code-workspace` | `guaardvark.code-workspace` |
| `scripts/llamax1.desktop` | `scripts/guaardvark.desktop` |
| `llamaX1_backup.json` | `guaardvark_backup.json` |

## Execution Batches (Parallel)

| Batch | Scope |
|-------|-------|
| 1 — Config | `backend/config.py`, `.env`, `.env.automation.example` |
| 2 — Scripts | `start.sh`, `stop.sh`, `start_celery.sh`, other shell scripts |
| 3 — Python backend | All `.py` files under `backend/` |
| 4 — Frontend | All `.js`/`.jsx` under `frontend/src/`, `index.html`, `package.json`, `vite.config.js` |
| 5 — Docs | `CLAUDE.md`, `GEMINI.md`, `INSTALL.md`, other docs |
| 6 — File renames | Workspace file, desktop launcher, backup JSON |

## Exclusions

- `venv/`, `node_modules/`, `logs/`, `backups/`, `.git/`
- `data/database/*.db`
- `dist/`, `__pycache__/`
