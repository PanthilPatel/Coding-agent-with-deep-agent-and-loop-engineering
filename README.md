# Autonomous Coding Agent (Deep Agent + Loop Engineering)

An autonomous "fix-until-green" coding agent. Give it a repo and a goal
(e.g. "make all tests pass"), and it will plan, edit code, run tests,
read failures, and retry — looping on its own until the goal is met or
a safety limit is hit.

## Architecture

- **Worker (deep agent)** — plans via a todo list, reads/writes files,
  and can spawn sub-agents for isolated sub-tasks.
- **Controller (loop)** — repeatedly invokes the worker, runs tests,
  checks termination conditions, and enforces iteration/time limits.
- **State/memory** — a JSON file logging every iteration so progress
  survives restarts and the agent doesn't repeat failed strategies
  blindly.

## Setup

```bash
python -m venv venv
source venv/bin/activate        
pip install -r requirements.txt
cp .env           
```

## Usage

```bash
python main.py --repo /path/to/target/repo --goal "make all tests pass" --max-iterations 10
```

Options:
- `--repo` — path to the target repository (required)
- `--goal` — natural language description of what the agent should achieve (required)
- `--test-cmd` — command used to run tests (default: `pytest`)
- `--max-iterations` — safety limit on loop iterations (default: 10)
- `--max-seconds` — safety limit on wall-clock time (default: 1800)
- `--require-approval` — if set, asks for confirmation before each commit

## How it works, iteration by iteration

1. Controller loads state (todo list, last test output, iteration count).
2. Controller checks termination condition (tests pass) — stop if met.
3. Controller checks failure exit condition (max iterations/time) — stop if hit.
4. Controller invokes the worker agent with the goal + latest test failure summary.
5. Worker agent edits files in the repo.
6. Controller runs tests and lint against the changes.
7. Controller logs the result to `state.json`.
8. If the same issue fails twice in a row, controller tells the agent to
   change strategy on the next call.
9. Repeat.

## Notes

- This is backend/CLI-only by design — no frontend is required.
- Runs against the repo directly by default. For safety, consider
  pointing `--repo` at a git worktree or a Docker-mounted copy rather
  than your main working directory until you trust the loop.
