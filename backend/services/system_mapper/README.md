# system_mapper

A static map of a codebase: its modules and imports, routes and the frontend
calls that reach them, Celery tasks, LLM tools, symbols and call sites, named
subsystems, and a flat list of findings. Built for Guaardvark's own checkout;
other trees get the parts that apply. Nothing in the mapped tree is imported
or run.

## What it produces

`codebase_map(root)` returns a `SystemMap`:

| Field | Source | What it holds |
|---|---|---|
| `dependency_graph` | `dependency_graph.py` | module → modules it imports at load time (Python and JavaScript) |
| `lazy_imports` | `dependency_graph.py` | module → modules it imports inside functions, by `import()`, `import.meta.glob`, `importlib.import_module` or by name |
| `node_meta` | `dependency_graph.py`, `subsystems.py` | every module: path, language, lifecycle, importer counts (load time, inside functions, tests), lines, subsystem, the scripts that start it |
| `reachability` | `reachability.py` | Flask routes, frontend HTTP calls with their URLs resolved, and `page_endpoints`: the endpoints each page reaches |
| `tool_graph` | `tool_graph.py` | every registered tool: class, file and line, registering function and its conditions, how the chat reaches it, MCP exposure |
| `celery_tasks` | `dispatch_graph.py` | name, module, file, function and line of every task |
| `subsystems` | `subsystems.py`, `subsystems.json` | the named areas and how many files and lines each holds |
| `findings` | all of the above | flat, ranked list of `Finding` |
| `stats` | all of the above | counts, and how long each analyzer took (`timings_s`) |

Module ids are dotted names for Python (`backend.services.chat_turn`) and
paths for JavaScript (`frontend/src/pages/ChatPage.jsx`).

With `codebase_map(root, with_index=True)` the query index is attached as
`smap.index` (not part of `to_dict()`): definitions with line ranges, resolved
imports with their lines, name bindings, call sites as written, the words of
each file, and the English interface strings with the code that uses each one.

## How files are read

`corpus.Corpus` lists the tree once and reads each file once. The listing is
`core.source_files`: in a git checkout, what `git ls-files --cached --others
--exclude-standard` lists (tracked files plus untracked ones git does not
ignore), minus folders named in `DEFAULT_EXCLUDE_DIRS` (`venv`,
`node_modules`, `data`, `logs`, `migrations`, ...); outside a checkout the
tree is walked. `facts.file_facts` parses a Python file once with `ast` and
scans a JavaScript file once with regular expressions, and keeps the result
in memory keyed by the file's modification time and size: a rebuild after a
one-file edit rereads one file.

## Import resolution

Python imports resolve with the same functions the Code Repository analysis
uses (`backend/utils/import_resolution.py`): relative imports from the file's
package, then absolute names against the repository root and, for a file
inside one, its own import root: each `plugins/<name>/` (whose code imports
`service.x` and `core.x`) and any folder with its own packaging file (`cli/`
ships `llx`). `from pkg import sub` points at `pkg/sub.py`, not at the
package. `importlib.import_module("a.b")` and `import_module(f"a.b.{x}")`
(every module under `a.b`) are imports made when the call runs, and so is a
string constant naming one of the repository's modules (`"commands.chat"` in
the Discord bot's extension list). JavaScript resolves relative specifiers,
index files, the `@/` alias for `frontend/src`, `import()` and
`import.meta.glob`.

Edge kinds: `top` runs when the module is imported; `lazy` runs only when a
function does; `type` sits under `if TYPE_CHECKING:` and never runs. Cycles
and over-coupling use `top` edges only. Lifecycle counts `top` and `lazy`
importers, but not tests: a module only tests import is `dormant`.

A module a script or manifest starts (`uvicorn service.app:app` in a plugin's
`start.sh`, `python3 -m plugins.x.service.app`, a `.py` path in a script,
`plugin.json` or a YAML file) is `auto-loaded`, never dormant.

## Finding kinds

| `kind` | `severity` | What it means |
|---|---|---|
| `url-path-collision` | `high` | Two files register the same path with an overlapping method |
| `url-prefix-collision` | `medium` | Two files register routes under the same `/api/<segment>` |
| `ghost-endpoint` | `low` | A backend route no frontend call reaches (25 listed, then one rollup); it may be called by scripts, MCP or the CLI |
| `ghost-api-caller` | `medium` | A frontend call to a path no route serves. A URL with no fixed segment after `/api` is counted in `stats.reachability.unresolved_callers` instead |
| `import-cycle` | `medium` (≤5 modules) / `low` | Modules that import each other at load time |
| `over-coupled` | `medium` | A module on five or more import cycles of at most four modules |
| `unwired-tool` | `high` | A tool registered for chat that no list, pin, route, slash command or the semantic selector can offer |
| `unregistered-tool` | `high` | A tool list names a tool nothing registers |
| `untested-module` | `info` | A `backend/` module no test imports and no `test_<name>.py` covers |
| `dormant-module` | `low` | Nothing outside tests imports it, and no Celery task, blueprint, plugin, tool table or script reaches it |
| `backup-artifact` | `low` | `_BACK` / `.BACK` / `.bak` / `__BACKUP` / `/backs/` / `/_archive/` paths in the tree |
| `dead-symbol` | `info`/`low` | A function referenced nowhere, not even in a string (100 listed, then one rollup) |
| `no-subsystem` | `info` | Source files `subsystems.json` does not claim (one finding, with a sample) |
| `runtime-zombie` / `contextual-discovery` / `hot-path-spike` | | Runtime liveness, from the 12-hour audit (`liveness.py`) |

A finding's `id` is a hash of its kind, its paths and `evidence["key"]`, the
analyzer's name for the defect (a route, a tool, a module); counts in the
summary do not change it, so dismissals and dispatches survive rebuilds.

## Freshness and storage (`store.py`)

A stored map is current while the tree it describes has not changed: each
snapshot carries the git HEAD, a hash of the file list, the newest
modification time and the mapper's version it was built from. Reading the key
takes a listing and a stat per file (about 0.15 s on this repository).

Reads never build. A stale or missing map starts one background build per
root (a second caller joins it) and the reader gets the last map, marked
`stale` and `refreshing`. Snapshot, index and liveness files are written aside
and renamed. The 12-hour audit (`runtime_audit.reconcile_and_emit`) stores its
liveness findings on their own and every rebuild merges them back in.

Files under `<storage>/cache/system_map/`, named after the root's last three
path segments: `<slug>.json` (snapshot), `<slug>.index.json.gz` (query index),
`<slug>.liveness.json`, `<slug>.dismissed.json`, `<slug>.dispatched.json`.

Measured on this repository (2,612 Python and JavaScript files, one
workstation, 2026-10-26): about 7 s for a map in a fresh process, 6 s of it
reading files; about 1.1 s for a rebuild in the same process. The snapshot is
about 1.9 MB, the compressed index about 4 MB.

## Asking questions (`query.py`)

`system_map_query` (`backend/tools/workstation_tools.py`), `GET
/api/system-map/query?mode=&q=` and `/sysmap <question>` answer from the stored
snapshot and index, as short text with `file:line` references:

| mode | answers |
|---|---|
| `where <feature text>` | files and lines that implement it, ranked from definitions, docstrings, interface text and the code that uses it, routes, tool names and subsystem descriptions; semantic hits too when the checkout has a zvec-grep index (`<root>/.zvec-grep`). Each hit says why it matched |
| `module <path or dotted name>` | imports, importers at load time and inside functions, test importers, routes, tools, Celery tasks, definitions, findings |
| `symbol <name>` | definitions with line ranges, callers resolved through imports, calls matching by name only, callees |
| `route <path>` | handler, frontend callers, pages that reach it |
| `tool <name>` | class, registration, how the chat reaches it, MCP exposure |
| `subsystem <name>` | what it holds, its routes, tools and tasks, what it depends on and what uses it |
| `impact <path or symbol>` | dependents up to four imports away, and the tests to run |
| `findings <scope>` | findings for a folder, file, subsystem or kind |

Free text is parsed too: `what calls run_chat_turn`, `what depends on
backend/services/agent_tools.py`, `where is the image seed box`.

Call resolution is static and best effort: calls through variables, `getattr`
or inherited methods may be missing, and a call on an object of unknown type
matches by name only. Answers label which is which.

## HTTP API (`backend/api/system_map_api.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/system-map/snapshot` | The stored map with `_cache: {stale, refreshing, age_seconds}`; `202 {"status": "computing"}` before the first map. `?refresh=1` starts a rebuild |
| GET | `/api/system-map/findings` | Ranked findings only (`?severity=`, `?kind=`, `?include_dismissed=1`) |
| GET | `/api/system-map/query` | `?mode=&q=`, or `?q=` alone |
| POST | `/api/system-map/refresh` | Start a rebuild in the background |
| POST | `/api/system-map/findings/<id>/dispatch` | Hand a dispatchable finding to self-improvement; refused while an earlier dispatch's PendingFix is open |
| POST | `/api/system-map/findings/<id>/dismiss` | Dismiss (`{"undo": true}` reverses it) |
| GET / POST | `/api/system-map/code-index` | Whether the checkout has a zvec-grep index; build or refresh it in the background with the plugin's `scripts/index.sh` (refused when the plugin or its model is not installed; downloads nothing) |
| GET | `/api/system-map/health` | Smoke check |

## Tools

- `map_codebase`: the most severe findings first, as text, then the numbers.
- `system_map_query`: the questions above. Read-only, offered over MCP, pinned
  for questions about the code.
- `dispatch_map_finding`: hands a finding to self-improvement; needs a
  person's approval and is not offered over MCP. A finding whose earlier fix
  is still open is not sent again (`actions.open_dispatch`).

No finding kind has a mechanical fix today (`actions.mechanical_proposal`):
every dispatch asks the agent, and its proposal waits for review.

## CLI

```bash
python -m backend.services.system_mapper /path/to/codebase --out /path/to/out
#   system_map.json   canonical
#   system_map.md     report grouped by severity
#   system_map.mmd    Mermaid graph of cycle modules
```

`--exclude <name>` (repeatable) skips more folder names.

## Limitations

- JavaScript is read with regular expressions, not a parser: definitions come
  from `backend/utils/code_symbol_extractor.py` (functions, arrow functions
  assigned to a name, classes), with end lines found by matching brackets;
  class methods are not definitions.
- A frontend URL built at run time from values other than module or block
  constants (`` `${BASE_URL}/${path}` ``) cannot be matched to a route.
- A module loaded by an outside program from a folder (ComfyUI's custom nodes
  are found through a YAML file) is only seen when a script, manifest or YAML
  file in the tree names it.
- Tool registration is read from `tool_registry_init.py` in the shapes it uses
  today: direct `register_tool(X())`, loops over imported lists or classes,
  and `getattr(module, "ClassName")`.
