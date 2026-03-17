#!/usr/bin/env python3

import asyncio
import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MCP_ENABLED = os.getenv("GUAARDVARK_MCP_ENABLED", "true").lower() == "true"
MCP_TIMEOUT = int(os.getenv("GUAARDVARK_MCP_TIMEOUT", "30"))

DEFAULT_MCP_SERVERS = {
}


@dataclass
class MCPServer:
    name: str
    command: List[str]
    args: Dict[str, Any] = field(default_factory=dict)
    connected: bool = False
    process: Any = None
    tools: List[Dict[str, Any]] = field(default_factory=list)
    resources: List[Dict[str, Any]] = field(default_factory=list)
    last_connected: Optional[datetime] = None
    error: Optional[str] = None


@dataclass
class MCPState:
    initialized: bool = False
    servers_configured: int = 0
    servers_connected: int = 0
    total_tools_available: int = 0
    total_calls: int = 0
    errors: List[str] = field(default_factory=list)


class MCPClientService:
    
    _instance: Optional["MCPClientService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    @classmethod
    def get_instance(cls) -> "MCPClientService":
        return cls()
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._state = MCPState()
        self._servers: Dict[str, MCPServer] = {}
        self._server_configs: Dict[str, Dict] = {}
        self._async_lock = None
        
        self._load_server_configs()
        
        logger.info("MCPClientService initialized")
        logger.info(f"MCP enabled: {MCP_ENABLED}")
        logger.info(f"Configured servers: {list(self._server_configs.keys())}")
    
    def _load_server_configs(self):
        self._server_configs = dict(DEFAULT_MCP_SERVERS)
        
        env_config = os.getenv("GUAARDVARK_MCP_SERVERS")
        if env_config:
            try:
                custom_servers = json.loads(env_config)
                self._server_configs.update(custom_servers)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse GUAARDVARK_MCP_SERVERS: {e}")
        
        config_file = os.path.join(
            os.environ.get("GUAARDVARK_ROOT", "."),
            "data",
            "config",
            "mcp_servers.json"
        )
        if os.path.exists(config_file):
            try:
                with open(config_file) as f:
                    file_servers = json.load(f)
                self._server_configs.update(file_servers)
                logger.info(f"Loaded MCP servers from {config_file}")
            except Exception as e:
                logger.error(f"Failed to load MCP config file: {e}")
        
        self._state.servers_configured = len(self._server_configs)
    
    async def _ensure_async_lock(self):
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock
    
    async def _send_jsonrpc(
        self,
        process: subprocess.Popen,
        method: str,
        params: Optional[Dict] = None,
        id: int = 1
    ) -> Dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": id
        }
        if params:
            request["params"] = params
        
        request_str = json.dumps(request) + "\n"
        
        try:
            process.stdin.write(request_str.encode())
            process.stdin.flush()
            
            response_line = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    process.stdout.readline
                ),
                timeout=MCP_TIMEOUT
            )
            
            if response_line:
                return json.loads(response_line.decode())
            else:
                return {"error": {"message": "No response from server"}}
                
        except asyncio.TimeoutError:
            return {"error": {"message": f"Request timed out after {MCP_TIMEOUT}s"}}
        except Exception as e:
            return {"error": {"message": str(e)}}
    
    async def connect_server(self, server_name: str) -> Dict[str, Any]:
        if not MCP_ENABLED:
            return {"success": False, "error": "MCP is disabled"}
        
        if server_name not in self._server_configs:
            return {
                "success": False,
                "error": f"Unknown server: {server_name}. Available: {list(self._server_configs.keys())}"
            }
        
        if server_name in self._servers and self._servers[server_name].connected:
            return {
                "success": True,
                "server": server_name,
                "message": "Already connected",
                "tools": len(self._servers[server_name].tools)
            }
        
        config = self._server_configs[server_name]
        command = config.get("command", [])
        
        if not command:
            return {"success": False, "error": f"No command configured for {server_name}"}
        
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **config.get("env", {})}
            )
            
            init_response = await self._send_jsonrpc(
                process,
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "guaardvark-mcp-client",
                        "version": "1.0.0"
                    }
                }
            )
            
            if "error" in init_response:
                process.terminate()
                return {
                    "success": False,
                    "error": f"Initialize failed: {init_response['error'].get('message')}"
                }
            
            await self._send_jsonrpc(process, "notifications/initialized")
            
            tools_response = await self._send_jsonrpc(process, "tools/list")
            tools = tools_response.get("result", {}).get("tools", [])
            
            resources_response = await self._send_jsonrpc(process, "resources/list")
            resources = resources_response.get("result", {}).get("resources", [])
            
            server = MCPServer(
                name=server_name,
                command=command,
                args=config.get("args", {}),
                connected=True,
                process=process,
                tools=tools,
                resources=resources,
                last_connected=datetime.now()
            )
            self._servers[server_name] = server
            
            self._state.servers_connected = sum(1 for s in self._servers.values() if s.connected)
            self._state.total_tools_available = sum(len(s.tools) for s in self._servers.values())
            
            logger.info(f"Connected to MCP server '{server_name}' with {len(tools)} tools")
            
            return {
                "success": True,
                "server": server_name,
                "tools": len(tools),
                "resources": len(resources),
                "tool_names": [t.get("name") for t in tools]
            }
            
        except FileNotFoundError:
            error = f"Command not found: {command[0]}"
            logger.error(error)
            return {"success": False, "error": error}
        except Exception as e:
            error = f"Failed to connect: {str(e)}"
            logger.error(error)
            return {"success": False, "error": error}
    
    async def disconnect_server(self, server_name: str) -> Dict[str, Any]:
        if server_name not in self._servers:
            return {"success": False, "error": f"Not connected to {server_name}"}
        
        server = self._servers[server_name]
        
        try:
            if server.process:
                server.process.terminate()
                server.process.wait(timeout=5)
        except Exception:
            pass
        
        server.connected = False
        server.process = None
        
        self._state.servers_connected = sum(1 for s in self._servers.values() if s.connected)
        self._state.total_tools_available = sum(len(s.tools) for s in self._servers.values() if s.connected)
        
        logger.info(f"Disconnected from MCP server '{server_name}'")
        
        return {"success": True, "server": server_name}
    
    async def list_tools(self, server_name: Optional[str] = None) -> Dict[str, Any]:
        if not MCP_ENABLED:
            return {"success": False, "error": "MCP is disabled"}
        
        if server_name:
            if server_name not in self._servers:
                return {"success": False, "error": f"Not connected to {server_name}"}
            
            server = self._servers[server_name]
            return {
                "success": True,
                "server": server_name,
                "tools": server.tools,
                "count": len(server.tools)
            }
        
        all_tools = {}
        for name, server in self._servers.items():
            if server.connected:
                all_tools[name] = server.tools
        
        return {
            "success": True,
            "servers": list(all_tools.keys()),
            "tools": all_tools,
            "total_count": sum(len(t) for t in all_tools.values())
        }
    
    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if not MCP_ENABLED:
            return {"success": False, "error": "MCP is disabled"}
        
        if server_name not in self._servers:
            return {"success": False, "error": f"Not connected to {server_name}"}
        
        server = self._servers[server_name]
        
        if not server.connected or not server.process:
            return {"success": False, "error": f"Server {server_name} is not connected"}
        
        tool_names = [t.get("name") for t in server.tools]
        if tool_name not in tool_names:
            return {
                "success": False,
                "error": f"Unknown tool '{tool_name}' on {server_name}. Available: {tool_names}"
            }
        
        try:
            response = await self._send_jsonrpc(
                server.process,
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments or {}
                }
            )
            
            self._state.total_calls += 1
            
            if "error" in response:
                return {
                    "success": False,
                    "error": response["error"].get("message", "Tool call failed")
                }
            
            result = response.get("result", {})
            
            return {
                "success": True,
                "server": server_name,
                "tool": tool_name,
                "result": result
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def list_configured_servers(self) -> Dict[str, Any]:
        servers = []
        for name, config in self._server_configs.items():
            connected = name in self._servers and self._servers[name].connected
            tool_count = len(self._servers[name].tools) if name in self._servers else 0
            
            servers.append({
                "name": name,
                "command": config.get("command", []),
                "connected": connected,
                "tool_count": tool_count
            })
        
        return {
            "success": True,
            "servers": servers,
            "total": len(servers)
        }
    
    def get_state(self) -> Dict[str, Any]:
        return {
            "mcp_enabled": MCP_ENABLED,
            "initialized": self._state.initialized,
            "servers_configured": self._state.servers_configured,
            "servers_connected": self._state.servers_connected,
            "total_tools_available": self._state.total_tools_available,
            "total_calls": self._state.total_calls,
            "connected_servers": [
                name for name, s in self._servers.items() if s.connected
            ],
            "errors": self._state.errors[-10:] if self._state.errors else []
        }
    
    async def shutdown(self):
        logger.info("Shutting down MCPClientService")
        
        for server_name in list(self._servers.keys()):
            await self.disconnect_server(server_name)
        
        self._state = MCPState()
        logger.info("MCPClientService shutdown complete")


def get_mcp_service() -> MCPClientService:
    return MCPClientService.get_instance()


def run_mcp_async(coro):
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=MCP_TIMEOUT + 5)
    except RuntimeError:
        return asyncio.run(coro)
