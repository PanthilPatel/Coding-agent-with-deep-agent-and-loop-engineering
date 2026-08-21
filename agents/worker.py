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
from typing import List, Any
from deepagents import create_deep_agent, SubAgent
from deepagents.backends import FilesystemBackend
class PatchedFilesystemBackend(FilesystemBackend):
    def __init__(self, root_dir, *args, **kwargs):
        super().__init__(root_dir=root_dir, *args, **kwargs)
        self.my_root_dir = root_dir
        # Tracks (file_path, cleaned_old) pairs that were rejected as ambiguous
        # so that re-submissions are caught instantly with a clearer directive.
        self._rejected_ambiguous_strings: set = set()

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
                
        # If the LLM passed a path prefixed with an example directory name, strip it if the file exists directly in root
        if not os.path.exists(os.path.join(self.my_root_dir, cleaned_path)):
            base_filename = os.path.basename(cleaned_path)
            if os.path.exists(os.path.join(self.my_root_dir, base_filename)):
                cleaned_path = base_filename

        return super()._resolve_path(cleaned_path)

    def write(self, file_path: str, content: str, *args, **kwargs):
        from deepagents.backends.protocol import WriteResult
        base = os.path.basename(file_path).lower()
        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):
            return WriteResult(error="Error: Modifying or editing test files is strictly forbidden. You must only fix bugs in the actual source/implementation files in this repository.")
        try:
            res = super().write(file_path=file_path, content=content, *args, **kwargs)
            from langgraph.errors import GraphRecursionError
            raise GraphRecursionError("[SHORT_CIRCUIT] File write completed successfully. Ending turn.")
        except Exception as e:
            if "[SHORT_CIRCUIT]" in str(e):
                raise
            return WriteResult(error=f"Tool Error: {e}. Please check your arguments and try again.")

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False, *args, **kwargs):
        from deepagents.backends.protocol import EditResult
        base = os.path.basename(file_path).lower()
        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):
            return EditResult(error="Error: Modifying or editing test files is strictly forbidden. You must only fix bugs in the actual source/implementation files in this repository.")
        
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
            return EditResult(error="Tool Error: No-op edit detected: old_string matches new_string. Please check your arguments and try again.")

        # Fast-reject: this exact (file_path, old_string) pair was already rejected
        # as ambiguous earlier in this run — avoid re-running the count() check and
        # repeating the same generic message that clearly isn't stopping the retry.
        rejection_key = (file_path, cleaned_old)
        if rejection_key in self._rejected_ambiguous_strings:
            return EditResult(
                error=f"Error: This exact old_string for {file_path} was already rejected as ambiguous "
                      f"earlier in this run. Do not resubmit it. Use a wider, unique old_string instead "
                      f"(e.g. include the full function signature or 2-4 neighboring lines)."
            )

        try:
            resolved_path = self._resolve_path(file_path)
            if not os.path.exists(resolved_path):
                return EditResult(error=f"Tool Error: File not found: {file_path}. Please check your arguments and try again.")
            
            with open(resolved_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()
                
            if cleaned_old not in file_content:
                # Secondary check: search for whitespace-insensitive match to provide
                # actionable indentation feedback rather than a blind failure.
                target_lines = [line.strip() for line in cleaned_old.splitlines() if line.strip()]
                if target_lines:
                    file_lines = file_content.splitlines()
                    target_len = len(target_lines)
                    matching_snippets = []
                    for i in range(len(file_lines) - target_len + 1):
                        window = file_lines[i : i + target_len]
                        if [line.strip() for line in window if line.strip()] == target_lines:
                            matching_snippets.append("\n".join(window))
                    
                    if len(matching_snippets) == 1:
                        hint = matching_snippets[0]
                        return EditResult(
                            error=(
                                f"Error: 'old_string' was not found in {file_path} due to whitespace/indentation mismatch. "
                                f"Here is the exact matching snippet with correct indentation from the file:\n"
                                f"```python\n{hint}\n```\n"
                                f"Please use this exact string in 'old_string'."
                            )
                        )
                    elif len(matching_snippets) > 1:
                        return EditResult(
                            error=(
                                f"Error: 'old_string' matched multiple locations in {file_path} (whitespace-insensitively). "
                                f"Please include more surrounding context (such as function definition or enclosing block) to make it unique."
                            )
                        )

                return EditResult(error=f"Error: 'old_string' was not found in {file_path}. Please read the file first to ensure exact string and whitespace matching.")

            if file_content.count(cleaned_old) > 1:
                # Record this key so the next identical attempt is fast-rejected with
                # a clearer, more directive message rather than the generic one.
                self._rejected_ambiguous_strings.add(rejection_key)
                return EditResult(
                    error=f"Error: 'old_string' matched multiple locations in {file_path} and was REJECTED. "
                          f"Do not retry this exact old_string — it will always be rejected. "
                          f"Read the file again and include enough unique surrounding context "
                          f"(e.g. the full function signature or 2-4 neighboring lines) "
                          f"to make it unique, then submit a DIFFERENT old_string."
                )

            res = super().edit(file_path=file_path, old_string=cleaned_old, new_string=cleaned_new, replace_all=replace_all, *args, **kwargs)
            from langgraph.errors import GraphRecursionError
            raise GraphRecursionError("[SHORT_CIRCUIT] File edit completed successfully. Ending turn.")
        except Exception as e:
            if "[SHORT_CIRCUIT]" in str(e):
                raise
            return EditResult(error=f"Tool Error: {e}. Please check your arguments and try again.")

    def delete(self, file_path: str, *args, **kwargs):
        from deepagents.backends.protocol import DeleteResult
        base = os.path.basename(file_path).lower()
        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):
            return DeleteResult(error="Error: Modifying or editing test files is strictly forbidden. You must only fix bugs in the actual source/implementation files in this repository.")
        try:
            return super().delete(file_path, *args, **kwargs)
        except Exception as e:
            return DeleteResult(error=f"Tool Error: {e}. Please check your arguments and try again.")

    def read(self, file_path: str, *args, **kwargs):
        from deepagents.backends.protocol import ReadResult
        try:
            return super().read(file_path, *args, **kwargs)
        except Exception as e:
            return ReadResult(error=f"Tool Error: {e}. Please check your arguments and try again.")

    def ls(self, *args, **kwargs):
        from deepagents.backends.protocol import LsResult
        try:
            return super().ls(*args, **kwargs)
        except Exception as e:
            return LsResult(error=f"Tool Error: {e}. Please check your arguments and try again.")

    def grep(self, *args, **kwargs):
        from deepagents.backends.protocol import GrepResult
        try:
            return super().grep(*args, **kwargs)
        except Exception as e:
            return GrepResult(error=f"Tool Error: {e}. Please check your arguments and try again.")

    def execute(self, command: str, *args, **kwargs) -> Any:
        from deepagents.backends.protocol import ExecuteResponse
        return ExecuteResponse(
            output="[System Notice]: Please use the 'execute_command' tool to run shell commands instead of backend 'execute'. Specify the command, cwd, and an appropriate risk_tier ('auto', 'confirm', or 'destructive').",
            exit_code=0
        )


class ReadonlyFilesystemBackend(PatchedFilesystemBackend):
    """A read-only variant of PatchedFilesystemBackend for use during chat turns.

    All write operations (write, edit, delete) are hard-blocked at the backend
    layer and return an informational error. Read operations (read, ls, grep)
    are inherited unchanged from PatchedFilesystemBackend.

    This ensures that plain chat-turn inputs — regardless of how they are
    phrased — can never mutate files on disk, create checkpoints, or trigger
    any commit path. No external guardrail is needed; the block is structural.
    """

    _CHAT_WRITE_ERROR = (
        "[Chat mode] File modifications are not permitted during plain chat turns. "
        "To make actual code changes, prefix your request with /run — e.g. "
        "/run add a size method to Stack in structures.py"
    )

    def write(self, file_path: str, content: str, *args, **kwargs):
        from deepagents.backends.protocol import WriteResult
        return WriteResult(error=self._CHAT_WRITE_ERROR)

    def edit(self, file_path: str, old_string: str, new_string: str, *args, **kwargs):
        from deepagents.backends.protocol import EditResult
        return EditResult(error=self._CHAT_WRITE_ERROR)

    def delete(self, file_path: str, *args, **kwargs):
        from deepagents.backends.protocol import DeleteResult
        return DeleteResult(error=self._CHAT_WRITE_ERROR)
from langchain_ollama import ChatOllama
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage

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

        def _extract_json_str(text: str):
            """Return the first brace-balanced JSON object substring, or None.

            Strategy:
              1. Fast path: look for a fully-closed ```json ... ``` fence.
              2. Fast path: look for any ``` ... ``` fence whose body starts with '{'.
              3. Brace-balanced scan: find the first '{' anywhere in the text,
                 walk forward tracking string literals and nested brace depth to
                 find the matching '}', regardless of fencing or surrounding prose.
            """
            # Fast path 1: fully-closed ```json fence
            m = re.search(r"```json\s*([\s\S]*?)\s*```", text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("{"):
                    return candidate

            # Fast path 2: fully-closed generic fence whose body looks like JSON
            m = re.search(r"```\s*([\s\S]*?)\s*```", text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("{"):
                    return candidate

            # Fast path 3: unclosed ```json fence — grab everything after the marker
            m = re.search(r"```json\s*([\s\S]+)", text)
            if m:
                candidate = m.group(1).strip()
                if candidate.startswith("{"):
                    # Try brace-balance on the candidate to handle trailing prose
                    balanced = _brace_balanced(candidate)
                    if balanced:
                        return balanced

            # Brace-balanced scan: find first '{' anywhere in text
            return _brace_balanced(text)

        def _brace_balanced(text: str):
            """Find the first '{...}' that is brace-balanced, skipping string contents."""
            start = text.find("{")
            if start == -1:
                return None
            depth = 0
            in_string = False
            escape_next = False
            for i, ch in enumerate(text[start:], start=start):
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\" and in_string:
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
            return None

        json_str = _extract_json_str(content)

        parsed_by_regex = False
        tool_calls = []

        if json_str:
            try:
                data = json.loads(json_str)
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
            except Exception as e:
                # If strict JSON parse fails, try heuristic regex matching for edit_file / read_file
                print(f"\n[patched_model] Strict JSON load failed: {e}. Attempting heuristic regex repair...")
                search_src = json_str or content
                # Try to extract the tool name
                tool_name_match = re.search(r'"name"\s*:\s*"([^"]+)"', search_src)
                if tool_name_match:
                    tool_name = tool_name_match.group(1)
                    if tool_name in ("edit_file", "read_file", "write_file"):
                        args = {}
                        fp_match = re.search(r'"file_path"\s*:\s*"([^"]+)"', search_src)
                        if fp_match:
                            args["file_path"] = fp_match.group(1)

                        if tool_name == "edit_file":
                            old_match = re.search(r'"old_string"\s*:\s*"(.*?)"\s*,\s*"new_string"', search_src, re.DOTALL)
                            new_match = re.search(r'"new_string"\s*:\s*"(.*?)"\s*(?:\}\s*\}|\}\s*\]|\}$)', search_src, re.DOTALL)
                            if old_match and new_match:
                                args["old_string"] = old_match.group(1)
                                args["new_string"] = new_match.group(1)
                            else:
                                old_match = re.search(r'"old_string"\s*:\s*"(.*?)"', search_src, re.DOTALL)
                                new_match = re.search(r'"new_string"\s*:\s*"(.*?)"', search_src, re.DOTALL)
                                if old_match:
                                    args["old_string"] = old_match.group(1)
                                if new_match:
                                    args["new_string"] = new_match.group(1)

                        if "file_path" in args:
                            tool_calls.append({
                                "name": tool_name,
                                "args": args,
                                "id": f"call_regex_{tool_name}",
                                "type": "tool_call"
                            })
                            parsed_by_regex = True

        if tool_calls:
            response.tool_calls = tool_calls
            if parsed_by_regex:
                print(f"\n[patched_model] Regex fallback parser successfully repaired tool calls: {tool_calls}")
            else:
                print(f"\n[patched_model] Fallback parser successfully parsed tool calls: {tool_calls}")
        else:
            # Warn if the response references a tool by name but we failed to extract a call —
            # this makes the failure mode visible in logs instead of silently producing a no-op.
            if re.search(r'"name"\s*:\s*"[^"]+"', content):
                print(
                    f"\n[patched_model] WARNING: Response content appears to reference a tool by name "
                    f"(e.g. '\"name\": \"edit_file\"') but no valid tool_call could be extracted. "
                    f"The LLM output will be treated as a plain text response — no tool will be executed. "
                    f"Content snippet: {content[:300]!r}"
                )

        return response

# Module-level side-channel: loop.py reads this after each run_worker_turn() call.
# Using a dict (not a return-value change) so existing test mocks stay valid.
_last_turn_token_usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

class TerminalLogCallbackHandler(BaseCallbackHandler):
    def __init__(self, log_dir: str = "agent_logs", cancellation_event=None):
        super().__init__()
        self.cancellation_event = cancellation_event
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            pass
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"agent_{timestamp}.log")
        # Running token totals accumulated across all LLM calls in this turn
        self._turn_token_totals: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def _log_to_file(self, text: str):
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def get_turn_token_totals(self) -> dict:
        """Return a copy of the accumulated token totals for this turn."""
        return dict(self._turn_token_totals)

    def _check_interrupted(self):
        if self.cancellation_event is not None and self.cancellation_event.is_set():
            # Interruption takes effect at the next tool-call/LLM-call boundary.
            # Documented limitation: not instant mid-token cancellation.
            raise InterruptedError("Turn interrupted by user.")

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._check_interrupted()
        prompt_chars = sum(len(p) for p in prompts) if prompts else 0
        est_tokens = prompt_chars // 4
        msg = f"[AGENT] Calling LLM to analyze and plan... (prompt: ~{est_tokens} tokens / {prompt_chars} chars)"
        print(msg)
        self._log_to_file(msg)

    def on_llm_new_token(self, token: str, **kwargs) -> None:
        if token:
            print(token, end="", flush=True)

    def on_llm_end(self, response, **kwargs):
        """Extract real token counts from the LLM response and accumulate them.

        Handles both provider formats:
          - ChatOllama / ChatOpenAI (NVIDIA NIM): response_metadata["token_usage"]
            with keys prompt_tokens, completion_tokens, total_tokens
          - Some Ollama builds: usage_metadata with keys input_tokens, output_tokens
        """
        print()  # newline after streaming output

        # Walk generations to find the AIMessage with metadata
        msg = None
        if hasattr(response, "generations") and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    if hasattr(gen, "message"):
                        msg = gen.message
                        break
                if msg is not None:
                    break

        # Try response_metadata["token_usage"] first (ChatOllama, ChatOpenAI/NVIDIA)
        obj = msg if msg is not None else response
        rm = getattr(obj, "response_metadata", None) or {}
        usage = rm.get("token_usage") if isinstance(rm, dict) else None

        # Fallback: usage_metadata (some Ollama builds use input_tokens/output_tokens)
        if not usage:
            um = getattr(obj, "usage_metadata", None) or {}
            if um:
                usage = um

        if usage:
            prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))

            # Per-call stdout + file log
            token_msg = (
                f"[TOKEN USAGE] Prompt: {prompt_tokens} | "
                f"Completion: {completion_tokens} | Total: {total_tokens}"
            )
            print(token_msg)
            self._log_to_file(token_msg)

            # Accumulate into turn totals
            self._turn_token_totals["prompt_tokens"] += prompt_tokens
            self._turn_token_totals["completion_tokens"] += completion_tokens
            self._turn_token_totals["total_tokens"] += total_tokens

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._check_interrupted()
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
        "actually addresses the reported goal. Does not edit files itself; "
        "only comments."
    ),
    system_prompt=(
        "You are an adversarial, rigorous code reviewer. You will be given a diff and the goal "
        "it was meant to achieve. Your job is to verify whether the diff genuinely and accurately "
        "implements the requested change on the SPECIFIC target class, function, or module.\n\n"
        "Review Rules:\n"
        "1. TARGET IDENTITY VERIFICATION: Carefully check the exact class, function, or file being modified in the diff hunks. "
        "If the goal specifies modifying class 'X' (e.g. 'Stack'), but the diff hunk modifies class 'Y' (e.g. 'Queue'), "
        "you MUST immediately REJECT the diff.\n"
        "2. ACCURACY: In your verdict reason, explicitly state which class/function the diff actually modifies and confirm whether it matches the goal.\n"
        "3. FORMAT: Start your response with either 'APPROVE' followed by a specific reason stating the modified target and why it fulfills the goal, "
        "or 'REJECT' followed by a specific, actionable reason explaining the discrepancy or bug."
    ),
)


def _extract_diff_targets(diff: str) -> list[str]:
    """Scan unified diff hunks for class/function definitions in context headers or content."""
    import re
    targets = []
    for line in diff.splitlines():
        # Match hunk header context e.g. @@ -36,6 +36,9 @@ class Queue:
        hunk_match = re.search(r"@@.*@@\s*(?:class\s+([A-Za-z0-9_]+)|def\s+([A-Za-z0-9_]+))", line)
        if hunk_match:
            name = hunk_match.group(1) or hunk_match.group(2)
            if name and name not in targets:
                targets.append(name)
        # Match class definitions within diff
        class_match = re.match(r"^[+ ]class\s+([A-Za-z0-9_]+)", line)
        if class_match:
            name = class_match.group(1)
            if name and name not in targets:
                targets.append(name)
    return targets


def review_diff(
    diff: str,
    goal: str,
    model_name: str = "qwen2.5-coder:7b",
    llm_provider: str = "ollama",
) -> tuple[bool, str]:
    """Invoke the REVIEWER_SUBAGENT to sanity check a code diff against the goal.

    Returns:
        (approved: bool, reason: str)
    """
    if not diff or not diff.strip():
        return True, "No changes detected."

    # Pre-check target mismatch if explicit class names are referenced
    targets = _extract_diff_targets(diff)
    target_hint = ""
    if targets:
        target_hint = f"\nNote: Static inspection detected the diff modifies or surrounds: {', '.join(targets)}.\n"

    reviewer_sys_prompt = REVIEWER_SUBAGENT.get("system_prompt") if isinstance(REVIEWER_SUBAGENT, dict) else getattr(REVIEWER_SUBAGENT, "system_prompt", "")
    prompt = (
        f"{reviewer_sys_prompt}\n\n"
        f"Goal: {goal}\n\n"
        f"{target_hint}"
        f"Diff:\n{diff[:4000]}\n\n"
        "Please provide your verdict ('APPROVE' or 'REJECT' followed by reason):"
    )

    try:
        model = _build_model(model_name, llm_provider)
        response = model.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
        text_str = text.strip() if isinstance(text, str) else str(text).strip()
        
        if "REJECT" in text_str.upper() and not text_str.upper().startswith("APPROVE"):
            return False, text_str
        return True, text_str
    except Exception as e:
        print(f"[REVIEWER] Reviewer subagent error: {e}. Defaulting to approval.")
        return True, f"Reviewer subagent error: {e}"


WORKER_SYSTEM_PROMPT = """You are an autonomous coding agent working inside a
real repository on disk. Your job is to achieve the given goal by inspecting the environment, executing commands, and reading/editing source files directly.

Rules:
- STEP 1 (REQUIRED): Before reading or editing any file, you MUST call list_directory on the repo root (".") to see what actually exists here.
- You must invoke tools directly to read, edit, execute, or manage files. Do not simply describe your plans in text responses; execute the tool calls to perform the work.
- The working directory root is already the target repo. All file paths must be relative to current directory.
- CRITICAL: Keep actions minimal. Complete your diagnosis and code edit in 2 to 4 steps.
- CRITICAL: Once you have edited the relevant file with `edit_file`, DO NOT run more exploratory tools. Immediately provide a brief 1-sentence summary and conclude your turn so your changes can be verified.
- CRITICAL: When using `edit_file`, always provide at least 3-5 lines of unique surrounding context in `old_string` (such as the preceding method or class definition) so the edit matches exactly one location and never fails with ambiguous match errors.
- CRITICAL: When adding methods to classes, make sure to insert them in the CORRECT class specified in the goal.
- CRITICAL: All argument values in tool calls MUST be valid JSON strings with proper escaping. Always wrap code strings in standard double quotes. Example:
  {"file_path": "foo.py", "old_string": "def bar():\\n    return 1", "new_string": "def bar():\\n    return 2"}
  Never emit unescaped newlines inside JSON string arguments.
- Do NOT guess or hallucinate file paths; always list or read files first.
"""

CHAT_MODE_SYSTEM_PROMPT = """You are an intelligent coding assistant in conversational chat mode, running directly inside the target repository.

Core Operating Rules:
1. GENERAL KNOWLEDGE & QUESTIONS UNRELATED TO THIS REPO (NO TOOLS):
   - For general knowledge questions, concepts, definitions, math, or language explanations unrelated to inspecting files (e.g. "explain recursion", "what is 15% of 240?"), answer DIRECTLY in plain conversational text. Do NOT call tools.

2. REPOSITORY CODE & STRUCTURE QUESTIONS (READ FILES FIRST):
   - When asked about files, classes, or code in this repository (e.g. "what's wrong with the Queue class in structures.py?", "what does the Stack class do in structures.py?"), you MUST IMMEDIATELY call `read_file` with the file path (e.g. `read_file(file_path="structures.py")`).
   - Do NOT guess or hallucinate code or methods from memory. Always inspect the file using `read_file` before answering questions about repository code.
   - NEVER ask the user to provide file paths or code, NEVER tell the user to run `/run read_file ...`, and NEVER output prose saying you need to read the file. Execute the `read_file` tool call directly in this turn!

3. READ-ONLY CHAT RESTRICTIONS:
   - You are in read-only mode for chat. File modifications must be done via `/run <goal>`.

4. RAW SHELL COMMANDS & UNSUPPORTED TOOL REQUESTS:
   - When the user enters a raw shell command (e.g. `git status ...`, `ls -la`, `pytest`, `cat ...`, `find ...`, `pip ...`, etc.) or asks you to run a terminal/shell tool that is not in your available tools, DO NOT fabricate, guess, or invent simulated shell output.
   - Instead, explicitly inform the user that you cannot execute raw shell commands directly in chat mode. If they want to inspect files, they can ask you to read them, and if they want autonomous test/edit execution, they should use `/run <goal>`.
"""

def _build_model(model_name: str, llm_provider: str):
    """Factory helper to construct the LLM chat client based on provider."""
    timeout_env = os.environ.get("OLLAMA_TIMEOUT", "60")
    try:
        timeout = float(timeout_env)
    except ValueError:
        timeout = 60.0

    num_ctx_env = os.environ.get("OLLAMA_NUM_CTX", "16384")
    try:
        num_ctx = int(num_ctx_env)
    except ValueError:
        num_ctx = 16384

    if llm_provider == "ollama_cloud":
        api_key = os.environ.get("OLLAMA_API_KEY")
        return PatchedChatOllama(
            model=model_name,
            base_url="https://ollama.com",
            api_key=api_key,
            num_ctx=num_ctx,
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
    elif llm_provider == "nvidia":
        from langchain_openai import ChatOpenAI
        api_key = os.environ.get("NVIDIA_API_KEY")
        model = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
        return ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
            temperature=0.2,
            max_tokens=4096,
        )
    else:
        raise ValueError(f"Unsupported llm_provider: {llm_provider}")

MAX_AGENT_MD_CHARS = 20000


def load_agent_md(repo_path: str) -> str:
    """Read project conventions from AGENT.md at the target repository root if present.

    Returns the formatted section string to append to the system prompt, or empty string.
    Truncates if file exceeds MAX_AGENT_MD_CHARS with a warning.
    """
    if not repo_path:
        return ""

    agent_md_path = os.path.join(repo_path, "AGENT.md")
    if not os.path.isfile(agent_md_path):
        return ""

    try:
        with open(agent_md_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if len(content) > MAX_AGENT_MD_CHARS:
            omitted = len(content) - MAX_AGENT_MD_CHARS
            content = content[:MAX_AGENT_MD_CHARS] + f"\n\n[... AGENT.md truncated, {omitted} characters omitted ...]"
            print(f"[AGENT] Warning: AGENT.md in '{repo_path}' is unusually large ({len(content) + omitted} chars) and was truncated.")

        return f"\n\n## Project-specific notes (from AGENT.md)\n{content.strip()}"
    except Exception as e:
        print(f"[AGENT] Warning: Failed to read AGENT.md: {e}")
        return ""


def build_worker_agent(
    repo_path: str,
    model_name: str = "qwen2.5-coder:7b",
    llm_provider: str = "ollama",
    extra_tools: list = None,
    checkpointer=None,
    custom_system_prompt: str = None,
):
    """Construct the deep agent worker, scoped to 'repo_path'.

    Args:
        repo_path:            Absolute path to the target repository.
        model_name:           Name of the Ollama model to use.
        llm_provider:         LLM provider identifier ('ollama' or 'ollama_cloud').
        extra_tools:          Additional LangChain BaseTool instances to register
                              alongside the FilesystemBackend's built-in tools.
        checkpointer:         Optional LangGraph checkpoint saver (e.g. MemorySaver)
                              to persist conversation state across multiple turns.
        custom_system_prompt: Optional override or pre-built system prompt.
                              If None, WORKER_SYSTEM_PROMPT is combined with AGENT.md if found.

    Returns a LangGraph-compiled agent that can be invoked with 
    'agent.invoke({"messages": [...]})'.
    """
    model = _build_model(model_name, llm_provider)
    backend = PatchedFilesystemBackend(root_dir=repo_path)
    
    if custom_system_prompt is not None:
        effective_prompt = custom_system_prompt
    else:
        project_notes = load_agent_md(repo_path)
        effective_prompt = WORKER_SYSTEM_PROMPT + project_notes
     
    agent = create_deep_agent(
        model=model,
        tools=extra_tools if extra_tools else None,
        system_prompt=effective_prompt,
        backend=backend,
        checkpointer=checkpointer,
    )
    return agent


def build_readonly_worker_agent(
    repo_path: str,
    model_name: str = "qwen2.5-coder:7b",
    llm_provider: str = "ollama",
    extra_tools: list = None,
    checkpointer=None,
):
    """Construct a read-only variant of the worker agent for use in chat turns.

    Identical to build_worker_agent() except it uses ReadonlyFilesystemBackend
    instead of PatchedFilesystemBackend. All write/edit/delete backend calls
    are structurally blocked — the LLM may attempt them but they will always
    return an error without touching disk.

    Args:
        repo_path:    Absolute path to the target repository (for AGENT.md and
                      scoping read-only file access).
        model_name:   LLM model identifier.
        llm_provider: LLM provider string ('ollama', 'ollama_cloud', 'nvidia').
        extra_tools:  Optional list of additional read-only tools to register.
        checkpointer: Optional LangGraph checkpoint saver — should be the SAME
                      instance as the full agent so chat-turn memory is shared.

    Returns:
        A LangGraph-compiled read-only agent.
    """
    model = _build_model(model_name, llm_provider)
    backend = ReadonlyFilesystemBackend(root_dir=repo_path)

    project_notes = load_agent_md(repo_path)
    effective_prompt = CHAT_MODE_SYSTEM_PROMPT + project_notes

    agent = create_deep_agent(
        model=model,
        tools=extra_tools if extra_tools else None,
        system_prompt=effective_prompt,
        backend=backend,
        checkpointer=checkpointer,
    )
    return agent


def run_worker_turn(
    agent,
    instruction: str,
    thread_id: str = None,
    cancellation_event=None,
) -> str:
    """Invoke the worker agent with a single instruction and return the
    text of its final response.

    Token usage is accumulated inside the TerminalLogCallbackHandler and
    written to the module-level ``_last_turn_token_usage`` dict after every
    exit path so that ``controller.loop`` can read it without a signature
    change (preserving all existing test-mock compatibility).

    Args:
        agent:              Compiled LangGraph deep agent instance.
        instruction:        Instruction prompt string for this turn.
        thread_id:          Optional thread ID string for session-persistent state.
        cancellation_event: Optional threading.Event to interrupt long turns.
    """
    global _last_turn_token_usage
    # Reset side-channel before each turn
    _last_turn_token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    from langgraph.errors import GraphRecursionError
    handler = TerminalLogCallbackHandler(cancellation_event=cancellation_event)
    
    config_dict = {
        "callbacks": [handler],
        "recursion_limit": 30,
    }
    if thread_id:
        config_dict["configurable"] = {"thread_id": thread_id}

    try:
        result = agent.invoke(
            {"messages": [{"role": "user", "content": instruction}]},
            config=config_dict,
        )
        # Publish token totals to side-channel before returning
        _last_turn_token_usage = handler.get_turn_token_totals()
        messages = result.get("messages", [])
        if not messages:
            return ""
        last = messages[-1]
        if isinstance(last, dict):
            return last.get("content", "") or ""
        return getattr(last, "content", "") or ""
    except InterruptedError:
        _last_turn_token_usage = handler.get_turn_token_totals()
        print("\n[worker] Turn interrupted by user.")
        return "Turn interrupted by user."
    except GraphRecursionError as gre:
        _last_turn_token_usage = handler.get_turn_token_totals()
        if "[SHORT_CIRCUIT]" in str(gre):
            print("\n[worker] Short-circuit triggered: Code edit/write completed. Ending turn immediately.")
            return "File changes made successfully. Turn completed."
        print("\n[worker] Warning: Agent hit recursion limit of 30 steps (potential loop). Aborting turn.")
        return "Agent hit recursion limit of 30 steps due to repeating operations."
    except Exception as e:
        _last_turn_token_usage = handler.get_turn_token_totals()
        err_msg = str(e)
        if "[SHORT_CIRCUIT]" in err_msg:
            print("\n[worker] Short-circuit triggered: Code edit/write completed. Ending turn immediately.")
            return "File changes made successfully. Turn completed."
        if "Turn interrupted by user" in err_msg or isinstance(e, InterruptedError):
            print("\n[worker] Turn interrupted by user.")
            return "Turn interrupted by user."
        timeout_keywords = ("timed out", "timeout", "read timeout", "connect timeout")
        if any(kw in err_msg.lower() for kw in timeout_keywords):
            timeout_val = os.environ.get("OLLAMA_TIMEOUT", "60")
            print(f"\n[worker] Timeout Error: LLM call exceeded {timeout_val}s timeout: {e}")
            return f"LLM call exceeded {timeout_val}-second timeout ({e})."
        if "localhost" in err_msg or "127.0.0.1" in err_msg or "connection" in err_msg.lower() or "connect" in err_msg.lower():
            print(f"\n[worker] Connection Error: Could not connect to Ollama. Please ensure your local Ollama server is running (usually at http://localhost:11434). Detail: {e}")
            return f"Agent failed with connection error: Could not connect to local Ollama. Please make sure the Ollama service is running locally on http://localhost:11434 and the model is pulled."
        print(f"\n[worker] Error during execution: {e}")
        return f"Agent failed with error: {e}"
