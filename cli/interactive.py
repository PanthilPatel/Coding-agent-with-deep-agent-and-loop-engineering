"""Interactive REPL mode for the coding agent.

Redesigned as a session-persistent, conversational agent session:
- The worker agent is built once with a MemorySaver checkpointer.
- All chat turns within a session share a stable thread_id.
- Regular inputs execute single-turn chat directly with the agent.
- Explicit `/run <goal>` command invokes the full autonomous controller loop.
- Mid-turn interruption via Ctrl+C cancels the current turn safely without terminating the session.
"""

import os
import sys
import uuid
import threading
import concurrent.futures
from typing import Optional

from config import Config
from controller.loop import run as run_controller_loop
from controller.permissions import PermissionHarness
from skills import list_skills
from agents.worker import build_readonly_worker_agent, run_worker_turn
from tools import build_readonly_tool_registry
from langgraph.checkpoint.memory import MemorySaver


def print_banner(config: Config) -> None:
    """Print the startup banner with real counts from registries and config."""
    repo_path = getattr(config, "local_repo_path", None) or getattr(config, "repo_path", "")
    # Count Phase 2 tools (full registry, for banner display)
    tool_count = len(build_readonly_tool_registry(
        repo_path=repo_path,
    ))

    # Count Phase 3 skills (if skills directory exists)
    skills_dir = config.skills_dir if config.skills_dir else "skills"
    skill_count = 0
    try:
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
            pass

    print("\n" + "="*60)
    print("                     Coding Agent (Session)")
    print("="*60)
    print(f"Repository:   {config.local_repo_path}")
    print(f"Model:        {config.model_name}")
    print(f"Tools:        {tool_count}")
    if skill_count > 0:
        print(f"Skills:       {skill_count}")
    if mcp_server_count > 0:
        print(f"MCP Servers:  {mcp_server_count}")
    print("="*60)
    print("\nType your message/question to converse with the agent.")
    print("Commands:")
    print("  /run [goal]   - Run autonomous fix-until-green loop (defaults to last goal)")
    print("  /help         - Show available commands and usage")
    print("  /exit, /quit  - Exit the interactive session")
    print()


def print_help() -> None:
    """Print interactive session help information."""
    print("\nAvailable Commands:")
    print("  <message>     - Conversational turn with the persistent agent (maintains session memory)")
    print("  /run [goal]   - Execute the autonomous multi-iteration fix loop (test suite, checkpoints, commits)")
    print("  /help         - Display this help message")
    print("  /exit, /quit  - End the interactive REPL session")
    print("  Ctrl+C        - Cancel an ongoing turn without exiting the REPL\n")


def run_interactive(config_template: Config) -> None:
    """Run the session-persistent interactive REPL.

    Args:
        config_template: A Config instance with configuration defaults.
    """
    print_banner(config_template)

    # Initialize MCP registry once if configured (shared across tasks)
    mcp_registry = None
    mcp_tools = []
    harness = PermissionHarness(interactive=False)

    if getattr(config_template, "mcp_config_path", None):
        import asyncio
        from mcp_agent.registry import MCPRegistry
        try:
            mcp_registry = MCPRegistry(config_template.mcp_config_path)
            asyncio.run(mcp_registry.initialize())
            if mcp_registry.tools:
                guarded_tools = []
                for _tool in mcp_registry.tools:
                    def _make_guarded(tool, h):
                        def _guarded_call(input_str=""):
                            return h.execute_guarded(
                                tool.run,
                                "confirm",
                                input_str,
                                tool_name=tool.name,
                            )
                        _guarded_call.__name__ = tool.name
                        _guarded_call.__doc__ = getattr(tool, "description", "")
                        return _guarded_call
                    guarded_tools.append(_make_guarded(_tool, harness))
                mcp_tools = guarded_tools
                print(f"[MCP] Initialized {len(mcp_registry.clients)} server(s) with {len(mcp_tools)} tool(s).\n")
        except Exception as e:
            print(f"[MCP] Failed to initialize: {e}\n")

    # Build read-only tools for chat turns (no edit_file, write_file, git_commit,
    # execute_command, move_file, delete_file, or create_directory).
    # /run's controller.loop.run() builds its own full-capability agent internally.
    repo_path = getattr(config_template, "local_repo_path", None) or getattr(config_template, "repo_path", "")
    readonly_tools = build_readonly_tool_registry(
        repo_path=repo_path,
        harness=harness,
    )

    # Persistent in-memory checkpointer & stable session thread_id
    checkpointer = MemorySaver()
    session_thread_id = str(uuid.uuid4())

    # Read-only chat-turn agent: uses ReadonlyFilesystemBackend which hard-blocks
    # write/edit/delete at the backend layer, regardless of LLM phrasing.
    # The full-write agent is NOT built here; /run creates its own inside loop.run().
    agent = build_readonly_worker_agent(
        repo_path=repo_path,
        model_name=config_template.model_name,
        llm_provider=config_template.llm_provider,
        extra_tools=readonly_tools,
        checkpointer=checkpointer,
    )

    last_goal = ""

    try:
        while True:
            try:
                user_input = input("> ").strip()
            except EOFError:
                print("\nExiting...")
                break
            except KeyboardInterrupt:
                print("\nExiting...")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                print("Exiting...")
                break

            if user_input.lower() == "/help":
                print_help()
                continue

            # Check if this is an explicit autonomous fix loop command
            if user_input.startswith("/run"):
                goal = user_input[4:].strip()
                if not goal:
                    goal = last_goal
                if not goal:
                    print("[interactive] Error: Please specify a goal for /run (e.g. '/run fix all failing unit tests').")
                    continue

                last_goal = goal
                cfg_kwargs = {
                    "repo_path": config_template.repo_path,
                    "goal": goal,
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

                print(f"\n[interactive] Starting autonomous fix loop for goal: '{goal}'...")
                success = run_controller_loop(config)
                status = "SUCCESS" if success else "FAILURE"
                print(f"\n[interactive] Autonomous loop completed: {status}\n")

                # Bridge /run outcome into conversational chat-turn agent's memory
                try:
                    from utils.git_utils import get_repo
                    repo = get_repo(repo_path)
                    commit_summary = ""
                    if repo.heads and len(repo.heads) > 0:
                        try:
                            last_commit = repo.head.commit
                            commit_summary = f"Commit {last_commit.hexsha[:8]}: {last_commit.summary}"
                            diff_summary = repo.git.diff("HEAD~1", "HEAD")[:2000]
                        except Exception:
                            commit_summary = ""
                            diff_summary = ""
                    else:
                        diff_summary = ""

                    bridge_msg = (
                        f"[SYSTEM NOTIFICATION: Autonomous task '/run {goal}' completed with status: {status}. "
                    )
                    if commit_summary:
                        bridge_msg += f"{commit_summary}. Changes made:\n{diff_summary}]"
                    else:
                        bridge_msg += "No commits were created.]"

                    # Update checkpointer thread with the /run outcome
                    from langchain_core.messages import HumanMessage, AIMessage
                    agent.update_state(
                        {"configurable": {"thread_id": session_thread_id}},
                        {"messages": [
                            HumanMessage(content=f"Executed autonomous loop: /run {goal}"),
                            AIMessage(content=bridge_msg),
                        ]},
                    )
                except Exception as ex:
                    print(f"[interactive] Warning: could not sync /run outcome to chat memory: {ex}")

                continue

            # Regular conversational turn with persistent agent & interruption support
            last_goal = user_input
            cancellation_event = threading.Event()

            # Execute turn in worker thread so main thread can catch KeyboardInterrupt for cancellation
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(
                run_worker_turn,
                agent=agent,
                instruction=user_input,
                thread_id=session_thread_id,
                cancellation_event=cancellation_event,
            )

            try:
                # Wait for future while handling KeyboardInterrupt
                while not future.done():
                    try:
                        future.result(timeout=0.1)
                        break
                    except concurrent.futures.TimeoutError:
                        continue
                if future.done() and not cancellation_event.is_set():
                    response_text = future.result()
                    if response_text and not response_text.startswith("Turn interrupted by user."):
                        print(f"\n{response_text}\n")
            except KeyboardInterrupt:
                print("\n[interactive] Interrupt received. Cancelling turn...")
                cancellation_event.set()
                try:
                    # Wait briefly for thread to abort at tool/LLM boundary
                    response_text = future.result(timeout=5.0)
                except Exception:
                    pass
                print("[interactive] Turn cancelled. Session remains active.\n")
            finally:
                executor.shutdown(wait=False)

    finally:
        # Clean shutdown: close MCP connections if any
        if mcp_registry:
            import asyncio
            try:
                asyncio.run(mcp_registry.close())
                print("[MCP] Connections closed.")
            except Exception as e:
                print(f"[MCP] Error during shutdown: {e}")

