"""Interactive REPL mode for the coding agent.

Provides a prompt loop where users can enter task descriptions, with each
task invoking the same ``controller.loop.run()`` function that one-shot mode
uses. All existing logging streams to the terminal as the task executes.
"""

import sys
from typing import Optional
from config import Config
from controller.loop import run as run_controller_loop
from tools import build_tool_registry
from skills import list_skills


def print_banner(config: Config) -> None:
    """Print the startup banner with real counts from registries and config."""
    repo_path = getattr(config, "local_repo_path", None) or getattr(config, "repo_path", "")
    # Count Phase 2 tools
    tool_count = len(build_tool_registry(
        repo_path=repo_path,
        test_cmd=getattr(config, "test_cmd", "pytest"),
        require_approval=getattr(config, "require_approval", False),
    ))

    
    # Count Phase 3 skills (if skills directory exists)
    skills_dir = config.skills_dir if config.skills_dir else "skills"
    skill_count = 0
    try:
        # Only count if the skills directory actually exists
        import os
        if os.path.isdir(skills_dir):
            skill_list = list_skills(skills_dir=skills_dir)
            skill_count = len(skill_list)
    except Exception:
        skill_count = 0
    
    # Count Phase 4 MCP servers (if configured)
    mcp_server_count = 0
    mcp_path = getattr(config, "mcp_config_path", None)
    if mcp_path:
        try:
            from mcp_agent.config_schema import load_mcp_config
            mcp_config = load_mcp_config(mcp_path)
            mcp_server_count = len(mcp_config.get("servers", {}))
        except Exception:
            pass  # MCP config not available or invalid

    
    print("\n" + "="*60)
    print("                     Coding Agent")
    print("="*60)
    print(f"Repository:   {config.local_repo_path}")
    print(f"Model:        {config.model_name}")
    print(f"Tools:        {tool_count}")
    if skill_count > 0:
        print(f"Skills:       {skill_count}")
    if mcp_server_count > 0:
        print(f"MCP Servers:  {mcp_server_count}")
    print("="*60)
    print("\nType a task description to execute, or 'exit'/'quit' to stop.")
    print()


def run_interactive(config_template: Config) -> None:
    """Run the interactive REPL.
    
    Each task gets a fresh state (matching one-shot behavior). The config
    is reused across tasks, only updating the goal field for each new task.
    
    Args:
        config_template: A Config instance with all fields set except goal
                         (which will be overridden per task).
    """
    print_banner(config_template)
    
    # Initialize MCP registry once if configured (shared across tasks)
    mcp_registry = None
    if getattr(config_template, "mcp_config_path", None):
        import asyncio
        from mcp_agent.registry import MCPRegistry
        try:
            mcp_registry = MCPRegistry(config_template.mcp_config_path)
            asyncio.run(mcp_registry.initialize())
            print(f"[MCP] Initialized {len(mcp_registry.clients)} server(s)\n")
        except Exception as e:
            print(f"[MCP] Failed to initialize: {e}\n")
    
    try:
        while True:
            try:
                task = input("> ").strip()
            except EOFError:
                # Ctrl+D/Ctrl+Z on Windows
                print("\nExiting...")
                break
            except KeyboardInterrupt:
                # Ctrl+C
                print("\nExiting...")
                break
            
            if not task:
                continue  # Empty line, prompt again
            
            if task.lower() in ("exit", "quit"):
                print("Exiting...")
                break
            
            # Build a new config with this task's goal
            # Each task gets a fresh state (state.json will be overwritten)
            cfg_kwargs = {
                "repo_path": config_template.repo_path,
                "goal": task,
                "test_cmd": config_template.test_cmd,
                "max_iterations": config_template.max_iterations,
                "max_seconds": config_template.max_seconds,
                "require_approval": config_template.require_approval,
                "model_name": config_template.model_name,
                "state_file": config_template.state_file,
                "llm_provider": config_template.llm_provider,
                "lint_cmd": config_template.lint_cmd,
                "skills_dir": config_template.skills_dir,
            }
            if getattr(config_template, "mcp_config_path", None) is not None:
                cfg_kwargs["mcp_config_path"] = config_template.mcp_config_path
            config = Config(**cfg_kwargs)

            
            # Run the same controller loop as one-shot mode
            success = run_controller_loop(config)
            
            # Print a short result summary
            status = "SUCCESS" if success else "FAILURE"
            print(f"\n[interactive] Task completed: {status}\n")
    
    finally:
        # Clean shutdown: close MCP connections if any
        if mcp_registry:
            import asyncio
            try:
                asyncio.run(mcp_registry.close())
                print("[MCP] Connections closed.")
            except Exception as e:
                print(f"[MCP] Error during shutdown: {e}")
