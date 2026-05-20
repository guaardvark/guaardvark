# system_mapper

X-ray vision for any codebase. Builds three connected maps + a flat list of
findings that downstream consumers (humans, the agent, self-improvement) can
iterate.

## What it produces

A `SystemMap` with:

| Component | Source | Surfaces |
|---|---|---|
| `dependency_graph` | AST imports across all `.py` | Module import edges, cycles, over-coupled hubs |
| `reachability` | Backend `@bp.route` × frontend `fetch`/`axios`/`apiClient` | Ghost endpoints, ghost callers, URL collisions |
| `tool_graph` | `tool_registry_init.py` × `*_TOOLS` constants | Registered-but-unwired tools, phantom listings |
| `findings` | All of the above | Flat, ranked actionable items |

Each `Finding` has `kind`, `severity` (high/medium/low/info), `summary`,
`paths`, and `evidence` — the bridge to self-improvement.

## CLI

```bash
python -m backend.services.system_mapper /path/to/codebase --out /tmp/out
# Outputs:
#   /tmp/out/system_map.json   — canonical (machine-readable)
#   /tmp/out/system_map.md     — human report grouped by severity
#   /tmp/out/system_map.mmd    — Mermaid graph of cycle modules
```

Optional flags:
- `--exclude <name>` (repeatable): additional directory names to skip beyond
  the defaults (`venv`, `node_modules`, `__pycache__`, `ComfyUI`, `voice`,
  `.swarm-worktrees`, `data`, `logs`, …).

## Library

```python
from backend.services.system_mapper import codebase_map
smap = codebase_map("/path/to/repo")  # defaults to config.GUAARDVARK_ROOT via the API

# All findings
for f in smap.findings:
    print(f.severity, f.kind, f.summary)

# Just the high-severity ones
high = [f for f in smap.findings if f.severity.value == "high"]

# Sub-maps
smap.dependency_graph         # dict[module, list[imported_modules]]
smap.reachability             # dict with routes, callers, edges
smap.tool_graph               # dict with registered, wired, unwired
```

## Finding kinds

| `kind` | `severity` (default) | What it means |
|---|---|---|
| `url-path-collision` | `high` | Two non-test, non-archived files register the same exact URL with overlapping methods — the second one's routes silently shadow |
| `url-prefix-collision` | `medium` | Two files register `/api/foo/*` — load-order dependent, fragile under refactor |
| `ghost-endpoint` | `low` | Backend route with no frontend caller — could be dead code, public API, or test surface |
| `ghost-api-caller` | `medium` | Frontend hits `/api/x` with no backend route — likely a bug or pending route |
| `import-cycle` | `medium` (≤5 modules) / `low` (longer) | Module A imports B imports … imports A. Works in Python but brittle |
| `over-coupled` | `medium` | Module participates in 5+ cycles — refactor candidate |
| `unwired-tool` | `high` if isolated / `medium` if referenced elsewhere | Tool registered but absent from any `*_TOOLS` constant — agent cannot reach it |
| `unregistered-tool` | `high` | A `*_TOOLS` constant lists a tool name with no `register_tool` call |
| `untested-module` | `low` | No `tests/test_<name>.py` |
| `dormant-module` | `low` | No static importer (skips tests, scripts, blueprints, `__init__`) |
| `backup-artifact` | `low` | `_BACK`/`.BACK`/`__BACKUP`/`/backs/`/`/_archive/` paths still in the source tree |

## HTTP API (built)

Auto-registered via `blueprint_discovery` from `backend/api/system_map_api.py`
(`url_prefix=/api/system-map`). Default root is `config.GUAARDVARK_ROOT`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/system-map/snapshot` | Full SystemMap JSON (disk-cached, 5-min TTL; `?refresh=1`, `?root=`). Includes `node_meta` (per-module lifecycle + importer count). |
| GET | `/api/system-map/findings` | Ranked findings only — lightweight panel feed. Filters: `?severity=high,medium`, `?kind=`, `?include_dismissed=1`. |
| POST | `/api/system-map/findings/<id>/dispatch` | Hand a finding to the self-improvement agent (`submit_directed_task`) → real proposed fix staged as a `PendingFix`. |
| POST | `/api/system-map/findings/<id>/dismiss` | Acknowledge a finding (persisted under `data/cache/system_map/`); `{"undo": true}` reverses it. |
| GET | `/api/system-map/health` | Smoke check. |

Finding IDs are stable across re-runs (`Finding.fingerprint()` over kind + paths +
summary), so dismissals survive re-analysis.

## The action loop (built)

The map is no longer read-only. `actions.py` ranks findings, persists dismissals,
and dispatches a finding to the existing self-improvement pipeline. **No fabricated
diffs** — `dispatch_finding` calls `submit_directed_task`, and the agent proposes a
real fix that lands as a `PendingFix` row for human approve/apply in Settings. The
frontend Findings panel (beside the constellation) drives this: click to locate a
module, dispatch to fix, dismiss to acknowledge.

## Other integration paths (not built)

### As an LLM tool

```python
# backend/tools/system_mapper_tool.py  (sketch)
from backend.services.agent_tools import BaseTool, register_tool
from backend.services.system_mapper import codebase_map

class SystemMapperTool(BaseTool):
    name = "analyze_codebase"
    description = "Map the architecture of a codebase: imports, routes, tools, findings."
    parameters = { "path": ToolParameter(type="string", required=True, description="Path to the code root") }

    def execute(self, **kwargs):
        smap = codebase_map(kwargs["path"])
        return ToolResult(success=True, output={
            "summary": smap.stats,
            "high_findings": [f.to_dict() for f in smap.findings if f.severity.value == "high"][:20],
            "medium_findings": [f.to_dict() for f in smap.findings if f.severity.value == "medium"][:20],
        })

register_tool(SystemMapperTool())
```

Add to a relevant `*_TOOLS` list in `unified_chat_engine.py` so the LLM can
reach it. Then the agent can answer "what's wrong with this codebase?"
grounded in real data.

### As a DocumentsPage action

When a user opens a code folder in DocumentsPage, surface an **Analyze
codebase** button. Wire to `GET /api/system-map/snapshot?root=<path>`. Render the
findings in a side panel; click on a finding navigates to the file.

### Auto-queue every high finding (partially built)

Today a finding reaches the fix pipeline **on demand** — the Findings panel (or a
`POST .../dispatch`) hands it to `submit_directed_task`. What's *not* built is a
scheduled job that bulk-creates a `PendingFix` candidate from every high finding so
self-improvement chews through the backlog unattended:

```python
# Sketch — a scheduled self-improvement pass
from backend.services.system_mapper import codebase_map, actions
smap = codebase_map(GUAARDVARK_ROOT)
for f in actions.ranked_findings(smap.to_dict(), GUAARDVARK_ROOT):
    if f["severity"] == "high" and f["dispatchable"] and not f["dismissed"]:
        actions.dispatch_finding(f)   # agent proposes a fix → PendingFix → human review
```

The per-finding action loop (rank → dispatch → real `PendingFix` → human approve)
is the real thing. Auto-draining the backlog on a schedule is the remaining step.

## Future expansion: language-agnostic discoverers

Today's discoverers are Python + JavaScript. The shape is pluggable — to add a
language, write a new module that returns the same finding structure:

```python
# backend/services/system_mapper/go_dependency_graph.py  (future)
def analyze(root: Path, extra_excludes: frozenset[str]) -> dict:
    # Walk *.go, build import graph, run cycle detection
    # Return {"graph": ..., "findings": [...], "stats": {...}}
```

Then register it in `core.codebase_map`. Same `Finding` model, same exporters,
same downstream consumers.

## Cost-of-running

On Guaardvark itself (712 files, 1267 import edges):
- ~3 seconds for `codebase_map(...)`
- 619 KB JSON, 9 KB markdown, 8 KB Mermaid

Cheap enough to run on every request via the API. Cache key = `(root_path,
mtime_of_newest_file)` if you want to skip re-analysis when nothing changed.

## Limitations

- **JS imports** captured by regex (`from '...'`, `import '...'`). Misses
  dynamic imports — `lazy(() => import('./Foo'))` or string-built paths.
- **Tool registration** matches `register_tool(...)` and `<obj>.register(...)`
  patterns. A future refactor that uses decorators (`@register("name")`) won't
  be picked up until the discoverer is extended.
- **Frontend route inventory** is not built — the discoverer doesn't yet walk
  React Router config to map URL → page component. Worth adding when needed.
- **No call-graph below module level** — module imports module, not function
  calls function. AST function call graphs are an obvious next discoverer.
