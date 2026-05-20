#!/usr/bin/env python3
"""
Code Agent for LLM Self-Improvement
Enables LLM to read, understand, and modify its own source code.

This module provides code manipulation functions that can be called by an LLM
(via function calling) to autonomously plan and execute code changes.

Milestone Goal: Enable LLM to remove "Snibbly Nips" button from SettingsPage.jsx
through natural language commands.
"""

import logging
import sys
import json
from typing import Optional, Dict, Any, List
from pathlib import Path

# Add project root to path for imports when run as script
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import our code manipulation tools
from backend.tools.llama_code_tools import (
    read_code,
    search_code,
    edit_code,
    list_files,
    verify_change
)

logger = logging.getLogger(__name__)


def get_code_tools_schema() -> List[Dict[str, Any]]:
    """
    Get OpenAI-compatible function calling schema for code manipulation tools.

    Returns:
        List of tool definitions suitable for LLM function calling
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "read_code",
                "description": (
                    "Read the complete contents of a source code file. "
                    "Returns file content with line count and character count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Relative path from project root (e.g., 'frontend/src/pages/SettingsPage.jsx')"
                        }
                    },
                    "required": ["filepath"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": (
                    "Search for code patterns across the project using case-insensitive regex. "
                    "Returns all matches with file paths, line numbers, and matched content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Text or regex pattern to search for (e.g., 'Snibbly Nips', 'Button.*onClick')"
                        },
                        "file_glob": {
                            "type": "string",
                            "description": "Glob pattern for files to search (default: '**/*.{py,jsx,js,tsx,ts}')",
                            "default": "**/*.{py,jsx,js,tsx,ts}"
                        }
                    },
                    "required": ["pattern"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "edit_code",
                "description": (
                    "Edit a source code file by replacing exact text. Creates automatic backup. "
                    "The old_text MUST be unique in the file or the edit will fail."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Relative path from project root"
                        },
                        "old_text": {
                            "type": "string",
                            "description": "The EXACT text to replace (must be unique in file)"
                        },
                        "new_text": {
                            "type": "string",
                            "description": "The new text to insert (can be empty string for deletion)"
                        }
                    },
                    "required": ["filepath", "old_text", "new_text"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files and directories to understand project structure.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Relative path from project root (default: 'frontend/src/pages')",
                            "default": "frontend/src/pages"
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum directory depth to show (default: 2)",
                            "default": 2
                        }
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "verify_change",
                "description": "Verify that a code change was successful by checking if text exists in file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "Relative path from project root"
                        },
                        "expected_text": {
                            "type": "string",
                            "description": "Text to check for"
                        },
                        "should_exist": {
                            "type": "boolean",
                            "description": "True if text should exist, False to verify deletion (default: True)",
                            "default": True
                        }
                    },
                    "required": ["filepath", "expected_text"]
                }
            }
        }
    ]


def execute_tool_call(tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    Execute a code tool by name with provided arguments.

    Args:
        tool_name: Name of the tool to execute
        arguments: Dictionary of arguments for the tool

    Returns:
        Tool execution result as string
    """
    tools_map = {
        "read_code": read_code,
        "search_code": search_code,
        "edit_code": edit_code,
        "list_files": list_files,
        "verify_change": verify_change
    }

    if tool_name not in tools_map:
        return f"ERROR: Unknown tool '{tool_name}'"

    try:
        result = tools_map[tool_name](**arguments)
        logger.info(f"Executed {tool_name} with args: {arguments}")
        return result
    except Exception as e:
        error_msg = f"ERROR executing {tool_name}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return error_msg


import re


_SEARCH_HIT_RE = re.compile(r"^\s*\d+\.\s+(?P<path>.+?):(?P<line>\d+)\s*$")
_FILE_CONTENT_START = "========== FILE CONTENT START =========="
_FILE_CONTENT_END = "========== FILE CONTENT END =========="


def _first_search_hit_path(search_result: str) -> Optional[str]:
    """Extract the path from the first 'N. path:line' hit in search_code output.

    Parses line-by-line rather than running a regex against the whole blob so
    paths containing colons (Windows drive letters, weird filenames) don't
    truncate at the first ':' the way the previous `r"1\\.\\s+([^\\:]+):"`
    pattern did. We anchor on `\\d+\\.` and require a trailing line number so
    we can't accidentally match the search header or the indented content
    preview lines.
    """
    for line in search_result.splitlines():
        m = _SEARCH_HIT_RE.match(line)
        if m:
            return m.group("path").strip()
    return None


def _extract_file_body(file_content: str) -> Optional[str]:
    """Strip the START/END markers that read_code wraps content in."""
    start = file_content.find(_FILE_CONTENT_START)
    end = file_content.find(_FILE_CONTENT_END)
    if start == -1 or end == -1 or end <= start:
        return None
    return file_content[start + len(_FILE_CONTENT_START):end].lstrip("\n").rstrip()


def _slice_enclosing_element(body: str, anchor_idx: int, tag: str) -> Optional[str]:
    """Return the full <tag>...</tag> region that wraps body[anchor_idx].

    Walks backwards from `anchor_idx` to find the most recent opening `<tag`,
    then forwards to the matching `</tag>`. Handles nesting by tracking depth,
    which the previous implementation didn't (it just took the first
    `</Tooltip>` after the anchor, which would be wrong if any inner tooltip
    closed before the outer one).
    """
    open_marker = f"<{tag}"
    close_marker = f"</{tag}>"

    start = body.rfind(open_marker, 0, anchor_idx)
    if start == -1:
        return None

    depth = 0
    cursor = start
    while cursor < len(body):
        next_open = body.find(open_marker, cursor + 1)
        next_close = body.find(close_marker, cursor + 1)
        if next_close == -1:
            return None
        if next_open != -1 and next_open < next_close:
            depth += 1
            cursor = next_open
            continue
        if depth == 0:
            return body[start:next_close + len(close_marker)]
        depth -= 1
        cursor = next_close
    return None


def remove_snibbly_nips_button() -> Dict[str, Any]:
    """
    Deterministic milestone test: find and remove the Snibbly Nips button
    using only the same tools the LLM uses (search/read/edit/verify).

    This version exists as a regression test for the tool layer. It does
    NOT involve an LLM. For the actual self-edit demo see run_llm_self_edit.
    """
    logger.info("=" * 70)
    logger.info("MILESTONE TEST (deterministic): Remove Snibbly Nips Button")
    logger.info("=" * 70)

    steps: List[Dict[str, Any]] = []

    try:
        logger.info("Step 1: search_code('Snibbly Nips', 'frontend/**/*.jsx')")
        search_result = search_code("Snibbly Nips", "frontend/**/*.jsx")
        steps.append({"step": 1, "action": "search_code", "result": search_result[:200]})

        if "No matches found" in search_result:
            return {"success": False, "message": "Button already removed or not found", "steps": steps}

        filepath = _first_search_hit_path(search_result)
        if not filepath:
            return {"success": False, "message": "Could not parse search results", "steps": steps}

        logger.info(f"Step 2: read_code({filepath!r})")
        file_content = read_code(filepath)
        steps.append({"step": 2, "action": "read_code", "result": f"Read {filepath}"})

        body = _extract_file_body(file_content)
        if body is None:
            return {"success": False, "message": "read_code output missing content markers", "steps": steps}

        anchor = 'title="The mysterious Snibbly Nips button'
        anchor_idx = body.find(anchor)
        if anchor_idx == -1:
            return {"success": False, "message": "Could not locate Snibbly Nips tooltip anchor in file", "steps": steps}

        old_text = _slice_enclosing_element(body, anchor_idx, "Tooltip")
        if not old_text:
            return {"success": False, "message": "Could not bracket the <Tooltip>...</Tooltip> region", "steps": steps}
        logger.info(f"Identified region ({len(old_text)} chars): {old_text[:80]}...")

        logger.info("Step 3: edit_code(...) -> remove region")
        edit_result = edit_code(filepath, old_text, "")
        steps.append({"step": 3, "action": "edit_code", "result": edit_result[:200]})
        if "ERROR" in edit_result:
            return {"success": False, "message": "Edit failed", "steps": steps, "error": edit_result}

        logger.info("Step 4: verify_change(..., should_exist=False)")
        verify_result = verify_change(filepath, "Snibbly Nips", should_exist=False)
        steps.append({"step": 4, "action": "verify_change", "result": verify_result})

        success = "✓ VERIFIED" in verify_result
        logger.info(f"TEST RESULT: {'SUCCESS' if success else 'FAILED'}")
        return {
            "success": success,
            "message": "Successfully removed Snibbly Nips button" if success else "Verification failed",
            "filepath": filepath,
            "steps": steps,
        }

    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        return {"success": False, "message": f"Exception during test: {str(e)}", "steps": steps}


# ---------------------------------------------------------------------------
# LLM-driven self-edit loop
# ---------------------------------------------------------------------------

_SELF_EDIT_SYSTEM_PROMPT = """You are a code-editing agent operating on a real codebase. You have access to a small set of tools for reading, searching, editing, and verifying source files. You must accomplish the user's task by calling these tools — you cannot ask clarifying questions.

Available tools (XML calling convention):

  <tool_call>
    <tool>search_code</tool>
    <pattern>regex or literal</pattern>
    <file_glob>**/*.{py,jsx,js,tsx,ts}</file_glob>
  </tool_call>

  <tool_call>
    <tool>read_code</tool>
    <filepath>relative/path/from/repo/root</filepath>
  </tool_call>

  <tool_call>
    <tool>list_files</tool>
    <directory>relative/path</directory>
    <max_depth>2</max_depth>
  </tool_call>

  <tool_call>
    <tool>edit_code</tool>
    <filepath>relative/path</filepath>
    <old_text>exact unique snippet to replace</old_text>
    <new_text>replacement (empty string = delete)</new_text>
  </tool_call>

  <tool_call>
    <tool>verify_change</tool>
    <filepath>relative/path</filepath>
    <expected_text>text to check</expected_text>
    <should_exist>true|false</should_exist>
  </tool_call>

Rules:
- Emit ONE OR MORE <tool_call> blocks per turn. The runtime executes them and replies with <tool_result> blocks.
- old_text in edit_code MUST be unique within the target file. If unsure, read_code first and pick a larger surrounding snippet.
- You are not given any path hints. Find the right file yourself.
- When the task is complete and verified, reply with a short final summary and NO <tool_call> tags. That signals you are done.
"""

_MAX_FEEDBACK_CHARS = 4000


def _truncate_for_feedback(text: str) -> str:
    if len(text) <= _MAX_FEEDBACK_CHARS:
        return text
    head = text[: _MAX_FEEDBACK_CHARS // 2]
    tail = text[-_MAX_FEEDBACK_CHARS // 2 :]
    return f"{head}\n... [truncated {len(text) - _MAX_FEEDBACK_CHARS} chars] ...\n{tail}"


def run_llm_self_edit(
    instruction: str,
    max_steps: int = 10,
    llm: Any = None,
) -> Dict[str, Any]:
    """
    Drive the code tools with an LLM. The model receives only `instruction`
    plus the tool schema — no file paths, no hints about where the target
    might live. It must use search/list/read to locate the work itself.

    Returns a dict with `success`, `iterations`, `transcript`, and `steps`
    (one entry per executed tool call).
    """
    if llm is None:
        from backend.utils.llm_service import get_llm_instance
        llm = get_llm_instance()
    if llm is None:
        return {"success": False, "message": "No LLM instance available (Ollama not running?)", "steps": []}

    try:
        from llama_index.core.base.llms.types import ChatMessage, MessageRole
    except Exception as e:
        return {"success": False, "message": f"llama_index ChatMessage import failed: {e}", "steps": []}

    from backend.utils.agent_output_parser import parse_tool_calls_xml

    chat_log: List[Any] = [
        ChatMessage(role=MessageRole.SYSTEM, content=_SELF_EDIT_SYSTEM_PROMPT),
        ChatMessage(role=MessageRole.USER, content=instruction.strip()),
    ]
    transcript: List[Dict[str, str]] = [{"role": "user", "content": instruction.strip()}]
    steps: List[Dict[str, Any]] = []

    for step in range(1, max_steps + 1):
        try:
            response = llm.chat(chat_log)
            assistant_text = response.message.content or ""
        except Exception as e:
            logger.error(f"LLM chat failed at step {step}: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"LLM call failed: {e}",
                "iterations": step - 1,
                "transcript": transcript,
                "steps": steps,
            }

        chat_log.append(ChatMessage(role=MessageRole.ASSISTANT, content=assistant_text))
        transcript.append({"role": "assistant", "content": assistant_text})

        parsed = parse_tool_calls_xml(assistant_text)
        if not parsed.tool_calls:
            logger.info(f"LLM signalled completion at step {step} ({len(assistant_text)} char reply)")
            return {
                "success": True,
                "message": assistant_text.strip(),
                "iterations": step,
                "transcript": transcript,
                "steps": steps,
            }

        feedback_blocks: List[str] = []
        for tc in parsed.tool_calls:
            tool_name = tc.tool_name
            args = dict(tc.parameters or {})
            # parse_tool_calls_xml returns string values; coerce a couple of
            # known types so the tool functions get what they expect.
            if tool_name == "verify_change" and isinstance(args.get("should_exist"), str):
                args["should_exist"] = args["should_exist"].strip().lower() in ("true", "1", "yes")
            if tool_name == "list_files" and isinstance(args.get("max_depth"), str):
                try:
                    args["max_depth"] = int(args["max_depth"])
                except ValueError:
                    args.pop("max_depth", None)

            result = execute_tool_call(tool_name, args)
            steps.append({
                "step": step,
                "tool": tool_name,
                "args": args,
                "result_preview": result[:200],
            })
            feedback_blocks.append(
                f"<tool_result tool=\"{tool_name}\">\n{_truncate_for_feedback(result)}\n</tool_result>"
            )

        feedback = "\n".join(feedback_blocks)
        chat_log.append(ChatMessage(role=MessageRole.USER, content=feedback))
        transcript.append({"role": "tool", "content": feedback})

    logger.warning(f"run_llm_self_edit hit max_steps={max_steps} without the LLM signalling completion")
    return {
        "success": False,
        "message": f"Reached max_steps ({max_steps}) without completion",
        "iterations": max_steps,
        "transcript": transcript,
        "steps": steps,
    }


# Export functions for external use
__all__ = [
    'get_code_tools_schema',
    'execute_tool_call',
    'remove_snibbly_nips_button',
    'run_llm_self_edit',
    'read_code',
    'search_code',
    'edit_code',
    'list_files',
    'verify_change'
]


if __name__ == "__main__":
    import sys

    print("=" * 70)
    print("Code Agent for LLM Self-Improvement")
    print("=" * 70)

    print("\n1. Available Code Manipulation Tools:")
    tools_schema = get_code_tools_schema()
    for tool in tools_schema:
        print(f"   - {tool['function']['name']}: {tool['function']['description'][:60]}...")

    argv = sys.argv[1:]
    if argv and argv[0] == "--run-test":
        print("\n2. Running deterministic milestone (no LLM): remove Snibbly Nips button")
        result = remove_snibbly_nips_button()
        print(f"   Result: {result['message']}")
        print(f"   Success: {result['success']}")
        if result.get("steps"):
            print(f"   Steps executed: {len(result['steps'])}")
    elif argv and argv[0] == "--llm-self-edit":
        # Optional trailing instruction. Default is intentionally vague so the
        # LLM has to discover the file itself.
        instruction = " ".join(argv[1:]).strip() or "Find and remove the Snibbly Nips button from this codebase."
        print(f"\n2. Running LLM-driven self-edit with instruction:\n   {instruction!r}")
        result = run_llm_self_edit(instruction)
        print(f"\n   Success: {result['success']}")
        print(f"   Iterations: {result.get('iterations')}")
        print(f"   Final message:\n   {result.get('message', '')[:500]}")
        steps = result.get("steps", [])
        if steps:
            print(f"\n   {len(steps)} tool call(s) executed:")
            for s in steps:
                print(f"     step {s['step']}: {s['tool']}({list(s['args'].keys())}) -> {s['result_preview'][:80]!r}")
    else:
        print("\n2. No mode flag — pass one of:")
        print("     --run-test           deterministic tool-level milestone")
        print("     --llm-self-edit      LLM-driven self-edit demo (uses configured LLM)")
        print("\n3. Smoke-testing search_code('Snibbly Nips'):")
        result = search_code("Snibbly Nips")
        print(f"   {result[:200]}...")

    print("\n" + "=" * 70)
