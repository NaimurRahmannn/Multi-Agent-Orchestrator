from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from agentorchestra.agents.manager import ManagerRouter, ManagerRoutingInterface
from agentorchestra.config import ConfigurationError, ensure_runtime_directories, get_settings
from agentorchestra.exceptions import ManagerExecutionError, ManagerOutputError
from agentorchestra.models import EditRequest, ManagerRunResult


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Route one AgentOrchestra Manager request.")
    parser.add_argument("--target-page", required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--output-path")
    return parser


def main(argv: Sequence[str] | None = None, router: ManagerRoutingInterface | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = EditRequest(target_page=args.target_page, instruction=args.instruction)
        manager_router = router or ManagerRouter()
        result = manager_router.route(request)
        _print_result(result)
        if args.output_path:
            _write_result(result, Path(args.output_path))
    except (ConfigurationError, ManagerExecutionError, ManagerOutputError, ValidationError, ValueError) as exc:
        print(f"Manager routing failed: {_redact(str(exc))}")
        return 1
    return 0


def _print_result(result: ManagerRunResult) -> None:
    plan = result.plan
    print(f"status: {plan.status.value}")
    print(
        "selected specialists: "
        + (", ".join(specialist.value for specialist in plan.selected_specialists) or "none")
    )
    print(f"routing rationale: {plan.routing_rationale}")
    print("assignments:")
    if plan.assignments:
        for assignment in plan.assignments:
            print(f"- {assignment.agent.value}: {assignment.task}")
    else:
        print("- none")
    print("acceptance criteria:")
    if plan.acceptance_criteria:
        for criterion in plan.acceptance_criteria:
            print(f"- {criterion}")
    else:
        print("- none")
    if plan.clarification_question:
        print(f"clarification question: {plan.clarification_question}")
    if plan.rejection_reason:
        print(f"rejection reason: {plan.rejection_reason}")
    print(f"latency ms: {result.latency_ms:.0f}")
    usage = result.token_usage.model_dump(mode="json")
    available_usage = {key: value for key, value in usage.items() if value is not None}
    print(f"token usage: {available_usage if available_usage else 'unavailable'}")


def _write_result(result: ManagerRunResult, output_path: Path) -> None:
    settings = get_settings()
    ensure_runtime_directories(settings)
    path = output_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"report path: {path}")


def _redact(message: str) -> str:
    clean = message.replace("\n", " ")
    try:
        settings = get_settings()
        if settings.groq_api_key:
            secret = settings.groq_api_key.get_secret_value()
            if secret:
                clean = clean.replace(secret, "[redacted]")
    except Exception:
        pass
    return clean[:700]
