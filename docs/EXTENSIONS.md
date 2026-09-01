# Extensions and profiles

Guaardvark is one code base with two ways to change its shape without forking it:

- a **profile** decides what is on by default, what the sidebar lists and where the app
  lands — `workstation` (everything), `creator` (the media workflow), or one an extension
  ships;
- an **extension** adds a vertical — its own API, models, background tasks, pages, theme,
  profile and optional sidecar — from one folder that core loads through fixed hook
  points. Core never names it, so syncing upstream never conflicts.

## Profiles

Select with `GUAARDVARK_PROFILE=<name>` in `.env` or `./start.sh --profile <name>`; the
first start asks, and Settings → Product Profile switches later (applies on restart, because
feature flags are read at boot). Unset means `workstation`, which sets nothing at all.

Two rules hold everywhere. **An explicit value always wins**: a profile is applied with
`setdefault`, so a key already in `.env`, a `start.sh` flag, a plugin toggle or a DB setting
overrides it. **Hidden means unlisted, never removed**: every route, API and test stays live;
the profile decides what the sidebar lists and where `/` lands.

A profile is a JSON file — `backend/profiles/<name>.json` or `extensions/<id>/profile.json`:

| key | effect |
|---|---|
| `env` | environment defaults (`GUAARDVARK_AGENT_BRAIN`, `GUAARDVARK_MCP_ENABLED`, …) |
| `plugins` | overrides `plugin.json` `default_enabled`; a user's toggle still wins |
| `startup` | `start.sh` steps to skip (`voice_check`, `bootstrap_models`) |
| `nav.hidden` | sidebar items unlisted |
| `landing_route` | what `/` opens |
| `chat_surfaces` | pages that are chat surfaces themselves (no floating chat) |
| `brand` | `app_name`, `tagline`, `theme` fallbacks below the DB branding setting |
| `default_models` | `chat` / `embed` model tags |

`python backend/profiles/__main__.py show creator` prints a resolved profile;
`export --shell` prints what `start.sh` applies. Full schema: `backend/profiles/README.md`.

## Extensions

Copy `extensions/_template/` to `extensions/<id>/` (lowercase, digits, underscore — the folder
name is the id; it may be a symlink to a checkout elsewhere). Every file is optional except
`extension.json`. Client folders are never committed to this repository:
`extensions/.gitignore` keeps them out without naming them.

```
extensions/<id>/
  extension.json      name, version, beat entries, url_prefixes, system_deps
  api/*.py            Flask blueprints — auto-discovered as extensions.<id>.api
  models.py           SQLAlchemy models on the core db, imported before create_all()
  tasks/*.py          Celery tasks; schedules come from extension.json "beat"
  migrations.py       ADD_COLUMNS = [(table, column, ddl)] + optional migrate(db)
  seed.py             seed(app), idempotent, run once per version
  bundles/            rule / lesson bundles for `flask load-rules` / `load-lessons`
  profile.json        the distribution profile
  plugin/plugin.json  an optional sidecar service, discovered like plugins/
  frontend/index.jsx  what the vertical adds to the UI
  requirements.txt    pip deps (not yet installed automatically)
  tests/              collected by pytest
```

### Backend

Models may reference core tables by name (`db.ForeignKey("documents.id")`); core models never
reference an extension's, so an extension can be removed cleanly. `ADD_COLUMNS` entries run
only when the column is missing, so plain `ALTER TABLE t ADD COLUMN c TYPE` is enough on every
database. Task types of the vertical's own are registered with
`backend.services.task_handler_registry.register_task_handler(type, fn)` at import time
instead of editing the unified task executor. Chat-time awareness uses the existing
`context_providers` and `knowledge_sources` registries. Video models an extension brings
are declared in `media_models.py`: its `register()` calls
`video_model_registry.register_video_model(id, entry)` (the same entry shape core uses, with
the capability keys the Video Generator, `generate_video` and Film Crew read) and
`register_family_spec(family, spec)` when the extension also ships a workflow builder for a
new family. Entries are verified on registration and a broken one is logged by id.

Failures are loud and contained: a broken extension is reported by id in the log and on
`GET /api/settings/profile`, every other extension still loads, and a declared `url_prefixes`
entry with no mounted route is an error at startup — a blueprint import failure must never
become a clean-looking 404.

### Frontend

`frontend/index.jsx` default-exports what the vertical adds; every key is optional:

```js
export default {
  routes:       [{ path: "/acme", element: <AcmeHome /> }],      // ahead of the catch-all
  navGroups:    [{ label: "Acme", items: [{ text, icon, path }] }], // ahead of the brand's groups
  themes:       { acme: { label, description, previewGradient, theme } },
  pageContext:  { routeMap: { "/acme": { page: "Acme", entityType: null } }, paramRoutes: [] },
  chatSurfaces: ["/acme/brain*"],
  storeSlice:   { state: (set) => ({ acmeSite: null, setAcmeSite: (v) => set({ acmeSite: v }) }), partialize: ["acmeSite"] },
  layout:       { header: AcmeHeaderBar },     // rendered above every page
  logo:         AcmeLogo,
  landingRoute: "/acme",
};
```

Import core through the `@` alias (`@/api/apiClient`, `@/components/layout/PageLayout`,
`@/theme/createTheme`) and lazy-import pages. The extension uses core's dependencies;
a package core does not ship has to be added to `frontend/package.json`.

### Running

Presence is intent: every folder with `extension.json` loads. `GUAARDVARK_EXTENSIONS=a,b`
restricts to a list. `pytest extensions/<id>/tests` runs the extension's tests with the same
bootstrap as core's; `npx vitest run ../extensions/<id>/frontend` the frontend's.
