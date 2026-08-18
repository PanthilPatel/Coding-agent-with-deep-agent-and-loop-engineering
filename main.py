import argparse
import os
import sys
import langchain
from config import Config
from controller.loop import run
from orchestrator.engine import DeepAgentOrchestrator


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous fix-until-green coding agent")
    parser.add_argument("--repo", default=None, help="Path to the target repository")
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
    parser.add_argument(
        "--decompose",
        "--orchestrate",
        dest="orchestrate",
        action="store_true",
        help="Decompose the goal into structured subtasks using DeepAgentOrchestrator",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run the automated E2E benchmark suite across target repositories",
    )
    parser.add_argument(
        "--benchmark-dir",
        default="my-buggy-test-repo/examples",
        help="Directory containing target benchmark repositories (default: 'my-buggy-test-repo/examples')",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Comma-separated filters for benchmark repo names (e.g. '01,06' or 'calculator')",
    )
    parser.add_argument(
        "--benchmark-timeout",
        type=int,
        default=300,
        help="Hard wall-clock timeout in seconds per benchmark run (default: 300)",
    )
    parser.add_argument(
        "--output-dir",
        default="benchmark_results",
        help="Directory to save JSON and Markdown benchmark reports (default: 'benchmark_results')",
    )
    return parser.parse_args(args)

def main() -> None:
    args = parse_args()
    
    if args.verbose:
        langchain.debug = True

    # Benchmark Suite Mode
    if args.benchmark:
        from benchmarks.runner import BenchmarkRunner
        from benchmarks.reporter import BenchmarkReporter

        filter_names = None
        if args.filter:
            filter_names = [f.strip() for f in args.filter.split(",") if f.strip()]

        runner = BenchmarkRunner()
        results = runner.run_suite(
            benchmark_dir=args.benchmark_dir,
            filter_names=filter_names,
            goal=args.goal,
            timeout=args.benchmark_timeout,
            test_cmd=args.test_cmd,
            model_name=args.model,
            llm_provider=args.llm_provider,
            max_iterations=args.max_iterations,
            lint_cmd=args.lint_cmd,
            skills_dir=args.skills_dir,
            mcp_config_path=args.mcp_config_path,
        )

        reporter = BenchmarkReporter()
        os.makedirs(args.output_dir, exist_ok=True)
        json_path = os.path.join(args.output_dir, "benchmark_report.json")
        md_path = os.path.join(args.output_dir, "benchmark_report.md")
        reporter.generate_json(results, json_path)
        reporter.generate_markdown(results, md_path)
        reporter.print_summary(results)
        print(f"[BENCHMARK] Reports saved to '{os.path.abspath(args.output_dir)}'")

        all_passed = all(r.passed for r in results) if results else True
        return sys.exit(0 if all_passed else 1)

    if not args.repo:
        print("Error: --repo is required when not in --benchmark mode.", file=sys.stderr)
        return sys.exit(2)
        
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
        return sys.exit(0)

    if getattr(args, "orchestrate", False):
        orchestrator = DeepAgentOrchestrator(config)
        result = orchestrator.run()
        return sys.exit(0 if result.get("success", False) else 1)

    success = run(config)
    return sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
