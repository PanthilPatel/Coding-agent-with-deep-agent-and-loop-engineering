"""Worker agent module.

Builds and runs the deep-agent worker that the controller loop invokes each
iteration. The worker owns the LLM, the filesystem backend, and all registered
tools; the controller only calls ``build_worker_agent()`` once at setup and
``run_worker_turn()`` once per iteration.

Logging convention:
  [agent]  — LLM call and tool-execution events (from TerminalLogCallbackHandler)
  [worker] — turn-level events (recursion limit, unhandled errors)
"""

import os
import json
import re
import datetime
from deepagents import create_deep_agent, SubAgent
from deepagents.backends import FilesystemBackend
class PatchedFilesystemBackend(FilesystemBackend):
    def __init__(self, root_dir, *args, **kwargs):
        super().__init__(root_dir=root_dir, *args, **kwargs)
        self.my_root_dir = root_dir

    def _resolve_path(self, path: str) -> str:
        cleaned_path = path.lstrip("/\\")
        
        normalized_repo = os.path.abspath(self.my_root_dir).replace("\\", "/").rstrip("/")
        normalized_path = cleaned_path.replace("\\", "/")
        
        repo_parts = normalized_repo.split("/")
        for i in range(len(repo_parts)):
            suffix = "/".join(repo_parts[i:])
            if suffix and normalized_path.startswith(suffix + "/"):
                cleaned_path = normalized_path[len(suffix) + 1:]
                break
                
        return super()._resolve_path(cleaned_path)

    def write(self, file_path: str, content: str, *args, **kwargs):
        base = os.path.basename(file_path).lower()
        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):
            raise ValueError("Modifying, creating, or rewriting test files is strictly forbidden. You must only fix bugs in implementation source files (e.g. inventory.py, discounts.py).")
        return super().write(file_path=file_path, content=content, *args, **kwargs)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False, *args, **kwargs):
        base = os.path.basename(file_path).lower()
        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):
            raise ValueError("Modifying or editing test files is strictly forbidden. You must only fix bugs in implementation source files (e.g. inventory.py, discounts.py).")
        
        def strip_line_prefix(s: str) -> str:
            lines = s.splitlines()
            cleaned_lines = []
            for line in lines:
                cleaned = re.sub(r"^\s*\d+[:\s]\s*", "", line)
                cleaned_lines.append(cleaned)
            return "\n".join(cleaned_lines)

        cleaned_old = strip_line_prefix(old_string)
        cleaned_new = strip_line_prefix(new_string)

        if cleaned_old == cleaned_new:
            raise ValueError(
                f"No-op edit detected: old_string matches new_string. "
                f"You passed: '{old_string}'. Please make sure you are changing the code instead of replacing a line with itself."
            )

        return super().edit(file_path=file_path, old_string=cleaned_old, new_string=cleaned_new, replace_all=replace_all, *args, **kwargs)

    def execute(self, command: str, *args, **kwargs) -> Any:
        from deepagents.backends.protocol import ExecuteResponse
        return ExecuteResponse(
            output="[System Notice]: Please use the 'execute_command' tool to run shell commands instead of backend 'execute'. Specify the command, cwd, and an appropriate risk_tier ('auto', 'confirm', or 'destructive').",
            exit_code=0
        )
from langchain_ollama import ChatOllama
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from typing import List, Any

class PatchedChatOllama(ChatOllama):
    def _generate(self, *args, **kwargs):
        result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            generation.message = self._parse_fallback_tool_calls(generation.message)
        return result

    async def _agenerate(self, *args, **kwargs):
        result = await super()._agenerate(*args, **kwargs)
        for generation in result.generations:
            generation.message = self._parse_fallback_tool_calls(generation.message)
        return result

    def _parse_fallback_tool_calls(self, response: BaseMessage) -> BaseMessage:
        if not isinstance(response, AIMessage):
            return response
            
        if response.tool_calls:
            for tc in response.tool_calls:
                args = tc.get("args", {})
                name = tc.get("name")
                if isinstance(args, dict):
                    if name == "ls" and "path" not in args:
                        keys = [k for k in args.keys() if isinstance(k, str) and not k.startswith("arg")]
                        path_val = keys[0] if keys else "."
                        if path_val in ("/path/to/repo", "/repo", "/", ""):
                            path_val = "."
                        tc["args"] = {"path": path_val}
                    elif name in ("read_file", "edit_file", "write_file") and "file_path" not in args:
                        if "path" in args:
                            args["file_path"] = args.pop("path")
            return response

        content = response.content.strip()
        if not content:
            return response

        # Try parsing JSON blocks: ```json ... ``` or directly {...}
        json_str = None
        if "```json" in content:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
            if match:
                json_str = match.group(1).strip()
        elif content.startswith("{") and content.endswith("}"):
            json_str = content

        if json_str:
            try:
                data = json.loads(json_str)
                tool_calls = []
                
                # Check if it's a list of tool calls or a single one
                items = data if isinstance(data, list) else [data]
                    
                for idx, item in enumerate(items):
                    if isinstance(item, dict):
                        name = item.get("name")
                        args = item.get("arguments") or item.get("args") or {}
                        if name:
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                    pass
                                except Exception:
                                    pass
                            if isinstance(args, dict):
                                if name == "ls" and "path" not in args:
                                    keys = [k for k in args.keys() if isinstance(k, str) and not k.startswith("arg")]
                                    path_val = keys[0] if keys else "."
                                    if path_val in ("/path/to/repo", "/repo", "/", ""):
                                        path_val = "."
                                    args = {"path": path_val}
                                elif name in ("read_file", "edit_file", "write_file") and "file_path" not in args:
                                    if "path" in args:
                                        args["file_path"] = args.pop("path")
                            tool_calls.append({
                                "name": name,
                                "args": args,
                                "id": item.get("id") or f"call_{name}_{idx}",
                                "type": "tool_call"
                            })
                
                if tool_calls:
                    response.tool_calls = tool_calls
                    print(f"\n[patched_model] Fallback parser successfully parsed tool calls: {tool_calls}")
            except Exception as e:
                print(f"\n[patched_model] Fallback parser failed to parse potential tool call: {e}")
                
        return response

class TerminalLogCallbackHandler(BaseCallbackHandler):
    def __init__(self, log_dir: str = "agent_logs"):
        super().__init__()
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"agent_{timestamp}.log")

    def _log_to_file(self, text: str):
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def on_llm_start(self, serialized, prompts, **kwargs):
        prompt_chars = sum(len(p) for p in prompts) if prompts else 0
        est_tokens = prompt_chars // 4
        msg = f"[AGENT] Calling LLM to analyze and plan... (prompt: ~{est_tokens} tokens / {prompt_chars} chars)"
        print(msg)
        self._log_to_file(msg)

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        if token:
            print(token, end="", flush=True)

    def on_llm_end(self, response, **kwargs):
        print()

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = serialized.get("name", "tool")
        print(f"[TOOL] Executing tool '{name}'")
        self._log_to_file(f"[TOOL] Executing tool '{name}' with input: {input_str}")

    def on_tool_end(self, output, **kwargs):
        out_str = str(output).strip()
        self._log_to_file(f"[RESULT] Tool returned: {out_str}")

REVIEWER_SUBAGENT = SubAgent(
    name="reviewer",
    description=(
        "Reviews a code diff for correctness, style, and whether it "
        "actually addresses the reported test failure. Does not edit "
        "files itself; only comments."
    ),
    system_prompt=(
        "You are a careful code reviewer. You will be given a diff and "
        "the test failure it was meant to fix. Check whether the diff "
        "plausibly fixed the failure, whether it introduces obvious bugs "
        "or regressions, and whether it follows the existing code style. "
        "Respond with either 'APPROVE' followed by a one-line reason, or "
        "'REJECT' followed by a specific, actionable reason."
    ),
)

WORKER_SYSTEM_PROMPT = """You are an autonomous coding agent working inside a
real repository on disk. Your job is to achieve the given goal by inspecting the environment, executing commands, and reading/editing source files directly.

Rules:
- You must invoke tools directly to read, edit, execute, or manage files. Do not simply describe your plans in text responses; execute the tool calls to perform the work.
- The working directory root is already the target repo. All file paths must be relative to current directory (e.g. 'inventory.py' or 'discounts.py', NOT '/examples/...').
- CRITICAL: Never modify, overwrite, weaken, or create test files (any file matching test_*.py or *_test.py). Only fix bugs in the source/implementation files (e.g. inventory.py, discounts.py).
- CRITICAL: When using 'read_file', the tool prefixes lines with line numbers for reference (e.g. ' 1  import pytest'). These numbers are NOT part of the actual file text. When using 'edit_file', do NOT include line numbers in old_string or new_string.
- Terminal & Execution: Use 'execute_command' to run shell commands, check compiler outputs, run sub-scripts, or inspect environment states. Always declare an appropriate risk_tier ('auto' for read/safe, 'confirm' for state-changing/moves, 'destructive' for deletions/resets). Observe exit codes and stderr to self-correct upon failures.
- Always start by writing a short todo list breaking the goal into concrete steps, and keep it updated as you make progress.
- Read the relevant files before editing them. Do not guess at code you have not read.
- Make the smallest change that could plausibly fix a reported issue.
- After editing or executing commands, briefly summarize what you changed and why, in one or two sentences, so the controller can log it.
- Once you have completed the necessary operations and verified your work, conclude your turn with a clear final response summary.
"""


def _build_model(model_name: str, llm_provider: str = "ollama_cloud") -> "PatchedChatOllama":
    """Instantiate and return the configured chat model.

    Supports both ``ollama_cloud`` (remote) and ``ollama`` (local).
    """
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "120.0"))
    num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
    if llm_provider == "ollama_cloud":
        return PatchedChatOllama(
            model=model_name,
            base_url="https://ollama.com",
            headers={"Authorization": f"Bearer {os.environ['OLLAMA_API_KEY']}"},
            client_kwargs={"timeout": timeout},
            sync_client_kwargs={"timeout": timeout},
        )
    elif llm_provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        return PatchedChatOllama(
            model=model_name,
            base_url=base_url,
            num_ctx=num_ctx,
            client_kwargs={"timeout": timeout},
            sync_client_kwargs={"timeout": timeout},
        )
    else:
        raise ValueError(f"Unsupported llm_provider: {llm_provider}")

def build_worker_agent(
    repo_path: str,
    model_name: str = "qwen2.5-coder:7b",
    llm_provider: str = "ollama",
    extra_tools: list = None,
):
    """Construct the deep agent worker, scoped to 'repo_path'.

    Args:
        repo_path:    Absolute path to the target repository.
        model_name:   Name of the Ollama model to use.
        llm_provider: LLM provider identifier ('ollama' or 'ollama_cloud').
        extra_tools:  Additional LangChain BaseTool instances to register
                      alongside the FilesystemBackend's built-in tools.
                      Passed as ``tools=`` to ``create_deep_agent()`` —
                      parameter name confirmed from deepagents 0.7.5 source.
                      When None (default), no extra tools are added.

    Returns a LangGraph-compiled agent that can be invoked with 
    'agent.invoke({"messages": [...]})'.
    """
    model = _build_model(model_name, llm_provider)
    backend = PatchedFilesystemBackend(root_dir=repo_path)
     
    agent = create_deep_agent(
        model=model,
        tools=extra_tools if extra_tools else None,
        system_prompt=WORKER_SYSTEM_PROMPT,
        backend=backend,
    )
    return agent

def run_worker_turn(agent, instruction: str) -> str:
    """Invoke the worker agent with a single instruction and return the 
    text of its final response.
    """
    from langgraph.errors import GraphRecursionError
    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": instruction}]},
            config={
                "callbacks": [TerminalLogCallbackHandler()],
                "recursion_limit": 60
            }
        )
        messages = result.get("messages", [])
        if not messages:
            return ""
        last = messages[-1]
        if isinstance(last, dict):
            return last.get("content", "") or ""
        return getattr(last, "content", "") or ""
    except GraphRecursionError:
        print("\n[worker] Warning: Agent hit recursion limit of 60 steps (potential loop). Aborting turn.")
        return "Agent hit recursion limit of 60 steps due to repeating operations."
    except Exception as e:
        err_msg = str(e)
        if "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
            print(f"\n[worker] Timeout Error: Request to Ollama timed out: {e}")
            return f"Agent failed with timeout: Ollama LLM call exceeded timeout threshold ({e})."
        if "localhost" in err_msg or "127.0.0.1" in err_msg or "connection" in err_msg.lower() or "connect" in err_msg.lower():
            print(f"\n[worker] Connection Error: Could not connect to Ollama. Please ensure your local Ollama server is running (usually at http://localhost:11434). Detail: {e}")
            return f"Agent failed with connection error: Could not connect to local Ollama. Please make sure the Ollama service is running locally on http://localhost:11434 and the model is pulled."
        print(f"\n[worker] Error during execution: {e}")
        return f"Agent failed with error: {e}"


