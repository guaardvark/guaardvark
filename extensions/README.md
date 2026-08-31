# Extensions

A client vertical — a distribution of Guaardvark for one trade or one customer —
lives here as `extensions/<id>/`, one folder, and never edits a core file. Core
loads it through fixed hook points; a downstream sync produces no conflicts.

Copy `_template/` to start. The folder name is the extension id (lowercase,
digits, underscore). Underscore folders are templates and never load. Presence
is intent: every folder with `extension.json` loads; `GUAARDVARK_EXTENSIONS=a,b`
in `.env` restricts to a list.

```
extensions/<id>/
  extension.json      name, version, beat entries, url_prefixes, system_deps
  api/*.py            Flask blueprints, auto-discovered (package extensions.<id>.api)
  models.py           SQLAlchemy models on the core db, imported before create_all()
  tasks/*.py          Celery tasks, imported for registration; beat comes from the manifest
  migrations.py       ADD_COLUMNS = [(table, column, ddl)]; optional migrate(db)
  seed.py             seed(app) — idempotent, run once per version
  bundles/            rule / lesson bundles for `flask load-rules` / `load-lessons`
  profile.json        the distribution profile (backend/profiles/README.md)
  plugin/plugin.json  an optional sidecar, discovered like plugins/
  frontend/index.jsx  routes, nav groups, themes, page context, chat surfaces
  requirements.txt    pip deps; system packages go in extension.json system_deps
  tests/              collected by pytest (testpaths includes extensions/)
```

What core guarantees:

- A broken extension is reported by id (log and `/api/settings/profile`) and every other
  extension still loads. Declared `url_prefixes` with no mounted route are an error at
  startup — a blueprint import failure must not become a clean-looking 404.
- `ADD_COLUMNS` is applied only when the column is missing, so plain
  `ALTER TABLE t ADD COLUMN c TYPE` is enough and works on every database.
- Nothing here is loaded when `GUAARDVARK_EXTENSIONS` names other extensions.

What it does not do (yet): install an extension's npm packages or system packages —
those are declared and reported, not installed; start an extension's sidecar from
`start.sh`'s boot pass (start it from the Plugins page or the API).
