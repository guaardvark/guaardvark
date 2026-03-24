#!/usr/bin/env python3
"""
Agent Tools System - Base Infrastructure
Provides tool definition, registry, and execution patterns for agent capabilities
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Callable, Optional, List
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
    
    def get_tool_schemas(self, format: str = 'xml') -> str:
        """
        Generate tool schemas for LLM prompt

        Args:
            format: 'xml', 'json', or 'json_prompt'
        """
        if format == 'xml':
            schemas = []
            for tool in self.tools.values():
                schemas.append(tool.get_schema())
            return "\n\n".join(schemas)
        elif format == 'json':
            import json
            schemas = [tool.get_json_schema() for tool in self.tools.values()]
            return json.dumps(schemas, indent=2)
        elif format == 'json_prompt':
            lines = []
            for tool in self.tools.values():
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
    
    def execute_tool(self, tool_name: str, agent_context: Optional[Dict[str, Any]] = None, **kwargs) -> ToolResult:
        """
        Execute a tool by name with given parameters and context
        
        Args:
            tool_name: Name of the tool to execute
            agent_context: Optional context dictionary (user, project, etc.)
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
        
        # Parameter recovery: fix common LLM mistakes before validation.
        # 1. If LLM sent param_name/value pairs instead of direct params, remap.
        # 2. If LLM sent synonyms (content_description, image_description, text, etc.), map to first required param.
        if not tool.can_execute(**kwargs) and kwargs:
            expected_required = [name for name, param in tool.parameters.items() if param.required]
            received = set(kwargs.keys()) - {"_agent_context"}

            # Strategy 1: param_name=X, value=Y → X=Y
            if "param_name" in received and "value" in received:
                pname_val = kwargs.pop("param_name")
                pval = kwargs.pop("value")
                kwargs[pname_val] = pval
                logger.info(f"Param recovery: remapped param_name={pname_val} to direct kwarg")

            # Strategy 2: Unrecognised param(s) → map to missing required param(s)
            if not tool.can_execute(**kwargs) and expected_required:
                known_params = set(tool.parameters.keys())
                current_keys = set(kwargs.keys()) - {"_agent_context"}
                unknown = current_keys - known_params
                missing = set(expected_required) - current_keys

                if len(unknown) == 1 and len(missing) == 1:
                    # Simple 1:1 remap
                    wrong_name = unknown.pop()
                    right_name = missing.pop()
                    kwargs[right_name] = kwargs.pop(wrong_name)
                    logger.info(f"Param recovery: remapped '{wrong_name}' → '{right_name}' for {tool_name}")
                elif len(missing) == 1 and len(unknown) > 1:
                    # Multiple unknown params, one missing required — pick the best match.
                    # Prefer params whose name contains "description", "prompt", "content", "text", "query".
                    right_name = missing.pop()
                    _hint_words = ("description", "prompt", "content", "text", "query", "value")
                    best = next((u for u in unknown if any(h in u.lower() for h in _hint_words)), None)
                    if best:
                        kwargs[right_name] = kwargs.pop(best)
                        # Drop remaining unknown params (LLM hallucinated extras)
                        for leftover in unknown - {best}:
                            kwargs.pop(leftover, None)
                        logger.info(f"Param recovery: remapped '{best}' → '{right_name}', dropped extras for {tool_name}")

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
            result = tool.execute(**kwargs)
            logger.info(f"Tool {tool_name} executed successfully: {result.success}")
            return result
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

