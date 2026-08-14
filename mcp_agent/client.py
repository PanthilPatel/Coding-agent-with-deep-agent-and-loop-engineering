import os
import asyncio
from typing import Dict, Any, Optional
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

class MCPClient:
    """Manages the connection/session lifecycle for a single MCP server using stdio transport."""
    def __init__(self, name: str, command: str, args: list, env: Dict[str, str]):
        self.name = name
        self.command = command
        self.args = args
        self.env = env
        self.server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=self.env
        )
        self._client_context = None
        self.session: Optional[ClientSession] = None

    async def connect(self, timeout: float = 10.0):
        print(f"[MCP] Connecting to server: {self.name}")
        self._client_context = stdio_client(self.server_params)
        try:
            read_stream, write_stream = await asyncio.wait_for(
                self._client_context.__aenter__(),
                timeout=timeout
            )
            self.session = ClientSession(read_stream, write_stream)
            await self.session.__aenter__()
            await asyncio.wait_for(self.session.initialize(), timeout=timeout)
        except Exception as e:
            # Clean up if partially initialized
            await self.disconnect()
            raise e

    async def disconnect(self):
        if self.session:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:
                pass
            self.session = None
        if self._client_context:
            try:
                await self._client_context.__aexit__(None, None, None)
            except Exception:
                pass
            self._client_context = None
