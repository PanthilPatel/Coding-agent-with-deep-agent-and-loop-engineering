warning: in the working copy of '.env.example', LF will be replaced by CRLF the next time Git touches it
[1mdiff --git a/.env.example b/.env.example[m
[1mindex cb6aa97..2adbe47 100644[m
[1m--- a/.env.example[m
[1m+++ b/.env.example[m
[36m@@ -1,3 +1,3 @@[m
[31m-LLM_PROVIDER=ollama_cloud [m
[31m-OLLAMA_API_KEY=240dcbe5cf0f4286be65c7d2d1505c58.vxbvib-UYCXN3FsbfUU1aH2K[m
[31m-OLLAMA_MODEL=gemma4[m
[32m+[m[32mLLM_PROVIDER=ollama[m
[32m+[m[32mOLLAMA_API_KEY=your_ollama_api_key_here[m
[32m+[m[32mOLLAMA_MODEL=qwen2.5-coder:7b[m
[1mdiff --git a/agents/worker.py b/agents/worker.py[m
[1mindex f7d92b9..138be0c 100644[m
[1m--- a/agents/worker.py[m
[1m+++ b/agents/worker.py[m
[36m@@ -11,11 +11,142 @@[m [mLogging convention:[m
 """[m
 [m
 import os[m
[32m+[m[32mimport json[m
[32m+[m[32mimport re[m
 import datetime[m
 from deepagents import create_deep_agent, SubAgent[m
 from deepagents.backends import FilesystemBackend[m
[32m+[m[32mclass PatchedFilesystemBackend(FilesystemBackend):[m
[32m+[m[32m    def __init__(self, root_dir, *args, **kwargs):[m
[32m+[m[32m        super().__init__(root_dir=root_dir, *args, **kwargs)[m
[32m+[m[32m        self.my_root_dir = root_dir[m
[32m+[m
[32m+[m[32m    def _resolve_path(self, path: str) -> str:[m
[32m+[m[32m        cleaned_path = path.lstrip("/\\")[m
[32m+[m[41m        [m
[32m+[m[32m        normalized_repo = os.path.abspath(self.my_root_dir).replace("\\", "/").rstrip("/")[m
[32m+[m[32m        normalized_path = cleaned_path.replace("\\", "/")[m
[32m+[m[41m        [m
[32m+[m[32m        repo_parts = normalized_repo.split("/")[m
[32m+[m[32m        for i in range(len(repo_parts)):[m
[32m+[m[32m            suffix = "/".join(repo_parts[i:])[m
[32m+[m[32m            if suffix and normalized_path.startswith(suffix + "/"):[m
[32m+[m[32m                cleaned_path = normalized_path[len(suffix) + 1:][m
[32m+[m[32m                break[m
[32m+[m[41m                [m
[32m+[m[32m        return super()._resolve_path(cleaned_path)[m
[32m+[m
[32m+[m[32m    def write(self, file_path: str, content: str, *args, **kwargs):[m
[32m+[m[32m        base = os.path.basename(file_path).lower()[m
[32m+[m[32m        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):[m
[32m+[m[32m            raise ValueError("Modifying, creating, or rewriting test files is strictly forbidden. You must only fix bugs in implementation source files (e.g. inventory.py, discounts.py).")[m
[32m+[m[32m        return super().write(file_path=file_path, content=content, *args, **kwargs)[m
[32m+[m
[32m+[m[32m    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False, *args, **kwargs):[m
[32m+[m[32m        base = os.path.basename(file_path).lower()[m
[32m+[m[32m        if base.startswith("test_") or base.endswith("_test.py") or "tests/" in file_path.replace("\\", "/"):[m
[32m+[m[32m            raise ValueError("Modifying or editing test files is strictly forbidden. You must only fix bugs in implementation source files (e.g. inventory.py, discounts.py).")[m
[32m+[m[32m        return super().edit(file_path=file_path, old_string=old_string, new_string=new_string, replace_all=replace_all, *args, **kwargs)[m
[32m+[m
[32m+[m[32m    def execute(self, command: str, *args, **kwargs) -> Any:[m
[32m+[m[32m        from deepagents.backends.protocol import ExecuteResponse[m
[32m+[m[32m        return ExecuteResponse([m
[32m+[m[32m            output="[System Notice]: The controller automatically runs pytest after your turn. Do NOT call execute or pytest. Please edit the source files (inventory.py, discounts.py) to fix the bugs and summarize your changes to conclude.",[m
[32m+[m[32m            exit_code=0[m
[32m+[m[32m        )[m
 from langchain_ollama import ChatOllama[m
 from langchain_core.callbacks import BaseCallbackHandler[m
[32m+[m[32mfrom langchain_core.messages import AIMessage, BaseMessage[m
[32m+[m[32mfrom typing import List, Any[m
[32m+[m
[32m+[m[32mclass PatchedChatOllama(ChatOllama):[m
[32m+[m[32m    def _generate(self, *args, **kwargs):[m
[32m+[m[32m        result = super()._generate(*args, **kwargs)[m
[32m+[m[32m        for generation in result.generations:[m
[32m+[m[32m            generation.message = self._parse_fallback_tool_calls(generation.message)[m
[32m+[m[32m        return result[m
[32m+[m
[32m+[m[32m    async def _agenerate(self, *args, **kwargs):[m
[32m+[m[32m        result = await super()._agenerate(*args, **kwargs)[m
[32m+[m[32m        for generation in result.generations:[m
[32m+[m[32m            generation.message = self._parse_fallback_tool_calls(generation.message)[m
[32m+[m[32m        return result[m
[32m+[m
[32m+[m[32m    def _parse_fallback_tool_calls(self, response: BaseMessage) -> BaseMessage:[m
[32m+[m[32m        if not isinstance(response, AIMessage):[m
[32m+[m[32m            return response[m
[32m+[m[41m            [m
[32m+[m[32m        if response.tool_calls:[m
[32m+[m[32m            for tc in response.tool_calls:[m
[32m+[m[32m                args = tc.get("args", {})[m
[32m+[m[32m                name = tc.get("name")[m
[32m+[m[32m                if isinstance(args, dict):[m
[32m+[m[32m                    if name == "ls" and "path" not in args:[m
[32m+[m[32m                        keys = [k for k in args.keys() if isinstance(k, str) and not k.startswith("arg")][m
[32m+[m[32m                        path_val = keys[0] if keys else "."[m
[32m+[m[32m                        if path_val in ("/path/to/repo", "/repo", "/", ""):[m
[32m+[m[32m                            path_val = "."[m
[32m+[m[32m                        tc["args"] = {"path": path_val}[m
[32m+[m[32m                    elif name in ("read_file", "edit_file", "write_file") and "file_path" not in args:[m
[32m+[m[32m                        if "path" in args:[m
[32m+[m[32m                            args["file_path"] = args.pop("path")[m
[32m+[m[32m            return response[m
[32m+[m
[32m+[m[32m        content = response.content.strip()[m
[32m+[m[32m        if not content:[m
[32m+[m[32m            return response[m
[32m+[m
[32m+[m[32m        # Try parsing JSON blocks: ```json ... ``` or directly {...}[m
[32m+[m[32m        json_str = None[m
[32m+[m[32m        if "```json" in content:[m
[32m+[m[32m            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)[m
[32m+[m[32m            if match:[m
[32m+[m[32m                json_str = match.group(1).strip()[m
[32m+[m[32m        elif content.startswith("{") and content.endswith("}"):[m
[32m+[m[32m            json_str = content[m
[32m+[m
[32m+[m[32m        if json_str:[m
[32m+[m[32m            try:[m
[32m+[m[32m                data = json.loads(json_str)[m
[32m+[m[32m                tool_calls = [][m
[32m+[m[41m                [m
[32m+[m[32m                # Check if it's a list of tool calls or a single one[m
[32m+[m[32m                items = data if isinstance(data, list) else [data][m
[32m+[m[41m                    [m
[32m+[m[32m                for idx, item in enumerate(items):[m
[32m+[m[32m                    if isinstance(item, dict):[m
[32m+[m[32m                        name = item.get("name")[m
[32m+[m[32m                        args = item.get("arguments") or item.get("args") or {}[m
[32m+[m[32m                        if name:[m
[32m+[m[32m                            if isinstance(args, str):[m
[32m+[m[32m                                try:[m
[32m+[m[32m                                    args = json.loads(args)[m
[32m+[m[32m                                except Exception:[m
[32m+[m[32m                                    pass[m
[32m+[m[32m                            if isinstance(args, dict):[m
[32m+[m[32m                                if name == "ls" and "path" not in args:[m
[32m+[m[32m