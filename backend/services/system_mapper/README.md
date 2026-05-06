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
smap = codebase_map("/home/llamax1/LLAMAX8")

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

## Integration paths (deferred — not built yet)

### 1. As a Flask blueprint endpoint

```python
# backend/api/system_mapper_api.py  (sketch)
from flask import Blueprint, request, jsonify
from backend.services.system_mapper import codebase_map

bp = Blueprint("system_mapper", __name__, url_prefix="/api/system-map")

@bp.route("/analyze", methods=["POST"])
def analyze():
    path = request.json["path"]
    smap = codebase_map(path)
    return jsonify(smap.to_dict())
```

Register in `app.py` (or rely on `blueprint_discovery.py`). DocumentsPage hooks
on a folder selection → this endpoint → renders the markdown report.

### 2. As an LLM tool

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

### 3. As a DocumentsPage action

When a user opens a code folder in DocumentsPage, surface an **Analyze
codebase** button. Wire to `POST /api/system-map/analyze {path}`. Render the
returned markdown in a side panel; click on a finding navigates to the file.

### 4. As fuel for self-improvement

`self_improvement_service` currently runs but does nothing useful (9 runs in a
month, 0 changes per April 14 audit). The map is the missing input:

```python
# Sketch — inside self_improvement_service
smap = codebase_map(GUAARDVARK_ROOT)
high_severity = [f for f in smap.findings if f.severity.value == "high"]
for f in high_severity:
    PendingFix.create(
        category=f.kind.value,
        description=f.summary,
        affected_paths=f.paths,
        evidence=f.evidence,
        confidence=0.9 if f.kind in {"url-path-collision", "unregistered-tool"} else 0.6,
    )
```

Each high finding becomes a candidate `pending_fix` row. The agent picks one
per run, proposes a fix, runs the existing `_verify_fix` flow, and merges if
green. That's the difference between placebo and real.

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
