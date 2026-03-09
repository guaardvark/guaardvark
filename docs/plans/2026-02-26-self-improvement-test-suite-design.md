# Self-Improvement Coding Test Suite Design

**Date:** 2026-02-26
**Purpose:** Comprehensive automated test suite for Guaardvark's self-improvement coding abilities

## Problem

Guaardvark has extensive agent-based coding capabilities (Code Assistant agent, code tools, ReACT loop, codegen) but no automated tests validating these work correctly. The only evidence is a single proof-of-concept ("remove Snibbly Nips button"). We need a repeatable test suite that validates the full pipeline from individual tools to end-to-end self-improvement scenarios.

## Architecture

Four test layers, bottom-up:

```
Layer 4: E2E Self-Improvement (test_self_improvement.py)
    |  Full pipeline: planted bugs, code quality, feature addition
Layer 3: Agent Executor (test_agent_executor.py)
    |  ReACT loop, tool selection, iteration limits, error handling
Layer 2: Code Generation (test_code_generation.py)
    |  LLM-backed codegen, syntax validation, file modification
Layer 1: Code Tools (test_code_tools.py)
    |  read_code, search_code, edit_code, verify_change, list_files
```

## Layer 1: Code Tools Unit Tests

**File:** `backend/tests/test_code_tools.py`
**LLM required:** No
**Safety:** Sandbox only (tmp directory)

Tests:
- `test_read_code_existing_file` — reads file, returns content with metadata
- `test_read_code_nonexistent` — returns error, no crash
- `test_read_code_path_traversal_blocked` — rejects `../../etc/passwd`
- `test_search_code_finds_pattern` — regex match in sandbox files
- `test_search_code_no_matches` — returns empty gracefully
- `test_edit_code_replacement` — exact text swap, backup created
- `test_edit_code_rollback_on_failure` — backup restored when verification fails
- `test_edit_code_forbidden_directory` — rejects .git, .env, node_modules
- `test_edit_code_multiline` — multi-line text replacement
- `test_verify_change_text_present` — confirms expected text exists
- `test_verify_change_text_absent` — confirms removed text is gone
- `test_list_files_tree` — directory listing with depth limit

## Layer 2: Code Generation Tests

**File:** `backend/tests/test_code_generation.py`
**LLM required:** Yes (Ollama)
**Safety:** Sandbox only

Tests:
- `test_generate_python_function` — generate function from description, verify syntax
- `test_generate_react_component` — generate JSX component, verify syntax
- `test_modify_existing_file` — add function to existing file
- `test_generated_code_is_valid_python` — compile check with `py_compile`

## Layer 3: Agent Executor Tests

**File:** `backend/tests/test_agent_executor.py`
**LLM required:** Yes (Ollama)
**Safety:** Sandbox only

Tests:
- `test_agent_reads_file` — "read file X" triggers read_code tool
- `test_agent_searches_code` — "find pattern Y" triggers search_code tool
- `test_agent_edit_sequence` — "change A to B" triggers read → edit → verify
- `test_agent_handles_tool_failure` — file not found handled gracefully
- `test_agent_respects_max_iterations` — stops at configured limit
- `test_agent_facts_extraction` — facts registry captures tool observations

## Layer 4: E2E Self-Improvement Tests

**File:** `backend/tests/test_self_improvement.py`
**LLM required:** Yes (Ollama)
**Safety:** Sandbox first, then one real-file test with rollback

Tests:
- `test_planted_bug_fix` — file with obvious bug, agent finds and fixes it
- `test_code_quality_improvement` — poorly formatted code, agent improves it
- `test_feature_addition` — minimal module, agent adds a function
- `test_real_file_modification_with_rollback` — modify real non-critical fixture, verify backup, verify rollback

## Test Infrastructure

**Sandbox fixture:** pytest fixture creates `tmp_test_sandbox/` with sample files, yields path, cleans up after.

**Fixture files:** `backend/tests/fixtures/sandbox_code/` contains intentionally buggy code for agent to fix.

**Skip decorators:** `@pytest.mark.skipif(not ollama_available())` for LLM tests.

**Timeouts:** `@pytest.mark.timeout(120)` for LLM-backed tests.

**Markers:** `@pytest.mark.llm` for tests requiring Ollama, `@pytest.mark.sandbox` for sandbox-only tests.

## Key Files

- Tools under test: `backend/tools/code/llama_code_tools.py`
- Agent config: `backend/agents/agent_config.py`
- Agent executor: `backend/services/agent_executor.py`
- Codegen tool: `backend/tools/code/code_tools.py`
- Agent API: `backend/api/agents_api.py`
- Code execution: `backend/api/code_execution_api.py`

## Verification

```bash
# Run all self-improvement tests
python3 -m pytest backend/tests/test_code_tools.py backend/tests/test_code_generation.py backend/tests/test_agent_executor.py backend/tests/test_self_improvement.py -vv

# Run sandbox-only (no LLM) tests
python3 -m pytest backend/tests/test_code_tools.py -vv -m "not llm"

# Run with timeout for CI
python3 -m pytest backend/tests/test_self_improvement.py -vv --timeout=300
```
