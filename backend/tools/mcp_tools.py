#!/usr/bin/env python3

import logging
from typing import Any, Dict, List, Optional

from backend.services.agent_tools import BaseTool, ToolParameter, ToolResult
from backend.services.mcp_client_service import (
    get_mcp_service,
    run_mcp_async,
    MCP_ENABLED
)

logger = logging.getLogger(__name__)


class MCPListServersTool(BaseTool):
    
    name = "mcp_list_servers"
    description = "List all configured MCP servers and their connection status."
    parameters = {}
    
    def execute(self, **kwargs) -> ToolResult:
        if not MCP_ENABLED:
            return ToolResult(
                success=False,
                error="MCP is disabled. Set GUAARDVARK_MCP_ENABLED=true to enable."
            )
        
        try:
            service = get_mcp_service()
            result = service.list_configured_servers()
            
            if result.get("success"):
                servers = result.get("servers", [])
                connected = sum(1 for s in servers if s.get("connected"))
                
                output_lines = [f"MCP Servers: {len(servers)} configured, {connected} connected"]
                for server in servers:
                    status = "✓ connected" if server.get("connected") else "○ not connected"
                    tools = f" ({server.get('tool_count')} tools)" if server.get("connected") else ""
                    output_lines.append(f"  - {server['name']}: {status}{tools}")
                
                return ToolResult(
                    success=True,
                    output="\n".join(output_lines),
                    metadata=result
                )
            else:
                return ToolResult(success=False, error=result.get("error"))
                
        except Exception as e:
            logger.error(f"MCP list servers error: {e}")
            return ToolResult(success=False, error=str(e))


class MCPConnectTool(BaseTool):
    
    name = "mcp_connect"
    description = "Connect to a configured MCP server to access its tools."
    parameters = {
        "server": ToolParameter(
            name="server",
            type="string",
            required=True,
            description="Name of the MCP server to connect to"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not MCP_ENABLED:
            return ToolResult(
                success=False,
                error="MCP is disabled. Set GUAARDVARK_MCP_ENABLED=true to enable."
            )
        
        server = kwargs.get("server")
        
        if not server:
            return ToolResult(success=False, error="server name is required")
        
        try:
            service = get_mcp_service()
            result = run_mcp_async(service.connect_server(server))
            
            if result.get("success"):
                tool_count = result.get("tools", 0)
                tool_names = result.get("tool_names", [])
                
                output = f"Connected to '{server}' with {tool_count} tools"
                if tool_names:
                    output += f": {', '.join(tool_names[:5])}"
                    if len(tool_names) > 5:
                        output += f" (and {len(tool_names) - 5} more)"
                
                return ToolResult(
                    success=True,
                    output=output,
                    metadata=result
                )
            else:
                return ToolResult(success=False, error=result.get("error"))
                
        except Exception as e:
            logger.error(f"MCP connect error: {e}")
            return ToolResult(success=False, error=str(e))


class MCPDisconnectTool(BaseTool):
    
    name = "mcp_disconnect"
    description = "Disconnect from a connected MCP server."
    parameters = {
        "server": ToolParameter(
            name="server",
            type="string",
            required=True,
            description="Name of the MCP server to disconnect from"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not MCP_ENABLED:
            return ToolResult(
                success=False,
                error="MCP is disabled. Set GUAARDVARK_MCP_ENABLED=true to enable."
            )
        
        server = kwargs.get("server")
        
        if not server:
            return ToolResult(success=False, error="server name is required")
        
        try:
            service = get_mcp_service()
            result = run_mcp_async(service.disconnect_server(server))
            
            if result.get("success"):
                return ToolResult(
                    success=True,
                    output=f"Disconnected from '{server}'",
                    metadata=result
                )
            else:
                return ToolResult(success=False, error=result.get("error"))
                
        except Exception as e:
            logger.error(f"MCP disconnect error: {e}")
            return ToolResult(success=False, error=str(e))


class MCPListToolsTool(BaseTool):
    
    name = "mcp_list_tools"
    description = "List all tools available from connected MCP servers."
    parameters = {
        "server": ToolParameter(
            name="server",
            type="string",
            required=False,
            description="Optional: specific server to list tools from (lists all if not specified)"
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not MCP_ENABLED:
            return ToolResult(
                success=False,
                error="MCP is disabled. Set GUAARDVARK_MCP_ENABLED=true to enable."
            )
        
        server = kwargs.get("server")
        
        try:
            service = get_mcp_service()
            result = run_mcp_async(service.list_tools(server))
            
            if result.get("success"):
                if server:
                    tools = result.get("tools", [])
                    output_lines = [f"Tools from '{server}': {len(tools)}"]
                    for tool in tools:
                        desc = tool.get("description", "No description")[:60]
                        output_lines.append(f"  - {tool.get('name')}: {desc}")
                else:
                    all_tools = result.get("tools", {})
                    total = result.get("total_count", 0)
                    output_lines = [f"Total MCP tools: {total}"]
                    
                    for srv_name, tools in all_tools.items():
                        output_lines.append(f"\n{srv_name} ({len(tools)} tools):")
                        for tool in tools[:5]:
                            desc = tool.get("description", "No description")[:50]
                            output_lines.append(f"  - {tool.get('name')}: {desc}")
                        if len(tools) > 5:
                            output_lines.append(f"  ... and {len(tools) - 5} more")
                
                return ToolResult(
                    success=True,
                    output="\n".join(output_lines),
                    metadata=result
                )
            else:
                return ToolResult(success=False, error=result.get("error"))
                
        except Exception as e:
            logger.error(f"MCP list tools error: {e}")
            return ToolResult(success=False, error=str(e))


class MCPExecuteTool(BaseTool):
    
    name = "mcp_execute"
    description = "Execute a tool on a connected MCP server. Use mcp_list_tools to see available tools."
    parameters = {
        "server": ToolParameter(
            name="server",
            type="string",
            required=True,
            description="Name of the MCP server"
        ),
        "tool": ToolParameter(
            name="tool",
            type="string",
            required=True,
            description="Name of the tool to execute"
        ),
        "arguments": ToolParameter(
            name="arguments",
            type="dict",
            required=False,
            description="Arguments to pass to the tool",
            default=None
        )
    }
    
    def execute(self, **kwargs) -> ToolResult:
        if not MCP_ENABLED:
            return ToolResult(
                success=False,
                error="MCP is disabled. Set GUAARDVARK_MCP_ENABLED=true to enable."
            )
        
        server = kwargs.get("server")
        tool = kwargs.get("tool")
        arguments = kwargs.get("arguments") or {}
        
        if not server or not tool:
            return ToolResult(success=False, error="server and tool are required")
        
        try:
            service = get_mcp_service()
            result = run_mcp_async(service.call_tool(server, tool, arguments))
            
            if result.get("success"):
                tool_result = result.get("result", {})
                
                if isinstance(tool_result, dict):
                    content = tool_result.get("content", [])
                    if content and isinstance(content, list):
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                                elif item.get("type") == "image":
                                    text_parts.append(f"[Image: {item.get('mimeType', 'image')}]")
                            else:
                                text_parts.append(str(item))
                        output = "\n".join(text_parts)
                    else:
                        output = str(tool_result)
                else:
                    output = str(tool_result)
                
                return ToolResult(
                    success=True,
                    output=f"MCP tool '{server}/{tool}' result:\n{output}",
                    metadata=result
                )
            else:
                return ToolResult(success=False, error=result.get("error"))
                
        except Exception as e:
            logger.error(f"MCP execute error: {e}")
            return ToolResult(success=False, error=str(e))


class MCPGetStateTool(BaseTool):
    
    name = "mcp_get_state"
    description = "Get the current state of the MCP client service."
    parameters = {}
    
    def execute(self, **kwargs) -> ToolResult:
        try:
            service = get_mcp_service()
            state = service.get_state()
            
            output_lines = [
                f"MCP Client State:",
                f"  Enabled: {state.get('mcp_enabled')}",
                f"  Servers configured: {state.get('servers_configured')}",
                f"  Servers connected: {state.get('servers_connected')}",
                f"  Total tools available: {state.get('total_tools_available')}",
                f"  Total calls made: {state.get('total_calls')}"
            ]
            
            connected = state.get("connected_servers", [])
            if connected:
                output_lines.append(f"  Connected to: {', '.join(connected)}")
            
            return ToolResult(
                success=True,
                output="\n".join(output_lines),
                metadata=state
            )
            
        except Exception as e:
            logger.error(f"MCP get state error: {e}")
            return ToolResult(success=False, error=str(e))


__all__ = [
    "MCPListServersTool",
    "MCPConnectTool",
    "MCPDisconnectTool",
    "MCPListToolsTool",
    "MCPExecuteTool",
    "MCPGetStateTool",
]
