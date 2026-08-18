"""Automated E2E Benchmark Reporter.

Generates machine-readable JSON and formatted Markdown reports summarizing
benchmark run outcomes, pass rates, durations, and guarded tool usages.
"""

import json
import os
from dataclasses import asdict
from typing import List, Optional

from benchmarks.runner import BenchmarkResult


class BenchmarkReporter:
    """Exports and formats benchmark results across JSON, Markdown, and console."""

    def generate_json(self, results: List[BenchmarkResult], output_path: str) -> None:
        """Export machine-readable metrics to JSON.

        Args:
            results: List of BenchmarkResult instances.
            output_path: Path where JSON file will be written.
        """
        abs_output = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(abs_output), exist_ok=True)

        total_runs = len(results)
        passed_runs = sum(1 for r in results if r.passed)
        failed_runs = total_runs - passed_runs
        pass_rate = (passed_runs / total_runs * 100.0) if total_runs > 0 else 0.0
        avg_duration = (sum(r.duration_seconds for r in results) / total_runs) if total_runs > 0 else 0.0
        total_iterations = sum(r.iterations for r in results)

        data = {
            "summary": {
                "total_runs": total_runs,
                "passed": passed_runs,
                "failed": failed_runs,
                "pass_rate_percent": round(pass_rate, 2),
                "avg_duration_seconds": round(avg_duration, 2),
                "total_iterations": total_iterations,
            },
            "results": [asdict(r) for r in results],
        }

        with open(abs_output, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def generate_markdown(
        self,
        results: List[BenchmarkResult],
        output_path: Optional[str] = None,
    ) -> str:
        """Format results into a clean Markdown table with summary metrics.

        Args:
            results: List of BenchmarkResult instances.
            output_path: Optional file path to write the markdown to.

        Returns:
            The formatted markdown text.
        """
        total_runs = len(results)
        passed_runs = sum(1 for r in results if r.passed)
        failed_runs = total_runs - passed_runs
        pass_rate = (passed_runs / total_runs * 100.0) if total_runs > 0 else 0.0
        avg_duration = (sum(r.duration_seconds for r in results) / total_runs) if total_runs > 0 else 0.0
        total_iterations = sum(r.iterations for r in results)

        lines = [
            "# Autonomous Coding Agent — Benchmark Report",
            "",
            "## 📊 Summary Metrics",
            "",
            f"- **Total Runs:** {total_runs}",
            f"- **Passed:** {passed_runs}",
            f"- **Failed:** {failed_runs}",
            f"- **Pass Rate:** {pass_rate:.1f}%",
            f"- **Average Duration:** {avg_duration:.2f}s",
            f"- **Total Iterations:** {total_iterations}",
            "",
            "## 📋 Benchmark Results Table",
            "",
            "| Repository | Status | Iterations | Duration (s) | Guarded Tool Calls | Error / Notes |",
            "|:---|:---:|:---:|:---:|:---|:---|",
        ]

        for r in results:
            status_badge = "✅ PASSED" if r.passed else "❌ FAILED"
            tool_calls_str = ", ".join(f"{k}: {v}" for k, v in r.tool_calls_count.items()) if r.tool_calls_count else "None"
            error_note = r.error_message.replace("\n", " ") if r.error_message else "-"
            # Truncate long error notes
            if len(error_note) > 80:
                error_note = error_note[:77] + "..."
            lines.append(
                f"| `{r.repo_name}` | {status_badge} | {r.iterations} | {r.duration_seconds:.2f} | {tool_calls_str} | {error_note} |"
            )

        lines.extend([
            "",
            "> **Note on Tool Calls:** The *Guarded Tool Calls* metric only covers tools routed through the",
            "> `PermissionHarness` confirmation gate (such as `execute_command`, `create_directory`, `move_file`,",
            "> `delete_file`, `list_directory`, and MCP tools). Native operations like `read_file`, `write_file`,",
            "> and `grep` operate directly in the agent backend and are excluded from this count.",
            "",
        ])

        md_content = "\n".join(lines)

        if output_path:
            abs_output = os.path.abspath(output_path)
            os.makedirs(os.path.dirname(abs_output), exist_ok=True)
            with open(abs_output, "w", encoding="utf-8") as f:
                f.write(md_content)

        return md_content

    def print_summary(self, results: List[BenchmarkResult]) -> None:
        """Print clean summary table and metrics to the console."""
        total_runs = len(results)
        passed_runs = sum(1 for r in results if r.passed)
        failed_runs = total_runs - passed_runs
        pass_rate = (passed_runs / total_runs * 100.0) if total_runs > 0 else 0.0
        avg_duration = (sum(r.duration_seconds for r in results) / total_runs) if total_runs > 0 else 0.0
        total_iterations = sum(r.iterations for r in results)

        print("\n" + "=" * 70)
        print("                     BENCHMARK SUITE SUMMARY")
        print("=" * 70)
        print(f"Total Repositories:    {total_runs}")
        print(f"Passed:                {passed_runs} ({pass_rate:.1f}%)")
        print(f"Failed:                {failed_runs}")
        print(f"Average Duration:      {avg_duration:.2f}s")
        print(f"Total Iterations:      {total_iterations}")
        print("-" * 70)
        print(f"{'Repository':<28} {'Status':<10} {'Iter':<6} {'Time(s)':<8} {'Guarded Tool Calls'}")
        print("-" * 70)
        for r in results:
            status = "PASSED" if r.passed else "FAILED"
            tool_calls_str = ", ".join(f"{k}:{v}" for k, v in r.tool_calls_count.items()) if r.tool_calls_count else "-"
            print(f"{r.repo_name:<28} {status:<10} {r.iterations:<6} {r.duration_seconds:<8.2f} {tool_calls_str}")
        print("=" * 70 + "\n")
