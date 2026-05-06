#!/usr/bin/env python3
"""
Agent Tools System - Base Infrastructure
Provides tool definition, registry, and execution patterns for agent capabilities
"""

import logging
import difflib
import types
from dataclasses import dataclass, field
from typing import Dict, Any, Callable, Optional, List, Union, Generator
from enum import Enum

logger = logging.getLogger(__name__)


@dataclass
class ToolParameter:
    """Tool parameter definition (follows FileMetadata pattern)"""
    name: str
    type: str  # 'string', 'int', 'bool', 'float', 'list', 'dict'
    required: bool = True
    description: str = ""
    default: Optional[Any] = None


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
    requires_confirmation: bool = False
    required_context: List[str] = field(default_factory=list)  # e.g., ['project_id', 'user_id']
    
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
        logger.info("Tool registry initialized")
    
    def register(self, tool: BaseTool):
        """Register a tool in the registry"""
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Can only register BaseTool instances, got {type(tool)}")
        
        if tool.name in self.tools:
            logger.warning(f"Tool '{tool.name}' already registered, replacing...")
        
        self.tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")
    
    def unregister(self, tool_name: str):
        """Unregister a tool"""
        if tool_name in self.tools:
            del self.tools[tool_name]
            logger.info(f"Unregistered tool: {tool_name}")
    
    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name"""
        return self.tools.get(name)
    
    def list_tools(self) -> List[str]:
        """Get list of all registered tool names"""
        return list(self.tools.keys())
    
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
                elif target_type in ("dict", "object", "list", "array"):
                    if isinstance(value, (dict, list)):
                        coerced[name] = value
                    else:
                        import json
                        coerced[name] = json.loads(value)
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

        # Strategy 2: Fuzzy matching for unrecognized params
        missing_required = set(expected_required) - received_keys
        unknown_params = received_keys - set(expected_all)
        
        if unknown_params and (missing_required or len(received_keys) < len(expected_all)):
            for unknown in list(unknown_params):
                # Try to find a close match in all expected parameters (including optional)
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
            kwargs[right_name] = kwargs.pop(wrong_name)
            received_keys = set(kwargs.keys()) - {"_agent_context"}
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
        
        try:
            logger.info(f"Executing tool: {tool_name}")
            
            # Inject context if available
            if agent_context:
                tool.set_context(agent_context)
                
            # Pass agent_context as a dedicated kwarg — tools opt in to reading it
            if agent_context:
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


# Global registry instance (like PYDANTIC_MODELS pattern)
_global_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Get the global tool registry instance"""
    global _global_tool_registry
    if _global_tool_registry is None:
        _global_tool_registry = ToolRegistry()
    return _global_tool_registry


# Convenience function
def register_tool(tool: BaseTool):
    """Register a tool in the global registry"""
    registry = get_tool_registry()
    registry.register(tool)

