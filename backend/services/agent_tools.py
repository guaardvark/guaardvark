#!/usr/bin/env python3
"""
Agent Tools System - Base Infrastructure
Provides tool definition, registry, and execution patterns for agent capabilities
"""

import os
import inspect
import threading
import logging
import difflib
import json
import types
from dataclasses import dataclass, field
from typing import Dict, Any, Callable, Optional, List, Union, Generator
from enum import Enum

logger = logging.getLogger(__name__)


def _coerce_list_param(value: Any) -> list:
    """Coerce tool list parameters from JSON arrays or comma-separated strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in stripped.split(",") if part.strip()]
    return [value]


@dataclass
class ToolParameter:
    """Tool parameter definition (follows FileMetadata pattern)"""
    name: str
    type: str  # 'string', 'int', 'bool', 'float', 'list', 'dict'
    required: bool = True
    description: str = ""
    default: Optional[Any] = None
    # JSON Schema constraints, published to MCP clients and checked before a call.
    enum: Optional[List[Any]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    items: Optional[str] = None  # element type of a list parameter, e.g. "string"


@dataclass
class ToolResult:
    """Tool execution result (follows ProcessedContent/GenerationResult pattern)"""
    success: bool
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            'success': self.success,
            'output': self.output,
            'error': self.error,
            'metadata': self.metadata
        }


class BaseTool:
    """Base class for all agent tools (follows FileProcessor pattern)"""

    name: str = ""
    description: str = ""
    parameters: Dict[str, ToolParameter] = None  # Set in subclass, don't use mutable default
    
    # Safety and Context flags
    is_dangerous: bool = False
    requires_approval: bool = False
    # True for read-only status tools that a caller polls with the same
    # arguments on purpose; exempts them from the duplicate-call guard.
    idempotent: bool = False
    # What a call does to state, declared on the tool rather than guessed from
    # its category. read_only: it changes nothing a person would notice.
    # destructive: it removes or irreversibly changes something. None means
    # undeclared, and the MCP server then advertises no hint.
    read_only: Optional[bool] = None
    destructive: Optional[bool] = None
    # Whether plan mode may run this tool while it researches an objective,
    # before anyone has approved a plan. None follows read_only. A read-only
    # tool that still must wait for approval (a long generation whose output
    # is the deliverable, a call into a third party's server) says False.
    plan_research: Optional[bool] = None
    # How much of this tool's result the chat model gets to read before the
    # next turn. 500 keeps a chatty tool from crowding the context; a search
    # tool whose whole point is the text it returns declares more.
    observation_chars: int = 500
    # The line the chat model reads in its tool list. Chat shows only the first
    # 80 characters of `description`, which cut the rules off tools whose use
    # depends on them; a tool that needs more sets this (keep it under ~250).
    # MCP clients always get the full description.
    chat_summary: str = ""
    requires_confirmation: bool = False
    required_context: List[str] = []  # e.g., ['project_id', 'user_id'] (read-only; never mutate)
    
    def __init__(self):
        if not self.name:
            raise ValueError(f"{self.__class__.__name__} must define 'name' attribute")
        if not self.description:
            raise ValueError(f"{self.__class__.__name__} must define 'description' attribute")
        # Ensure parameters is a dict (avoid mutable default at class level)
        if self.parameters is None:
            self.parameters = {}
        
        self._context: Dict[str, Any] = {}
            
    def set_context(self, context: Dict[str, Any]):
        """Inject execution context (user, project, session)"""
        self._context = context
    
    def can_execute(self, **kwargs) -> bool:
        """
        Check if tool can execute with given params
        Override in subclasses for validation logic
        """
        # Check required parameters
        missing_params = []
        for param_name, param in self.parameters.items():
            if param.required and param_name not in kwargs:
                missing_params.append(param_name)
        
        if missing_params:
            logger.warning(
                f"Tool {self.name} missing required parameters: {missing_params}. "
                f"Received parameters: {list(kwargs.keys())}"
            )
            return False
        return True
    
    def execute(self, **kwargs) -> ToolResult:
        """
        Execute the tool
        Must be implemented by subclasses
        """
        raise NotImplementedError(f"Tool {self.name} must implement execute() method")
    
    def get_schema(self) -> str:
        """Generate XML schema for this tool (for LLM prompt)"""
        schema = f"<tool name='{self.name}'>\n"
        schema += f"  <description>{self.description}</description>\n"
        schema += "  <parameters>\n"
        for param_name, param in self.parameters.items():
            req = "true" if param.required else "false"
            default_str = f" default='{param.default}'" if param.default is not None else ""
            schema += f"    <parameter name='{param_name}' type='{param.type}' required='{req}'{default_str}>"
            schema += f"{param.description}</parameter>\n"
        schema += "  </parameters>\n"
        schema += "</tool>"
        return schema
    
    def get_json_schema(self) -> Dict[str, Any]:
        """Generate JSON schema for this tool"""
        return {
            'name': self.name,
            'description': self.description,
            'parameters': {
                param_name: {
                    'type': param.type,
                    'required': param.required,
                    'description': param.description,
                    'default': param.default
                }
                for param_name, param in self.parameters.items()
            }
        }


class ToolRegistry:
    """Registry of all tools (follows PYDANTIC_MODELS pattern)"""
    
    def __init__(self):
        self.tools: Dict[str, BaseTool] = {}
        # Tools can be (un)registered at runtime (e.g. MCP servers connecting)
        # while chat threads read the registry.
        self._lock = threading.RLock()
        logger.info("Tool registry initialized")
    
    def register(self, tool: BaseTool):
        """Register a tool in the registry"""
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Can only register BaseTool instances, got {type(tool)}")
        
        with self._lock:
            if tool.name in self.tools:
                logger.warning(f"Tool '{tool.name}' already registered, replacing...")
            tools = dict(self.tools)
            tools[tool.name] = tool
            self.tools = tools
        logger.info(f"Registered tool: {tool.name}")
    
    def unregister(self, tool_name: str):
        """Unregister a tool"""
        with self._lock:
            if tool_name in self.tools:
                tools = dict(self.tools)
                del tools[tool_name]
                self.tools = tools
                logger.info(f"Unregistered tool: {tool_name}")
    
    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[str]:
        """Get list of all registered tool names"""
        return list(self.tools.keys())

    def subset(self, names: List[str]) -> "tuple[ToolRegistry, List[str]]":
        """A new registry holding only the named tools, in the order given.

        Returns (registry, missing): ``missing`` lists the names this registry
        does not have, so a caller can say which tools an agent lacks.
        """
        tools = self.tools
        picked: Dict[str, BaseTool] = {}
        missing: List[str] = []
        for name in names:
            tool = tools.get(name)
            if tool is None:
                missing.append(name)
            else:
                picked[name] = tool
        registry = ToolRegistry()
        registry.tools = picked
        return registry, missing

    def get_tool_schemas(self, format: str = 'xml', tool_filter: str = None) -> str:
        """
        Generate tool schemas for LLM prompt

        Args:
            format: 'xml', 'json', or 'json_prompt'
            tool_filter: Optional filter mode:
                'vision' — only agent control + screen tools (for vision/screen tasks)
                None — all tools (default)
        """
        tools = self.tools.values()

        if tool_filter == 'vision':
            # Only tools relevant to vision-based screen automation
            VISION_TOOL_PREFIXES = ('agent_', 'web_search')
            VISION_TOOL_NAMES = {
                'agent_mode_start', 'agent_mode_stop', 'agent_task_execute',
                'agent_screen_capture', 'agent_status', 'web_search',
            }
            tools = [t for t in tools if t.name in VISION_TOOL_NAMES
                     or t.name.startswith('agent_')]

        if format == 'xml':
            schemas = []
            for tool in tools:
                schemas.append(tool.get_schema())
            return "\n\n".join(schemas)
        elif format == 'json':
            import json
            schemas = [tool.get_json_schema() for tool in tools]
            return json.dumps(schemas, indent=2)
        elif format == 'json_prompt':
            lines = []
            for tool in tools:
                schema = tool.get_json_schema()
                params_desc = []
                for pname, pinfo in schema['parameters'].items():
                    req = " (required)" if pinfo['required'] else " (optional)"
                    params_desc.append(f'    "{pname}": {pinfo["type"]}{req} - {pinfo["description"]}')
                lines.append(f'Tool: "{schema["name"]}"')
                lines.append(f'  Description: {schema["description"]}')
                lines.append(f'  Parameters: {{')
                lines.extend(params_desc)
                lines.append(f'  }}')
                lines.append('')
            return '\n'.join(lines)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def as_llama_index_tools(self, tool_filter: str = None) -> List[Any]:
        """Convert registered tools to LlamaIndex FunctionTool objects for native function calling.

        Returns a list of llama_index.core.tools.FunctionTool instances whose
        metadata (name, description, parameter schema) mirrors our BaseTool
        definitions.  The actual execution still goes through ToolRegistry.execute_tool().
        """
        from pydantic import BaseModel, Field as PydanticField, create_model
        from llama_index.core.tools import FunctionTool

        TYPE_MAP = {
            'string': str, 'str': str,
            'int': int, 'integer': int,
            'float': float, 'number': float,
            'bool': bool, 'boolean': bool,
            'list': list, 'array': list,
            'dict': dict, 'object': dict,
        }

        tools = list(self.tools.values())
        if tool_filter == 'vision':
            VISION_TOOL_NAMES = {
                'agent_mode_start', 'agent_mode_stop', 'agent_task_execute',
                'agent_screen_capture', 'agent_status', 'web_search',
            }
            tools = [t for t in tools if t.name in VISION_TOOL_NAMES
                     or t.name.startswith('agent_')]

        li_tools = []
        for tool in tools:
            # Build a Pydantic model for the parameter schema
            fields = {}
            for pname, param in tool.parameters.items():
                py_type = TYPE_MAP.get(param.type, str)
                if param.required:
                    if param.default is not None:
                        fields[pname] = (py_type, PydanticField(default=param.default, description=param.description))
                    else:
                        fields[pname] = (py_type, PydanticField(description=param.description))
                else:
                    from typing import Optional as Opt
                    if param.default is not None:
                        fields[pname] = (Opt[py_type], PydanticField(default=param.default, description=param.description))
                    else:
                        fields[pname] = (Opt[py_type], PydanticField(default=None, description=param.description))

            schema_model = create_model(f"{tool.name}_Schema", **fields) if fields else None

            # Stub function — actual execution happens via execute_tool()
            def _stub(**kwargs):
                return ""

            li_tool = FunctionTool.from_defaults(
                fn=_stub,
                name=tool.name,
                description=tool.description,
                fn_schema=schema_model,
            )
            li_tools.append(li_tool)

        return li_tools

    def as_ollama_tools(self, tool_names: Optional[List[str]] = None,
                        tool_filter: str = None) -> List[Dict[str, Any]]:
        """Convert registered tools to Ollama's native ``tools=[...]`` schema.

        Returns a list of ``{"type": "function", "function": {...}}`` dicts in
        the shape Ollama's chat API expects for native function calling.  This
        is a PARALLEL emitter to ``as_llama_index_tools`` — it reuses the same
        ``ToolParameter`` metadata (name/type/required/description) without
        altering the existing LlamaIndex mapping.

        Args:
            tool_names: Optional explicit allow-list of tool names to emit
                (e.g. the in-scope ``selected_tools`` for a chat turn).  When
                provided, only those tools are emitted (preserving order).
            tool_filter: Optional 'vision' filter, mirroring the other emitters.

        The execution path is unchanged: the model returns structured
        ``tool_calls`` referencing these names, and the caller routes them
        through ``execute_tool()`` exactly like the XML path.
        """
        # JSON-Schema type strings (Ollama forwards these to the model's
        # function-calling template, which expects standard JSON-schema types).
        JSON_TYPE_MAP = {
            'string': 'string', 'str': 'string',
            'int': 'integer', 'integer': 'integer',
            'float': 'number', 'number': 'number',
            'bool': 'boolean', 'boolean': 'boolean',
            'list': 'array', 'array': 'array',
            'dict': 'object', 'object': 'object',
        }

        if tool_names is not None:
            tools = [self.tools[n] for n in tool_names if n in self.tools]
        else:
            tools = list(self.tools.values())

        if tool_filter == 'vision':
            VISION_TOOL_NAMES = {
                'agent_mode_start', 'agent_mode_stop', 'agent_task_execute',
                'agent_screen_capture', 'agent_status', 'web_search',
            }
            tools = [t for t in tools if t.name in VISION_TOOL_NAMES
                     or t.name.startswith('agent_')]

        ollama_tools = []
        for tool in tools:
            properties = {}
            required = []
            for pname, param in tool.parameters.items():
                json_type = JSON_TYPE_MAP.get(param.type, 'string')
                prop: Dict[str, Any] = {
                    'type': json_type,
                    'description': param.description or '',
                }
                properties[pname] = prop
                if param.required:
                    required.append(pname)

            ollama_tools.append({
                'type': 'function',
                'function': {
                    'name': tool.name,
                    'description': tool.description,
                    'parameters': {
                        'type': 'object',
                        'properties': properties,
                        'required': required,
                    },
                },
            })

        return ollama_tools

    def _coerce_params(self, tool: BaseTool, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Robustly coerce values to types defined in tool parameters."""
        coerced = {}
        for name, value in kwargs.items():
            if name == "_agent_context":
                coerced[name] = value
                continue
                
            param_def = tool.parameters.get(name)
            if not param_def:
                coerced[name] = value
                continue

            target_type = param_def.type.lower()
            if value is None:
                coerced[name] = None
                continue

            try:
                if target_type in ("int", "integer"):
                    coerced[name] = int(value)
                elif target_type in ("float", "number"):
                    coerced[name] = float(value)
                elif target_type in ("bool", "boolean"):
                    if isinstance(value, bool):
                        coerced[name] = value
                    else:
                        v_str = str(value).lower().strip()
                        coerced[name] = v_str in ("true", "yes", "1", "on", "t")
                elif target_type in ("dict", "object"):
                    if isinstance(value, dict):
                        coerced[name] = value
                    else:
                        coerced[name] = json.loads(value)
                elif target_type in ("list", "array"):
                    coerced[name] = _coerce_list_param(value)
                else:
                    coerced[name] = str(value)
            except (ValueError, TypeError, Exception) as e:
                logger.warning(f"Failed to coerce {name}={value} to {target_type}: {e}")
                coerced[name] = value
        
        # Fill in defaults for missing non-required params
        for name, param in tool.parameters.items():
            if name not in coerced and not param.required and param.default is not None:
                coerced[name] = param.default
                
        return coerced

    @staticmethod
    def _recover_literal_param_value(
        tool_name: str,
        param_name: str,
        agent_context: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Recover a real value when the model copied a schema param name literally."""
        if not agent_context:
            return None
        direct_params = agent_context.get("direct_tool_params") or {}
        if isinstance(direct_params, dict) and direct_params.get(param_name):
            return str(direct_params[param_name])
        if tool_name == "generate_image" and param_name == "prompt":
            pending = agent_context.get("pending_image_prompt")
            if pending:
                return str(pending)
            user_msg = agent_context.get("user_message") or agent_context.get("message")
            if user_msg:
                return str(user_msg)
        return None

    def execute_tool(self, tool_name: str, /, agent_context: Optional[Dict[str, Any]] = None,
                     on_output: Optional[Callable[[str], None]] = None, **kwargs) -> ToolResult:
        """
        Execute a tool by name with given parameters and context

        Args:
            tool_name: Name of the tool to execute (positional-only — the `/`
                makes it impossible for an LLM-supplied kwarg named `tool_name`
                to collide with this param. If the LLM passes `tool_name=...`
                in its args, it lands in **kwargs and the param-recovery
                logic below remaps it to the tool's actual schema name. The
                /` is what unblocks tools like mcp_execute whose own
                parameters can shadow registry-internal names.
            agent_context: Optional context dictionary (user, project, etc.)
            on_output: Optional callback for streaming tool output
            **kwargs: Tool parameters

        Returns:
            ToolResult with execution results
        """
        tool = self.get_tool(tool_name)
        
        if not tool:
            logger.error(f"Tool not found: {tool_name}")
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not found in registry"
            )
        
        # 1. Parameter recovery: fix common LLM mistakes before validation.
        expected_all = list(tool.parameters.keys())
        expected_required = [name for name, param in tool.parameters.items() if param.required]
        received_keys = set(kwargs.keys()) - {"_agent_context"}

        # Strategy 1: param_name=X, value=Y → X=Y
        if "param_name" in received_keys and "value" in received_keys:
            pname_val = kwargs.pop("param_name")
            pval = kwargs.pop("value")
            kwargs[pname_val] = pval
            received_keys = set(kwargs.keys()) - {"_agent_context"}
            logger.info(f"Param recovery: remapped param_name={pname_val} to direct kwarg")

        # Strategy 1b: param_name alone (placeholder copy) — do not use schema name as value
        if "param_name" in received_keys:
            pname_val = kwargs.pop("param_name")
            received_keys = set(kwargs.keys()) - {"_agent_context"}
            if pname_val in expected_all:
                logger.warning(
                    "Param recovery: rejected placeholder param_name=%r without value",
                    pname_val,
                )
            else:
                kwargs[pname_val] = ""
                received_keys.add(pname_val)

        # Strategy 2: Fuzzy matching for unrecognized params
        missing_required = set(expected_required) - received_keys
        unknown_params = received_keys - set(expected_all)

        if unknown_params and (missing_required or len(received_keys) < len(expected_all)):
            for unknown in list(unknown_params):
                matches = difflib.get_close_matches(unknown, expected_all, n=1, cutoff=0.6)
                if matches:
                    matched_name = matches[0]
                    if matched_name not in received_keys:
                        kwargs[matched_name] = kwargs.pop(unknown)
                        received_keys.remove(unknown)
                        received_keys.add(matched_name)
                        logger.info(f"Param recovery: fuzzy matched '{unknown}' → '{matched_name}'")

        # Strategy 3: Map remaining unknown params to missing required params (heuristic fallback)
        missing_required = set(expected_required) - received_keys
        unknown_params = received_keys - set(expected_all)
        if len(unknown_params) == 1 and len(missing_required) == 1:
            wrong_name = unknown_params.pop()
            right_name = missing_required.pop()
            wrong_val = kwargs.get(wrong_name)
            if isinstance(wrong_val, str) and wrong_val.strip().lower() == right_name.lower():
                kwargs.pop(wrong_name, None)
                recovered = self._recover_literal_param_value(
                    tool_name, right_name, agent_context,
                )
                if recovered is not None:
                    kwargs[right_name] = recovered
                    logger.info(
                        "Param recovery: replaced literal %r with context for %s",
                        wrong_val, right_name,
                    )
                else:
                    logger.warning(
                        "Param recovery: dropped literal placeholder %s=%r",
                        wrong_name, wrong_val,
                    )
            else:
                kwargs[right_name] = kwargs.pop(wrong_name)
                logger.info(f"Param recovery: mapped unknown '{wrong_name}' to missing required '{right_name}'")

        # Strategy 4: Handle comma-separated coords/lists
        missing_required = set(expected_required) - received_keys
        unknown_params = received_keys - set(expected_all)
        if len(unknown_params) == 1 and len(missing_required) > 1:
            wrong_name = list(unknown_params)[0]
            val = kwargs.get(wrong_name, "")
            if isinstance(val, str) and "," in val:
                parts = [p.strip() for p in val.split(",")]
                missing_sorted = sorted(missing_required)
                if len(parts) == len(missing_sorted):
                    for pname, pval in zip(missing_sorted, parts):
                        kwargs[pname] = pval
                    kwargs.pop(wrong_name)
                    received_keys = set(kwargs.keys()) - {"_agent_context"}
                    logger.info(f"Param recovery: split '{wrong_name}' → {missing_sorted}")

        # Strategy 5: fill missing required params from agent_context (slash / retry / user message)
        missing_required = set(expected_required) - (set(kwargs.keys()) - {"_agent_context"})
        for pname in list(missing_required):
            recovered = self._recover_literal_param_value(tool_name, pname, agent_context)
            if recovered is not None:
                kwargs[pname] = recovered
                logger.info("Param recovery: filled %s from agent_context", pname)

        # 2. Type Coercion
        kwargs = self._coerce_params(tool, kwargs)

        if not tool.can_execute(**kwargs):
            # Get expected parameters for better error message
            expected_params = [name for name, param in tool.parameters.items() if param.required]
            received_params = [k for k in kwargs.keys() if k != "_agent_context"]
            logger.error(
                f"Tool {tool_name} validation failed. "
                f"Expected required parameters: {expected_params}, "
                f"Received: {received_params}"
            )
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' validation failed - missing required parameters: {expected_params}. Received: {received_params}"
            )

        schema_error = tool_argument_error(tool, kwargs)
        if schema_error:
            if strict_tool_args_enabled():
                logger.warning(f"Tool {tool_name} refused: invalid arguments: {schema_error}")
                return ToolResult(
                    success=False,
                    error=(
                        f"Tool '{tool_name}' was not run: invalid arguments: {schema_error}. "
                        f"Call it again with arguments that match its parameters: "
                        f"{describe_tool_parameters(tool)}"
                    ),
                    metadata={"invalid_arguments": schema_error},
                )
            # Off by default: these calls ran before schema checks existed, so
            # report what strict mode would refuse without changing the outcome.
            logger.info(f"Tool {tool_name} arguments fail its schema (not enforced): {schema_error}")

        try:
            logger.info(f"Executing tool: {tool_name}")
            
            # Inject context if available
            if agent_context:
                tool.set_context(agent_context)
                
            # Pass agent_context as a dedicated kwarg — tools opt in by
            # accepting **kwargs or an explicit _agent_context parameter
            if agent_context:
                try:
                    sig = inspect.signature(tool.execute)
                    accepts_context = any(
                        p.kind is inspect.Parameter.VAR_KEYWORD
                        or p.name == "_agent_context"
                        for p in sig.parameters.values()
                    )
                except (TypeError, ValueError):
                    accepts_context = True
                if accepts_context:
                    kwargs["_agent_context"] = agent_context
            
            # Handle generator (streaming) tools vs normal tools
            result_obj = tool.execute(**kwargs)
            
            if isinstance(result_obj, types.GeneratorType):
                # Streaming tool: iterate through yields and call on_output callback
                final_result = None
                accumulated_output = []
                for chunk in result_obj:
                    if isinstance(chunk, str):
                        accumulated_output.append(chunk)
                        if on_output:
                            on_output(chunk)
                    elif isinstance(chunk, ToolResult):
                        # The last item yielded should be the final ToolResult
                        final_result = chunk
                        break
                
                # Fallback if no final ToolResult was yielded
                if not final_result:
                    final_result = ToolResult(success=True, output="".join(accumulated_output))
                elif not final_result.output and accumulated_output:
                    # If result doesn't have output but we accumulated some, attach it
                    final_result.output = "".join(accumulated_output)
                
                logger.info(f"Tool {tool_name} (streaming) finished successfully: {final_result.success}")
                return final_result
            else:
                # Normal tool: return ToolResult directly
                logger.info(f"Tool {tool_name} executed successfully: {result_obj.success}")
                return result_obj

        except Exception as e:
            logger.error(f"Tool {tool_name} execution failed: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"Tool execution failed: {str(e)}"
            )
    
    def __len__(self) -> int:
        """Return number of registered tools"""
        return len(self.tools)
    
    def __repr__(self) -> str:
        return f"<ToolRegistry: {len(self.tools)} tools registered>"


def _heuristic_coerce(v: str) -> Any:
    low = v.lower().strip()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("none", "null"):
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


# Gemma 4's chat template quotes a string argument with this token. In a text
# tool call the model sometimes writes it into the value
# ([query]... product profile<|"|>), and the search then ran on it.
_TEMPLATE_QUOTE = '<|"|>'


def _strip_template_quotes(value: Any) -> Any:
    if isinstance(value, str) and _TEMPLATE_QUOTE in value:
        return value.replace(_TEMPLATE_QUOTE, "").strip()
    return value


def coerce_params_to_schema(params: Dict[str, Any], tool: Optional[BaseTool]) -> Dict[str, Any]:
    """Coerce LLM-produced parameter values to the tool's declared types.

    Text-based tool-call formats deliver every value as a string. Values are
    converted only as their declared ``ToolParameter.type`` says: a ``string``
    parameter such as ``query="2024"`` stays a string, ``dict``/``list``
    parameters are JSON-decoded (a bare string becomes a one-item list), and
    parameters the tool does not declare fall back to the old heuristics.
    """
    import json

    if not params:
        return {}
    params = {key: _strip_template_quotes(value) for key, value in params.items()}
    aliases = {"integer": "int", "boolean": "bool", "number": "float", "str": "string"}
    schema = ({name: aliases.get(p.type, p.type) for name, p in (tool.parameters or {}).items()}
              if tool else {})
    out: Dict[str, Any] = {}
    for key, value in params.items():
        declared = schema.get(key)
        if not isinstance(value, str):
            if declared == "list" and value is not None and not isinstance(value, (list, tuple)):
                value = [value]
            out[key] = value
            continue
        text = value.strip()
        low = text.lower()
        if declared == "string":
            out[key] = value
        elif declared == "bool":
            out[key] = low in ("true", "yes", "1", "on")
        elif declared == "int":
            try:
                out[key] = int(text)
            except ValueError:
                try:
                    out[key] = int(float(text))
                except ValueError:
                    out[key] = value
        elif declared == "float":
            try:
                out[key] = float(text)
            except ValueError:
                out[key] = value
        elif declared in ("dict", "list"):
            decoded = None
            if text[:1] in ("{", "["):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    decoded = None
            if decoded is not None:
                out[key] = decoded
            elif low in ("", "none", "null"):
                out[key] = None
            elif declared == "list":
                out[key] = [value]
            else:
                out[key] = value
        elif declared is None and schema:
            out[key] = value  # unknown to a tool with a schema: pass through untouched
        else:
            out[key] = _heuristic_coerce(value)
    return out


def consent_reference(tool, params: Dict[str, Any]) -> Optional[str]:
    """The reference image a consent-gated tool would use, or None."""
    if not getattr(tool, "consent_gate", False):
        return None
    ref = (params or {}).get("image") or ""
    return ref or None


# Chat tool calls are checked against the same JSON Schema an MCP client sees
# (backend/mcp/tools_adapter.py). Enforced only when this is on: a stricter
# check refuses some calls (out-of-range numbers, values outside an enum) that
# run today after type coercion.
STRICT_TOOL_ARGS_ENV = "GUAARDVARK_STRICT_TOOL_ARGS"


def strict_tool_args_enabled() -> bool:
    return os.environ.get(STRICT_TOOL_ARGS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def tool_argument_error(tool, arguments: Dict[str, Any]) -> Optional[str]:
    """The schema violation in ``arguments`` for ``tool``, or None.

    Uses the MCP server's schema builder and validator, so chat and MCP judge a
    call the same way. Keys starting with ``_`` are the backend's own context,
    not arguments. A tool that publishes its own ``input_schema`` (an MCP proxy)
    is checked against that. None when the validator is unavailable.
    """
    try:
        from jsonschema import Draft202012Validator
        from backend.mcp.tools_adapter import _argument_error, _tool_input_schema
    except Exception as exc:  # noqa: BLE001 - jsonschema / mcp missing: no check
        logger.debug("tool argument schema check unavailable: %s", exc)
        return None
    schema = getattr(tool, "input_schema", None) or _tool_input_schema(tool)
    try:
        validator = Draft202012Validator(schema)
    except Exception as exc:  # noqa: BLE001 - a bad schema must not block the tool
        logger.warning("no argument validation for %s: %s", getattr(tool, "name", "?"), exc)
        return None
    present = {k: v for k, v in arguments.items() if not str(k).startswith("_")}
    return _argument_error(validator, present)


def describe_tool_parameters(tool) -> str:
    """One line naming each parameter, its type and whether it is required."""
    parts = []
    for name, param in (getattr(tool, "parameters", None) or {}).items():
        bits = [param.type or "string", "required" if param.required else "optional"]
        if getattr(param, "enum", None):
            bits.append("one of " + ", ".join(map(str, param.enum)))
        if getattr(param, "minimum", None) is not None:
            bits.append(f">= {param.minimum}")
        if getattr(param, "maximum", None) is not None:
            bits.append(f"<= {param.maximum}")
        parts.append(f"{name} ({', '.join(bits)})")
    return "; ".join(parts) or "none"


def tool_needs_approval_card(tool, tool_name: str, params: Dict[str, Any], preapproved: set) -> bool:
    """Whether this call must pause for a person's approval card.

    Chat shows the card; a caller with no card to show refuses the call.
    A consent-gated tool whose reference image already has a consent record
    does not ask again: the record is the durable answer.
    """
    if not tool or not getattr(tool, "requires_approval", False) or tool_name in preapproved:
        return False
    # A tool can narrow its own approval to the calls that need it, e.g.
    # find_files inside Guaardvark's folders. Unsure means ask.
    per_call = getattr(tool, "needs_approval", None)
    if callable(per_call):
        try:
            if not per_call(params or {}):
                return False
        except Exception:
            logger.debug("needs_approval check failed for %s; asking", tool_name, exc_info=True)
    if getattr(tool, "consent_gate", False):
        ref = consent_reference(tool, params)
        if ref:
            from backend.services.consent_records import has_consent
            if has_consent(ref):
                return False
    return True


# Global registry instance (like PYDANTIC_MODELS pattern)
_global_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry instance"""
    global _global_tool_registry
    if _global_tool_registry is None:
        _global_tool_registry = ToolRegistry()
    return _global_tool_registry


# Convenience function
def is_plan_research_tool(tool: Optional[BaseTool]) -> bool:
    """True when plan mode may run ``tool`` before a plan is approved.

    An undeclared tool counts as an action, so a tool nobody classified can
    never run during research.
    """
    if tool is None:
        return False
    declared = getattr(tool, "plan_research", None)
    if declared is not None:
        return bool(declared)
    return getattr(tool, "read_only", None) is True


def register_tool(tool: BaseTool):
    """Register a tool in the global registry"""
    registry = get_tool_registry()
    registry.register(tool)

