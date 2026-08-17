import argparse
import sys
import langchain
from config import Config
from controller.loop import run

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous fix-until-green coding agent")
    parser.add_argument("--repo", required=True, help="Path to the target repository")
    parser.add_argument("--goal", default=None, help="Goal for the agent to achieve (omitting enters interactive mode)")
    parser.add_argument("--test-cmd", default="pytest", help="Command used to run tests")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="Ask for confirmation before each commit",
    )
    parser.add_argument("--model", default="qwen2.5-coder:7b", help="Model name to use")
    parser.add_argument(
        "--llm-provider",
        choices=["ollama_cloud", "ollama"],
        default=None,
        help="LLM provider to use (defaults to LLM_PROVIDER env var, or 'ollama')",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose/debug execution logs to the terminal",
    )
    parser.add_argument(
        "--lint-cmd",
        default=None,
        help="Optional lint/type-check command to run after each iteration (e.g. 'flake8 .'). When not set, no lint step runs.",
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="Path to the skills directory (default: 'skills/' relative to the project root).",
    )
    parser.add_argument(
        "--mcp-config-path",
        default=None,
        help="Path to MCP configuration JSON file.",
    )
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    
    if args.verbose:
        langchain.debug = True
        
    config_kwargs = {
        "repo_path": args.repo,
        "goal": args.goal or "",
        "test_cmd": args.test_cmd,
        "max_iterations": args.max_iterations,
        "max_seconds": args.max_seconds,
        "require_approval": args.require_approval,
        "model_name": args.model,
        "lint_cmd": args.lint_cmd,
        "skills_dir": args.skills_dir,
    }
    if getattr(args, "mcp_config_path", None) is not None:
        config_kwargs["mcp_config_path"] = args.mcp_config_path
    if args.llm_provider is not None:
        config_kwargs["llm_provider"] = args.llm_provider

        
    config = Config(**config_kwargs)

    if not args.goal:
        from cli.interactive import run_interactive
        run_interactive(config)
        sys.exit(0)

    success = run(config)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

