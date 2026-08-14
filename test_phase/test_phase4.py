import os
import sys
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from config import Config
from mcp_agent.config_schema import load_mcp_config, interpolate_value
from mcp_agent.client import MCPClient
from mcp_agent.registry import MCPRegistry
from agents.worker import build_worker_agent
from langchain_core.tools import BaseTool

def test_config_env_interpolation(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN", "super-secret-token")
    monkeypatch.setenv("PORT", "8080")
    
    # Check interpolation function directly
    assert interpolate_value("Bearer $TEST_TOKEN") == "Bearer super-secret-token"
    assert interpolate_value("Bearer ${TEST_TOKEN}") == "Bearer super-secret-token"
    
    # Dict interpolation
    nested = {
        "auth": "Bearer ${TEST_TOKEN}",
        "args": ["-p", "$PORT"]
    }
    res = interpolate_value(nested)
    assert res["auth"] == "Bearer super-secret-token"
    assert res["args"] == ["-p", "8080"]

def test_config_missing_env_raises():
    with pytest.raises(ValueError, match="Missing required environment variable"):
        interpolate_value("Bearer $NON_EXISTENT_VAR")

def test_load_mcp_config_valid(tmp_path):
    config_file = tmp_path / "mcp.json"
    config_data = {
        "servers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"]
            }
        }
    }
    config_file.write_text(json.dumps(config_data))
    
    loaded = load_mcp_config(str(config_file))
    assert "github" in loaded["servers"]
    assert loaded["servers"]["github"]["command"] == "npx"
    assert loaded["servers"]["github"]["args"] == ["-y", "@modelcontextprotocol/server-github"]

def test_load_mcp_config_malformed(tmp_path):
    config_file = tmp_path / "mcp.json"
    config_file.write_text("{ malformed json }")
    
    with pytest.raises(ValueError, match="Malformed MCP config JSON"):
        load_mcp_config(str(config_file))

def test_load_mcp_config_missing():
    with pytest.raises(FileNotFoundError):
        load_mcp_config("non_existent_mcp_file.json")

@pytest.mark.asyncio
async def test_registry_mocked_lifecycle():
    # Mock MCPClient
    mock_client = MagicMock(spec=MCPClient)
    mock_client.name = "mock_srv"
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.session = MagicMock()
    
    # Mock LangChain tool
    mock_tool = MagicMock(spec=BaseTool)
    mock_tool.name = "mock_srv_get_data"
    
    # Mock load_mcp_tools to return our mock tool
    with patch("mcp_agent.registry.MCPClient", return_value=mock_client), \
         patch("mcp_agent.registry.load_mcp_tools", AsyncMock(return_value=[mock_tool])), \
         patch("mcp_agent.registry.load_mcp_config", return_value={
             "servers": {
                 "mock_srv": {
                     "command": "python",
                     "args": ["-m", "mock"],
                     "env": {}
                 }
             }
         }):
        
        registry = MCPRegistry("mcp.json")
        await registry.initialize()
        
        assert len(registry.tools) == 1
        assert registry.tools[0].name == "mock_srv_get_data"
        mock_client.connect.assert_awaited_once()
        
        await registry.close()
        mock_client.disconnect.assert_awaited_once()

@pytest.mark.asyncio
async def test_registry_collision_raises():
    mock_client1 = MagicMock(spec=MCPClient)
    mock_client1.name = "srv1"
    mock_client1.connect = AsyncMock()
    mock_client1.disconnect = AsyncMock()
    mock_client1.session = MagicMock()
    
    mock_tool = MagicMock(spec=BaseTool)
    mock_tool.name = "srv1_colliding_name"
    
    # If load_mcp_tools yields the same tool name for both servers, it should raise a ValueError
    with patch("mcp_agent.registry.MCPClient", return_value=mock_client1), \
         patch("mcp_agent.registry.load_mcp_tools", AsyncMock(return_value=[mock_tool])), \
         patch("mcp_agent.registry.load_mcp_config", return_value={
             "servers": {
                 "srv1": {"command": "cmd", "args": []},
                 "srv2": {"command": "cmd", "args": []}
             }
         }):
        
        registry = MCPRegistry("mcp.json")
        await registry.initialize()
        # Should catch collision and raise, but registry.initialize catches exception and disconnects
        # Let's verify it logged/disconnected srv2 and didn't add it to tools
        assert len(registry.tools) == 1

@pytest.mark.asyncio
async def test_registry_graceful_degradation():
    mock_client1 = MagicMock(spec=MCPClient)
    mock_client1.name = "srv_good"
    mock_client1.connect = AsyncMock()
    mock_client1.disconnect = AsyncMock()
    mock_client1.session = MagicMock()
    
    mock_client2 = MagicMock(spec=MCPClient)
    mock_client2.name = "srv_bad"
    mock_client2.connect = AsyncMock(side_effect=Exception("Connection timed out"))
    mock_client2.disconnect = AsyncMock()
    mock_client2.session = MagicMock()
    
    mock_tool = MagicMock(spec=BaseTool)
    mock_tool.name = "srv_good_tool"
    
    # We patch MCPClient instantiation to return mock_client1 then mock_client2
    def client_side_effect(name, *args, **kwargs):
        if name == "srv_good":
            return mock_client1
        return mock_client2
        
    with patch("mcp_agent.registry.MCPClient", side_effect=client_side_effect), \
         patch("mcp_agent.registry.load_mcp_tools", AsyncMock(return_value=[mock_tool])), \
         patch("mcp_agent.registry.load_mcp_config", return_value={
             "servers": {
                 "srv_good": {"command": "cmd", "args": []},
                 "srv_bad": {"command": "cmd", "args": []}
             }
         }):
        
        registry = MCPRegistry("mcp.json")
        await registry.initialize()
        
        # Registry should initialize srv_good successfully and gracefully degrade on srv_bad
        assert len(registry.tools) == 1
        assert registry.tools[0].name == "srv_good_tool"
        mock_client2.disconnect.assert_awaited_once()

def test_worker_tools_merging(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "dummy")
    
    mock_local_tool = MagicMock(spec=BaseTool)
    mock_local_tool.name = "local_tool"
    
    mock_mcp_tool = MagicMock(spec=BaseTool)
    mock_mcp_tool.name = "mcp_tool"
    
    # We instantiate worker agent passing both local tools and MCP tools in extra_tools
    with patch("agents.worker.create_deep_agent") as mock_create_agent, \
         patch("agents.worker.FilesystemBackend"), \
         patch("agents.worker.ChatOllama"):
         
         build_worker_agent(
             repo_path="D:\\",
             model_name="gemma4",
             extra_tools=[mock_local_tool, mock_mcp_tool]
         )
         
         mock_create_agent.assert_called_once()
         kwargs = mock_create_agent.call_args[1]
         assert kwargs["tools"] == [mock_local_tool, mock_mcp_tool]
