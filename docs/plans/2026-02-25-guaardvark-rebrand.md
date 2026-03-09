# Guaardvark Rebrand Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all LlamaX1/LLAMAX branding with Guaardvark and remove all code comments across the entire codebase.

**Architecture:** Seven parallel batches (config, scripts, Python backend, frontend, docs, CLI, plugins/scripts) process independent file groups simultaneously. File renames happen after all content changes. Final verification confirms zero LLAMAX references remain.

**Tech Stack:** Python 3.12, JavaScript/JSX (React 18), Bash shell scripts, JSON/Markdown config files

---

## Name Mapping Rules (apply in this order, most specific first)

| Pattern (regex) | Replacement | Notes |
|-----------------|-------------|-------|
| `LLAMAX_` | `GUAARDVARK_` | Covers all 30+ env var names |
| `LlamaX1` | `Guaardvark` | Primary project name |
| `llamaX1` | `guaardvark` | Lowercase variant |
| `LlamaX(?!Index)` | `Guaardvark` | Skip LlamaIndex (third-party library) |
| `llamax-frontend` | `guaardvark` | npm package name |
| `\bLLAMAX\b` | `GUAARDVARK` | Standalone uppercase |
| `\bllamax\b` | `guaardvark` | Standalone lowercase |
| `LlamaX1 v5\.2` | `Guaardvark 2.4.1` | Versioned name |
| `v5\.2\b` | `2.4.1` | Version number |
| `Version 5\.2` | `Version 2.4.1` | Version reference |

**Never change:** `LlamaIndex`, `llama_index`, `llama-index` (third-party library), `/home/llamax1/` paths (system username), `Ollama` (tool name), `llama` in pip/npm package imports.

## Comment Removal Rules

- **Python (.py):** Remove lines where stripped content starts with `#`. Keep `#!/...` shebangs. Remove triple-quoted docstrings at module/class/function level. Remove inline `# ...` at end of code lines. Collapse 3+ consecutive blank lines to 2.
- **JavaScript/JSX (.js, .jsx):** Remove `// ...` lines. Remove `/* ... */` and `/** ... */` blocks. Collapse 3+ blank lines to 2.
- **Shell (.sh):** Remove lines starting with `#`. Keep `#!/bin/bash` or `#!/usr/bin/env ...` shebang on line 1 only.
- **Markdown (.md):** No comment syntax — apply name mappings only.
- **JSON:** No comments — apply name mappings to string values only.

## Exclusions

Do not touch: `venv/`, `node_modules/`, `.git/`, `backups/`, `logs/`, `dist/`, `__pycache__/`, `data/models/`, `backend/tools/voice/whisper.cpp/build/`, `frontend/package-lock.json`, `.claude/settings.local.json`

---

## Parallel Batches Overview

Batches 1–7 are fully independent (no shared files) and can run simultaneously.

| Batch | Files |
|-------|-------|
| 1 — Core config | `backend/config.py`, `.env`, `.env.automation.example` |
| 2 — Shell scripts | `start.sh`, `stop.sh`, `start_celery.sh`, `run_tests.py`, `test_colors.sh`, `scripts/install-desktop-launcher.sh`, `scripts/lint.sh`, `scripts/manage-environments.sh`, `plugins/training/export_models.sh`, `plugins/training/finetune_command.sh` |
| 3 — Python backend | All `.py` under `backend/` (see full list below) |
| 4 — Frontend | All `.js`/`.jsx` under `frontend/src/`, `frontend/index.html`, `frontend/package.json`, `frontend/vite.config.js` |
| 5 — Docs | `CLAUDE.md`, `GEMINI.md`, `INSTALL.md`, `backend/seed_rules.json`, `plugins/gpu_embedding/plugin.json`, `plugins/gpu_embedding/README.md`, `plugins/training/PIPELINE.md`, `docs/plans/2026-02-24-llx-cli-design.md`, `docs/plans/2026-02-24-llx-cli-implementation.md` |
| 6 — CLI | `cli/llx/main.py`, `cli/llx/client.py`, `cli/llx/commands/system.py` |
| 7 — Scripts/plugins (Python) | `scripts/bulk_import_docs.py`, `scripts/system-manager/generate_session_context.py`, `scripts/system-manager/index_codebase.py`, `scripts/system-manager/lib/discovery.py`, `scripts/system-manager/lib/registry.py`, `plugins/training/scripts/finetune_model.py`, `plugins/training/scripts/quality_filter.py`, `plugins/training/scripts/transcript_parser.py` |

After all batches complete:

| Batch | Action |
|-------|--------|
| 8 — File renames | Rename 3 files (see Task 8) |
| 9 — Verification | Final grep to confirm zero remaining references |

---

### Task 1: Core Config Files

**Files:**
- Modify: `backend/config.py`
- Modify: `.env`
- Modify: `.env.automation.example`

**Step 1: Read backend/config.py**

Read the full file to understand its structure before editing.

**Step 2: Apply name mappings and remove all comments from backend/config.py**

Apply every mapping from the Name Mapping Rules table. All `LLAMAX_*` constants become `GUAARDVARK_*`. The string `"LlamaX1"` becomes `"Guaardvark"`. Version references become `2.4.1`. Remove ALL Python comments (lines starting with `#`, inline `# comments`, docstrings). Preserve all actual code logic unchanged.

Key changes (not exhaustive — apply pattern universally):
- `LLAMAX_ROOT` → `GUAARDVARK_ROOT`
- `LLAMAX_MODE` → `GUAARDVARK_MODE`
- `LLAMAX_PROJECT_NAME` → `GUAARDVARK_PROJECT_NAME` with default `"Guaardvark"`
- All 30+ other `LLAMAX_*` variables follow the same pattern

**Step 3: Apply name mappings to .env**

All lines with `LLAMAX_=` become `GUAARDVARK_=`. Variable values (paths, URLs, booleans) are NOT changed — only the variable names.

**Step 4: Apply name mappings to .env.automation.example**

Same as `.env`.

**Step 5: Verify**

```bash
grep -n "LLAMAX\|llamax\|LlamaX" backend/config.py .env .env.automation.example
```

Expected: zero output.

**Step 6: Commit**

```bash
git add backend/config.py .env .env.automation.example
git commit -m "rebrand: rename LLAMAX_ to GUAARDVARK_ in core config"
```

---

### Task 2: Shell Scripts and run_tests.py

**Files:**
- Modify: `start.sh` (53 matches — largest file)
- Modify: `stop.sh`
- Modify: `start_celery.sh`
- Modify: `run_tests.py`
- Modify: `test_colors.sh`
- Modify: `scripts/install-desktop-launcher.sh`
- Modify: `scripts/lint.sh`
- Modify: `scripts/manage-environments.sh`
- Modify: `plugins/training/export_models.sh`
- Modify: `plugins/training/finetune_command.sh`

**Step 1: Read start.sh**

Read it fully — it's the most complex script with 53 occurrences.

**Step 2: Apply name mappings and remove comments from start.sh**

Apply all name mappings. `export LLAMAX_ROOT=...` becomes `export GUAARDVARK_ROOT=...`. All other `LLAMAX_*` variable usages follow. String "LlamaX1" or "LlamaX" in echo/print output becomes "Guaardvark".

Remove all comment lines (keep `#!/bin/bash` shebang only). Collapse extra blank lines.

**Step 3: Apply name mappings and remove comments from stop.sh, start_celery.sh, test_colors.sh**

Same rules as step 2 for each file.

**Step 4: Apply name mappings and remove comments from run_tests.py**

Python script — apply mappings + remove Python comments.

**Step 5: Apply name mappings and remove comments from scripts/ shell files**

`scripts/install-desktop-launcher.sh` references the desktop launcher path — update `llamax1.desktop` reference to `guaardvark.desktop`. Apply all other name mappings. Remove comments (keep shebang).

Same for `scripts/lint.sh`, `scripts/manage-environments.sh`.

**Step 6: Apply name mappings to plugin training shell scripts**

`plugins/training/export_models.sh` and `plugins/training/finetune_command.sh` — apply mappings, remove comments (keep shebang).

**Step 7: Verify**

```bash
grep -n "LLAMAX\|llamax\|LlamaX" start.sh stop.sh start_celery.sh run_tests.py test_colors.sh \
  scripts/install-desktop-launcher.sh scripts/lint.sh scripts/manage-environments.sh \
  plugins/training/export_models.sh plugins/training/finetune_command.sh
```

Expected: zero output.

**Step 8: Commit**

```bash
git add start.sh stop.sh start_celery.sh run_tests.py test_colors.sh \
  scripts/install-desktop-launcher.sh scripts/lint.sh scripts/manage-environments.sh \
  plugins/training/export_models.sh plugins/training/finetune_command.sh
git commit -m "rebrand: rename LLAMAX to Guaardvark and strip comments from shell scripts"
```

---

### Task 3: Python Backend Files

**Files (all `.py` under `backend/`, excluding `venv/`):**

```
backend/app.py
backend/celery_app.py
backend/celery_tasks_isolated.py
backend/config.py  (already done in Task 1)
backend/cuda_config.py
backend/plugins/__init__.py
backend/plugins/plugin_base.py
backend/services/agent_config.py
backend/services/anatomy_improvement_service.py
backend/services/backup_service.py
backend/services/batch_image_generator.py
backend/services/browser_automation_service.py
backend/services/comfyui_video_generator.py
backend/services/desktop_automation_service.py
backend/services/entity_relationship_indexer.py
backend/services/face_restoration_service.py
backend/services/indexing_service.py
backend/services/interconnector_file_sync_service.py
backend/services/mcp_client_service.py
backend/services/offline_image_generator.py
backend/services/offline_video_generator.py
backend/services/task_handlers/csv_generation_handler.py
backend/services/task_handlers/__init__.py
backend/services/task_handlers/system_maintenance_handler.py
backend/services/training/scripts/finetune_model.py
backend/services/training/scripts/finetune_vision.py
backend/tasks/proven_csv_generation.py
backend/tasks/task_scheduler_celery.py
backend/tasks/training_tasks.py
backend/tasks/unified_task_executor.py
backend/tests/integration/test_embeddings.py
backend/tests/integration/test_gpu_embedding_indexing.py
backend/tests/integration/test_tasks_force_process_status.py
backend/tests/system/test_setup_env_startup.py
backend/tests/test_automation_tools.py
backend/tests/unit/test_index_manager.py
backend/tests/unit/test_seed_prompts_cli.py
backend/tools/browser_tools.py
backend/tools/desktop_tools.py
backend/tools/__init__.py
backend/tools/llama_code_tools.py
backend/tools/mcp_tools.py
backend/utils/cache_manager.py
backend/utils/code_storage_bridge.py
backend/utils/index_manager.py
backend/utils/migration_utils.py
backend/utils/monitoring.py
backend/utils/plugin_loader.py
backend/utils/project_config.py
backend/utils/task_failure_handling.py
backend/api/cache_api.py
backend/api/cache_stats_api.py
backend/api/diagnostics_api.py
backend/api/log_api.py
backend/api/state_api.py
backend/api/training/routes.py
backend/api/web_search_api.py
```

**Step 1: Write and run a comment-stripping + renaming Python script**

Create `/tmp/rebrand_py.py` with this content:

```python
import re
import sys
import tokenize
import io

MAPPINGS = [
    (r'LLAMAX_', 'GUAARDVARK_'),
    (r'LlamaX1', 'Guaardvark'),
    (r'llamaX1', 'guaardvark'),
    (r'LlamaX(?!Index)', 'Guaardvark'),
    (r'llamax-frontend', 'guaardvark'),
    (r'\bLLAMAX\b', 'GUAARDVARK'),
    (r'\bllamax\b', 'guaardvark'),
    (r'LlamaX1 v5\.2', 'Guaardvark 2.4.1'),
    (r'v5\.2\b', '2.4.1'),
    (r'Version 5\.2', 'Version 2.4.1'),
]

def apply_mappings(text):
    for pattern, replacement in MAPPINGS:
        text = re.sub(pattern, replacement, text)
    return text

def strip_python_comments(source):
    result_lines = []
    lines = source.splitlines(keepends=True)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except tokenize.TokenError:
        return source

    comment_lines = set()
    for tok_type, tok_string, (srow, scol), _, _ in tokens:
        if tok_type == tokenize.COMMENT:
            comment_lines.add(srow)

    in_docstring = False
    docstring_end = 0
    i = 0
    while i < len(lines):
        lineno = i + 1
        line = lines[i]
        stripped = line.strip()

        if lineno in comment_lines:
            full_stripped = stripped
            if full_stripped.startswith('#') and not full_stripped.startswith('#!'):
                i += 1
                continue
            else:
                line = re.sub(r'\s+#(?![^\'"]*[\'"]).*$', '', line.rstrip()) + '\n'

        result_lines.append(line)
        i += 1

    result = ''.join(result_lines)

    result = re.sub(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', lambda m: '' if m.group().strip() in ('"""', "'''") or '\n' in m.group() else m.group(), result)

    result = re.sub(r'\n{3,}', '\n\n', result)
    return result

def process_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        source = f.read()
    source = strip_python_comments(source)
    source = apply_mappings(source)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    print(f'processed: {path}')

if __name__ == '__main__':
    for path in sys.argv[1:]:
        try:
            process_file(path)
        except Exception as e:
            print(f'ERROR {path}: {e}', file=sys.stderr)
```

**Step 2: Run the script on all backend Python files**

```bash
cd /home/llamax1/LLAMAX7
python3 /tmp/rebrand_py.py \
  backend/app.py backend/celery_app.py backend/celery_tasks_isolated.py \
  backend/cuda_config.py \
  backend/plugins/__init__.py backend/plugins/plugin_base.py \
  backend/services/agent_config.py backend/services/anatomy_improvement_service.py \
  backend/services/backup_service.py backend/services/batch_image_generator.py \
  backend/services/browser_automation_service.py \
  backend/services/comfyui_video_generator.py \
  backend/services/desktop_automation_service.py \
  backend/services/entity_relationship_indexer.py \
  backend/services/face_restoration_service.py \
  backend/services/indexing_service.py \
  backend/services/interconnector_file_sync_service.py \
  backend/services/mcp_client_service.py \
  backend/services/offline_image_generator.py \
  backend/services/offline_video_generator.py \
  backend/services/task_handlers/csv_generation_handler.py \
  backend/services/task_handlers/__init__.py \
  backend/services/task_handlers/system_maintenance_handler.py \
  backend/services/training/scripts/finetune_model.py \
  backend/services/training/scripts/finetune_vision.py \
  backend/tasks/proven_csv_generation.py \
  backend/tasks/task_scheduler_celery.py \
  backend/tasks/training_tasks.py \
  backend/tasks/unified_task_executor.py \
  backend/tests/integration/test_embeddings.py \
  backend/tests/integration/test_gpu_embedding_indexing.py \
  backend/tests/integration/test_tasks_force_process_status.py \
  backend/tests/system/test_setup_env_startup.py \
  backend/tests/test_automation_tools.py \
  backend/tests/unit/test_index_manager.py \
  backend/tests/unit/test_seed_prompts_cli.py \
  backend/tools/browser_tools.py backend/tools/desktop_tools.py \
  backend/tools/__init__.py backend/tools/llama_code_tools.py \
  backend/tools/mcp_tools.py \
  backend/utils/cache_manager.py backend/utils/code_storage_bridge.py \
  backend/utils/index_manager.py backend/utils/migration_utils.py \
  backend/utils/monitoring.py backend/utils/plugin_loader.py \
  backend/utils/project_config.py backend/utils/task_failure_handling.py \
  backend/api/cache_api.py backend/api/cache_stats_api.py \
  backend/api/diagnostics_api.py backend/api/log_api.py \
  backend/api/state_api.py backend/api/training/routes.py \
  backend/api/web_search_api.py
```

**Step 3: Spot-check a few files**

Read `backend/config.py` and `backend/app.py` to confirm comments are gone and names are correct.

**Step 4: Verify**

```bash
grep -rn "LLAMAX\|llamaX\|LlamaX1\|llamax-" backend/ --include="*.py" | \
  grep -v "LlamaIndex\|llama_index\|llama-index"
```

Expected: zero output. If any remain, open the file and fix manually.

**Step 5: Commit**

```bash
git add backend/
git commit -m "rebrand: rename LLAMAX to Guaardvark and strip comments from Python backend"
```

---

### Task 4: Frontend Files

**Files:**
- All `.js` and `.jsx` under `frontend/src/` with LLAMAX references:
  ```
  frontend/src/App.jsx
  frontend/src/api/settingsService.js
  frontend/src/components/chat/ChatInput.jsx
  frontend/src/components/dashboard/SemanticSearchCard.jsx
  frontend/src/components/layout/Sidebar.jsx
  frontend/src/components/modals/ExportQuantizationModal.jsx
  frontend/src/components/modals/ParseJobModal.jsx
  frontend/src/pages/ChatPage.jsx
  frontend/src/pages/CodeEditorPage.jsx
  frontend/src/stores/useAppStore.js
  frontend/src/theme/tokens.js
  ```
- Modify: `frontend/index.html`
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.js`

**Step 1: Write and run a JS comment-stripping + renaming script**

Create `/tmp/rebrand_js.py`:

```python
import re
import sys

MAPPINGS = [
    (r'LLAMAX_', 'GUAARDVARK_'),
    (r'LlamaX1', 'Guaardvark'),
    (r'llamaX1', 'guaardvark'),
    (r'LlamaX(?!Index)', 'Guaardvark'),
    (r'llamax-frontend', 'guaardvark'),
    (r'\bLLAMAX\b', 'GUAARDVARK'),
    (r'\bllamax\b', 'guaardvark'),
    (r'LlamaX1 v5\.2', 'Guaardvark 2.4.1'),
    (r'v5\.2\b', '2.4.1'),
    (r'Version 5\.2', 'Version 2.4.1'),
]

def apply_mappings(text):
    for pattern, replacement in MAPPINGS:
        text = re.sub(pattern, replacement, text)
    return text

def strip_js_comments(source):
    result = re.sub(r'/\*[\s\S]*?\*/', '', source)
    lines = result.splitlines(keepends=True)
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('//'):
            continue
        line = re.sub(r'\s+//(?![^\'"]*[\'"]).*$', '', line.rstrip())
        if line or not cleaned or cleaned[-1].strip():
            cleaned.append(line + '\n' if line else '\n')
    result = ''.join(cleaned)
    result = re.sub(r'\n{3,}', '\n\n', result)
    return result

def process_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        source = f.read()
    if path.endswith(('.js', '.jsx')):
        source = strip_js_comments(source)
    source = apply_mappings(source)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    print(f'processed: {path}')

if __name__ == '__main__':
    for path in sys.argv[1:]:
        try:
            process_file(path)
        except Exception as e:
            print(f'ERROR {path}: {e}', file=sys.stderr)
```

**Step 2: Run on all frontend JS/JSX files**

```bash
cd /home/llamax1/LLAMAX7
python3 /tmp/rebrand_js.py \
  frontend/src/App.jsx \
  frontend/src/api/settingsService.js \
  frontend/src/components/chat/ChatInput.jsx \
  frontend/src/components/dashboard/SemanticSearchCard.jsx \
  frontend/src/components/layout/Sidebar.jsx \
  frontend/src/components/modals/ExportQuantizationModal.jsx \
  frontend/src/components/modals/ParseJobModal.jsx \
  frontend/src/pages/ChatPage.jsx \
  frontend/src/pages/CodeEditorPage.jsx \
  frontend/src/stores/useAppStore.js \
  frontend/src/theme/tokens.js \
  frontend/vite.config.js
```

**Step 3: Update frontend/index.html**

Replace `<title>llamaX1</title>` with `<title>Guaardvark</title>`. Apply any other name mappings. No comments to remove.

**Step 4: Update frontend/package.json**

- `"name": "llamax-frontend"` → `"name": "guaardvark"`
- If there is a `"version"` field, update to `"2.4.1"`
- Apply any other name mappings to string values

**Step 5: Verify**

```bash
grep -rn "LLAMAX\|llamax\|LlamaX" \
  frontend/src/App.jsx frontend/src/api/settingsService.js \
  frontend/src/components/chat/ChatInput.jsx \
  frontend/src/components/dashboard/SemanticSearchCard.jsx \
  frontend/src/components/layout/Sidebar.jsx \
  frontend/src/components/modals/ExportQuantizationModal.jsx \
  frontend/src/components/modals/ParseJobModal.jsx \
  frontend/src/pages/ChatPage.jsx frontend/src/pages/CodeEditorPage.jsx \
  frontend/src/stores/useAppStore.js frontend/src/theme/tokens.js \
  frontend/index.html frontend/package.json frontend/vite.config.js
```

Expected: zero output.

**Step 6: Commit**

```bash
git add frontend/src/ frontend/index.html frontend/package.json frontend/vite.config.js
git commit -m "rebrand: rename LLAMAX to Guaardvark and strip comments from frontend"
```

---

### Task 5: Documentation and Config Files

**Files:**
- Modify: `CLAUDE.md`
- Modify: `GEMINI.md`
- Modify: `INSTALL.md`
- Modify: `backend/seed_rules.json`
- Modify: `plugins/gpu_embedding/plugin.json`
- Modify: `plugins/gpu_embedding/README.md`
- Modify: `plugins/training/PIPELINE.md`
- Modify: `docs/plans/2026-02-24-llx-cli-design.md`
- Modify: `docs/plans/2026-02-24-llx-cli-implementation.md`

**Step 1: Apply name mappings to CLAUDE.md**

Read the file. Apply all name mappings:
- All `LlamaX1` → `Guaardvark`
- All `LLAMAX_*` references in code examples → `GUAARDVARK_*`
- All version references `5.2` → `2.4.1`
- Any path examples using old names
- Add `guaardvark.com` as the official site URL where appropriate

**Step 2: Apply name mappings to GEMINI.md**

Same as CLAUDE.md.

**Step 3: Apply name mappings to INSTALL.md**

Same. Replace any URL placeholders with `guaardvark.com`.

**Step 4: Update backend/seed_rules.json**

Read the file. Apply name mappings to all string values that reference LlamaX1/LLAMAX. This file contains AI system prompt content — update any self-referential mentions to say Guaardvark.

**Step 5: Update plugin JSON and README files**

Apply name mappings to `plugins/gpu_embedding/plugin.json`, `plugins/gpu_embedding/README.md`, `plugins/training/PIPELINE.md`.

**Step 6: Update old plan docs**

Apply name mappings to `docs/plans/2026-02-24-llx-cli-design.md` and `docs/plans/2026-02-24-llx-cli-implementation.md`.

**Step 7: Verify**

```bash
grep -n "LLAMAX\|llamax\|LlamaX1\|v5\.2" \
  CLAUDE.md GEMINI.md INSTALL.md \
  backend/seed_rules.json \
  plugins/gpu_embedding/plugin.json plugins/gpu_embedding/README.md \
  plugins/training/PIPELINE.md \
  docs/plans/2026-02-24-llx-cli-design.md \
  docs/plans/2026-02-24-llx-cli-implementation.md
```

Expected: zero output.

**Step 8: Commit**

```bash
git add CLAUDE.md GEMINI.md INSTALL.md backend/seed_rules.json \
  plugins/gpu_embedding/ plugins/training/PIPELINE.md docs/plans/
git commit -m "rebrand: update documentation and seed content for Guaardvark"
```

---

### Task 6: CLI Files

**Files:**
- Modify: `cli/llx/main.py`
- Modify: `cli/llx/client.py`
- Modify: `cli/llx/commands/system.py`

**Step 1: Run rebrand_py.py on CLI files**

```bash
python3 /tmp/rebrand_py.py \
  cli/llx/main.py \
  cli/llx/client.py \
  cli/llx/commands/system.py
```

**Step 2: Verify**

```bash
grep -n "LLAMAX\|llamax\|LlamaX" cli/llx/main.py cli/llx/client.py cli/llx/commands/system.py
```

Expected: zero output.

**Step 3: Commit**

```bash
git add cli/llx/main.py cli/llx/client.py cli/llx/commands/system.py
git commit -m "rebrand: rename LLAMAX to Guaardvark and strip comments from CLI"
```

---

### Task 7: Scripts and Plugin Python Files

**Files:**
- Modify: `scripts/bulk_import_docs.py`
- Modify: `scripts/system-manager/generate_session_context.py`
- Modify: `scripts/system-manager/index_codebase.py`
- Modify: `scripts/system-manager/lib/discovery.py`
- Modify: `scripts/system-manager/lib/registry.py`
- Modify: `plugins/training/scripts/finetune_model.py`
- Modify: `plugins/training/scripts/quality_filter.py`
- Modify: `plugins/training/scripts/transcript_parser.py`

**Step 1: Run rebrand_py.py on all files**

```bash
python3 /tmp/rebrand_py.py \
  scripts/bulk_import_docs.py \
  scripts/system-manager/generate_session_context.py \
  scripts/system-manager/index_codebase.py \
  scripts/system-manager/lib/discovery.py \
  scripts/system-manager/lib/registry.py \
  plugins/training/scripts/finetune_model.py \
  plugins/training/scripts/quality_filter.py \
  plugins/training/scripts/transcript_parser.py
```

**Step 2: Verify**

```bash
grep -n "LLAMAX\|llamax\|LlamaX" \
  scripts/bulk_import_docs.py \
  scripts/system-manager/generate_session_context.py \
  scripts/system-manager/index_codebase.py \
  scripts/system-manager/lib/discovery.py \
  scripts/system-manager/lib/registry.py \
  plugins/training/scripts/finetune_model.py \
  plugins/training/scripts/quality_filter.py \
  plugins/training/scripts/transcript_parser.py
```

Expected: zero output.

**Step 3: Commit**

```bash
git add scripts/ plugins/training/scripts/
git commit -m "rebrand: rename LLAMAX to Guaardvark and strip comments from utility scripts"
```

---

### Task 8: File Renames

Run after Tasks 1–7 are complete.

**Step 1: Rename workspace file**

```bash
mv /home/llamax1/LLAMAX7/LLAMAX7.code-workspace /home/llamax1/LLAMAX7/guaardvark.code-workspace
```

Read `guaardvark.code-workspace` and apply name mappings to any string values inside.

**Step 2: Rename desktop launcher**

```bash
mv /home/llamax1/LLAMAX7/scripts/llamax1.desktop /home/llamax1/LLAMAX7/scripts/guaardvark.desktop
```

Read `scripts/guaardvark.desktop` and update content:
- `Name=LlamaX1` → `Name=Guaardvark`
- Any `Exec=...llamax...` → `Exec=...guaardvark...`
- Apply all name mappings

**Step 3: Rename backup JSON**

```bash
mv /home/llamax1/LLAMAX7/llamaX1_backup.json /home/llamax1/LLAMAX7/guaardvark_backup.json
```

Read `guaardvark_backup.json` and apply name mappings to any string values.

**Step 4: Commit**

```bash
git add -A
git commit -m "rebrand: rename files from llamax to guaardvark"
```

---

### Task 9: Final Verification

**Step 1: Run comprehensive grep across all source files**

```bash
grep -rn "LLAMAX\|llamaX\|LlamaX1\|llamax-" \
  --include="*.py" --include="*.js" --include="*.jsx" \
  --include="*.sh" --include="*.json" --include="*.md" \
  --include="*.html" --include="*.yaml" --include="*.yml" \
  --include="*.env" --include="*.toml" \
  /home/llamax1/LLAMAX7/ \
  --exclude-dir=venv \
  --exclude-dir=node_modules \
  --exclude-dir=.git \
  --exclude-dir=backups \
  --exclude-dir=logs \
  --exclude-dir=__pycache__ \
  --exclude-dir=dist \
  --exclude-dir="data/models" \
  2>/dev/null | \
  grep -v "LlamaIndex\|llama_index\|llama-index"
```

**Step 2: Address any remaining occurrences**

For each file that still has references, open it, fix manually, and commit.

**Step 3: Verify the app still parses**

```bash
cd /home/llamax1/LLAMAX7
source backend/venv/bin/activate
python3 -c "from backend.config import *; print('config OK')"
python3 -c "from backend.app import create_app; print('app OK')"
```

Expected: both print their success messages without errors.

**Step 4: Final commit**

```bash
git add -A
git commit -m "rebrand: Guaardvark 2.4.1 - complete rebrand from LlamaX1"
```
