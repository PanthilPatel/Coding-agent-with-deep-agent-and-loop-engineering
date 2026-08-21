import os
import sys
from dotenv import load_dotenv
load_dotenv()
from unittest.mock import patch

WORKSPACE = r"d:\Skyllect Intership\Coding agent with deep agents and loop engineering"
sys.path.insert(0, WORKSPACE)
os.chdir(WORKSPACE)

from config import Config
from cli.interactive import run_interactive

repo_path = os.path.join(WORKSPACE, "coding-agent-testing-error-code", "examples", "12_stack_queue")
cfg = Config(
    repo_path=repo_path,
    goal="",
    model_name="qwen2.5-coder:7b",
    llm_provider="ollama",
    max_iterations=3,
)

goal_text = (
    "/run edit structures.py to add a size() method to Stack: "
    "read structures.py, then use edit_file with "
    "old_string='    def pop(self):\\n        if not self._items:\\n            raise IndexError(\"pop from empty stack\")\\n        return self._items.pop()\\n\\n    def peek(self):' "
    "and new_string='    def pop(self):\\n        if not self._items:\\n            raise IndexError(\"pop from empty stack\")\\n        return self._items.pop()\\n\\n    def size(self):\\n        return len(self._items)\\n\\n    def peek(self):'"
)

user_inputs = [goal_text, "exit"]

def input_mock(prompt=""):
    if "Approve and commit this change?" in prompt:
        print(f"{prompt} y")
        return "y"
    if user_inputs:
        val = user_inputs.pop(0)
        print(f"> {val}")
        return val
    return "exit"

with patch("builtins.input", side_effect=input_mock):
    run_interactive(cfg)
