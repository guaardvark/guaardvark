#!/usr/bin/env python3
"""
Agent Output Parser
Parses LLM responses to extract tool calls using structured output
"""

import logging
import re
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolCall(BaseModel):
    """Single tool call (Pydantic model following existing patterns)"""

    tool_name: str = Field(description="Name of the tool to call")
    parameters: Dict[str, Any] = Field(
        description="Parameters for the tool", default_factory=dict
    )
    reasoning: Optional[str] = Field(description="Why calling this tool", default=None)


class ToolCallResponse(BaseModel):
    """LLM response with tool calls (like GenerationResult pattern)"""

    thoughts: Optional[str] = Field(
        description="LLM's reasoning about the task", default=None
    )
    tool_calls: List[ToolCall] = Field(
        description="List of tool calls to execute", default_factory=list
    )
    final_answer: Optional[str] = Field(
        description="Final answer if no tools needed", default=None
    )


def parse_tool_calls_structured(llm_response: str, llm=None) -> ToolCallResponse:
    """
    Parse tool calls from LLM response using JSON-first approach.
    Falls back to XML parsing, then LLM parsing as last resort.

    Args:
        llm_response: Raw LLM response text
        llm: LLM instance (only used as fallback)

    Returns:
        ToolCallResponse with parsed tool calls
    """
    # PRIMARY: Try JSON parsing first (expected with structured output)
    json_result = parse_tool_calls_json(llm_response)

    # If JSON parsing produced a structurally valid result (has thoughts or
    # tool_calls or final_answer), trust it — even if tool_calls is empty.
    # This prevents XML fallback from misinterpreting raw JSON text.
    json_was_valid = (
        json_result.thoughts is not None
        or json_result.tool_calls
        or json_result.final_answer is not None
    )

    if json_was_valid:
        if json_result.tool_calls:
            logger.info(f"JSON parsing found {len(json_result.tool_calls)} tool calls")
        elif json_result.final_answer:
            logger.info("JSON parsing found final answer")
        else:
            logger.info(
                "JSON parsing found valid structure (no tool calls or answer yet)"
            )
        return json_result

    # FALLBACK: Try XML parsing (backward compat, non-JSON models)
    logger.info("JSON parsing found nothing, trying XML fallback")
    xml_result = parse_tool_calls_xml(llm_response)

    if xml_result.tool_calls:
        logger.info(f"XML fallback found {len(xml_result.tool_calls)} tool calls")
        return xml_result

    if xml_result.final_answer:
        logger.info("XML fallback found final answer")
        return xml_result

    # LAST RESORT: Only use LLM parsing if both JSON and XML found nothing
    logger.warning("JSON and XML parsing found nothing, trying LLM parsing as fallback")
    try:
        from backend.utils.llm_service import generate_structured_output

        # Extraction prompt for the LLM
        extraction_prompt = f"""Analyze this response and extract any tool calls:

{llm_response}

If the response contains tool calls (marked with <tool_call> tags or mentions of using tools), extract them.
If it's a final answer with no tool calls, set final_answer with the response text.

Tool calls should have:
- tool_name: the name of the tool
- parameters: a dictionary of parameter names and values
- reasoning: why the tool is being called

Output the structured data.
"""

        # Use existing structured output system
        result = generate_structured_output(
            prompt=extraction_prompt, output_cls=ToolCallResponse, llm=llm
        )

        logger.info(
            f"LLM parsing found: {len(result.tool_calls)} tool calls, has final answer: {result.final_answer is not None}"
        )
        return result

    except Exception as e:
        logger.error(f"LLM parsing also failed: {e}", exc_info=True)
        # Return XML result as final fallback (may have empty final_answer)
        return xml_result


def parse_tool_calls_xml(llm_response: str) -> ToolCallResponse:
    """
    Fallback XML parser for tool calls
    Parses <tool_call> tags directly

    Args:
        llm_response: Raw LLM response

    Returns:
        ToolCallResponse
    """
    try:
        tool_calls = []

        # Find all <tool_call> blocks
        tool_call_pattern = r"<tool_call>(.*?)</tool_call>"
        matches = re.findall(tool_call_pattern, llm_response, re.DOTALL)

        # Fallback: if no <tool_call> wrappers, look for bare <tool>name</tool> blocks
        # Many models output <tool>name</tool><param>val</param> without the wrapper
        if not matches:
            bare_tool_pattern = r"<tool>([\w_]+)</tool>"
            bare_matches = list(re.finditer(bare_tool_pattern, llm_response))
            if bare_matches:
                for i, m in enumerate(bare_matches):
                    start = m.start()
                    # Grab text from this <tool> tag to the next one (or end)
                    end = (
                        bare_matches[i + 1].start()
                        if i + 1 < len(bare_matches)
                        else len(llm_response)
                    )
                    block = llm_response[start:end]
                    matches.append(block)
                logger.info(
                    f"No <tool_call> wrappers found, extracted {len(matches)} bare <tool> blocks"
                )

        for match in matches:
            # Extract tool name
            tool_name_match = re.search(r"<tool>(.*?)</tool>", match)
            if not tool_name_match:
                continue
            tool_name = tool_name_match.group(1).strip()

            # Extract parameters (DOTALL to handle multi-line values)
            params = {}
            param_pattern = r"<(\w+)>(.*?)</\1>"
            param_matches = re.findall(param_pattern, match, re.DOTALL)
            logger.debug(
                f"XML param_matches for {tool_name}: {[(n, v[:80]) for n, v in param_matches]}"
            )

            # Handle two possible formats:
            # Format 1: Direct parameter names: <query>value</query>
            # Format 2: Nested format: <parameter>query</parameter><value>value</value>
            #   Also handles <parameter_name>query</parameter_name><value>value</value>
            has_nested_format = any(
                name in ("parameter", "parameter_name") for name, _ in param_matches
            )

            if has_nested_format:
                # Handle nested parameter/value format
                # Collect all parameter_name and value entries in order
                param_names_queue = []
                value_queue = []
                other_params = {}

                for pm_name, pm_val in param_matches:
                    if pm_name in ("parameter", "parameter_name"):
                        param_names_queue.append(pm_val.strip())
                    elif pm_name == "value":
                        value_queue.append(pm_val.strip())
                    elif pm_name not in ["tool", "reasoning"]:
                        # Direct-format tag mixed into nested format
                        other_params[pm_name] = pm_val.strip()

                # Pair parameter names with values positionally
                for i, pname in enumerate(param_names_queue):
                    if i < len(value_queue):
                        params[pname] = value_queue[i]
                    else:
                        logger.warning(f"Parameter '{pname}' has no matching value")

                # Handle orphan values (more values than parameter names)
                # These can't be assigned without schema info, log them
                if len(value_queue) > len(param_names_queue):
                    orphan_count = len(value_queue) - len(param_names_queue)
                    logger.warning(
                        f"{orphan_count} orphan value(s) without parameter names"
                    )

                # Include any direct-format params found in the block
                params.update(other_params)

                # Handle unclosed last <value> tag: if we have more param names
                # than values, try to extract text after the last matched tag
                if len(param_names_queue) > len(value_queue):
                    missing_param = param_names_queue[len(value_queue)]
                    # Find text after the last matched tag up to end of block
                    last_tag_end = 0
                    for pm_name, pm_val in param_matches:
                        tag_pattern = f"<{pm_name}>{re.escape(pm_val)}</{pm_name}>"
                        tag_match = re.search(
                            tag_pattern, match[last_tag_end:], re.DOTALL
                        )
                        if tag_match:
                            last_tag_end += tag_match.end()
                    # Look for unclosed <value>content or bare text after last tag
                    remaining = match[last_tag_end:].strip()
                    unclosed_value = re.search(
                        r"<value>(.*?)(?:</value>|$)", remaining, re.DOTALL
                    )
                    if unclosed_value and unclosed_value.group(1).strip():
                        params[missing_param] = unclosed_value.group(1).strip()
                        logger.info(
                            f"Recovered unclosed value for param '{missing_param}'"
                        )
            else:
                # Direct format: parameter name is the tag name
                for param_name, param_value in param_matches:
                    if param_name not in ["tool", "reasoning", "tool_call"]:
                        params[param_name] = param_value.strip()

            # Strip wrapping quotes from values (LLMs often quote XML values)
            for k in list(params.keys()):
                v = params[k]
                if isinstance(v, str) and len(v) >= 2:
                    if (v.startswith("'") and v.endswith("'")) or (
                        v.startswith('"') and v.endswith('"')
                    ):
                        params[k] = v[1:-1]

            logger.debug(
                f"XML parsed params for {tool_name}: {list(params.keys())} = {params}"
            )

            # Extract reasoning
            reasoning_match = re.search(
                r"<reasoning>(.*?)</reasoning>", match, re.DOTALL
            )
            reasoning = reasoning_match.group(1).strip() if reasoning_match else None

            tool_calls.append(
                ToolCall(tool_name=tool_name, parameters=params, reasoning=reasoning)
            )

        # If no tool calls found, check if this looks like a final answer
        if not tool_calls:
            # Check if response contains explicit answer indicators
            answer_indicators = [
                "final answer",
                "answer:",
                "conclusion:",
                "summary:",
                "based on",
                "according to",
                "the answer is",
            ]

            response_lower = llm_response.lower()
            has_answer_indicator = any(
                indicator in response_lower for indicator in answer_indicators
            )

            # Only treat as final answer if it looks like one (not just empty/error)
            if has_answer_indicator or len(llm_response.strip()) > 50:
                # Remove any partial XML tags
                clean_response = re.sub(r"<[^>]*>", "", llm_response)
                cleaned = clean_response.strip()
                if cleaned:
                    return ToolCallResponse(final_answer=cleaned)

            # If no clear answer, return empty (will trigger another iteration)
            return ToolCallResponse(
                thoughts=llm_response.strip() if llm_response.strip() else None,
                tool_calls=[],
                final_answer=None,
            )

        # Extract thoughts (text before first tool call)
        first_tool_call_pos = llm_response.find("<tool_call>")
        thoughts = (
            llm_response[:first_tool_call_pos].strip()
            if first_tool_call_pos > 0
            else None
        )

        return ToolCallResponse(thoughts=thoughts, tool_calls=tool_calls)

    except Exception as e:
        logger.error(f"XML parsing failed: {e}", exc_info=True)
        # Return response as final answer
        return ToolCallResponse(final_answer=llm_response)


def parse_tool_calls_json(llm_response: str) -> ToolCallResponse:
    """
    Parse JSON-formatted tool calls from LLM response.
    Returns empty ToolCallResponse on failure (signals to try next parser).

    Args:
        llm_response: Raw LLM response

    Returns:
        ToolCallResponse (empty if not valid JSON)
    """
    try:
        import json

        text = llm_response.strip()

        # Strip markdown code fences if present
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Try direct parse first (structured output produces clean JSON)
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: find JSON object in response text
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return ToolCallResponse()  # Empty = try next parser
            data = json.loads(match.group(0))

        # Parse tool calls — normalize field names for robustness
        tool_calls = []
        for tc in data.get("tool_calls", []):
            if isinstance(tc, dict):
                tool_calls.append(
                    ToolCall(
                        tool_name=tc.get(
                            "tool_name", tc.get("tool", tc.get("name", ""))
                        ),
                        parameters=tc.get("parameters", tc.get("args", {})),
                        reasoning=tc.get("reasoning"),
                    )
                )

        return ToolCallResponse(
            thoughts=data.get("thoughts"),
            tool_calls=tool_calls,
            final_answer=data.get("final_answer"),
        )

    except Exception as e:
        logger.debug(f"JSON parsing failed (expected for non-JSON): {e}")
        return ToolCallResponse()  # Empty = try next parser


def format_tool_result_for_llm(tool_name: str, result, format: str = "json") -> str:
    """
    Format tool result for LLM observation.

    Args:
        tool_name: Name of the tool that was executed
        result: ToolResult object
        format: 'json' (default) or 'xml' (legacy)

    Returns:
        Formatted observation text
    """
    if format == "json":
        import json

        obs = {"tool": tool_name, "status": "success" if result.success else "failed"}
        if result.success:
            obs["output"] = str(result.output) if result.output is not None else ""
            if result.metadata:
                obs["metadata"] = result.metadata
        else:
            obs["error"] = result.error
        return json.dumps(obs)

    # Legacy XML format (kept for unified_chat_engine)
    if result.success:
        output = f"<observation tool='{tool_name}'>\n"
        output += f"Status: Success\n"
        output += f"Output:\n{result.output}\n"
        if result.metadata:
            output += f"\nMetadata: {result.metadata}\n"
        output += "</observation>"
    else:
        output = f"<observation tool='{tool_name}'>\n"
        output += f"Status: Failed\n"
        output += f"Error: {result.error}\n"
        output += "</observation>"

    return output
