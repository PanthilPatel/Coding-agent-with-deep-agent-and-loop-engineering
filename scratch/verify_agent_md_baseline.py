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

repo_path = os.path.join(WORKSPACE, "coding-agent-testing-error-code", "examples", "16_prime_checker")
cfg = Config(
    repo_path=repo_path,
    goal="",
    model_name="qwen2.5-coder:7b",
    llm_provider="ollama",
    max_iterations=2,
)

goal_text = (
    "/run edit primes.py: append a helper function `is_even(n: int) -> bool` that returns `n % 2 == 0` after `nth_prime`. "
    "Use edit_file replacing '    return candidate' with '    return candidate\\ndef is_even(n: int) -> bool:\\n    return n % 2 == 0'"
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
