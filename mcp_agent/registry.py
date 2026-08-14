import os
import asyncio
from typing import Dict, List, Any, Optional
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.client.session import ClientSession

from mcp_agent.config_schema import load_mcp_config
from mcp_agent.client import MCPClient

class MCPRegistry:
    """Loads MCP server configs, manages the connection lifecycles of all servers,

    discovers/converts tools via langchain-mcp-adapters, prefixes tool names,
    detects collisions, and handles graceful degradation.
    """
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.clients: Dict[str, MCPClient] = {}
        self.tools: List[BaseTool] = []

    async def initialize(self):
        try:
            config = load_mcp_config(self.config_path)
        except Exception as e:
            print(f"[MCP] Failed to load config from {self.config_path}: {e} — continuing without MCP")
            return

        for name, srv_config in config.get("servers", {}).items():
            client = MCPClient(
                name=name,
                command=srv_config["command"],
                args=srv_config.get("args", []),
                env=srv_config.get("env", {})
            )
            try:
                await client.connect()
                self.clients[name] = client
                
                # Discover tools using langchain-mcp-adapters
                server_tools = await load_mcp_tools(
                    client.session,
                    server_name=name,
                    tool_name_prefix=True
                )
                
                print(f"[MCP] Connected: {name} ({len(server_tools)} tools discovered)")
                
                # Verify that no tool names collide across servers or with local tools
                for tool in server_tools:
                    # Check collision
                    collision = any(t.name == tool.name for t in self.tools)
                    if collision:
                        raise ValueError(f"Tool name collision detected: {tool.name}")
                    self.tools.append(tool)
                    
            except Exception as e:
                import traceback
                print(f"[MCP] Server unavailable: {name} — continuing without it. Error: {e}")
                traceback.print_exc()
                await client.disconnect()

    async def close(self):
        for name, client in list(self.clients.items()):
            try:
                await client.disconnect()
            except Exception as e:
                print(f"[MCP] Error disconnecting server {name}: {e}")
        self.clients.clear()
        self.tools.clear()
