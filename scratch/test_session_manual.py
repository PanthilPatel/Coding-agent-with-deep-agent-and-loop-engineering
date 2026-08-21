"""Script to test interactive session memory and /run command on 12_stack_queue."""

import os
import sys

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
)

# Simulate conversational inputs:
# 1. Ask what Queue.dequeue does and whether it is correct
# 2. Reference the prior answer to fix the bug
# 3. /run fix all failing unit tests
# 4. exit

print("[TEST RUN] Starting conversational session test...")
run_interactive(cfg)
