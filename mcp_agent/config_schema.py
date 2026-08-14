import os
import re
import json
from typing import Dict, Any, List

def interpolate_value(val: Any) -> Any:
    if isinstance(val, str):
        # Match ${VAR} or $VAR
        def replace(match):
            var_name = match.group(1) or match.group(2)
            if var_name not in os.environ:
                raise ValueError(f"Missing required environment variable for interpolation: {var_name}")
            return os.environ[var_name]
        return re.sub(r'\$\{(\w+)\}|\$(\w+)', replace, val)
    elif isinstance(val, list):
        return [interpolate_value(item) for item in val]
    elif isinstance(val, dict):
        return {k: interpolate_value(v) for k, v in val.items()}
    return val

def load_mcp_config(config_path: str) -> Dict[str, Any]:
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"MCP config file not found: {config_path}")
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed MCP config JSON: {e}")
    
    if not isinstance(data, dict) or "servers" not in data:
        raise ValueError("MCP config must be a JSON object containing a 'servers' key.")
        
    servers = data["servers"]
    if not isinstance(servers, dict):
        raise ValueError("'servers' key in MCP config must be a JSON object.")
        
    interpolated_servers = {}
    for name, srv_config in servers.items():
        if not isinstance(srv_config, dict):
            raise ValueError(f"Server config for '{name}' must be an object.")
        if "command" not in srv_config:
            raise ValueError(f"Server '{name}' is missing the required 'command' field.")
            
        cmd = srv_config["command"]
        args = srv_config.get("args", [])
        env = srv_config.get("env", {})
        
        if not isinstance(args, list):
            raise ValueError(f"'args' for server '{name}' must be a list of strings.")
        if not isinstance(env, dict):
            raise ValueError(f"'env' for server '{name}' must be a dictionary of strings.")
            
        # Interpolate variables
        interpolated_cmd = interpolate_value(cmd)
        interpolated_args = [interpolate_value(a) for a in args]
        interpolated_env = {}
        for k, v in env.items():
            interpolated_env[k] = interpolate_value(v)
            
        interpolated_servers[name] = {
            "command": interpolated_cmd,
            "args": interpolated_args,
            "env": {**dict(os.environ), **interpolated_env}
        }
        
    return {"servers": interpolated_servers}
