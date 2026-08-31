# Profiles

A profile is one switch that sets the product shape. `workstation` is today's full
product and carries no overrides; `creator` is the media workflow with the agent,
knowledge-index, outreach and automation subsystems unlisted and off by default; an
extension ships its own profile as `extensions/<id>/profile.json`, selected by the
extension's folder name (a *distribution*).

Select with `GUAARDVARK_PROFILE=<name>` in `.env`, or `./start.sh --profile <name>`
(which writes that key). Unset means `workstation`.

## Rules

- **An explicit value always wins.** Everything a profile sets is applied with
  `setdefault`: a key already in `.env`, a `start.sh` flag, a plugin toggle in the UI, or
  a DB setting overrides the profile.
- **Hidden means unlisted, never removed.** Routes, APIs and tests stay live; the profile
  decides what the sidebar lists and where `/` lands.
- An unknown or unreadable profile falls back to `workstation` and reports why (log,
  `/api/settings/branding` → `profile.fallback_reason`); it never stops the boot.

## Keys

| key | type | effect |
|---|---|---|
| `name`, `label`, `description` | string | identity; `name` must match the file name |
| `env` | `{VAR: value}` | environment defaults (`backend/config.py` applies them before any flag is read; `start.sh` exports them). Booleans become `"true"`/`"false"`. |
| `plugins` | `{plugin_id: bool}` | overrides `plugin.json` `default_enabled`; carried in `GUAARDVARK_PROFILE_PLUGIN_DEFAULTS`, read by `PluginMetadata.from_json_file` and `start.sh plugin_effective_enabled`. A user's toggle still wins. |
| `startup` | `{voice_check: bool, bootstrap_models: bool}` | start.sh steps to skip; a CLI flag still wins |
| `nav.hidden` | `[route]` | sidebar items unlisted (routes stay live). A profile cannot add items — only an extension can. |
| `landing_route` | route | what `/` navigates to |
| `chat_surfaces` | `[route]` | pages where the floating chat is hidden (they are chat surfaces themselves) |
| `brand.app_name` / `tagline` / `theme` | string | fallbacks below the DB branding setting and above `brand.jsx` |
| `default_models.chat` / `embed` | model tag | exported as `GUAARDVARK_DEFAULT_LLM` / `GUAARDVARK_EMBEDDING_MODEL` when unset |

`python backend/profiles/__main__.py show creator` prints the resolved profile;
`... export --shell` prints what `start.sh` evaluates; `... list` lists what is available.
