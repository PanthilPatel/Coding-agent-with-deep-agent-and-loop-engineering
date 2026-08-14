# Phase 5: main.py Changes (Before/After)

## Change 1: Make --goal Optional

### BEFORE (Phase 4)
```python
parser.add_argument("--goal", required=True, help="Goal for the agent to achieve")
```

### AFTER (Phase 5)
```python
parser.add_argument("--goal", help="Goal for the agent to achieve (omit for interactive mode)")
```

**Impact:** `--goal` is now optional. When omitted, interactive mode is triggered.

---

## Change 2: Add Mode Detection and Branching

### BEFORE (Phase 4)
```python
def main() -> None:
    args = parse_args()
    
    if args.verbose:
        langchain.debug = True
        
    config_kwargs = {
        "repo_path": args.repo,
        "goal": args.goal,
        "test_cmd": args.test_cmd,
        "max_iterations": args.max_iterations,
        "max_seconds": args.max_seconds,
        "require_approval": args.require_approval,
        "model_name": args.model,
        "lint_cmd": args.lint_cmd,
        "skills_dir": args.skills_dir,
        "mcp_config_path": args.mcp_config,
    }
    if args.llm_provider is not None:
        config_kwargs["llm_provider"] = args.llm_provider
        
    config = Config(**config_kwargs)

    success = run(config)
    sys.exit(0 if success else 1)
```

### AFTER (Phase 5)
```python
def main() -> None:
    args = parse_args()
    
    if args.verbose:
        langchain.debug = True
    
    # Check if we're in one-shot or interactive mode
    is_interactive = args.goal is None
    
    if is_interactive:
        # Interactive mode: build a config template without a goal
        # The goal will be set per-task in the REPL
        from cli.interactive import run_interactive
        
        # We need a dummy goal to pass Config validation, but interactive.py
        # will override it for each task
        config_template_kwargs = {
            "repo_path": args.repo,
            "goal": "",  # Dummy goal, will be overridden per task
            "test_cmd": args.test_cmd,
            "max_iterations": args.max_iterations,
            "max_seconds": args.max_seconds,
            "require_approval": args.require_approval,
            "model_name": args.model,
            "lint_cmd": args.lint_cmd,
            "skills_dir": args.skills_dir,
            "mcp_config_path": args.mcp_config,
        }
        if args.llm_provider is not None:
            config_template_kwargs["llm_provider"] = args.llm_provider
        
        config_template = Config(**config_template_kwargs)
        run_interactive(config_template)
        sys.exit(0)
    else:
        # One-shot mode: exactly as before
        config_kwargs = {
            "repo_path": args.repo,
            "goal": args.goal,
            "test_cmd": args.test_cmd,
            "max_iterations": args.max_iterations,
            "max_seconds": args.max_seconds,
            "require_approval": args.require_approval,
            "model_name": args.model,
            "lint_cmd": args.lint_cmd,
            "skills_dir": args.skills_dir,
            "mcp_config_path": args.mcp_config,
        }
        if args.llm_provider is not None:
            config_kwargs["llm_provider"] = args.llm_provider
            
        config = Config(**config_kwargs)

        success = run(config)
        sys.exit(0 if success else 1)
```

**Impact:**
- When `--goal` is provided: One-shot mode (unchanged behavior)
- When `--goal` is omitted: Interactive mode (new behavior)
- The one-shot code path is **identical** to Phase 4 (wrapped in `else` block)
- Interactive mode imports `run_interactive` and calls it with a config template

---

## Summary of Changes

1. **Line changed:** 1 (made `--goal` optional)
2. **Lines added:** ~30 (mode detection + interactive branch)
3. **One-shot behavior:** UNCHANGED (exact same code path, just indented in `else` block)
4. **Backward compatibility:** FULL (all existing commands work identically)

## Verification

### One-shot mode still works:
```bash
# These all work exactly as before Phase 5
python main.py --repo ./my-repo --goal "fix tests"
python main.py --repo ./my-repo --goal "add logging" --lint-cmd "flake8"
```

### Interactive mode (new):
```bash
# This now enters interactive mode
python main.py --repo ./my-repo
```

## Testing Confirmation

The test `TestOneShotCLIPreserved::test_one_shot_with_goal_calls_run` verifies that:
1. When `--goal` is provided, `controller.loop.run()` is called (not `run_interactive()`)
2. The Config passed to `run()` has the correct goal
3. The exit code is 0 on success, 1 on failure (unchanged)

**Result:** ✅ PASSED
