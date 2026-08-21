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

# Multi-step session script with unique surrounding context
user_inputs = [
    # 1. Plain chat-turn 1
    "What does the Stack class in structures.py do and what methods does it have?",
    # 2. Plain chat-turn 2 (references turn 1)
    "What method in Queue has a bug, and what is the difference between Queue and the Stack you just described?",
    # 3. Non-test-driven /run goal (incorporates AGENT.md # END convention)
    "/run edit structures.py: add helper function `is_empty_stack(stack: Stack) -> bool` after Stack class. "
    "Use edit_file replacing '    def peek(self):\\n        if not self._items:\\n            raise IndexError(\"peek from empty stack\")\\n        return self._items[-1]' with '    def peek(self):\\n        if not self._items:\\n            raise IndexError(\"peek from empty stack\")\\n        return self._items[-1]\\n\\ndef is_empty_stack(stack: Stack) -> bool:\\n    return stack.is_empty()\\n    # END'",
    # 4. Test-driven /run goal (fix the bug in Queue.dequeue with full unique context)
    "/run fix all failing unit tests in structures.py: in Queue.dequeue, use edit_file with old_string='    def dequeue(self):\\n        if not self._items:\\n            raise IndexError(\"dequeue from empty queue\")\\n        # BUG: should remove and return the FIRST item (self._items.pop(0))\\n        return self._items.pop()' and new_string='    def dequeue(self):\\n        if not self._items:\\n            raise IndexError(\"dequeue from empty queue\")\\n        # BUG: should remove and return the FIRST item (self._items.pop(0))\\n        return self._items.pop(0)'",
    "exit"
]

def input_mock(prompt=""):
    if "Approve and commit this change?" in prompt:
        print(f"{prompt} y")
        return "y"
    if user_inputs:
        val = user_inputs.pop(0)
        print(f"> {val}")
        return val
    return "exit"

print("==================== STARTING CONTINUOUS SESSION ====================")
with patch("builtins.input", side_effect=input_mock):
    run_interactive(cfg)
print("==================== CONTINUOUS SESSION FINISHED ====================")
