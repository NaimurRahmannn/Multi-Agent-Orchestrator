from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from agentorchestra.agents.manager import ManagerRouter, ManagerRoutingInterface
from agentorchestra.evaluation.routing_cases import (
    DIAGNOSTIC_ROUTING_CASES,
    REQUIRED_ROUTING_CASES,
    target_page_for_case,
)
from agentorchestra.evaluation.routing_runner import (
    RoutingBenchmarkRunner,
    default_report_path,
    write_routing_benchmark_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AgentOrchestra Manager routing benchmark.")
    parser.add_argument("--include-diagnostics", action="store_true")
    parser.add_argument("--report-path")
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=None,
        help="Delay between live cases (default: 1.0; injected test routers: 0).",
    )
    return parser


def main(argv: Sequence[str] | None = None, router: ManagerRoutingInterface | None = None) -> int:
    args = build_parser().parse_args(argv)
    selected_cases = list(REQUIRED_ROUTING_CASES)
    if args.include_diagnostics:
        selected_cases.extend(DIAGNOSTIC_ROUTING_CASES)

    manager_router = router or ManagerRouter()
    delay_seconds = args.delay_seconds
    if delay_seconds is None:
        delay_seconds = 1.0 if router is None else 0.0
    runner = RoutingBenchmarkRunner(
        manager_router,
        target_page_resolver=target_page_for_case,
        delay_seconds=delay_seconds,
    )
    report = runner.run(selected_cases)
    path = Path(args.report_path) if args.report_path else default_report_path()
    written_path = write_routing_benchmark_report(report, path)

    print(f"structurally valid: {report.structurally_valid_cases}/{report.total_cases}")
    print(f"correct routes: {report.correct_cases}/{report.total_cases}")
    print(f"accuracy: {report.routing_accuracy}")
    print(f"report path: {written_path}")

    required_total = len(REQUIRED_ROUTING_CASES)
    required_results = report.results[:required_total]
    required_valid = sum(result.structurally_valid for result in required_results)
    required_correct = sum(result.routing_correct for result in required_results)
    return 0 if required_valid == required_total and required_correct == required_total else 1
