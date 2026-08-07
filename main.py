import argparse
import sys
import langchain
from config import Config
from controller.loop import run

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous fix-until-green coding agent")
    parser.add_argument("--repo", required=True, help="Path to the target repository")
    parser.add_argument("--goal", required=True, help="Goal for the agent to achieve")
    parser.add_argument("--test-cmd", default="pytest", help="Command used to run tests")
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-seconds", type=int, default=1800)
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="Ask for confirmation before each commit",
    )
    parser.add_argument("--model", default="gemma4", help="Model name to use")
    parser.add_argument(
        "--llm-provider",
        choices=["ollama_cloud"],
        default="ollama_cloud",
        help="LLM provider to use",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose/debug execution logs to the terminal",
    )
    return parser.parse_args()

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
    }
    if args.llm_provider is not None:
        config_kwargs["llm_provider"] = args.llm_provider
        
    config = Config(**config_kwargs)

    success = run(config)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

