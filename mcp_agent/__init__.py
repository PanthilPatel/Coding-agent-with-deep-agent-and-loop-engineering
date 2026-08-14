"""MCP (Model Context Protocol) agent package.

Provides a client and registry for connecting to external MCP servers over
stdio transport, discovering their tools, and integrating them into the
agent's tool set.  All connection failures degrade gracefully — the agent
continues without MCP tools rather than crashing at startup.

Main public API:
  - ``MCPRegistry``      — manages multiple server connections lifecycle.
  - ``MCPClient``        — manages one server connection (stdio transport).
  - ``load_mcp_config``  — parses and validates the mcp.json config file.
"""

from mcp_agent.config_schema import load_mcp_config
from mcp_agent.client import MCPClient
from mcp_agent.registry import MCPRegistry

__all__ = ["load_mcp_config", "MCPClient", "MCPRegistry"]
