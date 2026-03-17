#!/usr/bin/env python3
"""
Code Agent for LLM Self-Improvement
Enables LLM to read, understand, and modify its own source code.

This module provides code manipulation functions that can be called by an LLM
(via function calling) to autonomously plan and execute code changes.

Milestone Goal: Enable LLM to remove "Snibbly Nips" button from SettingsPage (Copy).jsx
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
    verify_change,
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
                            "description": "Relative path from project root (e.g., 'frontend/src/pages/SettingsPage (Copy).jsx')",
                        }
                    },
                    "required": ["filepath"],
                },
            },
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
                            "description": "Text or regex pattern to search for (e.g., 'Snibbly Nips', 'Button.*onClick')",
                        },
                        "file_glob": {
                            "type": "string",
                            "description": "Glob pattern for files to search (default: '**/*.{py,jsx,js,tsx,ts}')",
                            "default": "**/*.{py,jsx,js,tsx,ts}",
                        },
                    },
                    "required": ["pattern"],
                },
            },
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
                            "description": "Relative path from project root",
                        },
                        "old_text": {
                            "type": "string",
                            "description": "The EXACT text to replace (must be unique in file)",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "The new text to insert (can be empty string for deletion)",
                        },
                    },
                    "required": ["filepath", "old_text", "new_text"],
                },
            },
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
                            "default": "frontend/src/pages",
                        },
                        "max_depth": {
                            "type": "integer",
                            "description": "Maximum directory depth to show (default: 2)",
                            "default": 2,
                        },
                    },
                },
            },
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
                            "description": "Relative path from project root",
                        },
                        "expected_text": {
                            "type": "string",
                            "description": "Text to check for",
                        },
                        "should_exist": {
                            "type": "boolean",
                            "description": "True if text should exist, False to verify deletion (default: True)",
                            "default": True,
                        },
                    },
                    "required": ["filepath", "expected_text"],
                },
            },
        },
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
        "verify_change": verify_change,
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


def remove_snibbly_nips_button() -> Dict[str, Any]:
    """
    Milestone test: Programmatically remove the Snibbly Nips button.

    This demonstrates LLM self-improvement by following these steps:
    1. Search for "Snibbly Nips" in the codebase
    2. Read SettingsPage (Copy).jsx to find exact context
    3. Edit the file to remove the button
    4. Verify the button is gone

    Returns:
        Dict with test results including success status and steps taken
    """
    logger.info("=" * 70)
    logger.info("MILESTONE TEST: Remove Snibbly Nips Button")
    logger.info("=" * 70)

    steps = []

    try:
        # Step 1: Search for the button (only in frontend files)
        logger.info("Step 1: Searching for 'Snibbly Nips'...")
        search_result = search_code("Snibbly Nips", "frontend/**/*.jsx")
        steps.append(
            {"step": 1, "action": "search_code", "result": search_result[:200]}
        )

        if "No matches found" in search_result:
            return {
                "success": False,
                "message": "Button already removed or not found",
                "steps": steps,
            }

        # Extract filepath from search results
        import re

        match = re.search(r"1\.\s+([^\:]+):", search_result)
        if not match:
            return {
                "success": False,
                "message": "Could not parse search results",
                "steps": steps,
            }

        filepath = match.group(1).strip()
        logger.info(f"Found in file: {filepath}")

        # Step 2: Read the file to understand context
        logger.info("Step 2: Reading file to find exact button code...")
        file_content = read_code(filepath)
        steps.append({"step": 2, "action": "read_code", "result": f"Read {filepath}"})

        # Step 3: Find the button code
        # Look for the complete Tooltip+Button structure containing "Snibbly Nips"
        # The structure is: <Tooltip...><span><Button...>Snibbly Nips</Button></span></Tooltip>
        start_marker = "========== FILE CONTENT START =========="
        end_marker = "========== FILE CONTENT END =========="
        content_start = file_content.find(start_marker)
        content_end = file_content.find(end_marker)
        # Skip the marker line + newline
        actual_content = file_content[
            content_start + len(start_marker) + 1 : content_end
        ]

        # Find the text manually by looking for the Tooltip
        search_text = 'title="The mysterious Snibbly Nips button'
        start_idx = actual_content.find(search_text)
        if start_idx == -1:
            return {
                "success": False,
                "message": "Could not find Snibbly Nips button in file",
                "steps": steps,
            }

        # Search backwards from this position to find the opening <Tooltip tag
        # but only within a reasonable distance (500 chars)
        search_start = max(0, start_idx - 100)
        tooltip_start = actual_content.rfind("<Tooltip", search_start, start_idx)
        if tooltip_start == -1:
            return {
                "success": False,
                "message": "Could not find opening Tooltip tag",
                "steps": steps,
            }

        # Find the closing </Tooltip> after our start position
        tooltip_end = actual_content.find("</Tooltip>", start_idx)
        if tooltip_end == -1:
            return {
                "success": False,
                "message": "Could not find closing Tooltip tag",
                "steps": steps,
            }

        old_text = actual_content[tooltip_start : tooltip_end + len("</Tooltip>")]
        logger.info(f"Found button code ({len(old_text)} chars): {old_text[:100]}...")

        # Step 4: Edit to remove the button
        logger.info("Step 3: Removing button from file...")
        edit_result = edit_code(filepath, old_text, "")
        steps.append({"step": 3, "action": "edit_code", "result": edit_result[:200]})

        if "ERROR" in edit_result:
            return {
                "success": False,
                "message": "Edit failed",
                "steps": steps,
                "error": edit_result,
            }

        # Step 5: Verify removal
        logger.info("Step 4: Verifying button removal...")
        verify_result = verify_change(filepath, "Snibbly Nips", should_exist=False)
        steps.append({"step": 4, "action": "verify_change", "result": verify_result})

        success = "✓ VERIFIED" in verify_result

        logger.info("=" * 70)
        logger.info(f"TEST RESULT: {'SUCCESS' if success else 'FAILED'}")
        logger.info("=" * 70)

        return {
            "success": success,
            "message": (
                "Successfully removed Snibbly Nips button"
                if success
                else "Verification failed"
            ),
            "filepath": filepath,
            "steps": steps,
        }

    except Exception as e:
        logger.error(f"Test failed with exception: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Exception during test: {str(e)}",
            "steps": steps,
        }


# Export functions for external use
__all__ = [
    "get_code_tools_schema",
    "execute_tool_call",
    "remove_snibbly_nips_button",
    "read_code",
    "search_code",
    "edit_code",
    "list_files",
    "verify_change",
]


if __name__ == "__main__":
    # Test the code tools
    print("=" * 70)
    print("Code Agent for LLM Self-Improvement")
    print("=" * 70)

    # Show available tools
    print("\n1. Available Code Manipulation Tools:")
    tools_schema = get_code_tools_schema()
    for tool in tools_schema:
        print(
            f"   - {tool['function']['name']}: {tool['function']['description'][:60]}..."
        )

    # Test milestone goal
    print("\n2. Running Milestone Test: Remove Snibbly Nips Button")
    print("   (This will actually modify the code file!)")

    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--run-test":
        result = remove_snibbly_nips_button()
        print(f"\n   Result: {result['message']}")
        print(f"   Success: {result['success']}")
        if result.get("steps"):
            print(f"   Steps executed: {len(result['steps'])}")
    else:
        print("   (Skipped - use --run-test flag to actually execute)")
        print("\n3. Testing search function...")
        result = search_code("Snibbly Nips")
        print(f"   {result[:200]}...")

    print("\n" + "=" * 70)
    print("Code tools ready for LLM function calling!")
    print("=" * 70)
