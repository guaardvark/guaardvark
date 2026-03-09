#!/usr/bin/env python3

import os
import sys
import json
from pathlib import Path

GUAARDVARK_ROOT = Path(os.path.dirname(os.path.abspath(__file__))).parent

def count_lines_of_code():
    total_python = 0
    total_js = 0

    for py_file in Path(GUAARDVARK_ROOT).glob('backend/**/*.py'):
        if '__pycache__' not in str(py_file):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    total_python += len(f.readlines())
            except:
                pass

    for js_file in Path(GUAARDVARK_ROOT).glob('frontend/**/*.{js,jsx}'):
        if 'node_modules' not in str(js_file):
            try:
                with open(js_file, 'r', encoding='utf-8') as f:
                    total_js += len(f.readlines())
            except:
                pass

    return total_python, total_js

def get_api_endpoints():
    endpoints = []
    api_dir = Path(GUAARDVARK_ROOT) / 'backend' / 'api'

    if api_dir.exists():
        import re
        for api_file in api_dir.glob('*.py'):
            try:
                with open(api_file, 'r') as f:
                    content = f.read()

                pattern = r'@\w+\.route\(["\']([^"\']+)["\']\s*,?\s*(?:methods=\[([^\]]+)\])?'
                for match in re.finditer(pattern, content):
                    route = match.group(1)
                    methods = match.group(2) if match.group(2) else 'GET'
                    endpoints.append((methods, route, api_file.stem))
            except:
                pass

    return endpoints

def load_catalog():
    catalog_path = Path(GUAARDVARK_ROOT) / 'data' / 'code_catalog.json'

    if catalog_path.exists():
        try:
            with open(catalog_path, 'r') as f:
                return json.load(f)
        except:
            return None
    return None

def generate_full_context():
    python_loc, js_loc = count_lines_of_code()
    endpoints = get_api_endpoints()
    catalog = load_catalog()

    context = f"""
# 🤖 SESSION CONTEXT: LLM010.5.3

## System Metrics
- **Python Code**: {python_loc:,} lines
- **Frontend Code**: {js_loc:,} lines
- **API Endpoints**: {len(endpoints)} endpoints
- **Tech Stack**: Flask + React + Celery + LlamaIndex (RAG)
- **Design**: Offline-first, self-contained, no Docker

---

## 🎯 CRITICAL: Before Writing Code

**This system has extensive existing code that needs wiring up, not rewriting!**

### Code That Exists But Isn't Integrated:

1. **Task Scheduler** (`backend/services/task_scheduler.py`)
   - Cron-like scheduling, recurring jobs
   - Initialized but no API/UI
   - **Need**: API endpoints + SchedulerPage.jsx

2. **Resource Manager** (`backend/services/resource_manager.py`)
   - CPU/GPU monitoring, auto-throttling
   - Initialized but not actively monitoring
   - **Need**: Wire into generation workflows

3. **Checkpoint System** (partial in `bulk_csv_generator.py`)
   - Save/resume generation progress
   - Exists but not exposed
   - **Need**: API + "Resume" button in UI

4. **Entity Context Enhancer** (`backend/utils/entity_context_enhancer.py`)
   - Advanced RAG with relationships
   - Exists but disabled by default
   - **Need**: UI toggle + parameter passing

5. **Training Datasets** (`backend/api/training_datasets_api.py`)
   - API exists, no UI
   - **Need**: Frontend integration

---

## 🔍 How to Find Existing Code

```bash
# Search the catalog
python scripts/index_codebase.py --query "task scheduler"
python scripts/index_codebase.py --query "checkpoint"
python scripts/index_codebase.py --show-unused

# Or grep
grep -r "TaskScheduler" backend/
grep -r "ResourceManager" backend/
```

---

## Key Architecture Patterns

### API Endpoints (Auto-Registered!)
```python
# Create: backend/api/my_feature_api.py
from flask import Blueprint, jsonify

my_bp = Blueprint("my_feature", __name__, url_prefix="/api/my-feature")

@my_bp.route("/action", methods=["POST"])
def action():
    return jsonify({{"success": True}})
# That's it! Auto-registered by blueprint_discovery.py
```

### Progress Tracking
```python
from backend.utils.unified_progress_system import get_unified_progress, ProcessType

progress = get_unified_progress()
job_id = progress.create_process(ProcessType.CSV_PROCESSING, "Desc")
progress.update_progress(job_id, 50, "Halfway")
progress.complete_process(job_id, "Done")
```

### Background Jobs
```python
# Always use Celery, not threading
from backend.celery_app import celery
result = celery.send_task('backend.celery_tasks_isolated.task_name', [args])
```

---

## Quick Reference: Key Files

| What | Where |
|------|-------|
| Main app | `backend/app.py` (1500 lines) |
| Models | `backend/models.py` |
| CSV generation | `backend/utils/bulk_csv_generator.py` |
| XML generation | `backend/utils/bulk_xml_generator.py` |
| Progress system | `backend/utils/unified_progress_system.py` |
| Task scheduler ⚠️ | `backend/services/task_scheduler.py` |
| Resource monitor ⚠️ | `backend/services/resource_manager.py` |
| Main UI | `frontend/src/pages/FileGenerationPage.jsx` |

---

## API Endpoints ({len(endpoints)} total)

"""

    key_routes = [
        '/api/bulk-generate/csv',
        '/api/bulk-generate/xml',
        '/api/health',
        '/api/tasks',
    ]

    context += "### Critical Endpoints:\n"
    for methods, route, file in sorted(endpoints):
        if any(kr in route for kr in key_routes):
            context += f"- `{methods:10} {route:40}` ({file})\n"

    context += f"\n*See all {len(endpoints)} endpoints: `curl http://localhost:5000/api/routes`*\n"

    if catalog:
        context += f"""
---

## Code Catalog Stats

- **Total Artifacts**: {catalog['metadata']['total_artifacts']}
- **By Type**:
"""
        for artifact_type, items in sorted(catalog['by_type'].items()):
            context += f"  - {artifact_type}: {len(items)}\n"

        if 'unused' in catalog['by_status']:
            unused_count = len(catalog['by_status']['unused'])
            context += f"\n⚠️ **{unused_count} Unused Artifacts** - Run: `python scripts/index_codebase.py --show-unused`\n"

    context += """
---

## Integration Checklist

When wiring up existing code:

1. Search: `python scripts/index_codebase.py --query "<feature>"`
2. Check: Is code already there? (Usually yes!)
3. Create API: `backend/api/<feature>_api.py` (auto-registers)
4. Create UI: `frontend/src/pages/<Feature>Page.jsx`
5. Wire progress: Use `unified_progress_system`
6. Test: Start backend, verify endpoint

---

## Agent Workflow

```
User: "Add task scheduling"

❌ DON'T: Start writing a new scheduler
✅ DO: Search → Find TaskScheduler → Wire it up (API + UI)
```

---

## Next Session: Quick Commands

```bash
# See what's unused
python scripts/index_codebase.py --show-unused

# Search for feature
python scripts/index_codebase.py --query "scheduler"

# Check health
curl http://localhost:5000/api/health

# View all endpoints
curl http://localhost:5000/api/routes
```

---

**📖 Full Details**: Read `SYSTEM_MAP.md` and `AGENT_PRIMER.md`

**🎯 Remember**: Check if code exists BEFORE writing new code!

---

*Generated: {Path(GUAARDVARK_ROOT).name} | Total LOC: {python_loc + js_loc:,}*
"""

    return context

def generate_brief_context():
    return """
# 🤖 QUICK CONTEXT: LLM010.5.3

**System**: AI content generation (1000+ pages/day)
**Stack**: Flask + React + Celery + LlamaIndex
**Design**: Offline-first, no Docker

## ⚠️ CRITICAL: Code Exists But Needs Wiring

Before writing code, check if it exists:
```bash
python scripts/index_codebase.py --query "<your-feature>"
```

**Unused Code That Needs Integration:**
- Task Scheduler: `backend/services/task_scheduler.py`
- Resource Manager: `backend/services/resource_manager.py`
- Checkpoint System: (partial, needs API)
- Entity Context: `backend/utils/entity_context_enhancer.py`

## Quick Integration Pattern

1. Search: `python scripts/index_codebase.py --query "feature"`
2. If exists: Wire it up (API + UI)
3. If doesn't exist: Write it (follow patterns in SYSTEM_MAP.md)

**📖 Full context**: `SYSTEM_MAP.md`, `AGENT_PRIMER.md`
"""

def generate_feature_context(feature: str):
    catalog = load_catalog()

    if not catalog:
        return f"❌ Catalog not found. Run: python scripts/index_codebase.py --update-all\n"

    results = []
    query_lower = feature.lower()

    for artifact in catalog['artifacts']:
        searchable = ' '.join([
            artifact['name'],
            artifact['description'],
            ' '.join(artifact.get('tags', []))
        ]).lower()

        if query_lower in searchable:
            results.append(artifact)

    if not results:
        return f"❌ No results found for '{feature}'\n\nTry: python scripts/index_codebase.py --query \"{feature}\"\n"

    context = f"""
# 🎯 FEATURE CONTEXT: {feature.title()}

Found {len(results)} relevant artifact(s):

"""

    for i, artifact in enumerate(results[:10], 1):
        context += f"""
## {i}. {artifact['name']} ({artifact['type']})

- **File**: `{artifact['file_path']}:{artifact['line_number']}`
- **Status**: {artifact['status']}
- **Description**: {artifact['description'][:200]}...
"""
        if artifact.get('tags'):
            context += f"- **Tags**: {', '.join(artifact['tags'])}\n"

        if artifact.get('usage_example'):
            context += f"- **Usage**: `{artifact['usage_example']}`\n"

        context += "\n"

    if len(results) > 10:
        context += f"\n*...and {len(results) - 10} more. Run full search for complete results.*\n"

    context += f"""
---

## Next Steps

1. Review the files above
2. Check implementation status
3. If "unused" or "partial", wire it up!
4. If "implemented", integrate or extend

## Integration Template

```python
# 1. Create API: backend/api/{feature}_api.py
from flask import Blueprint, jsonify

{feature}_bp = Blueprint("{feature}_api", __name__, url_prefix="/api/{feature}")

@{feature}_bp.route("/action", methods=["POST"])
def action():
    # Use existing code from: {results[0]['file_path'] if results else 'N/A'}
    return jsonify({{"success": True}})

# 2. Create UI: frontend/src/pages/{feature.title()}Page.jsx
# 3. Test: curl http://localhost:5000/api/{feature}/action
```

**📖 Full details**: `SYSTEM_MAP.md`
"""

    return context

def main():
    import argparse

    parser = argparse.ArgumentParser(description='Generate AI Agent Session Context')
    parser.add_argument('--brief', action='store_true', help='Generate brief context')
    parser.add_argument('--feature', type=str, help='Generate context for specific feature')
    parser.add_argument('--output', type=str, help='Save to file instead of stdout')

    args = parser.parse_args()

    if args.feature:
        context = generate_feature_context(args.feature)
    elif args.brief:
        context = generate_brief_context()
    else:
        context = generate_full_context()

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(context)
        print(f"✅ Context saved to: {output_path}")
    else:
        print(context)

if __name__ == '__main__':
    main()
