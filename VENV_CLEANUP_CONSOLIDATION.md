# Virtual Environment Consolidation Plan

## Objective
Consolidate all Python virtual environments across the LLAMAX8 project into a single, unified environment located at `backend/venv`. This will reduce disk space usage, simplify dependency management, and ensure consistency across the CLI, backend, and all plugins.

## Current State
Currently, the project creates or expects multiple virtual environments:
- `backend/venv` (Primary)
- `cli/venv` (For the `guaardvark` CLI tool)
- `plugins/swarm/venv`
- `plugins/upscaling/venv`
- `plugins/vision_pipeline/venv` (Falls back to backend/venv if its own doesn't exist)
- `plugins/discord/venv` (Falls back to backend/venv if its own doesn't exist)
- `plugins/comfyui/venv` (Already correctly uses `backend/venv` via `PROJECT_ROOT/backend/venv/bin/python`)

## Implementation Steps

### Phase 1: Update Startup Scripts
We need to modify the startup scripts to point to `backend/venv` and remove any logic that creates isolated `venv` directories.

1. **`start.sh` (CLI Setup)**
   - **Target:** Lines ~971-1019
   - **Action:** Remove the creation of `cli/venv`. Activate `backend/venv` instead, run `pip install -e "$CLI_DIR"`, and update the symlink logic to point to `backend/venv/bin/guaardvark`.

2. **`plugins/swarm/scripts/start.sh`**
   - **Target:** Lines ~33-38
   - **Action:** Remove the `python3 -m venv "$PLUGIN_ROOT/venv"` block. Change the activation command to `source "$PROJECT_ROOT/backend/venv/bin/activate"`.

3. **`plugins/upscaling/scripts/start.sh`**
   - **Target:** Lines ~33-40
   - **Action:** Remove the venv creation block. Change the activation command to `source "$PROJECT_ROOT/backend/venv/bin/activate"`.

4. **`plugins/vision_pipeline/scripts/start.sh`**
   - **Target:** Lines ~33-38
   - **Action:** Remove the conditional check. Hardcode the activation to `source "$PROJECT_ROOT/backend/venv/bin/activate"`.

5. **`plugins/discord/scripts/start.sh`**
   - **Target:** Lines ~46-51
   - **Action:** Remove the conditional check. Hardcode the activation to `source "$PROJECT_ROOT/backend/venv/bin/activate"`.

### Phase 2: Cleanup Legacy Environments
Add a cleanup step or manually remove the existing isolated virtual environments to reclaim disk space and prevent confusion.

- `rm -rf cli/venv`
- `rm -rf plugins/*/venv`

### Phase 3: Update Gitignore (Optional but recommended)
- Verify `.gitignore` rules. Currently, it ignores `cli/venv/` and `plugins/*/venv/`. These can remain or be cleaned up, but no functional change is strictly required here.

## Testing Plan
1. Run `./start.sh --fast` to ensure the CLI installs into the backend venv and the symlink works.
2. Verify `which guaardvark` points to `~/.local/bin/guaardvark` and that it executes successfully.
3. Start plugins (e.g., `./start.sh --plugins`) and verify they boot successfully using the shared `backend/venv`.
4. Check `logs/` for any import errors related to missing dependencies in the unified venv.